"""Детерминированные нормы времени станочных операций.

Формулы основного времени — классические из общемашиностроительных
нормативов; конкретные режимы (скорость, подача, глубина) и коэффициенты
приходят ТОЛЬКО из пака (packs2), у каждого набора обязательный источник
«справочник / издание / страница». Код не содержит ни одного числа режимов.

Модель (агент 3) передаёт геометрические параметры операции; всё остальное
вычисляется здесь. Неизвестная комбинация операция/материал даёт abstain
на уровне пака (InvalidRatePack), а не ноль.

Формулы:
  точение/сверление:  n = 1000*v / (pi*D);  T_o = L*i / (n*f)   [мин]
  фрезерование:       T_o = L*i / v_f                            [мин]
  эрозия/шлифовка:    T_o = S_cm2 * removal_min_per_cm2          [мин]
  проходы:            i = ceil(stock_mm / depth_mm)
  штучное время:      T_shift = (T_o + t_aux) * (1 + k_service_rest/100)
  на деталь в партии: T_piece = T_shift + t_setup / batch
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Any

from .errors import InvalidState
from .packs2 import PipelinePack
from .util import public_number

PI = Decimal("3.141592653589793")
MINUTE = Decimal("0.01")


def _qmin(value: Decimal) -> Decimal:
    return value.quantize(MINUTE)


def _passes(stock_mm: Decimal, depth_mm: Decimal) -> Decimal:
    if stock_mm <= 0:
        return Decimal(1)
    return (stock_mm / depth_mm).to_integral_value(rounding=ROUND_CEILING)


#: Полный набор параметров каждой операции. Лишний ключ — почти всегда
#: опечатка в имени (`diametr_mm`) или параметр чужой операции; молча
#: выброшенный, он неотличим для агента от учтённого.
PARAMS_BY_OP: dict[str, frozenset[str]] = {
    "turning": frozenset({"diameter_mm", "length_mm", "stock_mm"}),
    "drilling": frozenset({"diameter_mm", "length_mm"}),
    "milling": frozenset({"length_mm", "stock_mm"}),
    "edm": frozenset({"surface_cm2"}),
    "grinding": frozenset({"surface_cm2"}),
}

#: Физические потолки параметров. Это не вкус, а ловушка перепутанных единиц:
#: surface_cm2=10000 — это квадратный метр на ОДНОЙ детали, и почти всегда
#: означает, что агент передал мм² (независимый аудит 30.08 насчитал на этом
#: 86 млн ₽ завышения). Потолки щедрые: honest-значения крупных деталей
#: проходят, перепутанный разряд — нет. Заодно отсекаются экстремумы вида
#: 1e999999, которые прежде падали безликим InternalError уже в арифметике.
PARAM_MAX: dict[str, Decimal] = {
    "diameter_mm": Decimal(5000),
    "length_mm": Decimal(1_000_000),  # суммарный ход: глубина × число отверстий
    "stock_mm": Decimal(500),
    "surface_cm2": Decimal(100_000),
}


def _require_positive(params: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for key in keys:
        raw = params.get(key)
        if isinstance(raw, bool) or raw is None:
            raise InvalidState(f"Missing operation parameter: {key}")
        try:
            value = Decimal(str(raw))
        except ArithmeticError as exc:
            raise InvalidState(f"Invalid operation parameter: {key}") from exc
        if not value.is_finite() or value <= 0:
            raise InvalidState(f"Invalid operation parameter: {key}")
        maximum = PARAM_MAX.get(key)
        if maximum is not None and value > maximum:
            raise InvalidState(
                f"Параметр {key}={value} выходит за физический потолок "
                f"{maximum}. Проверьте единицы: длины в мм, поверхность в см² "
                f"(не в мм²)."
            )
        out[key] = value
    return out


def _reject_unknown_params(op_code: str, params: dict[str, Any]) -> None:
    allowed = PARAMS_BY_OP.get(op_code, frozenset())
    unknown = set(params) - allowed
    if unknown:
        raise InvalidState(
            f"Неизвестные параметры операции {op_code}: "
            f"{', '.join(sorted(unknown))}. Ожидаются только: "
            f"{', '.join(sorted(allowed))} — опечатка в имени поля иначе "
            f"неотличима от верного вызова"
        )


def machine_minutes(
    pack: PipelinePack, op_code: str, material_group: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Основное (машинное) время одной операции для ОДНОЙ детали, минуты."""
    norms = pack.norms_for(op_code, material_group)
    _reject_unknown_params(op_code, params)
    if op_code in {"turning", "drilling"}:
        geom = _require_positive(params, ("diameter_mm", "length_mm"))
        stock = _require_positive(params, ("stock_mm",))["stock_mm"] if op_code == "turning" else Decimal(0)
        n_rpm = Decimal(1000) * norms["cutting_speed_m_min"] / (PI * geom["diameter_mm"])
        # У сверления проход один: сверло идёт насквозь с непрерывной
        # подачей, глубина резания равна радиусу инструмента и не задаётся.
        # Поэтому `depth_mm` в норме сверления — лишнее поле, а не забытый
        # множитель; оно убрано из требуемых, см. NORM_FIELDS_BY_OP.
        passes = _passes(stock, norms["depth_mm"]) if op_code == "turning" else Decimal(1)
        t_main = geom["length_mm"] * passes / (n_rpm * norms["feed_mm_rev"])
        detail = {
            "n_rpm": public_number(n_rpm.quantize(Decimal("0.1"))),
            "passes": public_number(passes),
        }
    elif op_code == "milling":
        geom = _require_positive(params, ("length_mm",))
        # Припуск обязателен так же, как у точения. Раньше его отсутствие
        # молча давало один проход, и забытое поле было неотличимо от
        # честной фрезеровки за один проход.
        stock = _require_positive(params, ("stock_mm",))["stock_mm"]
        passes = _passes(stock, norms["depth_mm"])
        t_main = geom["length_mm"] * passes / norms["feed_table_mm_min"]
        detail = {"passes": public_number(passes)}
    elif op_code in {"edm", "grinding"}:
        geom = _require_positive(params, ("surface_cm2",))
        t_main = geom["surface_cm2"] * norms["removal_min_per_cm2"]
        detail = {}
    else:  # закрытый список гарантирован паком, но fail-closed
        raise InvalidState("Unsupported machine operation")
    return {
        "op_code": op_code,
        "material_group": material_group,
        # Показ — четыре знака: микрофрезеровка 0,004 мин/деталь при двух
        # знаках показывалась нулём, и ноль же уезжал в деньги на весь тираж
        # (0,00 × 1 000 000 деталей). Деньги считаются от неокруглённого.
        "t_main_min": t_main.quantize(Decimal("0.0001")),
        "_t_main_min_dec": t_main,
        "detail": detail,
        "norm_source": norms["source"],
    }


