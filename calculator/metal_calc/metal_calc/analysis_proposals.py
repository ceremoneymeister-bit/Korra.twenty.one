"""Validate unverified per-source proposals; source ownership is server-derived."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json

from jsonschema import Draft202012Validator

from .analysis_recipe import proposal_schema
from .errors import InvalidState

CAPS = {"positions": 100, "facts": 300, "evidence": 500, "relations": 200, "issues": 100}


def _bounded(value, depth=0):
    if depth > 16:
        raise InvalidState("Предложение слишком глубоко вложено")
    if isinstance(value, str) and len(value) > 4000:
        raise InvalidState("Текст предложения превышает допустимый размер")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise InvalidState("Ключи предложения должны быть строками")
            _bounded(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 500:
            raise InvalidState("Слишком много элементов предложения")
        for child in value:
            _bounded(child, depth + 1)


def validate_proposal(proposal, *, source, page_count, viewed_pages, observation):
    _bounded(proposal)
    try:
        encoded = json.dumps(proposal, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidState("Предложение должно быть корректным JSON") from exc
    if len(encoded) > 200 * 1024:
        raise InvalidState("Предложение превышает 200 КиБ")
    errors = list(Draft202012Validator(proposal_schema()).iter_errors(proposal))
    if errors:
        error = errors[0]
        path = ".".join(map(str, error.absolute_path)) or "proposal"
        raise InvalidState(f"Предложение не соответствует схеме: {path}")
    for key, limit in CAPS.items():
        if len(proposal[key]) > limit:
            raise InvalidState(f"Слишком много элементов: {key}")
    if not proposal["positions"] and not proposal["issues"]:
        raise InvalidState("Укажите найденные позиции или конкретную причину отсутствия состава")
    kind = observation.get("document_type")
    if kind == "pdf":
        if type(page_count) is not int or page_count < 1 or set(viewed_pages) != set(range(1, page_count + 1)):
            raise InvalidState("Перед публикацией просмотрите каждую страницу PDF")
    elif kind != "xlsx":
        raise InvalidState("Этот формат пока не допускается к смысловому разбору")

    def identifiers(rows, key):
        values = [row[key] for row in rows if key in row]
        if len(set(values)) != len(values):
            raise InvalidState(f"Повтор идентификатора: {key}")
        return set(values)

    position_ids = identifiers(proposal["positions"], "position_id")
    fact_ids = identifiers(proposal["facts"], "fact_id")
    evidence_ids = identifiers(proposal["evidence"], "evidence_id")
    issue_ids = identifiers(proposal["issues"], "issue_id")
    identifiers(proposal["relations"], "relation_id")
    products = {p["product_id"] for p in proposal["positions"]}
    subjects = position_ids | products | {source["source_id"]}
    observed_cells = {(sheet["name"], cell["cell"]) for sheet in observation.get("sheets", [])
                      for cell in sheet.get("cells", [])}

    def refs(ids, allowed, name):
        if not set(ids) <= allowed:
            raise InvalidState(f"Неизвестная ссылка: {name}")

    def quantity(value):
        refs(value["evidence_ids"], evidence_ids, "quantity.evidence_ids")
        if value["basis"] == "operator_statement":
            raise InvalidState("Модель не записывает решения сотрудника")
        if value["value"] is not None:
            if Decimal(value["value"]) <= 0 or not value["unit"] or value["basis"] != "explicit_source":
                raise InvalidState("Для количества нужны положительное значение, единица и явный источник")

    for evidence in proposal["evidence"]:
        if evidence["source_id"] != source["source_id"] or evidence["source_sha256"] != source["sha256"]:
            raise InvalidState("Доказательство относится к другому исходнику")
        locator = evidence["locator"]
        if locator["kind"] != kind:
            raise InvalidState("Тип ссылки не соответствует документу")
        if kind == "pdf":
            if locator["page"] not in viewed_pages:
                raise InvalidState("Страница доказательства не просмотрена")
        elif (locator["sheet"], locator["cell"]) not in observed_cells:
            # Ranges must be split into concrete observed cells in this slice.
            raise InvalidState("Укажите конкретную прочитанную XLSX-ячейку")

    quantity(proposal["quantity"])
    facts_by_id = {fact["fact_id"]: fact for fact in proposal["facts"]}
    for position in proposal["positions"]:
        quantity(position["quantity"])
        refs(position["role_evidence_ids"], evidence_ids, "role_evidence_ids")
        refs(position["blocking_issue_ids"], issue_ids, "blocking_issue_ids")
        refs(position.get("requirement_fact_ids", []), fact_ids, "requirement_fact_ids")
        own_subjects = {position["position_id"], position["product_id"]}
        for key, field in (("designation_fact_id", "designation"), ("variant_fact_id", "variant")):
            if position.get(key) is not None:
                refs([position[key]], fact_ids, key)
                fact = facts_by_id[position[key]]
                if fact["subject_id"] not in own_subjects or fact["field_key"] != field:
                    raise InvalidState(f"Ссылка {key} должна указывать на {field} этой позиции или её изделия")
        for fact_id in position.get("requirement_fact_ids", []):
            fact = facts_by_id[fact_id]
            if fact["subject_id"] not in own_subjects or fact["field_key"] != "requirement":
                raise InvalidState("Требование должно относиться к этой позиции или её изделию")
        if position["role"] != "unknown" and not position["role_evidence_ids"]:
            raise InvalidState("Для участия позиции в работе нужен источник")

    for fact in proposal["facts"]:
        if fact["method"] == "operator_statement":
            raise InvalidState("Модель не записывает решения сотрудника")
        if (fact["field_key"] in {"designation", "variant", "name", "material", "requirement"}
                and fact["normalized_value"] is not None and not isinstance(fact["normalized_value"], str)):
            raise InvalidState("Обозначение, исполнение, название, материал и требование должны быть текстом")
        refs([fact["subject_id"]], subjects, "fact.subject_id")
        refs(fact["evidence_ids"], evidence_ids, "fact.evidence_ids")
        refs(fact.get("dependency_fact_ids", []), fact_ids, "fact.dependency_fact_ids")
        if fact["normalized_value"] is not None and not fact["evidence_ids"]:
            raise InvalidState("Для найденного факта нужен источник")
        if fact["normalized_value"] is None and not fact["unknown_reason"]:
            raise InvalidState("Для неизвестного факта нужна причина")

    graph = {}
    for relation in proposal["relations"]:
        refs(relation["evidence_ids"], evidence_ids, "relation.evidence_ids")
        if not relation["evidence_ids"]:
            raise InvalidState("Для связи изделий нужен источник")
        for endpoint in (relation["from"], relation["to"]):
            refs([endpoint["id"]], subjects, "relation.endpoint")
        if "quantity_per_parent" in relation:
            quantity(relation["quantity_per_parent"])
        if relation["kind"] == "component_of":
            graph.setdefault(relation["from"]["id"], []).append(relation["to"]["id"])
    done = set()

    def visit(node, active):
        if node in active:
            raise InvalidState("Состав сборки содержит циклическую связь")
        if node in done:
            return
        for target in graph.get(node, []):
            visit(target, active | {node})
        done.add(node)
    for node in graph:
        visit(node, set())
    for issue in proposal["issues"]:
        refs(issue.get("subject_ids", []), subjects, "issue.subject_ids")
        refs(issue.get("evidence_ids", []), evidence_ids, "issue.evidence_ids")
    result = deepcopy(proposal)
    result["status"] = "review_required"
    return result
