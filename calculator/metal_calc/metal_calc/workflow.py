"""Статусная машина схемы разделения V2 (методолог LD, 28.08.2026).

Чистые функции без БД и без часов: сервис (service3) вызывает их внутри
``registry.mutate`` под блокировкой. Статусы — дословно термины схемы,
UPPERCASE: это контракт с методологом, а не наша выдумка, и человек в
документе и на экране должен видеть одни и те же слова.

Главные инварианты машины (закреплены тестами):

* ``ROUTE_FROZEN`` обязателен всегда, даже при единственном варианте;
* формальный возврат маршрута ровно один: второй — ``BLOCK_FOR_TECH_REVIEW``
  и человеческий разбор, автоматический цикл остановлен;
* ``READY_FOR_LD`` требует три PASS трёх гейтов к ОДНОЙ calculation_revision;
  ``NO_EVIDENCE`` не равен PASS;
* любой ADJUST поднимает calculation_revision — прежние квитанции QA
  становятся историей, всё начинается с механического гейта.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import InvalidState
from .intake import SourceManifestEntry

#: Статусы процесса — дословно из схемы V2. Порядок — хронология пути.
STATUSES = (
    "INPUT_FROZEN",
    "BOM_VALIDATED",
    "ROUTE_OPTIONS_READY",
    "PRELIMINARY_READY",  # M2: предварительная оценка нескольких вариантов
    "ROUTE_SELECTED",  # M2
    "ROUTE_FROZEN",
    "DETAILED_COSTING",
    "COSTING_COMPLETE",
    "BOOK_ASSEMBLED",
    "QA_MECHANICAL_PASS",
    "QA_TECHNOLOGICAL_PASS",
    "READY_FOR_LD",
    "BLOCK_FOR_TECH_REVIEW",
)

#: Русские названия для панели и сообщений. Машина живёт на UPPERCASE-кодах.
STATUS_TITLES = {
    "INPUT_FROZEN": "Вход зафиксирован",
    "BOM_VALIDATED": "Состав проверен",
    "ROUTE_OPTIONS_READY": "Варианты маршрута готовы",
    "PRELIMINARY_READY": "Предварительная оценка готова",
    "ROUTE_SELECTED": "Маршрут выбран",
    "ROUTE_FROZEN": "Маршрут заморожен",
    "DETAILED_COSTING": "Подробный расчёт",
    "COSTING_COMPLETE": "Расчёт стадий завершён",
    "BOOK_ASSEMBLED": "Книга собрана",
    "QA_MECHANICAL_PASS": "Механическая проверка пройдена",
    "QA_TECHNOLOGICAL_PASS": "Технологическая проверка пройдена",
    "READY_FOR_LD": "Готово для решения человека",
    "BLOCK_FOR_TECH_REVIEW": "Остановлено: нужен технологический разбор",
}

QA_GATES = ("mechanical", "technological", "commercial")
QA_VERDICTS = ("PASS", "ADJUST", "BLOCK", "NO_EVIDENCE")

#: Кто может быть адресатом возврата. tech здесь нет намеренно: возврат
#: к технологу — это изменение маршрута, у него свой формальный путь
#: (route_return) со счётчиком, и прятать его в обычный ADJUST значило бы
#: обходить лимит одного возврата.
ADJUST_OWNERS = ("supply", "norm", "front")

#: Статусы, из которых разрешён формальный возврат маршрута: маршрут уже
#: заморожен, решение человеку ещё не передано.
ROUTE_RETURN_FROM = frozenset(
    {
        "ROUTE_FROZEN",
        "DETAILED_COSTING",
        "COSTING_COMPLETE",
        "BOOK_ASSEMBLED",
        "QA_MECHANICAL_PASS",
        "QA_TECHNOLOGICAL_PASS",
    }
)


def title(status: str) -> str:
    return STATUS_TITLES.get(status, status)


def require_status(workflow: dict[str, Any], allowed: tuple[str, ...], action_ru: str) -> None:
    """Отказ по-русски с текущим и ожидаемым статусом.

    «Invalid state» не говорит агенту, что делать дальше; название статуса
    из схемы — говорит, потому что схема у методолога перед глазами.
    """
    status = workflow.get("status")
    if status not in allowed:
        raise InvalidState(
            f"{action_ru} нельзя в статусе «{title(str(status))}» ({status}); "
            f"ожидается: {', '.join(allowed)}"
        )


def new_workflow(
    *,
    quantity: int,
    kd_revision: str,
    source_manifest: list[SourceManifestEntry],
    calculator_version: str,
    process_catalog_version: str,
    rate_registry_version: str | None,
    input_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "status": "INPUT_FROZEN",
        "calculation_revision": 1,
        "route_return_count": 0,
        "quantity": quantity,
        "kd_revision": kd_revision,
        "source_manifest": source_manifest,
        "calculator_version": calculator_version,
        "process_catalog_version": process_catalog_version,
        "rate_registry_version": rate_registry_version,
        **input_metadata,
    }


def qa_receipts(state: dict[str, Any]) -> dict[str, Any]:
    """Квитанции QA текущей calculation_revision (и только её)."""
    revision = str(state["workflow"]["calculation_revision"])
    return state.setdefault("qa", {}).setdefault(revision, {})


def gate_requirement(gate: str, workflow: dict[str, Any]) -> None:
    """Последовательность гейтов: каждый требует статуса после предыдущего."""
    if gate not in QA_GATES:
        raise InvalidState(f"Неизвестный QA-гейт «{gate}»; есть: {', '.join(QA_GATES)}")
    required_status = {
        "mechanical": ("BOOK_ASSEMBLED",),
        "technological": ("QA_MECHANICAL_PASS",),
        "commercial": ("QA_TECHNOLOGICAL_PASS",),
    }[gate]
    require_status(workflow, required_status, f"Гейт «{gate}»")


def apply_gate_pass(state: dict[str, Any], gate: str) -> None:
    workflow = state["workflow"]
    workflow["status"] = {
        "mechanical": "QA_MECHANICAL_PASS",
        "technological": "QA_TECHNOLOGICAL_PASS",
        "commercial": "READY_FOR_LD",
    }[gate]


def bump_revision(
    state: dict[str, Any],
    *,
    to_status: str,
    reason: str,
    by: str,
    at: str,
    drop: tuple[str, ...] = ("blank", "time", "book"),
) -> None:
    """Новая calculation_revision: прежние расчёты и квитанции — в историю.

    История не стирается никогда: цену могли уже назвать заказчику, и
    пересчёт, который переписывает прошлое, делает журнал враньём.

    ``drop`` — какие части перестают быть живыми: адресный ADJUST снимает
    только зону виновника (blank у supply, time у norm, book у front), а
    смена маршрута или ставок — всё. Книга и квитанции QA падают всегда:
    они собраны из прежней ревизии по построению. Целая часть чужой зоны
    остаётся живой — её digest-цепочка сама скажет, если она устарела.
    """
    workflow = state["workflow"]
    revision = int(workflow["calculation_revision"])
    # deepcopy обязателен: без него история держит ССЫЛКИ на живые dict, и
    # последующие pop/правки редактируют «архив» задним числом — история,
    # которую можно изменить, хуже отсутствующей (находка Codex M7).
    state.setdefault("workflow_history", []).append(
        deepcopy(
            {
                "calculation_revision": revision,
                "reason": reason,
                "by": by,
                "at": at,
                "status_before": workflow["status"],
                "costing": state.get("costing"),
                "book": state.get("book"),
                "qa": (state.get("qa") or {}).get(str(revision)),
            }
        )
    )
    costing = state.get("costing") or {}
    for part in ("blank", "time"):
        if part in drop:
            costing.pop(part, None)
    if not (set(costing) - {"pack_fingerprint"}):
        state.pop("costing", None)
    state.pop("book", None)
    (state.get("qa") or {}).pop(str(revision), None)
    workflow["calculation_revision"] = revision + 1
    workflow["status"] = to_status


def route_return(state: dict[str, Any], *, reason: str, by: str, at: str) -> str:
    """Один формальный возврат маршрута; второй — стоп до человека.

    Второй возврат НЕ исключение, а переход в ``BLOCK_FOR_TECH_REVIEW``:
    исключение внутри ``registry.mutate`` откатило бы транзакцию, и стоп
    существовал бы только в тексте отказа — а он обязан пережить сессию и
    быть виден на экране «Заказы» до решения человека.
    """
    workflow = state["workflow"]
    require_status(workflow, tuple(sorted(ROUTE_RETURN_FROM)), "Возврат маршрута")
    count = int(workflow.get("route_return_count", 0))
    if count >= 1:
        workflow["status"] = "BLOCK_FOR_TECH_REVIEW"
        return "BLOCK_FOR_TECH_REVIEW"
    workflow["route_return_count"] = count + 1
    variants = state.get("route_variants") or {}
    for variant in variants.values():
        if variant.get("status") in {"frozen", "selected"}:
            variant["status"] = "superseded"
    bump_revision(
        state, to_status="ROUTE_OPTIONS_READY", reason=f"route_return: {reason}", by=by, at=at
    )
    # КП подписано digest конкретного замороженного маршрута. После возврата
    # оно остаётся в workflow_history, но не может быть current-pointer для
    # нового маршрута (в том числе маршрута уже без этого outsource-шага).
    costing = state.get("costing") or {}
    costing.pop("contractor_quotes", None)
    costing.pop("pack_fingerprint", None)
    if not costing:
        state.pop("costing", None)
    return "ROUTE_OPTIONS_READY"


__all__ = [
    "ADJUST_OWNERS",
    "QA_GATES",
    "QA_VERDICTS",
    "ROUTE_RETURN_FROM",
    "STATUSES",
    "STATUS_TITLES",
    "apply_gate_pass",
    "bump_revision",
    "gate_requirement",
    "new_workflow",
    "qa_receipts",
    "require_status",
    "route_return",
    "title",
]
