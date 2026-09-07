"""Глобальный каталог операций `metal_processes@1.0.0`.

Постоянная идентичность операции — `process_code`; строка Excel-книги любого
предприятия — только место вывода и живёт в версионном адаптере, никогда
здесь. Источник каталога — предложение методолога (LD) от 28.08.2026
(`PROCESS_CODE_CATALOG_PROPOSAL_V1.json`, sha256 10db07bd…), перенесённое в
код дословно: каталог поставляется ОБРАЗОМ, а не паком. Смена состава — новая
версия каталога и новый релиз расчётчика; какой версией считался заказ,
записано в самом заказе (`process_catalog_version`).

Парк конкретного предприятия — ПОДМНОЖЕСТВО каталога и живёт в паке данных
(`packs2`, секция `process_park`): у одного предприятия парк усечённый, у
другого полный, а коды у всех одни и те же. Код, которого нет в парке, для
агента не «неизвестен», а «технически возможен, но не у нас» — это разные
ответы, и оба должны быть честными.

`formula_family` — мост в расчётное ядро: какому семейству формул timenorms
принадлежит операция (turning/milling/edm/grinding). Семейства нет у
формообразования, сварки, покрытий и сборки — их время в M1 не считается
формулой, а операция либо тарифицируется ставкой из реестра, либо явно
уходит в подряд/неоценённое. Ноль вместо честного «не считаем» запрещён.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import InvalidState

CATALOG_VERSION = "metal_processes@1.0.0"

#: Владельцы расчёта в терминах ролей движка (METAL_CALC_ROLE):
#: supply — Агент 2 (материалы/закупка/заготовка), norm — Агент 3 (нормировщик).
#: `inherit` — у OTHER.SPECIFIED владельца определяет фактический процесс.
COST_OWNERS = ("supply", "norm", "inherit")

BLANK_CUTOFF_METHODS = ("BAND_SAW", "ANGLE_GRINDER", "MECHANICAL_PIPE_CUTTER")


@dataclass(frozen=True)
class ProcessSpec:
    code: str
    title_ru: str
    cost_owner: str  # supply | norm | inherit
    formula_family: str | None = None  # turning | milling | edm | grinding | None
    method_codes: tuple[str, ...] = ()
    method_required: bool = False
    #: OTHER.SPECIFIED — совместимый слот вывода, а не операция: он требует
    #: точного имени фактического процесса и подрядного режима исполнения.
    is_process: bool = True


def _spec(
    code: str,
    title_ru: str,
    cost_owner: str,
    *,
    formula_family: str | None = None,
    method_codes: tuple[str, ...] = (),
    method_required: bool = False,
    is_process: bool = True,
) -> tuple[str, ProcessSpec]:
    assert cost_owner in COST_OWNERS
    return code, ProcessSpec(
        code=code,
        title_ru=title_ru,
        cost_owner=cost_owner,
        formula_family=formula_family,
        method_codes=method_codes,
        method_required=method_required,
        is_process=is_process,
    )


#: Дословно каталог-предложение V1 (30 позиций). Порядок — как в документе
#: методолога: заготовка, резка, механообработка, формообразование, финиш.
CATALOG: dict[str, ProcessSpec] = dict(
    (
        _spec(
            "BLANK.CUTOFF",
            "Распил",
            "supply",
            method_codes=BLANK_CUTOFF_METHODS,
            method_required=True,
        ),
        _spec("CUT.LASER.SHEET", "Лазерная резка", "supply"),
        _spec("CUT.LASER.TUBE", "Лазерная резка на труборезе", "supply"),
        _spec("CUT.WATERJET", "Гидроабразивная резка", "supply"),
        _spec("CUT.PLASMA", "Плазменная резка", "supply"),
        _spec("CUT.SHEAR.GUILLOTINE", "Гильотина", "supply"),
        _spec("MACHINING.MILL.PORTAL", "Фрезерный портальник", "norm", formula_family="milling"),
        _spec("MACHINING.MILL.VERTICAL", "Фрезерный вертикальный", "norm", formula_family="milling"),
        _spec("MACHINING.MILL.5_AXIS", "Фрезерный 5-осевой", "norm", formula_family="milling"),
        _spec("MACHINING.MILL.C_AXIS", "Фрезерный с осью С", "norm", formula_family="milling"),
        _spec("MACHINING.MILL.CNC", "Фрезерный ЧПУ", "norm", formula_family="milling"),
        _spec(
            "MACHINING.TURN.LONG_BED_10M",
            "Токарный 10-метровый",
            "norm",
            formula_family="turning",
        ),
        _spec(
            "MACHINING.TURN.LONG_BED_5M",
            "Токарный 5-метровый",
            "norm",
            formula_family="turning",
        ),
        _spec("MACHINING.TURN.VERTICAL", "Токарный карусельный", "norm", formula_family="turning"),
        _spec("MACHINING.TURN.C_AXIS", "Токарный с осью С", "norm", formula_family="turning"),
        _spec("MACHINING.TURN.CNC", "Токарный ЧПУ", "norm", formula_family="turning"),
        _spec("MACHINING.TURN.MANUAL", "Токарный универсал", "norm", formula_family="turning"),
        _spec("MACHINING.GRIND.GENERAL", "Шлифовка", "norm", formula_family="grinding"),
        _spec("MACHINING.EDM.GENERAL", "Эрозия", "norm", formula_family="edm"),
        _spec("MACHINING.EDM.HOLE_DRILL", "Эрозия-дрель", "norm", formula_family="edm"),
        _spec("FORM.ROLL", "Вальцовка", "norm"),
        _spec("FORM.TUBE_BEND", "Трубогиб", "norm"),
        _spec("FORM.PRESS_BEND", "Гибка", "norm"),
        _spec("FINISH.POWDER_COAT", "Порошковое окрашивание", "norm"),
        _spec("FINISH.LIQUID_PAINT", "Малярка", "norm"),
        _spec("JOIN.WELD", "Сварочные работы", "norm"),
        _spec("FINISH.FITTER", "Слесарка", "norm"),
        _spec("ASSEMBLY.GENERAL", "Сборочные работы", "norm"),
        _spec("FORM.WIRE_BEND", "Проволокогиб", "norm"),
        _spec(
            "OTHER.SPECIFIED",
            "Вид обработки у подрядчика",
            "inherit",
            is_process=False,
        ),
    )
)

assert len(CATALOG) == 30, "catalog must carry exactly the 30 proposed codes"


def get(code: str) -> ProcessSpec:
    try:
        return CATALOG[code]
    except KeyError as exc:
        # Список похожих кодов важнее полного: полный на 30 позиций в отказе
        # нечитаем, а опечатка почти всегда в пределах своей группы.
        prefix = str(code).split(".", 1)[0]
        near = sorted(c for c in CATALOG if c.startswith(prefix + ".")) or sorted(CATALOG)
        raise InvalidState(
            f"Код операции «{code}» отсутствует в каталоге {CATALOG_VERSION}; "
            f"похожие: {', '.join(near[:8])}"
        ) from exc


def catalog_listing() -> dict[str, dict[str, object]]:
    """Каталог глазами агента и панели — только публичные поля."""
    return {
        code: {
            "title_ru": spec.title_ru,
            "cost_owner": spec.cost_owner,
            "formula_family": spec.formula_family,
            "method_codes": list(spec.method_codes),
            "method_required": spec.method_required,
            "is_process": spec.is_process,
        }
        for code, spec in sorted(CATALOG.items())
    }


__all__ = [
    "BLANK_CUTOFF_METHODS",
    "CATALOG",
    "CATALOG_VERSION",
    "ProcessSpec",
    "catalog_listing",
    "get",
]
