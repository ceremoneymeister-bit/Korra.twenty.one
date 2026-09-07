"""Конвейер V2: заготовка -> маршрут -> нормы времени -> КП.

Слой поверх Registry, не трогающий поля и статусную модель v1: весь
результат конвейера живёт в state["stages"] и state["pipeline_events"].
Деньги и время считает только этот код; модель передаёт параметры и
пояснения. Каждая стадия предлагается агентом (proposed) и утверждается
человеком (approved) — та же supervised-дисциплина, что manual facts v1.

Handoff-контракт для раннера вкладок: каждое изменение стадии дописывает
событие {stage, event, at} в state["pipeline_events"]; раннер поллит
реестр и открывает сессию следующего агента по событию "approved".
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .errors import (
    Conflict,
    InvalidRatePack,
    InvalidState,
    MetalCalcError,
    PackChanged,
)
from .packs2 import PipelinePack, PipelinePackStore, norm_coverage
from .pricing import qmoney, _round_price
from .registry import Registry
from .timenorms import machine_minutes, piece_minutes
from .util import digest_json, public_number, utcnow, validate_id

STAGES = ("blank", "route", "time", "quote")

#: Названия стадий для сообщений человеку. Модель читает те же сообщения и
#: пересказывает их методологу — «Стадия route» в такой пересказ не годится.
STAGE_TITLES = {
    "blank": "Заготовка",
    "route": "Маршрут",
    "time": "Нормы времени",
    "quote": "КП",
}

HUNDRED = Decimal("100")
MAX_QTY = Decimal("1000000")


def _dec_positive(value: Any, field: str, *, maximum: Decimal = MAX_QTY) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise InvalidState(f"Invalid {field}")
    try:
        out = Decimal(str(value))
    except ArithmeticError as exc:
        raise InvalidState(f"Invalid {field}") from exc
    if not out.is_finite() or out <= 0 or out > maximum:
        raise InvalidState(f"Invalid {field}")
    return out


def _note(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise InvalidState(f"{field} requires a short note")
    return value.strip()


class PipelineService:
    def __init__(self, registry: Registry, packs: PipelinePackStore) -> None:
        self.registry = registry
        self.packs = packs

    # ── инфраструктура стадий ────────────────────────────────────────────

    @staticmethod
    def _stages(state: dict[str, Any]) -> dict[str, Any]:
        # Заказ схемы V2 для legacy-конвейера закрыт наглухо: две статусные
        # машины на одном заказе — это две правды о его состоянии. Проверка
        # именно здесь, в точке, через которую проходят ВСЕ мутаторы стадий,
        # и под блокировкой реестра — а не в обёртке инструмента.
        if "workflow" in state:
            raise Conflict(
                "Заказ ведётся по схеме разделения V2 — используйте "
                "workflow_status и инструменты конвейера V3, а не legacy-стадии"
            )
        return state.setdefault("stages", {})

    @staticmethod
    def _event(state: dict[str, Any], stage: str, event: str) -> None:
        state.setdefault("pipeline_events", []).append(
            {"stage": stage, "event": event, "at": utcnow()}
        )

    def _forbid_after_quote(self, state: dict[str, Any]) -> None:
        quote = self._stages(state).get("quote")
        if quote and quote.get("status") == "approved":
            raise Conflict("Pipeline is closed by an approved quote")

    # ── данные предприятия под заказом ───────────────────────────────────

    def _resolve_pack(self, pack_revision: str | None) -> PipelinePack:
        """Пак заказа: явно названный или действующий.

        Ревизию агент больше не подставляет сам. Раньше её номер жил в тексте
        SOUL, и каждая публикация новых ставок требовала правки трёх профилей;
        теперь действующую ревизию знает указатель, а в стадию по-прежнему
        пишется её настоящее имя — история остаётся точной.
        """
        pack = (
            self.packs.load(pack_revision) if pack_revision else self.packs.load_active()
        )
        if pack.status != "active":
            raise InvalidRatePack(
                f"Данные предприятия в состоянии «{pack.status}» — это образец, "
                "а не ваши ставки. Заполните и опубликуйте их на экране «Данные»: "
                "цена по образцу выглядит настоящей и уйдёт заказчику такой же."
            )
        return pack

    @staticmethod
    def _stage_is_stale(stage: dict[str, Any] | None, pack: PipelinePack) -> bool:
        """Посчитана ли стадия по другим данным, чем действующие.

        Одно правило на два потребителя — отказ инструмента и статус заказа;
        разойдись они, экран показывал бы «всё в порядке» ровно там, где
        инструмент откажет.
        """
        if not stage:
            return False
        result = stage.get("result") or {}
        recorded = result.get("pack_fingerprint")
        current = pack.fingerprint
        if not recorded:
            recorded, current = result.get("pack_sha256"), pack.sha256
        return bool(recorded) and recorded != current

    @staticmethod
    def _require_same_pack(stage: dict[str, Any] | None, pack: PipelinePack, name: str) -> None:
        """Не дать собрать одну цену из двух разных наборов ставок.

        Методолог правит данные предприятия в любой момент — это норма работы,
        а не авария. Опасна не сама правка, а тихий гибрид: суммы стадий
        посчитаны по вчерашним ставкам, а маржа и НДС берутся из сегодняшней
        политики, и в КП уходит цифра, которой не соответствует ни один пак.

        Сверяем отпечаток ДАННЫХ, а не хеш файла: повторное сохранение без
        правок меняет имя ревизии и хеш файла, но ни одной ставки не трогает,
        и ронять на этом живые заказы значило бы наказывать за нажатие
        «Сохранить». Стадии, посчитанные до появления отпечатка, сверяем по
        старому полю — иначе их пришлось бы пересчитывать без причины.
        """
        if not PipelineService._stage_is_stale(stage, pack):
            return
        result = stage.get("result") or {}
        recorded_revision = result.get("pack_revision") or "неизвестной"
        raise PackChanged(
            f"Стадия «{STAGE_TITLES.get(name, name)}» посчитана по ревизии данных "
            f"{recorded_revision}, а сейчас действует {pack.revision}. "
            f"Пересчитайте стадию «{STAGE_TITLES.get(name, name)}» и следующие за ней."
        )

    def _pack_state(self, stages: dict[str, Any]) -> dict[str, Any]:
        """Действующие данные предприятия и стадии, отставшие от них.

        Отдаётся вместе со статусом, чтобы «пересчитай стадию» человек и
        агент узнавали до вызова инструмента, а не из отказа в середине
        работы. Недоступный пак здесь не авария: заказ существует и без
        заведённых данных, просто считать по нему пока нечем.
        """
        try:
            pack = self.packs.load_active()
        except MetalCalcError as exc:
            return {"active_revision": None, "unavailable": exc.public_message}
        return {
            "active_revision": pack.revision,
            "active_sha256": pack.sha256,
            "stale_stages": [
                name for name in STAGES if self._stage_is_stale(stages.get(name), pack)
            ],
        }

    def rates_catalog(self) -> dict[str, Any]:
        """Что заведено в данных предприятия — глазами агента.

        До этого инструмента у агента не было НИ ОДНОГО способа узнать состав
        данных: он обязан передать `material_code` и `op_code`, а взять их
        было неоткуда. На практике это значит угадывание. Неизвестный код
        движок отвергает — но код, который существует и не тот («09Г2С»
        вместо «40Х»), проходит молча, и ошибка уезжает в цену.

        Только чтение. Инструмента записи паков в MCP нет и не появится:
        публикация живёт в CLI методолога.
        """
        try:
            pack = self.packs.load_active()
        except MetalCalcError as exc:
            return {"configured": False, "reason": exc.public_message}
        return {
            "configured": True,
            "revision": pack.revision,
            "blank_ops": {
                code: {"rate_kind": op["rate_kind"], "rate_rub": str(op["rate_rub"])}
                for code, op in sorted(pack.blank_ops.items())
            },
            "materials": {
                code: {
                    "grade": material["grade"],
                    "group": material["group"],
                    "stock": material["stock"],
                    "rate_rub_per_kg": str(material["rate_rub_per_kg"]),
                }
                for code, material in sorted(pack.materials.items())
            },
            "machines": {
                code: {"rate_rub_per_hour": str(machine["rate_rub_per_hour"])}
                for code, machine in sorted(pack.machines.items())
            },
            "norms": sorted(pack.norm_params),
            # Пары без нормы — не ошибка данных, но именно на них третья стадия
            # падает после двух утверждённых. Агент должен знать это ДО того,
            # как предложит маршрут через такую операцию.
            "norms_missing": norm_coverage(pack),
            "extras": sorted(pack.extras),
        }

    def pipeline_status(self, order_id: str) -> dict[str, Any]:
        revision, state = self.registry.get(order_id)
        stages = state.get("stages", {})
        return {
            "order_id": order_id,
            "revision": revision,
            "stages": {
                name: {
                    key: value
                    for key, value in (stages.get(name) or {}).items()
                    if key != "result" or name == "quote"
                }
                for name in STAGES
                if stages.get(name)
            },
            "pack": self._pack_state(stages),
            "events": state.get("pipeline_events", [])[-20:],
        }

    # ── агент 1: заготовка ───────────────────────────────────────────────

    def blank_cost(
        self,
        order_id: str,
        expected_revision: int,
        material_code: str,
        quantity: Any,
        mass_kg: Any,
        mass_basis: Any,
        mass_note: str,
        items: list[dict[str, Any]],
        pack_revision: str | None = None,
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        pack = self._resolve_pack(pack_revision)
        material = pack.material(material_code)
        # Количество деталей живёт на заказе с первой стадии. Без него масса
        # была числом без основания: агент имел в виду массу одной детали,
        # заказ был на сто штук — и цена уезжала в семь раз, потому что
        # связать 12,5 кг с сотней было нечем.
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 1_000_000:
            raise InvalidState(
                "quantity — число деталей в заказе, целое от 1: оно связывает "
                "массу заготовки и нормы времени"
            )
        if mass_basis not in {"per_piece", "total"}:
            raise InvalidState(
                "mass_basis обязателен: per_piece (масса одной детали) или "
                "total (масса всей партии). Без него 12,5 кг на заказ из ста "
                "деталей — это либо 1250 кг металла, либо ошибка в семь раз."
            )
        mass = _dec_positive(mass_kg, "mass_kg")
        total_mass = mass * Decimal(quantity) if mass_basis == "per_piece" else mass
        note = _note(mass_note, "mass_kg")
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise InvalidState("blank items must be a list of 1..32 entries")

        lines: list[dict[str, Any]] = []
        provisional = material["stock"] == "purchase"
        material_subtotal = qmoney(total_mass * material["rate_rub_per_kg"])
        lines.append(
            {
                "code": f"material:{material_code}",
                "quantity": public_number(total_mass),
                "unit": "kg",
                "rate_rub": public_number(material["rate_rub_per_kg"]),
                "rate_source": material["rate_source"],
                "subtotal_rub": public_number(material_subtotal),
                "provisional": provisional,
                "note": note,
            }
        )
        total = material_subtotal
        quantity_field = {"per_cut_m": "length_m", "per_cut": "cuts", "per_hour": "hours"}
        for item in items:
            if not isinstance(item, dict) or set(item) - {"op_code", "length_m", "cuts", "hours", "note"}:
                raise InvalidState("Invalid blank item fields")
            op = pack.blank_op(str(item.get("op_code")))
            field = quantity_field[op["rate_kind"]]
            # Ровно одно поле количества — то, которого требует вид ставки.
            # Агент, приславший и cuts, и length_m, не узнал бы, какое из двух
            # чисел стало деньгами: лишнее выбрасывалось молча.
            stray = ({"length_m", "cuts", "hours"} - {field}) & set(item)
            if stray:
                raise InvalidState(
                    f"Операция «{item['op_code']}» тарифицируется полем {field}; "
                    f"лишние поля количества: {', '.join(sorted(stray))} — "
                    f"уберите их, иначе неясно, какое число пошло в цену"
                )
            quantity_value = _dec_positive(item.get(field), field)
            item_note = _note(item.get("note"), "blank item")
            subtotal = qmoney(quantity_value * op["rate_rub"])
            total += subtotal
            lines.append(
                {
                    "code": f"blank:{item['op_code']}",
                    "quantity": public_number(quantity_value),
                    "unit": {"length_m": "m", "cuts": "pcs", "hours": "h"}[field],
                    "rate_rub": public_number(op["rate_rub"]),
                    "rate_source": op["rate_source"],
                    "subtotal_rub": public_number(subtotal),
                    "provisional": False,
                    "note": item_note,
                }
            )
        result = {
            "pack_revision": pack.revision,
            "pack_sha256": pack.sha256,
            "pack_fingerprint": pack.fingerprint,
            "pack_status": pack.status,
            "material_code": material_code,
            "quantity": quantity,
            "mass_basis": mass_basis,
            "lines": lines,
            "total_rub": public_number(qmoney(total)),
            "provisional": provisional,
        }
        result["digest"] = digest_json(result)

        def mutate(state: dict[str, Any]) -> None:
            self._forbid_after_quote(state)
            stage = self._stages(state).get("blank")
            if stage and stage.get("status") == "approved":
                raise Conflict("Blank stage already approved; use supply_confirm or a new order")
            self._stages(state)["blank"] = {
                "status": "proposed",
                "proposed_at": utcnow(),
                "result": result,
            }
            self._event(state, "blank", "proposed")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "stage": "blank", "result": result}

    def supply_confirm(
        self,
        order_id: str,
        expected_revision: int,
        rate_rub_per_kg: Any,
        confirmed_by: str,
        source_ref: str,
    ) -> dict[str, Any]:
        """Снабжение подтвердило закупочную цену металла — снимаем звёздочку."""
        rate = _dec_positive(rate_rub_per_kg, "rate_rub_per_kg")
        who = _note(confirmed_by, "confirmed_by")
        ref = _note(source_ref, "source_ref")

        def mutate(state: dict[str, Any]) -> None:
            self._forbid_after_quote(state)
            stage = self._stages(state).get("blank")
            if not stage:
                raise InvalidState("Blank stage is missing")
            result = stage["result"]
            material_lines = [
                line for line in result["lines"] if str(line["code"]).startswith("material:")
            ]
            if not any(line.get("provisional") for line in material_lines):
                raise Conflict("Material price is not provisional")
            total = Decimal("0")
            for line in result["lines"]:
                if str(line["code"]).startswith("material:") and line.get("provisional"):
                    quantity = Decimal(str(line["quantity"]))
                    line["rate_rub"] = public_number(rate)
                    line["rate_source"] = {
                        "kind": "supplier_quote",
                        "ref": f"{ref}; confirmed_by={who}",
                        "as_of": utcnow()[:10],
                    }
                    line["subtotal_rub"] = public_number(qmoney(quantity * rate))
                    line["provisional"] = False
                total += Decimal(str(line["subtotal_rub"]))
            result["total_rub"] = public_number(qmoney(total))
            result["provisional"] = False
            result["digest"] = digest_json({k: v for k, v in result.items() if k != "digest"})
            self._event(state, "blank", "supply_confirmed")

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "stage": "blank",
            "result": state["stages"]["blank"]["result"],
        }

    # ── агент 2: маршрут ─────────────────────────────────────────────────

    def route_propose(
        self,
        order_id: str,
        expected_revision: int,
        steps: list[dict[str, Any]],
        pack_revision: str | None = None,
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        pack = self._resolve_pack(pack_revision)
        _, current_state = self.registry.get(order_id)
        blank_now = (current_state.get("stages") or {}).get("blank")
        # Цепочка стадий — часть контракта, а не пожелание SOUL. Маршрут без
        # утверждённой заготовки открывал обход сверки материалов: время
        # считалось по одному материалу, заготовка добавлялась потом по
        # другому, и в КП уезжал гибрид — сверка в time_calc видит только ту
        # заготовку, которая существует в момент вызова.
        if not blank_now or blank_now.get("status") != "approved":
            raise InvalidState(
                "Маршрут строится после утверждённой заготовки: сначала "
                "blank_cost и утверждение стадии «Заготовка»"
            )
        self._require_same_pack(blank_now, pack, "blank")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 40:
            raise InvalidState("Route must contain 1..40 steps")
        allowed_ops = set(pack.machines) | set(pack.blank_ops) | {"thermal", "control", "wash"}
        normalized: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or set(step) != {"seq", "op_code", "note"}:
                raise InvalidState("Invalid route step fields")
            if step["seq"] != index:
                raise InvalidState("Route seq must be contiguous starting at 1")
            op_code = str(step["op_code"])
            if op_code not in allowed_ops:
                # Список в ошибке — единственный надёжный канал сообщить модели
                # словарь пака: докстринг статичен, а состав зависит от ревизии.
                raise InvalidState(
                    "Route step uses an unknown operation; allowed: "
                    + ", ".join(sorted(allowed_ops))
                )
            normalized.append(
                {"seq": index, "op_code": op_code, "note": _note(step["note"], "route step")}
            )
        payload = {
            "pack_revision": pack.revision,
            "pack_sha256": pack.sha256,
            "pack_fingerprint": pack.fingerprint,
            "steps": normalized,
        }
        payload["digest"] = digest_json(payload)

        def mutate(state: dict[str, Any]) -> None:
            self._forbid_after_quote(state)
            stage = self._stages(state).get("route")
            if stage and stage.get("status") == "approved":
                raise Conflict("Route already approved")
            time_stage = self._stages(state).get("time")
            if time_stage and time_stage.get("status") != "reopened":
                raise Conflict("Time stage exists; route can no longer change")
            # Ещё раз под блокировкой реестра: публикация новых ставок могла
            # случиться ровно между чтением выше и этой записью.
            blank_locked = self._stages(state).get("blank")
            if not blank_locked or blank_locked.get("status") != "approved":
                raise Conflict("Blank stage changed while proposing the route")
            self._require_same_pack(blank_locked, pack, "blank")
            self._stages(state)["route"] = {
                "status": "proposed",
                "proposed_at": utcnow(),
                "result": payload,
            }
            self._event(state, "route", "proposed")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "stage": "route", "result": payload}

    # ── человек: утверждение стадии ──────────────────────────────────────

    def stage_approve(
        self, order_id: str, expected_revision: int, stage_name: str, approved_by: str
    ) -> dict[str, Any]:
        if stage_name not in {"blank", "route", "time", "quote"}:
            raise InvalidState("Unknown stage")
        who = _note(approved_by, "approved_by")
        # Утверждение — принятие цифры как действующей, поэтому стадию,
        # посчитанную по уже сменившимся данным, утверждать нельзя: раньше
        # отказ приходил позже и в другом месте (в следующем инструменте),
        # и человек не понимал, что утвердил уже устаревшее.
        #
        # Недоступный пак — отказ, а не пропуск сверки: битый указатель в
        # момент утверждения означал бы, что старое КП утверждается именно
        # тогда, когда проверить его нечем (fail-open поймал независимый
        # аудит 30.08).
        try:
            active_pack = self.packs.load_active()
        except MetalCalcError as exc:
            raise InvalidState(
                "Данные предприятия сейчас недоступны, утверждение невозможно: "
                "нечем проверить, что стадия посчитана по действующим ставкам. "
                f"Причина: {exc.public_message}"
            ) from exc

        def mutate(state: dict[str, Any]) -> None:
            stages = self._stages(state)
            stage = stages.get(stage_name)
            if not stage:
                raise InvalidState("Stage is missing")
            if stage.get("status") == "approved":
                raise Conflict("Stage already approved")
            if stage.get("status") == "reopened":
                raise InvalidState(
                    "Стадия открыта под пересчёт: сначала посчитайте её заново, "
                    "иначе утверждается прежняя цифра по прежним данным"
                )
            if active_pack is not None:
                self._require_same_pack(stage, active_pack, stage_name)
            if stage_name == "quote":
                # КП собиралось из стадий на момент сборки, а утверждается
                # позже. `supply_confirm` в этом промежутке меняет суммы
                # заготовки, и раньше КП утверждалось со старыми: измерено
                # 11 437,92 ₽ вместо 26 197,92 ₽ — недобор 14 760 ₽, и
                # `stale_stages` при этом оставался пустым. Проверяем то же,
                # что и сборка: стадии не изменились с тех пор.
                result = stage.get("result") or {}
                for name, digest in (
                    ("blank", result.get("blank_digest")),
                    ("time", result.get("time_digest")),
                ):
                    source = stages.get(name) or {}
                    if (source.get("result") or {}).get("digest") != digest:
                        raise Conflict(
                            f"Стадия «{name}» изменилась после сборки КП — "
                            f"соберите КП заново, иначе утверждается цена, "
                            f"которой уже нет"
                        )
            if stage_name == "blank" and stage["result"].get("provisional"):
                # Утверждать можно — но звёздочка остаётся в результате и КП,
                # пока снабжение не подтвердит цену через supply_confirm.
                pass
            if stage_name == "quote" and stage["result"].get("provisional"):
                # А вот КП со звёздочкой утвердить нельзя: утверждение
                # закрывает конвейер, и supply_confirm после него запрещён —
                # предварительная цена застыла бы как окончательная.
                raise InvalidState(
                    "КП собрано по предварительной цене металла (звёздочка). "
                    "Сначала подтвердите цену снабжением (supply_confirm), "
                    "пересоберите КП и утверждайте окончательное."
                )
            stage["status"] = "approved"
            stage["approved_by"] = who
            stage["approved_at"] = utcnow()
            self._event(state, stage_name, "approved")

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "stage": stage_name,
            "status": state["stages"][stage_name]["status"],
        }

    def pipeline_repack(
        self,
        order_id: str,
        expected_revision: int,
        stage_name: str,
        requested_by: str,
    ) -> dict[str, Any]:
        """Открыть утверждённую стадию под пересчёт по новым данным.

        Без этого правка прайса загоняет заказ в тупик: статус говорит
        «пересчитайте стадию «Заготовка»», а `blank_cost` отвечает «стадия
        уже утверждена, заведите новый заказ». Система называла лечение и
        сама же его запрещала, и единственным выходом был новый заказ —
        то есть потеря истории решений ради изменения одной ставки.

        Три свойства, которые здесь важнее удобства:

        * **следующие стадии тоже теряют утверждение.** Маршрут и нормы
          считались от цифр заготовки; оставить их утверждёнными значит
          собрать КП наполовину из старых данных — ровно та тихая ошибка,
          от которой защищает `PackChanged`;
        * **прежний расчёт не стирается.** Он уходит в историю заказа
          вместе с ревизией данных и именем утвердившего. Пересчёт, стирающий
          прошлое, — это переписывание прошлого, а цену уже могли назвать
          заказчику;
        * **делает это человек.** Автоматический пересчёт менял бы названную
          цену молча.
        """
        if stage_name not in STAGES:
            raise InvalidState("Unknown stage")
        who = _note(requested_by, "requested_by")
        reopened: list[str] = []

        def mutate(state: dict[str, Any]) -> None:
            stages = self._stages(state)
            stage = stages.get(stage_name)
            if not stage:
                raise InvalidState("Stage is missing")
            if stage.get("status") != "approved":
                raise InvalidState("Stage is not approved; recalculate it directly")
            start = STAGES.index(stage_name)
            for name in STAGES[start:]:
                current = stages.get(name)
                # Открываются ВСЕ последующие стадии, не только утверждённые.
                # Оставленная proposed-стадия времени запирала заказ иначе:
                # маршрут отвечал «Time stage exists», а repack по времени —
                # «Stage is not approved» (тупик нашёл независимый аудит 30.08).
                if not current or current.get("status") == "reopened":
                    continue
                history = state.setdefault("pipeline_history", [])
                history.append(
                    {
                        "stage": name,
                        "at": utcnow(),
                        "reopened_by": who,
                        "approved_by": current.get("approved_by"),
                        "approved_at": current.get("approved_at"),
                        "pack_revision": current.get("pack_revision"),
                        "result": current.get("result"),
                    }
                )
                current["status"] = "reopened"
                current.pop("approved_by", None)
                current.pop("approved_at", None)
                reopened.append(name)
                self._event(state, name, "reopened")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "reopened": reopened,
            "next": stage_name,
        }

    # ── агент 3: нормы времени ───────────────────────────────────────────

    def time_calc(
        self,
        order_id: str,
        expected_revision: int,
        material_code: str,
        entries: list[dict[str, Any]],
        pack_revision: str | None = None,
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        pack = self._resolve_pack(pack_revision)
        material = pack.material(material_code)
        _, state = self.registry.get(order_id)
        stages_now = state.get("stages") or {}
        route = stages_now.get("route")
        if not route or route.get("status") != "approved":
            raise InvalidState("Time calc requires an approved route")
        self._require_same_pack(stages_now.get("blank"), pack, "blank")
        self._require_same_pack(route, pack, "route")

        # Материал стадии времени обязан совпадать с утверждённой заготовкой.
        # Без этой сверки код валиден, но не тот: режимы резания берутся по
        # группе ЧУЖОГО материала, и получается гибрид «сталь по режимам
        # алюминия» — на демо-данных разница 11%, на паре титан/алюминий
        # разы. `PackChanged` этого не ловит: он про смешение РЕВИЗИЙ, а тут
        # оба материала из одной.
        blank_stage = stages_now.get("blank") or {}
        blank_material = (blank_stage.get("result") or {}).get("material_code")
        if blank_material and blank_material != material_code:
            raise InvalidState(
                f"Заготовка посчитана по материалу «{blank_material}», а нормы "
                f"времени запрошены по «{material_code}». Режимы резания "
                f"зависят от материала — расчёт был бы гибридом двух разных."
            )
        # Количество деталей одно на заказ и задано в заготовке. Без сверки
        # металл считался на одну деталь, время — на сто, и обе цифры молча
        # складывались в одно КП.
        order_quantity = (blank_stage.get("result") or {}).get("quantity")
        if not isinstance(order_quantity, int):
            raise InvalidState(
                "В стадии «Заготовка» нет количества деталей — она посчитана "
                "прежней версией расчётчика. Пересчитайте заготовку, затем "
                "возвращайтесь к нормам времени."
            )
        route_ops = {step["seq"]: step["op_code"] for step in route["result"]["steps"]}
        if not isinstance(entries, list) or not 1 <= len(entries) <= 40:
            raise InvalidState("Time entries must be a list of 1..40 items")

        # Каждый станочный шаг утверждённого маршрута обязан быть оплачен ровно
        # один раз. Без этой сверки движок одинаково спокоен к обеим ошибкам:
        # пропущенная операция считается в ноль и на экране неотличима от
        # отсутствующей (человек утвердил маршрут и уверен, что утвердил состав
        # работ), а дубль `route_seq` оплачивается дважды — переплата тоже
        # проигранный тендер.
        machine_steps = {
            seq for seq, op_code in route_ops.items() if op_code in pack.machines
        }
        given = [entry.get("route_seq") for entry in entries if isinstance(entry, dict)]
        duplicates = sorted({seq for seq in given if given.count(seq) > 1})
        if duplicates:
            raise InvalidState(
                f"Шаги маршрута {duplicates} посчитаны дважды — операция была бы "
                f"оплачена повторно."
            )
        missing = sorted(machine_steps - set(given))
        if missing:
            names = ", ".join(f"{seq} ({route_ops[seq]})" for seq in missing)
            raise InvalidState(
                f"Не посчитаны станочные операции утверждённого маршрута: {names}. "
                f"Без них они уйдут в цену нулём и на экране будут неотличимы от "
                f"отсутствующих."
            )

        items: list[dict[str, Any]] = []
        total_min = Decimal("0")
        total_cost = Decimal("0")
        for entry in entries:
            expected = {"route_seq", "quantity", "batch", "params"}
            if not isinstance(entry, dict) or set(entry) != expected:
                raise InvalidState("Invalid time entry fields")
            seq = entry["route_seq"]
            op_code = route_ops.get(seq)
            if op_code is None:
                raise InvalidState("Time entry references a step outside the route")
            if op_code not in pack.machines:
                raise InvalidState("Time entry targets a non-machine route step")
            if entry["quantity"] != order_quantity:
                raise InvalidState(
                    f"В заказе {order_quantity} шт (стадия «Заготовка»), а шаг "
                    f"{seq} посчитан на {entry['quantity']} — количество одно "
                    f"на заказ; партиями управляет поле batch"
                )
            main = machine_minutes(pack, op_code, material["group"], dict(entry["params"] or {}))
            piece = piece_minutes(
                pack, main, quantity=entry["quantity"], batch=entry["batch"]
            )
            minutes_shown = piece.pop("_t_total_min_dec")
            machine = pack.machine(op_code)
            cost = qmoney(minutes_shown / Decimal(60) * machine["rate_rub_per_hour"])
            total_min += minutes_shown
            total_cost += cost
            items.append(
                {
                    "route_seq": seq,
                    **piece,
                    "rate_rub_per_hour": public_number(machine["rate_rub_per_hour"]),
                    "rate_source": machine["rate_source"],
                    "cost_rub": public_number(cost),
                }
            )
        result = {
            "pack_revision": pack.revision,
            "pack_sha256": pack.sha256,
            "pack_fingerprint": pack.fingerprint,
            "pack_status": pack.status,
            "material_code": material_code,
            "route_digest": route["result"]["digest"],
            "items": items,
            "total_min": public_number(total_min.quantize(Decimal("0.01"))),
            "total_rub": public_number(qmoney(total_cost)),
        }
        result["digest"] = digest_json(result)

        def mutate(mut_state: dict[str, Any]) -> None:
            self._forbid_after_quote(mut_state)
            current_route = (mut_state.get("stages") or {}).get("route")
            if (
                not current_route
                or current_route.get("status") != "approved"
                or current_route["result"]["digest"] != route["result"]["digest"]
            ):
                raise Conflict("Route changed while calculating time")
            self._require_same_pack(current_route, pack, "route")
            self._require_same_pack(
                (mut_state.get("stages") or {}).get("blank"), pack, "blank"
            )
            stage = self._stages(mut_state).get("time")
            if stage and stage.get("status") == "approved":
                raise Conflict("Time stage already approved")
            self._stages(mut_state)["time"] = {
                "status": "proposed",
                "proposed_at": utcnow(),
                "result": result,
            }
            self._event(mut_state, "time", "proposed")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "stage": "time", "result": result}

    # ── сборка КП ────────────────────────────────────────────────────────

    def quote_build(
        self,
        order_id: str,
        expected_revision: int,
        extras: dict[str, Any] | None = None,
        pack_revision: str | None = None,
    ) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        pack = self._resolve_pack(pack_revision)
        if pack.schema_version >= 4:
            raise InvalidState(
                "Пак schema v4 поддерживается только ролевым конвейером V3: "
                "legacy quote_build не умеет дополнительные ставки и "
                "раздельную наценку материала"
            )
        extras = extras or {}
        if set(extras) - {"packaging", "logistics"}:
            raise InvalidState("Unknown extras")

        _, state = self.registry.get(order_id)
        stages = state.get("stages") or {}
        blank = stages.get("blank")
        route = stages.get("route")
        time_stage = stages.get("time")
        if not blank or blank.get("status") != "approved":
            raise InvalidState("Quote requires an approved blank stage")
        if not route or route.get("status") != "approved":
            raise InvalidState("Quote requires an approved route stage")
        if not time_stage or time_stage.get("status") != "approved":
            raise InvalidState("Quote requires an approved time stage")
        # Главная точка: именно здесь суммы стадий встречаются с политикой
        # цены, и именно здесь гибрид двух паков превращается в цифру для
        # заказчика.
        self._require_same_pack(blank, pack, "blank")
        self._require_same_pack(time_stage, pack, "time")

        blank_total = Decimal(str(blank["result"]["total_rub"]))
        time_total = Decimal(str(time_stage["result"]["total_rub"]))
        extra_lines: list[dict[str, Any]] = []
        extra_total = Decimal("0")
        for code, quantity_raw in extras.items():
            extra = pack.extras.get(code)
            if extra is None:
                raise InvalidState("Extra is not configured in the pack")
            quantity = _dec_positive(quantity_raw, code)
            subtotal = qmoney(quantity * extra["rate_rub"])
            extra_total += subtotal
            extra_lines.append(
                {
                    "code": f"extra:{code}",
                    "quantity": public_number(quantity),
                    "rate_rub": public_number(extra["rate_rub"]),
                    "rate_source": extra["rate_source"],
                    "subtotal_rub": public_number(subtotal),
                }
            )

        cost_total = qmoney(blank_total + time_total + extra_total)
        pricing = pack.pricing
        percent = pricing["margin_percent"]
        if pricing["margin_basis"] == "on_cost":
            net_price = cost_total * (Decimal(1) + percent / HUNDRED)
        else:
            net_price = cost_total / (Decimal(1) - percent / HUNDRED)
        price = net_price
        if pricing["vat_included"]:
            price *= Decimal(1) + pricing["vat_rate_pct"] / HUNDRED
        price = _round_price(price, pricing["rounding"])
        effective_net = (
            price / (Decimal(1) + pricing["vat_rate_pct"] / HUNDRED)
            if pricing["vat_included"]
            else price
        )
        # Показанные net и НДС обязаны сходиться в показанный итог копейка в
        # копейку: округлённые порознь, они на граничных суммах расходились с
        # ним на копейку — ровно там, где расчёт проверяют на бумаге.
        net_shown = qmoney(effective_net)
        vat_shown = qmoney(price - net_shown)
        # Шаги маршрута, за которыми не стоит ни станочная норма, ни строка
        # заготовки, не стоят в КП ничего — и раньше об этом не говорил никто.
        # Термообработка на стороне — живые деньги подрядчику; молчание про
        # неё неотличимо от «учтено». Отказом это быть не может: контроль и
        # мойка бесплатны штатно. Но список обязан быть на виду.
        blank_codes = {
            str(line["code"]).split(":", 1)[1]
            for line in blank["result"]["lines"]
            if str(line["code"]).startswith("blank:")
        }
        route_unpriced = [
            {"seq": step["seq"], "op_code": step["op_code"], "note": step["note"]}
            for step in route["result"]["steps"]
            if step["op_code"] not in pack.machines and step["op_code"] not in blank_codes
        ]

        result = {
            "pack_revision": pack.revision,
            "pack_sha256": pack.sha256,
            "pack_fingerprint": pack.fingerprint,
            "pack_status": pack.status,
            "blank_digest": blank["result"]["digest"],
            "time_digest": time_stage["result"]["digest"],
            "route_unpriced": route_unpriced,
            "provisional": bool(blank["result"].get("provisional")),
            "cost": {
                "blank_rub": public_number(qmoney(blank_total)),
                "machining_rub": public_number(qmoney(time_total)),
                "extras": extra_lines,
                "extras_rub": public_number(qmoney(extra_total)),
                "total_rub": public_number(cost_total),
            },
            "price": {
                "net_total_rub": public_number(net_shown),
                "vat_amount_rub": public_number(vat_shown),
                "total_rub": public_number(price),
                "currency": "RUB",
                "vat_included": pricing["vat_included"],
                "vat_rate_pct": public_number(pricing["vat_rate_pct"]),
                # Срок действия был в версии 1 и потерялся в версии 2. КП без
                # срока — это обещание на неопределённое время: металл дорожает,
                # а заказчик приходит с прошлогодней бумагой.
                "valid_until": (
                    date.today() + timedelta(days=int(pricing["valid_days"]))
                ).isoformat(),
            },
            # Фактическая наценка, а не заявленная. При округлении вверх она
            # уезжает от заданной (20% превращаются в 23%), и не видит этого
            # никто: в КП её просто не было.
            # Фактический процент считается на том же основании, что и
            # заявленный: при on_price прибыль делится на цену без НДС, а не
            # на себестоимость — иначе рядом с basis=on_price стояла бы цифра
            # по чужой формуле, и «запас на скидку» читался бы на 5 п.п. выше.
            "margin": {
                "basis": pricing["margin_basis"],
                "declared_percent": public_number(pricing["margin_percent"]),
                "effective_percent": public_number(
                    (
                        (effective_net - cost_total)
                        / (cost_total if pricing["margin_basis"] == "on_cost" else effective_net)
                        * HUNDRED
                    ).quantize(Decimal("0.01"))
                    if cost_total and effective_net
                    else Decimal(0)
                ),
            },
        }
        result["digest"] = digest_json(result)

        def mutate(mut_state: dict[str, Any]) -> None:
            current = mut_state.get("stages") or {}
            for name, digest in (("blank", result["blank_digest"]), ("time", result["time_digest"])):
                stage = current.get(name)
                if (
                    not stage
                    or stage.get("status") != "approved"
                    or stage["result"]["digest"] != digest
                ):
                    raise Conflict("Stage changed while building the quote")
                self._require_same_pack(stage, pack, name)
            quote = current.get("quote")
            if quote and quote.get("status") == "approved":
                raise Conflict("Quote already approved")
            self._stages(mut_state)["quote"] = {
                "status": "proposed",
                "proposed_at": utcnow(),
                "result": result,
            }
            self._event(mut_state, "quote", "proposed")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "stage": "quote", "result": result}