def piece_minutes(
    pack: PipelinePack,
    main: dict[str, Any],
    *,
    quantity: int,
    batch: int,
) -> dict[str, Any]:
    """Штучное время и время на партию по коэффициентам пака."""
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise InvalidState("Invalid quantity")
    if not isinstance(batch, int) or isinstance(batch, bool) or batch < 1:
        raise InvalidState("Invalid batch")
    over = pack.overheads
    if batch > quantity:
        # Партия больше заказа — почти всегда перепутанные поля. Молча это
        # обнуляет подготовительно-заключительное время: batch=1000 при
        # quantity=1 превращал 20 минут наладчика в 1,2 секунды.
        raise InvalidState("Batch cannot exceed quantity")
    t_main_exact = main.get("_t_main_min_dec", main["t_main_min"])
    t_shift = (t_main_exact + over["t_aux_min"]) * (
        Decimal(1) + over["k_service_rest_pct"] / Decimal(100)
    )
    # Наладок столько, сколько РАЗ станок переналаживают, — целое число.
    # Делить наладку на партию и умножать на количество давало дробное:
    # при quantity=100 и batch=7 выходило 14,29 наладки вместо 15, то есть
    # систематический недосчёт на любом заказе, не кратном партии. На
    # мелкосерийке наладка — основная часть цены.
    setups = -(-quantity // batch)
    total = t_shift * Decimal(quantity) + over["t_setup_min"] * Decimal(setups)
    # Показанное штучное время выводится ИЗ показанного итога, а не рядом с
    # ним. Иначе человек, проверяющий расчёт на бумаге, умножает 2,81 × 1000
    # и получает 2810 против наших 2806,4 — расхождение ровно там, где его
    # ищут. В деньгах итог остаётся точным: они считаются от `total`.
    total_shown = _qmin(total)
    t_piece = total_shown / Decimal(quantity)
    return {
        **{k: v for k, v in main.items() if k not in {"t_main_min", "_t_main_min_dec"}},
        "t_main_min": public_number(main["t_main_min"]),
        "t_piece_min": public_number(_qmin(t_piece)),
        "quantity": quantity,
        "batch": batch,
        "t_total_min": public_number(_qmin(total)),
        "overheads_source": over["source"],
        "_t_total_min_dec": _qmin(total),  # деньги считаются от показанных минут
    }
