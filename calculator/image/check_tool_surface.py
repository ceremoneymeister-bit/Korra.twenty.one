"""Поверхность MCP-инструментов расчётчика — машинный гейт сборки.

Зачем это существует. Каталог ставок стал доступен на запись изнутри
контейнера: иначе методолог не смогла бы вводить данные предприятия сама.
Барьер «никто в контейнере физически не может изменить деньги» мы этим
разменяли, и на его месте остался другой — у агента нет инструмента, которым
можно записать пак. Барьер, который держится на дисциплине, надо проверять
машиной, иначе он держится до первого удобного случая.

Раньше здесь стояло ``assert len(TOOL_NAMES) == 8``. Длина не годится: она не
меняется при переименовании инструмента и не заметит подмену одного другим.
Сверяем точные множества имён — любое новое имя валит сборку и требует
осознанного решения, а не молча уезжает в образ.
"""
from __future__ import annotations

import sys

from metal_calc.mcp_server import (
    PIPELINE_TOOL_NAMES,
    ROLE_TOOLS,
    TOOL_NAMES,
    V3_TOOL_NAMES,
)

EXPECTED_V1 = {
    "ingest_attachment",
    "analyze_drawing",
    "calculate_quote",
    "render_quote_xlsx",
    "order_upsert",
    "order_get",
    "order_list",
    "order_stats",
}

EXPECTED_PIPELINE = {
    # Только чтение состава данных предприятия. Записи паков в MCP нет
    # и не должно появиться: публикация живёт в CLI методолога.
    "rates_catalog",
    "pipeline_status",
    "blank_cost",
    "supply_confirm",
    "route_propose",
    "stage_approve",
    "pipeline_repack",
    "time_calc",
    "quote_build",
}

EXPECTED_V3 = {
    # Схема разделения V2 (28.08.2026). Записи паков нет и здесь: реестр
    # ставок и парк публикует только CLI методолога.
    "workflow_status",
    "report_get",
    "render_book_xlsx",
    "process_catalog",
    "input_freeze",
    "bom_upsert",
    "route_variants_propose",
    "route_freeze",
    "blank_drivers_set",
    "time_norms_set",
    "book_assemble",
    "calc_revision_open",
    "qa_run_mechanical",
}

HUMAN_ONLY_WRITES = {"qa_verdict", "route_return", "manual_review_complete"}

#: Записи, которых рабочая роль не должна видеть НИКОГДА (F10):
#: чужая зона у заготовки/нормировщика/технолога и любой QA-вердикт.
FORBIDDEN_BY_ROLE = {
    "front": {"qa_verdict"},
    "tech": {"blank_drivers_set", "time_norms_set", "book_assemble", "qa_verdict",
             "qa_run_mechanical", "supply_confirm", "input_freeze"},
    "supply": {"bom_upsert", "route_variants_propose", "route_freeze", "book_assemble",
               "qa_verdict", "qa_run_mechanical", "time_norms_set", "input_freeze"},
    "norm": {"bom_upsert", "route_variants_propose", "route_freeze", "book_assemble",
             "qa_verdict", "qa_run_mechanical", "blank_drivers_set", "supply_confirm",
             "input_freeze"},
    "book_machine": {
        "input_freeze", "bom_upsert", "route_variants_propose", "route_freeze",
        "route_return", "blank_drivers_set", "time_norms_set", "qa_verdict",
        "report_get", "render_book_xlsx", "supply_confirm",
    },
}

EXPECTED_BOOK_MACHINE = {
    "workflow_status", "order_get", "book_assemble", "qa_run_mechanical"
}


def _compare(label: str, actual: tuple[str, ...], expected: set[str]) -> list[str]:
    seen = set(actual)
    problems = []
    if len(seen) != len(actual):
        problems.append(f"{label}: duplicate names in {actual!r}")
    if seen - expected:
        problems.append(f"{label}: unexpected tools {sorted(seen - expected)!r}")
    if expected - seen:
        problems.append(f"{label}: missing tools {sorted(expected - seen)!r}")
    return problems


def main() -> int:
    problems = _compare("TOOL_NAMES", TOOL_NAMES, EXPECTED_V1)
    problems += _compare("PIPELINE_TOOL_NAMES", PIPELINE_TOOL_NAMES, EXPECTED_PIPELINE)
    problems += _compare("V3_TOOL_NAMES", V3_TOOL_NAMES, EXPECTED_V3)
    overlap = set(TOOL_NAMES) & set(PIPELINE_TOOL_NAMES)
    if overlap:
        problems.append(f"v1 and pipeline surfaces overlap: {sorted(overlap)!r}")
    # supply_confirm намеренно общий (диспетчер по типу заказа) — остальное
    # между legacy и V3 пересекаться не должно.
    v3_overlap = (set(V3_TOOL_NAMES) & (set(TOOL_NAMES) | set(PIPELINE_TOOL_NAMES)))
    if v3_overlap:
        problems.append(f"v3 overlaps legacy surfaces: {sorted(v3_overlap)!r}")
    union = set(TOOL_NAMES) | set(PIPELINE_TOOL_NAMES) | set(V3_TOOL_NAMES) | {"supply_confirm"}
    for role, tools in sorted(ROLE_TOOLS.items()):
        strays = set(tools) - union
        if strays:
            problems.append(f"role {role}: tools outside every surface {sorted(strays)!r}")
        human_writes = set(tools) & HUMAN_ONLY_WRITES
        if human_writes:
            problems.append(
                f"role {role}: human-only writes exposed to model {sorted(human_writes)!r}"
            )
        forbidden = set(tools) & FORBIDDEN_BY_ROLE.get(role, set())
        if forbidden:
            problems.append(f"role {role}: sees alien writes {sorted(forbidden)!r}")
    if set(ROLE_TOOLS.get("book_machine", ())) != EXPECTED_BOOK_MACHINE:
        problems.append(
            "role book_machine: surface is not exact: "
            f"{sorted(ROLE_TOOLS.get('book_machine', ()))}"
        )
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "Поверхность инструментов изменилась. Если инструмент добавлен "
            "намеренно — обновите этот файл и объясните в описании изменения, "
            "почему он не даёт агенту записывать данные предприятия и не "
            "нарушает границы ролей схемы V2.",
            file=sys.stderr,
        )
        return 78
    print(
        f"tool surface ok: {len(TOOL_NAMES)} v1 + {len(PIPELINE_TOOL_NAMES)} pipeline "
        f"+ {len(V3_TOOL_NAMES)} v3; roles: "
        + ", ".join(f"{role}={len(tools)}" for role, tools in sorted(ROLE_TOOLS.items()))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
