"""QA_MECHANICAL — машинный гейт схемы V2. Только код, никогда агент.

Каждая проверка возвращает {code, ok, detail, adjust_owner}: вердикт гейта
вычисляется движком из совокупности, и «улучшить» его вызывающий не может
по построению — функции чистые, входы приходят из реестра под блокировкой.

Проверки намеренно пере-проверяют то, что сервис уже гарантировал при
записи: механический гейт — независимый пересчёт, а не доверие к автору
записи. Если сервис и гейт разойдутся, это дефект движка, и узнать о нём
надо из красного вердикта, а не из жалобы заказчика.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .errors import InvalidRatePack
from .packs2 import PipelinePack
from .pricing import qmoney, _round_price
from .util import digest_json

Check = dict[str, Any]

HUNDRED = Decimal("100")
PER_PIECE_DRIVER_FIELD = {
    "per_pierce": "pierces_per_piece",
    "per_bend": "bends_per_piece",
}


def _check(code: str, ok: bool, detail: str, adjust_owner: str = "front") -> Check:
    return {"code": code, "ok": bool(ok), "detail": detail, "adjust_owner": adjust_owner}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def mechanical_checks(state: dict[str, Any], pack: PipelinePack) -> list[Check]:
    """Полный список механических проверок книги текущей ревизии."""
    checks: list[Check] = []
    workflow = state.get("workflow") or {}
    costing = state.get("costing") or {}
    book = state.get("book") or {}
    blank = costing.get("blank") or {}
    time_part = costing.get("time") or {}
    variants = state.get("route_variants") or {}
    frozen = variants.get(str(workflow.get("frozen_variant_id"))) or {}
    contractor_quotes = costing.get("contractor_quotes") or {}

    # 1. Книга относится к текущей ревизии расчёта.
    checks.append(
        _check(
            "book_revision",
            book.get("calculation_revision") == workflow.get("calculation_revision"),
            "Книга собрана для текущей calculation_revision",
        )
    )

    # 2. Отпечаток книги не подделан: пересчёт digest сходится.
    if book:
        recomputed = digest_json({k: v for k, v in book.items() if k != "digest"})
        checks.append(
            _check(
                "book_digest",
                recomputed == book.get("digest"),
                "Digest книги совпадает с пересчитанным",
            )
        )

        material_markup = pack.pricing.get("material_markup_percent")
        if material_markup is not None:
            material_total = next(
                (
                    _dec(line["subtotal_rub"])
                    for line in blank.get("lines", [])
                    if line.get("cost_line_id") == "blank:material"
                ),
                Decimal("0"),
            )
            expected_amount = qmoney(material_total * material_markup / HUNDRED)
            recorded = (book.get("component_markups") or {}).get("material") or {}
            component_ok = (
                _dec(recorded.get("base_rub", "-1")) == qmoney(material_total)
                and _dec(recorded.get("percent", "-1")) == material_markup
                and _dec(recorded.get("amount_rub", "-1")) == expected_amount
                and _dec(recorded.get("sell_rub", "-1"))
                == qmoney(material_total + expected_amount)
            )
            checks.append(
                _check(
                    "material_markup",
                    component_ok,
                    "Наценка материала разложена и сходится с исходной строкой"
                    if component_ok
                    else "Разложение наценки материала не сходится",
                )
            )

    # 3. Цепочка digest: книга собрана из этих стадий, время — по материалу
    # заготовки (от цены металла нормы не зависят — см. time_norms_set).
    checks.append(
        _check(
            "digest_chain",
            bool(blank)
            and bool(time_part)
            and time_part.get("material_code") == blank.get("material_code")
            and book.get("blank_digest") == blank.get("digest")
            and book.get("time_digest") == time_part.get("digest")
            and book.get("route_digest") == frozen.get("digest")
            and book.get("contractor_quotes_digest") == digest_json(contractor_quotes),
            "Digest-цепочка стадий, книги и маршрута сходится",
        )
    )

    # 4. Один пак на весь расчёт, и он действующий.
    contractor_lines = list((book.get("cost") or {}).get("contractor_quotes", []))
    current_contractor_lines = [
        line for line in contractor_lines if line.get("current") is True
    ]
    fingerprints = {
        part.get("pack_fingerprint")
        for part in (blank, time_part, book, *current_contractor_lines)
        if part
    }
    checks.append(
        _check(
            "single_pack",
            fingerprints == {pack.fingerprint},
            "Все стадии и книга посчитаны по действующим данным предприятия"
            if fingerprints == {pack.fingerprint}
            else "Данные предприятия сменились — нужен пересчёт (calc_revision_open)",
        )
    )

    # 5. Суммы строк сходятся с итогами стадий.
    if blank:
        lines_total = sum((_dec(line["subtotal_rub"]) for line in blank.get("lines", [])), Decimal(0))
        checks.append(
            _check(
                "blank_sum",
                lines_total == _dec(blank.get("total_rub", "0")),
                f"Сумма строк заготовки {lines_total} = итогу {blank.get('total_rub')}",
                "supply",
            )
        )
    if time_part:
        time_total = sum(
            (_dec(item["cost_rub"]) for item in time_part.get("items", [])), Decimal(0)
        ) + sum(
            (_dec(line["subtotal_rub"]) for line in time_part.get("service_lines", [])),
            Decimal(0),
        )
        checks.append(
            _check(
                "time_sum",
                time_total == _dec(time_part.get("total_rub", "0")),
                f"Сумма норм и прочих работ {time_total} = итогу {time_part.get('total_rub')}",
                "norm",
            )
        )

    # 6. Итог книги = заготовка + нормы + extras, каждый блок один раз.
    if book:
        cost = book.get("cost") or {}
        extras_total = sum(
            (_dec(line["subtotal_rub"]) for line in cost.get("extras", [])), Decimal(0)
        )
        contractor_total = sum(
            (_dec(line["subtotal_rub"]) for line in current_contractor_lines),
            Decimal(0),
        )
        expected_total = (
            _dec(cost.get("blank_rub", "0"))
            + _dec(cost.get("machining_rub", "0"))
            + _dec(cost.get("contractor_rub", "0"))
            + _dec(cost.get("extras_rub", "0"))
        )
        checks.append(
            _check(
                "book_total",
                _dec(cost.get("total_rub", "0")) == expected_total
                and extras_total == _dec(cost.get("extras_rub", "0"))
                and contractor_total == _dec(cost.get("contractor_rub", "0"))
                and _dec(cost.get("blank_rub", "0")) == _dec(blank.get("total_rub", "-1"))
                and _dec(cost.get("machining_rub", "0")) == _dec(time_part.get("total_rub", "-1")),
                "Итог книги равен сумме блоков, каждый блок входит один раз",
            )
        )

        # 7. net + НДС = итог, копейка в копейку.
        price = book.get("price") or {}
        checks.append(
            _check(
                "net_plus_vat",
                _dec(price.get("net_total_rub", "0")) + _dec(price.get("vat_amount_rub", "0"))
                == _dec(price.get("total_rub", "-1")),
                "net + НДС сходятся в показанный итог",
            )
        )

        outsource = {
            step["seq"]
            for step in frozen.get("steps", [])
            if step.get("execution_mode") == "outsource"
        }
        quoted = [line.get("route_seq") for line in current_contractor_lines]
        unpriced = [line.get("seq") for line in book.get("route_unpriced", [])]
        contractor_coverage_ok = (
            len(quoted) == len(set(quoted))
            and len(unpriced) == len(set(unpriced))
            and not (set(quoted) & set(unpriced))
            and set(quoted) | set(unpriced) == outsource
        )
        checks.append(
            _check(
                "contractor_coverage",
                contractor_coverage_ok,
                "Каждый outsource-шаг имеет текущее КП или явный route_unpriced"
                if contractor_coverage_ok
                else "Подряд выпал или задвоился между КП и route_unpriced",
            )
        )

        # 8. INTERNAL_COST и CUSTOMER_PRICE не смешаны.
        checks.append(
            _check(
                "amount_kinds",
                cost.get("amount_kind") == "INTERNAL_COST"
                and price.get("amount_kind") == "CUSTOMER_PRICE",
                "Себестоимость и цена заказчика разведены по amount_kind",
            )
        )

    # 9. Биекция «замороженный маршрут ↔ оплаченные строки».
    if frozen:
        in_house = {
            step["seq"]: step
            for step in frozen.get("steps", [])
            if step.get("execution_mode") == "in_house"
        }
        priced: list[tuple[int, str | None]] = []
        for line in blank.get("lines", []):
            if "route_seq" in line:
                priced.append((line["route_seq"], line.get("rate_id")))
        for item in time_part.get("items", []):
            priced.append((item["route_seq"], item.get("rate_id")))
        for line in time_part.get("service_lines", []):
            priced.append((line["route_seq"], line.get("rate_id")))
        required = {
            (seq, rate_id)
            for seq, step in in_house.items()
            for rate_id in (
                [pack.park_entry(step["process_code"])["rate_id"]]
                + pack.park_entry(step["process_code"]).get("supplementary_rate_ids", [])
            )
        }
        duplicates = sorted({key for key in priced if priced.count(key) > 1})
        missing = sorted(required - set(priced))
        alien = sorted(set(priced) - required)
        checks.append(
            _check(
                "route_bijection",
                not duplicates and not missing and not alien,
                "Каждая ставка собственного шага маршрута оплачена ровно один раз"
                if not duplicates and not missing and not alien
                else (
                    f"дубли: {duplicates or '—'}; не оплачены: {missing or '—'}; "
                    f"вне маршрута: {alien or '—'}"
                ),
            )
        )

    # 10. cost_line_id уникальны, included_in — не больше одного (M1: нет).
    all_lines = (
        list(blank.get("lines", []))
        + list(time_part.get("items", []))
        + list(time_part.get("service_lines", []))
        + current_contractor_lines
        + list((book.get("cost") or {}).get("extras", []))
    )
    line_ids = [line.get("cost_line_id") for line in all_lines]
    dup_ids = sorted({line_id for line_id in line_ids if line_ids.count(line_id) > 1})
    multi_included = [
        line.get("cost_line_id")
        for line in all_lines
        if isinstance(line.get("included_in"), list) and len(line["included_in"]) > 1
    ]
    checks.append(
        _check(
            "cost_line_identity",
            not dup_ids and not multi_included and all(line_ids),
            "cost_line_id уникальны, included_in не задваивает строку"
            if not dup_ids and not multi_included
            else f"дубли id: {dup_ids}; множественный included_in: {multi_included}",
        )
    )

    # 11. Все строки — INTERNAL_COST (цена заказчика живёт только в price).
    wrong_kind = [
        line.get("cost_line_id")
        for line in all_lines
        if line.get("amount_kind") != "INTERNAL_COST"
    ]
    checks.append(
        _check(
            "lines_internal",
            not wrong_kind,
            "Все строки стоимости — INTERNAL_COST"
            if not wrong_kind
            else f"строки с чужим amount_kind: {wrong_kind}",
        )
    )

    # 12. Ставки строк разрешаются в действующем паке; значения совпадают —
    # у матричных пересчитываются по сохранённым осям (Codex M10/B1).
    rate_drift: list[str] = []
    for line in all_lines:
        rate_id = line.get("rate_id")
        if not rate_id:
            continue
        try:
            rate = pack.rate(rate_id)
        except InvalidRatePack:
            rate_drift.append(f"{rate_id}: нет в реестре")
            continue
        recorded = line.get("rate_value")
        if recorded is None:
            continue
        if (line.get("rate_source") or {}).get("kind") == "supplier_quote":
            # Подтверждённая снабжением цена металла — легитимное
            # расхождение с паком: источник строки говорит об этом.
            if pack.schema_version >= 4 and (
                type(line.get("rate_includes_vat")) is not bool
                or line.get("rate_includes_vat")
                is not bool(pack.pricing.get("rates_include_vat"))
            ):
                rate_drift.append(
                    f"{rate_id}: база НДС подтверждённой ставки не совпадает с паком"
                )
            continue
        if rate["kind"] == "scalar":
            if _dec(recorded) != rate["value"]:
                rate_drift.append(f"{rate_id}: в строке {recorded}, в паке {rate['value']}")
        else:
            axes = line.get("axes")
            try:
                expected_value = pack.rate_value(rate_id, dict(axes or {}))
            except InvalidRatePack as exc:
                rate_drift.append(f"{rate_id}: оси строки не разрешаются ({exc.public_message})")
                continue
            if _dec(recorded) != expected_value:
                rate_drift.append(
                    f"{rate_id}: в строке {recorded}, пересчёт по сетке {expected_value}"
                )
    checks.append(
        _check(
            "rates_resolve",
            not rate_drift,
            "Все rate_id разрешаются, значения совпадают с паком"
            if not rate_drift
            else "; ".join(rate_drift),
        )
    )

    # 13. Количество одно на заказ; штучные строки — ровно на заказ.
    quantity = workflow.get("quantity")
    per_piece_drift = [
        line.get("cost_line_id")
        for line in all_lines
        if line.get("unit") == "pieces" and _dec(line.get("quantity", "-1")) != _dec(quantity or 0)
    ]
    checks.append(
        _check(
            "one_quantity",
            all(
                part.get("quantity") == quantity
                for part in (blank, time_part)
                if part
            )
            and not per_piece_drift,
            f"Количество деталей одно на заказ: {quantity}"
            if not per_piece_drift
            else f"штучные строки не на весь заказ: {per_piece_drift}",
        )
    )

    # 14. Для врезок и гибов хранится драйвер НА ОДНУ деталь, а денежная
    # строка обязана покрывать всю партию. Проверяем это независимо от
    # subtotal и digest: иначе согласованно заниженная строка выглядела бы
    # арифметически правильной.
    driver_drift: list[str] = []
    order_quantity = _dec(workflow.get("quantity") or 0)
    for line in all_lines:
        rate_id = line.get("rate_id")
        if not rate_id:
            continue
        try:
            tariff_unit = pack.rate(rate_id)["tariff_unit"]
        except InvalidRatePack:
            continue
        expected_field = PER_PIECE_DRIVER_FIELD.get(tariff_unit)
        if expected_field is None:
            continue
        try:
            driver_value = _dec(line.get("driver_value"))
            recorded_quantity = _dec(line.get("quantity"))
        except Exception:
            driver_drift.append(f"{line.get('cost_line_id')}: драйвер отсутствует")
            continue
        expected_quantity = driver_value * order_quantity
        if (
            line.get("driver_basis") != "per_piece"
            or line.get("unit") != expected_field
            or driver_value <= 0
            or driver_value != driver_value.to_integral_value()
            or recorded_quantity != expected_quantity
        ):
            driver_drift.append(
                f"{line.get('cost_line_id')}: {driver_value} × {order_quantity} "
                f"≠ {recorded_quantity} ({expected_field})"
            )
    checks.append(
        _check(
            "per_piece_drivers",
            not driver_drift,
            "Врезки и гибы умножены на количество заказа"
            if not driver_drift
            else "; ".join(driver_drift),
        )
    )

    # 15. Независимый пересчёт арифметики строк: q × ставка = subtotal,
    # минуты × станко-час = cost. Внутренняя согласованность (пп. 5–6)
    # ловит потерянную строку, но не неверное произведение (Codex B1).
    arithmetic_drift: list[str] = []
    for line in all_lines:
        if "subtotal_rub" in line and "rate_value" in line and "quantity" in line:
            expected = qmoney(_dec(line["quantity"]) * _dec(line["rate_value"]))
            if _dec(line["subtotal_rub"]) != expected:
                arithmetic_drift.append(
                    f"{line.get('cost_line_id')}: {line['quantity']} × "
                    f"{line['rate_value']} ≠ {line['subtotal_rub']}"
                )
    for item in time_part.get("items", []) if time_part else []:
        expected = qmoney(_dec(item["t_total_min"]) / Decimal(60) * _dec(item["rate_value"]))
        if _dec(item["cost_rub"]) != expected:
            arithmetic_drift.append(
                f"{item.get('cost_line_id')}: {item['t_total_min']} мин × "
                f"{item['rate_value']} ₽/ч ≠ {item['cost_rub']}"
            )
    checks.append(
        _check(
            "line_arithmetic",
            not arithmetic_drift,
            "Произведения строк сходятся с независимым пересчётом"
            if not arithmetic_drift
            else "; ".join(arithmetic_drift),
        )
    )

    # 16. Независимый пересчёт цены из себестоимости по политике пака.
    if book:
        cost_total = _dec((book.get("cost") or {}).get("total_rub", "0"))
        pricing = pack.pricing
        percent = pricing["margin_percent"]
        vat_factor = Decimal(1) + pricing["vat_rate_pct"] / HUNDRED
        rates_include_vat = bool(pricing.get("rates_include_vat"))
        material_total = next(
            (
                _dec(line["subtotal_rub"])
                for line in blank.get("lines", [])
                if line.get("cost_line_id") == "blank:material"
            ),
            Decimal("0"),
        )
        material_markup = pricing.get("material_markup_percent")
        pricing_cost_total = cost_total / vat_factor if rates_include_vat else cost_total
        pricing_material_total = (
            material_total / vat_factor if rates_include_vat else material_total
        )
        if material_markup is None:
            if pricing["margin_basis"] == "on_cost":
                net = pricing_cost_total * (Decimal(1) + percent / HUNDRED)
            else:
                net = pricing_cost_total / (Decimal(1) - percent / HUNDRED)
        else:
            non_material = pricing_cost_total - pricing_material_total
            if pricing["margin_basis"] == "on_cost":
                non_material_net = non_material * (Decimal(1) + percent / HUNDRED)
            else:
                non_material_net = non_material / (Decimal(1) - percent / HUNDRED)
            net = non_material_net + pricing_material_total * (
                Decimal(1) + material_markup / HUNDRED
            )
        expected_price = net
        if pricing["vat_included"]:
            expected_price *= Decimal(1) + pricing["vat_rate_pct"] / HUNDRED
        expected_price = _round_price(expected_price, pricing["rounding"])
        recorded_price = _dec((book.get("price") or {}).get("total_rub", "-1"))
        checks.append(
            _check(
                "price_recompute",
                recorded_price == expected_price,
                f"Цена {recorded_price} = пересчёту по политике пака {expected_price}",
            )
        )

        expected_manual_review = [
            {
                "seq": step["seq"],
                "process_code": step["process_code"],
                "reason": pack.park_entry(step["process_code"]).get(
                    "note", "Для операции требуется ручная коммерческая сверка"
                ),
            }
            for step in frozen.get("steps", [])
            if step.get("execution_mode") == "in_house"
            and pack.park_entry(step["process_code"]).get("requires_manual_review")
        ]
        for item in time_part.get("items", []):
            sources = [
                item.get("norm_source") or {},
                item.get("overheads_source") or {},
            ]
            if any(
                "TEMPLATE" in str(source.get("ref", "")).upper()
                for source in sources
            ):
                expected_manual_review.append(
                    {
                        "seq": item["route_seq"],
                        "process_code": item["process_code"],
                        "reason": "Норма времени или накладные параметры помечены TEMPLATE",
                    }
                )
        expected_final = (
            not book.get("route_unpriced")
            and not bool(blank.get("provisional"))
        )
        completeness_ok = (
            contractor_coverage_ok
            and book.get("manual_review_required") == expected_manual_review
            and (book.get("price") or {}).get("is_final") is expected_final
        )
        checks.append(
            _check(
                "commercial_completeness",
                completeness_ok,
                "Подряд, ручные проверки и признак финальной цены выведены заново"
                if completeness_ok
                else "Подряд, ручные проверки или признак финальной цены не сходятся",
            )
        )

    # 17. Закупочные узлы BOM в этом релизе запрещены: узел без тарификации —
    # нулевая стоимость закупки при зелёных гейтах (Codex B2; вторая линия
    # после отказа в bom_upsert).
    buy_nodes = [
        node_id
        for node_id, node in ((state.get("bom") or {}).get("nodes") or {}).items()
        if node.get("node_type") == "PURCHASED_ITEM" or node.get("make_or_buy") == "BUY"
    ]
    checks.append(
        _check(
            "no_unpriced_purchases",
            not buy_nodes,
            "Закупочных узлов без тарификации нет"
            if not buy_nodes
            else f"покупные узлы без денег: {buy_nodes}",
        )
    )

    return checks


__all__ = ["mechanical_checks"]
