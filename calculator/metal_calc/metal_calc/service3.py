"""Конвейер V3 — схема разделения расчёта V2 методолога (28.08.2026).

Роли: front (человек-оркестратор), book_machine (машинная сборка книги и
механический QA), tech (Агент 1 — маршрутчик, денег не касается), supply
(Агент 2 — материалы/заготовка), norm (Агент 3 — нормировщик), qa (гейты).
Процесс:

    INPUT_FROZEN → BOM_VALIDATED → ROUTE_OPTIONS_READY → ROUTE_FROZEN
    → DETAILED_COSTING → COSTING_COMPLETE → BOOK_ASSEMBLED
    → QA×3 → READY_FOR_LD

Весь V3-заказ живёт в state_json реестра (разделы workflow / bom /
route_variants / costing / book / qa) — тот же приём, что stages конвейера
v6, и по той же причине: таблицы реестра не меняются, legacy-заказы
продолжают жить своим путём. Признак V3-заказа — наличие state["workflow"].

Деньги и время считает только этот код. Агенты передают параметры и
выбирают утверждённый rate_id; значение ставки применяет движок. Каждый
write проверяет роль вызывающего (`actor_role`) — это вторая линия обороны
после нарезки инструментов по ролям в MCP-сервере (F10): даже если
поверхность протекла, запись чужой роли откажет.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from . import process_catalog, workflow as wf
from .book_xlsx import render_client_book
from .errors import Conflict, InvalidRatePack, InvalidState, MetalCalcError, PackChanged
from .intake import SourceManifestEntry, bind_source_manifest, input_metadata
from .packs2 import PipelinePack, PipelinePackStore
from .pricing import qmoney, _round_price
from .registry import Registry
from .service2 import _dec_positive, _note
from .timenorms import machine_minutes, piece_minutes
from .util import SHA256_RE, digest_json, public_number, sha256_bytes, utcnow, validate_id

CALCULATOR_VERSION = "v9"
HUNDRED = Decimal("100")

#: Роли движка. Пустая роль (legacy-набор инструментов) права на V3-write
#: не имеет вовсе: у канарейки без ролей V3-заказ read-only.
ROLES = ("front", "book_machine", "tech", "supply", "norm", "qa")

#: Поле количества по единице тарифа — та же дисциплина «ровно одно поле»,
#: что в blank_cost v6: лишнее число молча выброшенным быть не может.
QTY_FIELD_BY_UNIT = {
    "per_cut_m": "length_m",
    "per_cut": "cuts",
    "per_pierce": "pierces_per_piece",
    "per_bend": "bends_per_piece",
    "per_hour": "hours",
    "per_piece": "pieces",
    "per_kg": "kg",
    "per_m2": "m2",
    "per_km": "km",
}

# Для непрерывного физического драйвера одно число само по себе неоднозначно:
# 12 м может означать контур одной детали или уже сумму на весь заказ. Движок
# не выбирает это за агента — basis обязателен в typed input.
DRIVER_BASIS_UNITS = frozenset({"per_cut_m", "per_hour", "per_kg", "per_m2", "per_km"})

# КП подрядчика — не ставка реестра и не свободная строка книги. Человек
# фиксирует ровно один из двух недвусмысленных базисов, а движок приводит его
# к стоимости всего заказа на той же НДС-базе, что остальные расходы пака.
CONTRACTOR_QUOTE_BASES = frozenset({"per_piece", "order_total"})
CONTRACTOR_QUOTE_STATUSES = (
    "ROUTE_FROZEN",
    "DETAILED_COSTING",
    "COSTING_COMPLETE",
    "BOOK_ASSEMBLED",
    "QA_MECHANICAL_PASS",
    "QA_TECHNOLOGICAL_PASS",
    "READY_FOR_LD",
)


def _require_role(actor_role: str | None, allowed: tuple[str, ...], action_ru: str) -> str:
    if actor_role not in allowed:
        raise InvalidState(
            f"{action_ru} — действие роли {'/'.join(allowed)}; ваша роль: "
            f"{actor_role or 'не задана'}. Разделение ролей — часть схемы "
            f"расчёта, а не пожелание."
        )
    return actor_role


class WorkflowService:
    """Сервис V3. Использует те же Registry и PipelinePackStore, что v6."""

    def __init__(self, registry: Registry, packs: PipelinePackStore) -> None:
        self.registry = registry
        self.packs = packs

    # ── общее ────────────────────────────────────────────────────────────

    @staticmethod
    def _workflow(state: dict[str, Any]) -> dict[str, Any]:
        workflow = state.get("workflow")
        if not isinstance(workflow, dict):
            raise InvalidState(
                "Заказ не ведётся по схеме V2 (нет раздела workflow). Старые "
                "заказы дозакрываются прежним конвейером."
            )
        return workflow

    @staticmethod
    def _event(
        state: dict[str, Any],
        event: str,
        by: str | None = None,
        **audit: Any,
    ) -> None:
        state.setdefault("workflow_events", []).append(
            {
                "event": event,
                "at": utcnow(),
                **({"by": by} if by else {}),
                **audit,
            }
        )

    def _resolve_pack(self) -> PipelinePack:
        pack = self.packs.load_active()
        if pack.status != "active":
            raise InvalidRatePack(
                f"Данные предприятия в состоянии «{pack.status}» — это образец, "
                "а не ваши ставки. Заполните и опубликуйте их на экране «Данные»."
            )
        if pack.schema_version < 3:
            raise InvalidRatePack(
                "Действующий пак данных — прежнего формата (v2), без парка "
                "процессов. Обновите его: metal-calc-admin pack-upgrade даёт "
                "черновик, публикация — через экран «Данные»."
            )
        return pack

    @staticmethod
    def _require_same_pack(recorded_fingerprint: str | None, pack: PipelinePack, what_ru: str) -> None:
        if recorded_fingerprint and recorded_fingerprint != pack.fingerprint:
            raise PackChanged(
                f"{what_ru} посчитан(а) по другой ревизии данных предприятия, "
                f"чем действующая ({pack.revision}). Откройте пересчёт "
                f"(calc_revision_open) и посчитайте заново — гибрид двух "
                f"наборов ставок в одну цену не собирается."
            )

    # ── чтение ───────────────────────────────────────────────────────────

    def workflow_status(self, order_id: str) -> dict[str, Any]:
        revision, state = self.registry.get(order_id)
        workflow = self._workflow(state)
        contractor_quotes = self._checked_contractor_quotes(state)
        variants = {
            variant_id: {
                "status": variant["status"],
                "steps": variant["steps"],
            }
            for variant_id, variant in (state.get("route_variants") or {}).items()
        }
        costing = state.get("costing") or {}
        qa = (state.get("qa") or {}).get(str(workflow["calculation_revision"]), {})
        try:
            pack = self.packs.load_active()
            saved_parts = [
                costing[name]
                for name in ("blank", "time")
                if name in costing
            ] + list(contractor_quotes.values())
            pack_info: dict[str, Any] = {
                "active_revision": pack.revision,
                "schema_version": pack.schema_version,
                "stale": any(
                    not isinstance(part, dict)
                    or part.get("pack_fingerprint") != pack.fingerprint
                    for part in saved_parts
                ),
            }
        except MetalCalcError as exc:
            pack_info = {"active_revision": None, "unavailable": exc.public_message}
        return {
            "order_id": order_id,
            "revision": revision,
            "workflow": workflow,
            "status_title": wf.title(str(workflow.get("status"))),
            "bom": state.get("bom"),
            "route_variants": variants,
            "costing_parts": sorted(
                key for key in costing if key in {"blank", "time"}
            ),
            "contractor_quotes": [
                deepcopy(contractor_quotes[key])
                for key in sorted(contractor_quotes, key=int)
            ],
            "contractor_quote_history_count": len(
                costing.get("contractor_quote_history") or []
            ),
            "book": {
                key: value
                for key, value in (state.get("book") or {}).items()
                if key in {
                    "digest",
                    "provisional",
                    "route_unpriced",
                    "manual_review_required",
                    "component_markups",
                    "price",
                }
            },
            "qa": qa,
            "pack": pack_info,
            "events": state.get("workflow_events", [])[-20:],
        }

    def report_get(
        self,
        order_id: str,
        mode: Literal["internal", "client"],
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Return a deterministic V9 report without mutating QA or the order.

        ``internal`` includes the exact frozen input, current book (including
        INTERNAL_COST), current-revision QA receipts and detailed blockers.
        ``client`` is a separate allowlist: CUSTOMER_PRICE and public blockers
        only, with an explicit FINAL/PRELIMINARY label. It never copies the
        internal report and never infers a missing QA PASS.
        """
        _require_role(actor_role, ("front",), "Отчёт по книге")
        validate_id(order_id, field="order_id")
        if mode not in {"internal", "client"}:
            raise InvalidState("mode — internal или client")
        revision, state = self.registry.get(order_id)
        return self._report_from_state(order_id, revision, state, mode)

    def render_book_xlsx(
        self,
        order_id: str,
        expected_book_digest: str,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Render the exact current V9 book as a client-only XLSX.

        The required digest is optimistic locking for the book itself. The
        renderer has no dependency on legacy ``calculation`` fields, exposes
        no INTERNAL_COST/rate sources, stages nothing and sends nothing. A
        retry with unchanged state returns byte-identical base64 and sha256.
        """
        _require_role(actor_role, ("front",), "Экспорт книги")
        validate_id(order_id, field="order_id")
        if not isinstance(expected_book_digest, str) or not SHA256_RE.fullmatch(
            expected_book_digest
        ):
            raise InvalidState("expected_book_digest — sha256 текущей книги")
        revision, state = self.registry.get(order_id)
        book = self._checked_book(state, required=True)
        if book["digest"] != expected_book_digest:
            raise Conflict("Book digest mismatch")
        preview = self._report_from_state(order_id, revision, state, "client")
        payload, rows = render_client_book(preview)
        return {
            "order_id": order_id,
            "book_digest": expected_book_digest,
            "document_status": preview["document_status"],
            "file_name": f"quote_{order_id}_{expected_book_digest[:12]}.xlsx",
            "sha256": sha256_bytes(payload),
            "bytes": len(payload),
            "rows": rows,
            "content_base64": base64.b64encode(payload).decode("ascii"),
            "byte_stable": True,
        }

    def _report_from_state(
        self,
        order_id: str,
        revision: int,
        state: dict[str, Any],
        mode: Literal["internal", "client"],
    ) -> dict[str, Any]:
        workflow, frozen_input = self._validated_v9_input(state, order_id)
        book = self._checked_book(state, required=mode == "client")
        calculation_revision = int(workflow["calculation_revision"])
        qa = (state.get("qa") or {}).get(str(calculation_revision), {})
        if not isinstance(qa, dict):
            raise Conflict("QA receipts of the current calculation revision are invalid")
        gate_verdicts = self._verified_gate_verdicts(qa)

        pack_error: MetalCalcError | None = None
        pack_changed = False
        active_pack: PipelinePack | None = None
        try:
            active_pack = self._resolve_pack()
            pack_changed = bool(
                book and book.get("pack_fingerprint") != active_pack.fingerprint
            )
        except MetalCalcError as exc:
            pack_error = exc

        blockers = self._report_blockers(
            workflow,
            book,
            gate_verdicts,
            manual_review_complete=bool(
                book
                and active_pack
                and (
                    not book.get("manual_review_required")
                    or self._current_manual_review_receipt(state, book, active_pack)
                )
            ),
            pack_changed=pack_changed,
            pack_unavailable=pack_error is not None,
        )
        price_valid = self._price_valid_on(book, date.fromisoformat(utcnow()[:10]))
        is_final = bool(
            book
            and book.get("price", {}).get("is_final") is True
            and price_valid
            and workflow.get("status") == "READY_FOR_LD"
            and all(gate_verdicts.get(gate) == "PASS" for gate in wf.QA_GATES)
            and (
                not book.get("manual_review_required")
                or bool(
                    active_pack
                    and self._current_manual_review_receipt(state, book, active_pack)
                )
            )
            and not pack_changed
            and pack_error is None
        )
        document_status = "FINAL" if is_final else "PRELIMINARY"

        if mode == "internal":
            return {
                "report_version": "v9",
                "mode": "internal",
                "order_id": order_id,
                "order_revision": revision,
                "customer": deepcopy(state["customer"]),
                "input": frozen_input,
                "workflow": {
                    key: deepcopy(workflow[key])
                    for key in (
                        "status",
                        "calculation_revision",
                        "route_return_count",
                        "calculator_version",
                        "process_catalog_version",
                        "rate_registry_version",
                    )
                    if key in workflow
                },
                "book": deepcopy(book),
                "qa": deepcopy(qa),
                "blockers": blockers,
                "document_status": document_status,
                "is_final": is_final,
            }

        if book is None:  # guarded by _checked_book, keeps type narrowing explicit
            raise InvalidState("У заказа нет текущей книги для клиентского preview")
        if pack_error is not None:
            raise pack_error
        if pack_changed:
            if active_pack is None:
                raise InvalidRatePack("Действующие данные предприятия недоступны")
            self._require_same_pack(book.get("pack_fingerprint"), active_pack, "Книга")
            raise PackChanged("Книга посчитана по другой ревизии данных предприятия")
        price = book.get("price")
        if not isinstance(price, dict) or price.get("amount_kind") != "CUSTOMER_PRICE":
            raise Conflict("Книга не содержит типизированную CUSTOMER_PRICE")
        public_price_keys = (
            "amount_kind",
            "net_total_rub",
            "vat_amount_rub",
            "total_rub",
            "currency",
            "vat_included",
            "vat_rate_pct",
            "valid_until",
        )
        if any(key not in price for key in public_price_keys):
            raise Conflict("Клиентская цена в книге неполна")
        return {
            "report_version": "v9",
            "mode": "client",
            "order_id": order_id,
            "customer": {"name": str(state["customer"]["name"])},
            "quantity": workflow["quantity"],
            "kd_revision": workflow["kd_revision"],
            "document_status": document_status,
            "is_final": is_final,
            "status_text": "Окончательная цена" if is_final else "Предварительная цена",
            "price": {key: deepcopy(price[key]) for key in public_price_keys},
            "blockers": self._public_blockers(blockers),
        }

    @staticmethod
    def _price_valid_on(book: dict[str, Any] | None, today: date) -> bool:
        if not isinstance(book, dict):
            return False
        price = book.get("price")
        if not isinstance(price, dict):
            return False
        value = price.get("valid_until")
        if not isinstance(value, str):
            return False
        try:
            return date.fromisoformat(value) >= today
        except ValueError:
            return False

    def _validated_v9_input(
        self, state: dict[str, Any], order_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        workflow = self._workflow(state)
        if workflow.get("calculator_version") != "v9" or workflow.get(
            "input_contract_version"
        ) != 1:
            raise InvalidState(
                "Новый отчёт доступен только заказам с типизированным V9 input_freeze"
            )
        input_revision = workflow.get("input_order_revision")
        if (
            not isinstance(input_revision, int)
            or isinstance(input_revision, bool)
            or input_revision < 1
        ):
            raise Conflict("Frozen input revision is invalid")
        quantity = workflow.get("quantity")
        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or not 1 <= quantity <= 1_000_000
        ):
            raise Conflict("Frozen input quantity is invalid")
        kd_revision = workflow.get("kd_revision")
        if (
            not isinstance(kd_revision, str)
            or not kd_revision.strip()
            or len(kd_revision) > 512
        ):
            raise Conflict("Frozen input KD revision is invalid")
        manifest = bind_source_manifest(
            state.get("source_files"), workflow.get("source_manifest")
        )
        expected = input_metadata(
            order_id=order_id,
            order_revision=input_revision,
            customer=state.get("customer"),
            quantity=quantity,
            kd_revision=kd_revision,
            source_manifest=manifest,
        )
        for key, value in expected.items():
            if workflow.get(key) != value:
                raise Conflict("Frozen input digest/revision/origin mismatch")
        return workflow, {
            "digest": workflow["input_digest"],
            "order_revision": input_revision,
            "origin": workflow["input_origin"],
            "quantity": quantity,
            "kd_revision": kd_revision,
            "source_manifest": deepcopy(manifest),
        }

    def _checked_book(
        self, state: dict[str, Any], *, required: bool
    ) -> dict[str, Any] | None:
        book = state.get("book")
        if book is None:
            if required:
                raise InvalidState("У заказа нет текущей книги для отчёта или экспорта")
            return None
        if not isinstance(book, dict):
            raise Conflict("Current book is invalid")
        recorded_digest = book.get("digest")
        if not isinstance(recorded_digest, str) or not SHA256_RE.fullmatch(recorded_digest):
            raise Conflict("Current book digest is invalid")
        calculated_digest = digest_json(
            {key: value for key, value in book.items() if key != "digest"}
        )
        if calculated_digest != recorded_digest:
            raise Conflict("Current book digest mismatch")
        workflow = self._workflow(state)
        if book.get("calculation_revision") != workflow.get("calculation_revision"):
            raise Conflict("Книга не относится к текущей ревизии расчёта")
        costing = state.get("costing") or {}
        for part, book_key in (("blank", "blank_digest"), ("time", "time_digest")):
            current = costing.get(part)
            if not isinstance(current, dict) or current.get("digest") != book.get(book_key):
                raise Conflict("Книга не относится к текущим стадиям расчёта")
        _, variant = self._frozen_route(state)
        if variant.get("digest") != book.get("route_digest"):
            raise Conflict("Книга не относится к текущему маршруту")
        quotes = self._checked_contractor_quotes(state)
        if book.get("contractor_quotes_digest") != digest_json(quotes):
            raise Conflict("Книга не относится к текущим КП подрядчиков")
        return book

    @staticmethod
    def _current_manual_review_receipt(
        state: dict[str, Any],
        book: dict[str, Any],
        pack: PipelinePack,
    ) -> dict[str, Any] | None:
        """Return the latest receipt only when every immutable binding matches."""
        workflow = state.get("workflow") or {}
        revision_qa = (state.get("qa") or {}).get(
            str(workflow.get("calculation_revision")), {}
        )
        raw_receipts = (
            revision_qa.get("manual_review_receipts")
            if isinstance(revision_qa, dict)
            else None
        )
        if not isinstance(raw_receipts, list) or not raw_receipts:
            return None
        receipt = raw_receipts[-1]
        manual_items = book.get("manual_review_required")
        if not isinstance(receipt, dict) or not isinstance(manual_items, list):
            return None
        recorded_digest = receipt.get("digest")
        if (
            not isinstance(recorded_digest, str)
            or not SHA256_RE.fullmatch(recorded_digest)
            or digest_json({key: value for key, value in receipt.items() if key != "digest"})
            != recorded_digest
        ):
            return None
        expected = {
            "contract_version": 1,
            "book_digest": book.get("digest"),
            "manual_items_digest": digest_json(manual_items),
            "calculation_revision": workflow.get("calculation_revision"),
            "pack_revision": pack.revision,
            "pack_fingerprint": pack.fingerprint,
            "actor_role": "qa",
        }
        if any(receipt.get(field) != value for field, value in expected.items()):
            return None
        if not isinstance(receipt.get("reviewed_by"), str) or not receipt[
            "reviewed_by"
        ].strip():
            return None
        reviews = receipt.get("reviews")
        if not isinstance(reviews, list) or len(reviews) != len(manual_items):
            return None
        for index, (item, review) in enumerate(zip(manual_items, reviews, strict=True)):
            if not isinstance(item, dict) or not isinstance(review, dict):
                return None
            if set(review) != {
                "item_index",
                "item_digest",
                "item",
                "evidence",
                "reference",
            }:
                return None
            if (
                review.get("item_index") != index
                or review.get("item_digest") != digest_json(item)
                or review.get("item") != item
                or not isinstance(review.get("evidence"), str)
                or not review["evidence"].strip()
                or not isinstance(review.get("reference"), str)
                or not review["reference"].strip()
            ):
                return None
        return receipt

    def manual_review_complete(
        self,
        order_id: str,
        expected_revision: int,
        expected_book_digest: str,
        reviews: list[dict[str, Any]],
        reviewed_by: str,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Append one human receipt for every manual item in the current book."""
        _require_role(actor_role, ("qa",), "Закрытие ручной сверки")
        validate_id(order_id, field="order_id")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise InvalidState("expected_revision — целая ревизия заказа от 1")
        if not isinstance(expected_book_digest, str) or not SHA256_RE.fullmatch(
            expected_book_digest
        ):
            raise InvalidState("expected_book_digest — sha256 текущей книги")
        if not isinstance(reviews, list) or not 1 <= len(reviews) <= 64:
            raise InvalidState("reviews — непустой список ручных проверок до 64 пунктов")
        reviewer = _note(reviewed_by, "reviewed_by")
        pack = self._resolve_pack()
        saved: list[dict[str, Any]] = []

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(
                workflow,
                ("BOOK_ASSEMBLED", "QA_MECHANICAL_PASS", "QA_TECHNOLOGICAL_PASS"),
                "Закрытие ручной сверки",
            )
            book = self._checked_book(state, required=True)
            if book is None:  # narrowed by required=True
                raise InvalidState("Текущая книга отсутствует")
            if book["digest"] != expected_book_digest:
                raise Conflict("Book digest mismatch")
            self._require_same_pack(book.get("pack_fingerprint"), pack, "Книга")
            manual_items = book.get("manual_review_required")
            if not isinstance(manual_items, list) or not manual_items:
                raise InvalidState("В текущей книге нет пунктов ручной сверки")
            if len(reviews) != len(manual_items):
                raise InvalidState("Нужно проверить каждый пункт текущей книги ровно один раз")

            normalized: list[dict[str, Any]] = []
            for index, (item, raw) in enumerate(zip(manual_items, reviews, strict=True)):
                if not isinstance(item, dict) or not isinstance(raw, dict):
                    raise InvalidState("Пункт ручной сверки имеет неверный формат")
                if set(raw) != {"item_index", "item_digest", "evidence", "reference"}:
                    raise InvalidState(
                        "Проверка содержит только item_index, item_digest, evidence, reference"
                    )
                expected_item_digest = digest_json(item)
                if (
                    raw.get("item_index") != index
                    or raw.get("item_digest") != expected_item_digest
                ):
                    raise Conflict("Пункты ручной сверки изменились или переданы не по порядку")
                normalized.append(
                    {
                        "item_index": index,
                        "item_digest": expected_item_digest,
                        "item": deepcopy(item),
                        "evidence": _note(raw.get("evidence"), "evidence"),
                        "reference": _note(raw.get("reference"), "reference"),
                    }
                )

            receipt: dict[str, Any] = {
                "contract_version": 1,
                "book_digest": book["digest"],
                "manual_items_digest": digest_json(manual_items),
                "calculation_revision": workflow["calculation_revision"],
                "pack_revision": pack.revision,
                "pack_fingerprint": pack.fingerprint,
                "reviewed_by": reviewer,
                "actor_role": actor_role,
                "recorded_at": utcnow(),
                "reviews": normalized,
            }
            receipt["digest"] = digest_json(receipt)
            receipts = wf.qa_receipts(state).setdefault(
                "manual_review_receipts", []
            )
            if not isinstance(receipts, list):
                raise Conflict("Manual review receipt history is invalid")
            receipts.append(receipt)
            saved.append(deepcopy(receipt))
            self._event(
                state,
                "manual_review_completed",
                reviewer,
                receipt_digest=receipt["digest"],
                book_digest=book["digest"],
                manual_items_digest=receipt["manual_items_digest"],
            )

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "status": state["workflow"]["status"],
            "receipt": saved[-1],
        }

    @staticmethod
    def _checked_contractor_quotes(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Return current immutable quote records after verifying their digests."""
        costing = state.get("costing") or {}
        raw = costing.get("contractor_quotes") or {}
        if not isinstance(raw, dict):
            raise Conflict("Current contractor quotes are invalid")
        checked: dict[str, dict[str, Any]] = {}
        required = {
            "contract_version",
            "amount_kind",
            "cost_line_id",
            "route_seq",
            "process_code",
            "amount_rub",
            "currency",
            "basis",
            "vat_included",
            "quoted_at",
            "valid_until",
            "source",
            "reference",
            "panel_actor",
            "order_quantity",
            "order_amount_rub",
            "normalized_amount_rub",
            "normalized_basis",
            "normalized_vat_included",
            "vat_rate_pct",
            "subtotal_rub",
            "route_digest",
            "calculation_revision",
            "pack_revision",
            "pack_fingerprint",
            "recorded_at",
            "digest",
        }
        for key, quote in raw.items():
            if not isinstance(key, str) or not key.isdigit() or int(key) < 1:
                raise Conflict("Contractor quote key is invalid")
            if not isinstance(quote, dict) or not required <= set(quote):
                raise Conflict("Contractor quote record is incomplete")
            if quote.get("route_seq") != int(key):
                raise Conflict("Contractor quote route sequence mismatch")
            recorded = quote.get("digest")
            if not isinstance(recorded, str) or not SHA256_RE.fullmatch(recorded):
                raise Conflict("Contractor quote digest is invalid")
            if digest_json({k: v for k, v in quote.items() if k != "digest"}) != recorded:
                raise Conflict("Contractor quote digest mismatch")
            checked[key] = quote
        return checked

    @staticmethod
    def _contractor_quote_stale_reasons(
        quote: dict[str, Any],
        *,
        workflow: dict[str, Any],
        variant: dict[str, Any],
        step: dict[str, Any],
        pack: PipelinePack,
    ) -> list[str]:
        reasons: list[str] = []
        expected = {
            "route_seq": step["seq"],
            "process_code": step["process_code"],
            "route_digest": variant["digest"],
            "calculation_revision": workflow["calculation_revision"],
            "pack_fingerprint": pack.fingerprint,
            "order_quantity": workflow["quantity"],
            "normalized_basis": "order_total",
            "normalized_vat_included": bool(pack.pricing.get("rates_include_vat")),
        }
        for field, value in expected.items():
            if quote.get(field) != value:
                reasons.append(field)
        if quote.get("contract_version") != 1 or quote.get("currency") != "RUB":
            reasons.append("contract_version")
        try:
            quote_date = date.fromisoformat(str(quote.get("quoted_at")))
            valid_until = date.fromisoformat(str(quote.get("valid_until")))
            utc_today = date.fromisoformat(utcnow()[:10])
        except ValueError:
            reasons.append("valid_until")
        else:
            if quote_date > utc_today:
                reasons.append("quoted_at")
            if valid_until < quote_date or valid_until < utc_today:
                reasons.append("valid_until")
        return reasons

    def contractor_quote_set(
        self,
        order_id: str,
        expected_revision: int,
        route_seq: int,
        amount_rub: Any,
        basis: str,
        vat_included: bool,
        quoted_at: str,
        valid_until: str,
        source: str,
        reference: str,
        panel_actor: str,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Record one human-supplied contractor quote for the frozen route.

        ``panel`` is deliberately not an MCP role. The authenticated dashboard
        reaches this method through ``metal-calc-admin``; model-visible MCP
        surfaces have no writer for this record.
        """
        _require_role(actor_role, ("panel",), "Фиксация КП подрядчика")
        validate_id(order_id, field="order_id")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise InvalidState("expected_revision — целая ревизия заказа от 1")
        if isinstance(route_seq, bool) or not isinstance(route_seq, int) or route_seq < 1:
            raise InvalidState("route_seq — целый номер шага маршрута от 1")
        amount = qmoney(
            _dec_positive(
                amount_rub,
                "amount_rub",
                maximum=Decimal("1000000000000"),
            )
        )
        if basis not in CONTRACTOR_QUOTE_BASES:
            raise InvalidState("basis — per_piece или order_total")
        if type(vat_included) is not bool:
            raise InvalidState("vat_included — boolean")
        if not isinstance(quoted_at, str):
            raise InvalidState("quoted_at — дата YYYY-MM-DD")
        try:
            quote_date = date.fromisoformat(quoted_at)
        except ValueError as exc:
            raise InvalidState("quoted_at — дата YYYY-MM-DD") from exc
        if quote_date.isoformat() != quoted_at:
            raise InvalidState("quoted_at — дата YYYY-MM-DD")
        if quote_date > date.fromisoformat(utcnow()[:10]):
            raise InvalidState("quoted_at не может быть датой из будущего")
        if not isinstance(valid_until, str):
            raise InvalidState("valid_until — дата YYYY-MM-DD не раньше quoted_at")
        try:
            valid_date = date.fromisoformat(valid_until)
        except ValueError as exc:
            raise InvalidState(
                "valid_until — дата YYYY-MM-DD не раньше quoted_at"
            ) from exc
        if valid_date.isoformat() != valid_until or valid_date < quote_date:
            raise InvalidState("valid_until — дата YYYY-MM-DD не раньше quoted_at")
        source_text = _note(source, "source")
        reference_text = _note(reference, "reference")
        actor = _note(panel_actor, "panel_actor")
        pack = self._resolve_pack()
        saved: list[dict[str, Any]] = []

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(
                workflow,
                CONTRACTOR_QUOTE_STATUSES,
                "Фиксация КП подрядчика",
            )
            _, variant = self._frozen_route(state)
            self._require_same_pack(
                variant.get("pack_fingerprint"), pack, "Замороженный маршрут"
            )
            matches = [
                step
                for step in variant.get("steps", [])
                if step.get("seq") == route_seq
                and step.get("execution_mode") == "outsource"
            ]
            if len(matches) != 1:
                raise InvalidState(
                    f"Шаг {route_seq} не является ровно одним outsource-шагом "
                    "замороженного маршрута"
                )
            step = matches[0]

            quotes_before = self._checked_contractor_quotes(state)
            prior_before = quotes_before.get(str(route_seq))
            receipts = (state.get("qa") or {}).get(
                str(workflow["calculation_revision"]), {}
            )
            has_downstream = isinstance(state.get("book"), dict) or bool(receipts)
            if prior_before is not None or has_downstream:
                costing_now = state.get("costing") or {}
                blank_current = isinstance(costing_now.get("blank"), dict)
                time_current = isinstance(costing_now.get("time"), dict)
                if has_downstream and (not blank_current or not time_current):
                    raise Conflict("Нельзя пересчитать книгу без текущих стадий расчёта")
                wf.bump_revision(
                    state,
                    to_status=(
                        "COSTING_COMPLETE"
                        if blank_current and time_current
                        else "DETAILED_COSTING"
                        if blank_current
                        else "ROUTE_FROZEN"
                    ),
                    reason=f"contractor_quote_changed:{route_seq}",
                    by=actor,
                    at=utcnow(),
                    drop=("book",),
                )
                workflow = self._workflow(state)
                state["status"] = workflow["status"]

            costing = state.setdefault("costing", {})
            quotes = costing.setdefault("contractor_quotes", {})
            if not isinstance(quotes, dict):
                raise Conflict("Current contractor quotes are invalid")
            prior = quotes.get(str(route_seq))
            if prior is not None:
                # Запись не редактируется на месте: прежний подписанный digest
                # остаётся в append-only истории, current pointer меняется.
                self._checked_contractor_quotes(state)
                history = costing.setdefault("contractor_quote_history", [])
                if not isinstance(history, list):
                    raise Conflict("Contractor quote history is invalid")
                history.append(deepcopy(prior))

            quantity = int(workflow["quantity"])
            order_amount = qmoney(
                amount * Decimal(quantity) if basis == "per_piece" else amount
            )
            vat_rate = Decimal(str(pack.pricing["vat_rate_pct"]))
            pack_vat_basis = bool(pack.pricing.get("rates_include_vat"))
            normalized = order_amount
            if vat_included != pack_vat_basis:
                vat_factor = Decimal(1) + vat_rate / HUNDRED
                normalized = (
                    order_amount / vat_factor
                    if vat_included
                    else order_amount * vat_factor
                )
            normalized = qmoney(normalized)
            quote: dict[str, Any] = {
                "contract_version": 1,
                "amount_kind": "INTERNAL_COST",
                "cost_line_id": f"contractor:{route_seq}",
                "route_seq": route_seq,
                "process_code": step["process_code"],
                **(
                    {"actual_process_name": step["actual_process_name"]}
                    if "actual_process_name" in step
                    else {}
                ),
                "amount_rub": public_number(amount),
                "currency": "RUB",
                "basis": basis,
                "vat_included": vat_included,
                "quoted_at": quoted_at,
                "valid_until": valid_until,
                "source": source_text,
                "reference": reference_text,
                "panel_actor": actor,
                "order_quantity": quantity,
                "order_amount_rub": public_number(order_amount),
                "normalized_amount_rub": public_number(normalized),
                "normalized_basis": "order_total",
                "normalized_vat_included": pack_vat_basis,
                "vat_rate_pct": public_number(vat_rate),
                "subtotal_rub": public_number(normalized),
                "route_digest": variant["digest"],
                "calculation_revision": workflow["calculation_revision"],
                "pack_revision": pack.revision,
                "pack_fingerprint": pack.fingerprint,
                "recorded_at": utcnow(),
            }
            quote["digest"] = digest_json(quote)
            quotes[str(route_seq)] = quote
            costing["pack_fingerprint"] = pack.fingerprint
            saved.append(deepcopy(quote))
            self._event(state, f"contractor_quote_set:{route_seq}", actor)

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "calculation_revision": state["workflow"]["calculation_revision"],
            "status": state["workflow"]["status"],
            "quote": saved[-1],
        }

    @staticmethod
    def _verified_gate_verdicts(qa: dict[str, Any]) -> dict[str, str | None]:
        verdicts: dict[str, str | None] = {}
        for gate in wf.QA_GATES:
            receipt = qa.get(gate)
            if receipt is None:
                verdicts[gate] = None
                continue
            if not isinstance(receipt, dict):
                raise Conflict("QA receipt is invalid")
            verdict = receipt.get("verdict")
            verdicts[gate] = str(verdict) if verdict is not None else None
        mechanical = qa.get("mechanical")
        if isinstance(mechanical, dict) and mechanical.get("verdict") == "PASS":
            checks = mechanical.get("checks")
            if (
                mechanical.get("computed_by") != "engine"
                or not isinstance(checks, list)
                or not checks
                or any(not isinstance(check, dict) or check.get("ok") is not True for check in checks)
            ):
                verdicts["mechanical"] = "INVALID_PASS"
        for gate in ("technological", "commercial"):
            receipt = qa.get(gate)
            if (
                isinstance(receipt, dict)
                and receipt.get("verdict") == "PASS"
                and receipt.get("actor_role") != "qa"
            ):
                verdicts[gate] = "INVALID_PASS"
        return verdicts

    @staticmethod
    def _report_blockers(
        workflow: dict[str, Any],
        book: dict[str, Any] | None,
        gate_verdicts: dict[str, str | None],
        *,
        manual_review_complete: bool,
        pack_changed: bool,
        pack_unavailable: bool,
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        if workflow.get("status") == "BLOCK_FOR_TECH_REVIEW":
            blockers.append(
                {"code": "technical_review", "message": "Требуется технологический разбор"}
            )
        if book is None:
            blockers.append({"code": "book_missing", "message": "Текущая книга ещё не собрана"})
        else:
            if book.get("provisional"):
                blockers.append(
                    {"code": "material_price_preliminary", "message": "Цена материала предварительная"}
                )
            if book.get("route_unpriced"):
                blockers.append(
                    {
                        "code": "route_unpriced",
                        "message": "Есть операции подрядчика без цены",
                        "items": deepcopy(book["route_unpriced"]),
                    }
                )
            if book.get("manual_review_required") and not manual_review_complete:
                blockers.append(
                    {
                        "code": "manual_review_required",
                        "message": "Есть операции для ручной коммерческой сверки",
                        "items": deepcopy(book["manual_review_required"]),
                    }
                )
            if not WorkflowService._price_valid_on(
                book, date.fromisoformat(utcnow()[:10])
            ):
                blockers.append(
                    {
                        "code": "price_expired",
                        "message": "Срок действия цены истёк или задан некорректно",
                    }
                )
        if not all(verdict == "PASS" for verdict in gate_verdicts.values()):
            blockers.append(
                {
                    "code": "qa_incomplete",
                    "message": "QA текущей ревизии не завершён",
                    "gates": gate_verdicts,
                }
            )
        if pack_unavailable:
            blockers.append(
                {"code": "pack_unavailable", "message": "Данные предприятия недоступны"}
            )
        elif pack_changed:
            blockers.append(
                {"code": "pack_changed", "message": "Книга собрана по другой ревизии данных"}
            )
        return blockers

    @staticmethod
    def _public_blockers(blockers: list[dict[str, Any]]) -> list[dict[str, str]]:
        mapping = {
            "technical_review": (
                "technical_review_pending",
                "Расчёт проходит технологическое уточнение",
            ),
            "material_price_preliminary": (
                "material_price_pending",
                "Уточняется цена материала",
            ),
            "route_unpriced": (
                "contractor_quote_pending",
                "Уточняется стоимость работ подрядчика",
            ),
            "manual_review_required": (
                "commercial_review_pending",
                "Расчёт проходит коммерческое уточнение",
            ),
            "qa_incomplete": (
                "approval_pending",
                "Расчёт проходит внутреннюю проверку",
            ),
            "price_expired": (
                "price_expired",
                "Срок действия цены требуется обновить",
            ),
        }
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        for blocker in blockers:
            public = mapping.get(str(blocker.get("code")))
            if public is None or public[0] in seen:
                continue
            result.append({"code": public[0], "message": public[1]})
            seen.add(public[0])
        return result

    def process_catalog_tool(self) -> dict[str, Any]:
        """Каталог операций и парк предприятия — глазами агента.

        Каталог отвечает «что бывает», парк — «что умеем мы». Оба нужны
        одновременно: маршрут строится по каталогу, а исполняется по парку,
        и разница между ними — это подряд, а не отказ.
        """
        listing = process_catalog.catalog_listing()
        try:
            pack = self.packs.load_active()
        except MetalCalcError as exc:
            return {
                "catalog_version": process_catalog.CATALOG_VERSION,
                "catalog": listing,
                "park": None,
                "reason": exc.public_message,
            }
        park = {
            code: {
                **entry,
                "rate_known": entry.get("rate_id") is not None,
                "rates": [
                    {
                        "rate_id": rate_id,
                        "kind": pack.rate(rate_id)["kind"],
                        "tariff_unit": pack.rate(rate_id)["tariff_unit"],
                        "quantity_field": QTY_FIELD_BY_UNIT[
                            pack.rate(rate_id)["tariff_unit"]
                        ],
                        "quantity_basis_required": (
                            pack.rate(rate_id)["tariff_unit"] in DRIVER_BASIS_UNITS
                        ),
                        **(
                            {
                                "axes": [
                                    {
                                        "name": axis["name"],
                                        "unit": axis["unit"],
                                    }
                                    for axis in pack.rate(rate_id)["axes"]
                                ]
                            }
                            if pack.rate(rate_id)["kind"] == "matrix"
                            else {}
                        ),
                    }
                    for rate_id in (
                        ([entry["rate_id"]] if entry.get("rate_id") else [])
                        + entry.get("supplementary_rate_ids", [])
                    )
                ],
            }
            for code, entry in sorted(pack.process_park.items())
        }
        return {
            "catalog_version": process_catalog.CATALOG_VERSION,
            "catalog": listing,
            "park": park,
            "pack_revision": pack.revision,
            "rate_registry": sorted(pack.rate_registry),
        }

    # ── front: фиксация входа ────────────────────────────────────────────

    def input_freeze(
        self,
        order_id: str,
        expected_revision: int,
        quantity: Any,
        kd_revision: str,
        source_manifest: list[SourceManifestEntry],
        note: str,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        _require_role(actor_role, ("front",), "Фиксация входа")
        validate_id(order_id, field="order_id")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
        ):
            raise InvalidState("expected_revision — целая ревизия заказа от 1")
        if (
            not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or not 1 <= quantity <= 1_000_000
        ):
            raise InvalidState(
                "quantity — число деталей в заказе, целое от 1. Оно "
                "объявляется один раз при фиксации входа и сверяется всеми "
                "стадиями."
            )
        kd = _note(kd_revision, "kd_revision")
        _note(note, "note")
        try:
            pack = self._resolve_pack()
            registry_version: str | None = pack.revision
        except MetalCalcError:
            # Вход фиксируется и без заведённых данных: заказ существует,
            # считать его пока нечем — и это честно записано.
            registry_version = None

        def mutate(state: dict[str, Any]) -> None:
            if "workflow" in state:
                raise Conflict("Вход уже зафиксирован — повторная фиксация запрещена")
            stages = state.get("stages") or {}
            if stages:
                raise Conflict(
                    "По заказу уже шёл конвейер v6 — доведите его прежними "
                    "инструментами или заведите новый заказ"
                )
            # Заказ с v1-расчётом или закрытым статусом в схему V2 не
            # переводится: state держал бы ДВЕ живые цены (старую v1 наверху
            # и новую в книге), а список заказов показывал бы старую
            # (находка Codex B5).
            if state.get("calculation") or state.get("price") or state.get("cost"):
                raise Conflict(
                    "По заказу уже есть расчёт прежнего калькулятора — две "
                    "живые цены в одном заказе запрещены; заведите новый заказ"
                )
            if state.get("status") in {"calculated", "quoted", "won", "lost", "cancelled"}:
                raise Conflict(
                    f"Заказ в статусе «{state.get('status')}» в схему V2 не "
                    f"переводится — заведите новый заказ"
                )
            frozen_manifest = bind_source_manifest(
                state.get("source_files"), source_manifest
            )
            metadata = input_metadata(
                order_id=order_id,
                order_revision=expected_revision,
                customer=state.get("customer"),
                quantity=quantity,
                kd_revision=kd,
                source_manifest=frozen_manifest,
            )
            state["workflow"] = wf.new_workflow(
                quantity=quantity,
                kd_revision=kd,
                source_manifest=frozen_manifest,
                calculator_version=CALCULATOR_VERSION,
                process_catalog_version=process_catalog.CATALOG_VERSION,
                rate_registry_version=registry_version,
                input_metadata=metadata,
            )
            state["status"] = "INPUT_FROZEN"
            self._event(state, "input_frozen", note)

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "workflow": state["workflow"]}

    # ── tech: BOM и маршрут ──────────────────────────────────────────────

    def bom_upsert(
        self,
        order_id: str,
        expected_revision: int,
        nodes: list[dict[str, Any]],
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Вырожденный BOM M1: одна изготавливаемая деталь + покупные позиции.

        Поля контракта (bom_node_id, parent_bom_id, node_type, make_or_buy)
        заложены полностью — рекурсия make-детей придёт отдельной фазой, и
        схема состояния при этом не изменится.
        """
        _require_role(actor_role, ("tech",), "Состав изделия")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 32:
            raise InvalidState("BOM — список из 1..32 узлов")
        normalized: dict[str, dict[str, Any]] = {}
        roots: list[str] = []
        for node_raw in nodes:
            expected = {"bom_node_id", "parent_bom_id", "node_type", "make_or_buy", "quantity", "note"}
            if not isinstance(node_raw, dict) or set(node_raw) != expected:
                raise InvalidState(
                    "Узел BOM: bom_node_id, parent_bom_id, node_type, "
                    "make_or_buy, quantity, note"
                )
            node_id = validate_id(str(node_raw["bom_node_id"]), field="bom_node_id")
            if node_id in normalized:
                raise InvalidState(f"Узел «{node_id}» задан дважды")
            node_type = node_raw["node_type"]
            make_or_buy = node_raw["make_or_buy"]
            if node_type == "PURCHASED_ITEM":
                # Тарификации покупных в этом релизе НЕТ, а узел без денег —
                # это ноль в цене всех болтов при трёх зелёных гейтах
                # (находка Codex B2). Fail-closed до фазы закупок.
                raise InvalidState(
                    "Покупные позиции в этом релизе не тарифицируются — узел "
                    "с ними дал бы нулевую стоимость закупки при зелёных "
                    "проверках. Учтите покупное отдельной строкой снабжения "
                    "вне расчёта; закупочные узлы BOM придут следующей фазой."
                )
            if node_type != "MANUFACTURED_PART":
                raise InvalidState(
                    "В этом релизе node_type — только MANUFACTURED_PART; "
                    "сборки и рекурсия придут следующей фазой"
                )
            if (node_type == "MANUFACTURED_PART") != (make_or_buy == "MAKE"):
                raise InvalidState("node_type и make_or_buy противоречат друг другу")
            parent = node_raw["parent_bom_id"]
            if parent is not None:
                raise InvalidState(
                    "Изготавливаемая деталь в этом релизе — корень "
                    "(parent_bom_id: null)"
                )
            roots.append(node_id)
            quantity = _dec_positive(node_raw["quantity"], "quantity")
            normalized[node_id] = {
                "bom_node_id": node_id,
                "parent_bom_id": parent,
                "node_type": node_type,
                "make_or_buy": make_or_buy,
                "quantity": public_number(quantity),
                "note": _note(node_raw["note"], "bom note"),
            }
        if len(roots) != 1:
            raise InvalidState("Ровно одна изготавливаемая деталь-корень в этом релизе")
        root_id = roots[0]

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(workflow, ("INPUT_FROZEN", "BOM_VALIDATED"), "Состав изделия")
            state["bom"] = {"root": root_id, "nodes": normalized}
            workflow["status"] = "BOM_VALIDATED"
            state["status"] = "BOM_VALIDATED"
            self._event(state, "bom_validated")

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "bom": state["bom"]}

    def route_variants_propose(
        self,
        order_id: str,
        expected_revision: int,
        steps: list[dict[str, Any]],
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Вариант маршрута от технолога. Денег в нём нет по построению."""
        _require_role(actor_role, ("tech",), "Предложение маршрута")
        pack = self._resolve_pack()
        if not isinstance(steps, list) or not 1 <= len(steps) <= 40:
            raise InvalidState("Маршрут — список из 1..40 шагов")
        normalized: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            expected = {"seq", "process_code", "execution_mode", "note"}
            optional = {"actual_process_name"}
            if not isinstance(step, dict) or not expected <= set(step) or set(step) - expected - optional:
                raise InvalidState(
                    "Шаг маршрута: seq, process_code, execution_mode "
                    "(in_house|outsource), note; actual_process_name — только "
                    "для OTHER.SPECIFIED"
                )
            if step["seq"] != index:
                raise InvalidState("Шаги маршрута нумеруются подряд с 1")
            code = str(step["process_code"])
            spec = process_catalog.get(code)
            mode = step["execution_mode"]
            if mode not in {"in_house", "outsource"}:
                raise InvalidState("execution_mode — in_house или outsource")
            entry: dict[str, Any] = {
                "seq": index,
                "process_code": code,
                "execution_mode": mode,
                "cost_owner": spec.cost_owner,
                "note": _note(step["note"], "route step"),
            }
            if not spec.is_process:
                # OTHER.SPECIFIED: только подряд и только с точным именем
                # фактического процесса — дословно правило методолога.
                if mode != "outsource":
                    raise InvalidState(
                        "OTHER.SPECIFIED — слот подряда: execution_mode может "
                        "быть только outsource"
                    )
                entry["actual_process_name"] = _note(
                    step.get("actual_process_name"), "actual_process_name"
                )
            elif "actual_process_name" in step:
                raise InvalidState(
                    "actual_process_name допустим только у OTHER.SPECIFIED — "
                    "у профильного кода имя уже есть"
                )
            if mode == "in_house":
                park_entry = pack.park_entry(code)  # отказ, если кода нет в парке
                if spec.method_required:
                    entry["method_code"] = park_entry["method_code"]
            normalized.append(entry)
        payload = {
            "steps": normalized,
            "pack_revision": pack.revision,
            "pack_fingerprint": pack.fingerprint,
        }
        variant_digest = digest_json(payload)
        created: list[str] = []

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(
                workflow, ("BOM_VALIDATED", "ROUTE_OPTIONS_READY"), "Предложение маршрута"
            )
            variants = state.setdefault("route_variants", {})
            active = [v for v in variants.values() if v.get("status") not in {"superseded"}]
            if len(active) >= 5:
                raise InvalidState("Не больше пяти живых вариантов маршрута")
            variant_id = f"variant-{len(variants) + 1}"
            variants[variant_id] = {
                "status": "proposed",
                "proposed_at": utcnow(),
                "digest": variant_digest,
                **payload,
            }
            workflow["status"] = "ROUTE_OPTIONS_READY"
            state["status"] = "ROUTE_OPTIONS_READY"
            created.append(variant_id)
            self._event(state, f"route_variant_proposed:{variant_id}")

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        variant_id = created[-1]
        return {
            "order_id": order_id,
            "revision": revision,
            "variant_id": variant_id,
            "variant": state["route_variants"][variant_id],
        }

    def route_freeze(
        self,
        order_id: str,
        expected_revision: int,
        variant_id: str,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """ROUTE_FROZEN — обязателен всегда, даже при единственном варианте."""
        _require_role(actor_role, ("tech",), "Заморозка маршрута")
        pack = self._resolve_pack()

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(workflow, ("ROUTE_OPTIONS_READY",), "Заморозка маршрута")
            variants = state.get("route_variants") or {}
            variant = variants.get(variant_id)
            if not variant or variant.get("status") == "superseded":
                raise InvalidState(
                    f"Вариант «{variant_id}» не найден среди живых; есть: "
                    + (", ".join(sorted(variants)) or "—")
                )
            self._require_same_pack(variant.get("pack_fingerprint"), pack, "Вариант маршрута")
            for other_id, other in variants.items():
                if other_id != variant_id and other.get("status") == "frozen":
                    raise Conflict("Другой вариант уже заморожен")
            variant["status"] = "frozen"
            variant["frozen_at"] = utcnow()
            workflow["status"] = "ROUTE_FROZEN"
            workflow["frozen_variant_id"] = variant_id
            state["status"] = "ROUTE_FROZEN"
            self._event(state, f"route_frozen:{variant_id}")

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "variant_id": variant_id,
            "status": state["workflow"]["status"],
        }

    def route_return(
        self,
        order_id: str,
        expected_revision: int,
        reason: str,
        returned_by: str | None = None,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        _require_role(actor_role, ("qa",), "Возврат маршрута")
        reason_text = _note(reason, "reason")
        actor = _note(returned_by, "returned_by") if returned_by is not None else str(actor_role)

        def mutate(state: dict[str, Any]) -> None:
            self._workflow(state)
            outcome = wf.route_return(
                state, reason=reason_text, by=actor, at=utcnow()
            )
            state["status"] = state["workflow"]["status"]
            self._event(state, f"route_return:{outcome}", actor, reason=reason_text)

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        workflow = state["workflow"]
        blocked = workflow["status"] == "BLOCK_FOR_TECH_REVIEW"
        return {
            "order_id": order_id,
            "revision": revision,
            "status": workflow["status"],
            "route_return_count": workflow["route_return_count"],
            "blocked": blocked,
            "message": (
                "Формальный возврат уже был использован: автоматический цикл "
                "остановлен, нужен человеческий технологический разбор."
                if blocked
                else "Маршрут возвращён технологу; расчёт начнётся заново с "
                "новой calculation_revision."
            ),
        }

    # ── supply / norm: подробный расчёт ─────────────────────────────────

    def _frozen_route(self, state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        workflow = self._workflow(state)
        variant_id = workflow.get("frozen_variant_id")
        variant = (state.get("route_variants") or {}).get(str(variant_id))
        if not variant or variant.get("status") != "frozen":
            raise InvalidState("Замороженного маршрута нет — расчёт стадий не открыт")
        return str(variant_id), variant

    @staticmethod
    def _costing(state: dict[str, Any]) -> dict[str, Any]:
        return state.setdefault("costing", {})

    def _line_from_rate(
        self,
        pack: PipelinePack,
        *,
        cost_line_id: str,
        rate_id: str,
        item: dict[str, Any],
        note: str,
        order_quantity: int,
        in_house_axis_max: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Строка стоимости из утверждённой ставки: значение применяет движок."""
        rate = pack.rate(rate_id)
        unit = rate["tariff_unit"]
        field = QTY_FIELD_BY_UNIT[unit]
        provided = {k for k in QTY_FIELD_BY_UNIT.values() if k in item}
        if provided != {field}:
            raise InvalidState(
                f"Ставка «{rate_id}» тарифицируется полем {field} ({unit}); "
                f"переданы: {', '.join(sorted(provided)) or 'ничего'}"
            )
        driver_quantity = _dec_positive(item[field], field)
        if unit in {"per_piece", "per_cut", "per_pierce", "per_bend"} and (
            driver_quantity != driver_quantity.to_integral_value()
        ):
            raise InvalidState(
                f"Ставка «{rate_id}» требует целое количество {field}; "
                f"передано {driver_quantity}"
            )
        driver_basis = item.get("quantity_basis")
        if unit in DRIVER_BASIS_UNITS:
            if driver_basis not in {"per_piece", "order_total"}:
                raise InvalidState(
                    f"Ставка «{rate_id}» требует quantity_basis: per_piece "
                    f"(значение на одну деталь) или order_total (на весь заказ)"
                )
            quantity = (
                driver_quantity * Decimal(order_quantity)
                if driver_basis == "per_piece"
                else driver_quantity
            )
        else:
            if driver_basis is not None:
                raise InvalidState(
                    f"Ставка «{rate_id}» не принимает quantity_basis"
                )
            quantity = (
                driver_quantity * Decimal(order_quantity)
                if unit in {"per_pierce", "per_bend"}
                else driver_quantity
            )
        # Штучный тариф умножается на ЗАКАЗ, а не на число из головы агента:
        # pieces=1 при заказе на сто изделий давал 1 × тариф при зелёном QA
        # (находка Codex B1). Количество одно на заказ — как масса и нормы.
        if unit == "per_piece" and quantity != Decimal(order_quantity):
            raise InvalidState(
                f"Ставка «{rate_id}» штучная: pieces обязан равняться "
                f"количеству заказа ({order_quantity}), передано {quantity}"
            )
        axes = item.get("axes")
        if rate["kind"] == "matrix":
            if not isinstance(axes, dict):
                raise InvalidState(
                    f"Ставка «{rate_id}» матричная: передайте axes со "
                    f"значениями осей ({', '.join(a['name'] for a in rate['axes'])})"
                )
            value = pack.rate_value(rate_id, axes)
            for axis_name, maximum_raw in (in_house_axis_max or {}).items():
                actual = Decimal(str(axes[axis_name]))
                maximum = Decimal(str(maximum_raw))
                if actual > maximum:
                    raise InvalidState(
                        f"Собственный парк ограничен {axis_name} ≤ {maximum}; "
                        f"передано {actual}. Верните маршрут технологу и "
                        "переведите операцию в подряд."
                    )
        else:
            if axes:
                raise InvalidState(f"Ставка «{rate_id}» скалярная: axes лишний")
            value = pack.rate_value(rate_id)
        subtotal = qmoney(quantity * value)
        return {
            "cost_line_id": cost_line_id,
            "rate_id": rate_id,
            "amount_kind": "INTERNAL_COST",
            "quantity": public_number(quantity),
            "unit": field,
            **(
                {
                    "driver_basis": (
                        str(driver_basis)
                        if unit in DRIVER_BASIS_UNITS
                        else "per_piece"
                    ),
                    "driver_value": public_number(driver_quantity),
                }
                if unit in {"per_pierce", "per_bend"} or unit in DRIVER_BASIS_UNITS
                else {}
            ),
            "rate_value": public_number(value),
            "rate_source": rate["source"],
            "subtotal_rub": public_number(subtotal),
            "note": note,
            **({"axes": {k: str(v) for k, v in axes.items()}} if isinstance(axes, dict) and axes else {}),
        }

    def blank_drivers_set(
        self,
        order_id: str,
        expected_revision: int,
        material_code: str,
        mass_kg: Any,
        mass_basis: Any,
        mass_note: str,
        items: list[dict[str, Any]],
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Агент 2: материал и стоимость шагов заготовки замороженного маршрута.

        Каждый in_house-шаг зоны supply оплачивается ровно один раз — та же
        биекция, что у норм времени: пропуск считается в ноль и невидим,
        дубль оплачивается дважды.
        """
        _require_role(actor_role, ("supply",), "Расчёт заготовки")
        validate_id(order_id, field="order_id")
        pack = self._resolve_pack()
        material = pack.material(material_code)
        if mass_basis not in {"per_piece", "total"}:
            raise InvalidState(
                "mass_basis обязателен: per_piece (масса одной детали) или "
                "total (масса всей партии)"
            )
        mass = _dec_positive(mass_kg, "mass_kg")
        note = _note(mass_note, "mass_kg")
        if not isinstance(items, list) or len(items) > 32:
            raise InvalidState("items — список до 32 строк заготовки")

        _, state_now = self.registry.get(order_id)
        _, variant = self._frozen_route(state_now)
        workflow_now = self._workflow(state_now)
        quantity = int(workflow_now["quantity"])
        total_mass = mass * Decimal(quantity) if mass_basis == "per_piece" else mass

        supply_steps = {
            step["seq"]: step
            for step in variant["steps"]
            if step["cost_owner"] == "supply" and step["execution_mode"] == "in_house"
        }
        required_rates: dict[int, list[str]] = {}
        for seq, step in supply_steps.items():
            park_entry = pack.park_entry(step["process_code"])
            main_rate_id = park_entry["rate_id"]
            if main_rate_id is None:
                raise InvalidRatePack(
                    f"Для «{step['process_code']}» в парке не назначена ставка "
                    f"(NEEDS_RATE): заведите её на экране «Данные» или "
                    f"переведите шаг в подряд"
                )
            required_rates[seq] = [
                main_rate_id, *park_entry.get("supplementary_rate_ids", [])
            ]

        given_keys: list[tuple[Any, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            seq = item.get("route_seq")
            rates = required_rates.get(seq, [])
            given_keys.append((seq, item.get("rate_id") or (rates[0] if rates else None)))
        duplicates = sorted({key for key in given_keys if given_keys.count(key) > 1})
        if duplicates:
            raise InvalidState(
                f"Строки маршрута {duplicates} посчитаны дважды — операция была "
                f"бы оплачена повторно"
            )
        required_keys = {
            (seq, rate_id)
            for seq, rate_ids in required_rates.items()
            for rate_id in rate_ids
        }
        missing = sorted(required_keys - set(given_keys))
        if missing:
            names = ", ".join(
                f"{seq} ({supply_steps[seq]['process_code']}, {rate_id})"
                for seq, rate_id in missing
            )
            raise InvalidState(
                f"Не посчитаны шаги заготовки замороженного маршрута: {names}. "
                f"Пропуск уходит в цену нулём и невидим на экране."
            )

        provisional = material["stock"] == "purchase"
        material_subtotal = qmoney(total_mass * material["rate_rub_per_kg"])
        lines: list[dict[str, Any]] = [
            {
                "cost_line_id": "blank:material",
                "rate_id": f"material:{material_code}",
                "amount_kind": "INTERNAL_COST",
                "quantity": public_number(total_mass),
                "unit": "kg",
                "rate_value": public_number(material["rate_rub_per_kg"]),
                "rate_source": material["rate_source"],
                "subtotal_rub": public_number(material_subtotal),
                "provisional": provisional,
                "note": note,
            }
        ]
        total = material_subtotal
        for item in items:
            allowed = {"route_seq", "rate_id", "axes", "note", "quantity_basis"} | set(QTY_FIELD_BY_UNIT.values())
            if not isinstance(item, dict) or not {"route_seq", "note"} <= set(item) or set(item) - allowed:
                raise InvalidState(
                    "Строка заготовки: route_seq, note, ровно одно поле "
                    "количества и axes для матричной ставки"
                )
            seq = item["route_seq"]
            step = supply_steps.get(seq)
            if step is None:
                raise InvalidState(
                    f"Шаг {seq} — не in_house-шаг зоны заготовки замороженного "
                    f"маршрута"
                )
            rate_ids = required_rates[seq]
            rate_id = item.get("rate_id") or rate_ids[0]
            if rate_id not in rate_ids:
                raise InvalidRatePack(
                    f"Ставка «{rate_id}» не назначена процессу «{step['process_code']}»; "
                    f"доступны: {', '.join(rate_ids)}"
                )
            line = self._line_from_rate(
                pack,
                cost_line_id=(
                    f"blank:step-{seq}"
                    if rate_id == rate_ids[0]
                    else f"blank:step-{seq}:{rate_id}"
                ),
                rate_id=rate_id,
                item=item,
                note=_note(item.get("note"), "blank item"),
                order_quantity=quantity,
                in_house_axis_max=pack.park_entry(step["process_code"]).get(
                    "in_house_axis_max"
                ),
            )
            line["route_seq"] = seq
            line["process_code"] = step["process_code"]
            total += Decimal(str(line["subtotal_rub"]))
            lines.append(line)

        result = {
            "pack_revision": pack.revision,
            "pack_fingerprint": pack.fingerprint,
            "material_code": material_code,
            "quantity": quantity,
            "mass_basis": mass_basis,
            "route_digest": variant["digest"],
            "lines": lines,
            "total_rub": public_number(qmoney(total)),
            "provisional": provisional,
        }
        result["digest"] = digest_json(result)

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(
                workflow, ("ROUTE_FROZEN", "DETAILED_COSTING"), "Расчёт заготовки"
            )
            _, variant_locked = self._frozen_route(state)
            if variant_locked["digest"] != variant["digest"]:
                raise Conflict("Маршрут изменился во время расчёта заготовки")
            # Отпечаток пака у ВАРИАНТА не сверяется намеренно: маршрут — это
            # состав работ, смена ставок его не отменяет; коды шагов против
            # ТЕКУЩЕГО парка уже разрешены выше (park_entry). Требование
            # same-pack здесь загоняло заказ в тупик после каждой правки цен
            # (находка Codex B6).
            costing = self._costing(state)
            existing = costing.get("time")
            if existing and existing.get("material_code") not in {None, material_code}:
                # Материал сменился — прежние нормы посчитаны по чужим режимам
                # резания и живыми оставаться не могут; но и запрет на смену
                # материала запирал адресный ADJUST снабжению (Codex M8).
                # Нормы уходят: нормировщик получит новую карточку событием.
                costing.pop("time", None)
                self._event(state, "time_dropped_material_changed")
            costing["blank"] = result
            costing["pack_fingerprint"] = pack.fingerprint
            workflow["status"] = (
                "COSTING_COMPLETE" if costing.get("time") else "DETAILED_COSTING"
            )
            state["status"] = workflow["status"]
            self._event(state, "blank_costed")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "result": result}

    def supply_confirm(
        self,
        order_id: str,
        expected_revision: int,
        rate_rub_per_kg: Any,
        confirmed_by: str,
        source_ref: str,
        rate_includes_vat: bool | None = None,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Снабжение подтвердило закупочную цену металла — снимаем звёздочку."""
        _require_role(actor_role, ("supply", "front"), "Подтверждение цены металла")
        pack = self._resolve_pack()
        rate = _dec_positive(rate_rub_per_kg, "rate_rub_per_kg")
        who = _note(confirmed_by, "confirmed_by")
        ref = _note(source_ref, "source_ref")
        if pack.schema_version >= 4:
            if type(rate_includes_vat) is not bool:
                raise InvalidState(
                    "Для schema v4 явно укажите rate_includes_vat: включает "
                    "ли подтверждённая цена металла НДС"
                )
            expected_vat_basis = bool(pack.pricing.get("rates_include_vat"))
            if rate_includes_vat is not expected_vat_basis:
                expected = "с НДС" if expected_vat_basis else "без НДС"
                raise InvalidState(
                    f"Пак ожидает закупочную ставку {expected}; приведите цену "
                    "к этой базе перед подтверждением"
                )

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(
                workflow,
                (
                    "DETAILED_COSTING",
                    "COSTING_COMPLETE",
                    "BOOK_ASSEMBLED",
                    "QA_MECHANICAL_PASS",
                    "QA_TECHNOLOGICAL_PASS",
                ),
                "Подтверждение цены металла",
            )
            blank = (state.get("costing") or {}).get("blank")
            if not blank:
                raise InvalidState("Стадия заготовки ещё не посчитана")
            if not blank.get("provisional"):
                raise Conflict("Цена металла уже не предварительная")
            receipts = (state.get("qa") or {}).get(
                str(workflow["calculation_revision"]), {}
            )
            if state.get("book") or receipts:
                # Книга и квитанции QA собраны по прежней цене металла — для
                # новой они больше не правда: ревизия растёт, прежнее уходит
                # в историю, гейты начинаются заново. Архив снимается ДО
                # правки заготовки — иначе история держала бы старую книгу
                # рядом с УЖЕ новой заготовкой (находка Codex M7).
                wf.bump_revision(
                    state,
                    to_status="COSTING_COMPLETE",
                    reason="supply_confirmed",
                    by=who,
                    at=utcnow(),
                    drop=("book",),
                )
                state["status"] = state["workflow"]["status"]
                blank = state["costing"]["blank"]
            total = Decimal("0")
            for line in blank["lines"]:
                if line["cost_line_id"] == "blank:material":
                    quantity = Decimal(str(line["quantity"]))
                    line["rate_value"] = public_number(rate)
                    line["rate_source"] = {
                        "kind": "supplier_quote",
                        "ref": f"{ref}; confirmed_by={who}",
                        "as_of": utcnow()[:10],
                    }
                    if pack.schema_version >= 4:
                        line["rate_includes_vat"] = rate_includes_vat
                    line["subtotal_rub"] = public_number(qmoney(quantity * rate))
                    line["provisional"] = False
                total += Decimal(str(line["subtotal_rub"]))
            blank["total_rub"] = public_number(qmoney(total))
            blank["provisional"] = False
            blank["digest"] = digest_json({k: v for k, v in blank.items() if k != "digest"})
            self._event(state, "supply_confirmed", who)

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "result": state["costing"]["blank"],
        }

    def time_norms_set(
        self,
        order_id: str,
        expected_revision: int,
        entries: list[dict[str, Any]],
        service_lines: list[dict[str, Any]] | None = None,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Агент 3: нормы времени станочных шагов + строки прочих работ.

        Материал берётся из посчитанной заготовки: режимы резания зависят от
        материала, и владеет им Агент 2. Поэтому в M1 заготовка считается
        раньше времени; параллельная предварительная оценка — фаза M2.
        """
        _require_role(actor_role, ("norm",), "Нормы времени")
        validate_id(order_id, field="order_id")
        pack = self._resolve_pack()
        service_lines = service_lines or []
        if not isinstance(entries, list) or len(entries) > 40:
            raise InvalidState("entries — список до 40 станочных шагов")
        if not isinstance(service_lines, list) or len(service_lines) > 40:
            raise InvalidState("service_lines — список до 40 строк")

        _, state_now = self.registry.get(order_id)
        _, variant = self._frozen_route(state_now)
        workflow_now = self._workflow(state_now)
        quantity = int(workflow_now["quantity"])
        blank = (state_now.get("costing") or {}).get("blank")
        if not blank:
            raise InvalidState(
                "Сначала расчёт заготовки (Агент 2): материал и его группа "
                "принадлежат ему, а режимы резания зависят от материала"
            )
        self._require_same_pack(blank.get("pack_fingerprint"), pack, "Заготовка")
        material = pack.material(blank["material_code"])

        norm_steps: dict[int, dict[str, Any]] = {}
        service_steps: dict[int, dict[str, Any]] = {}
        for step in variant["steps"]:
            if step["cost_owner"] != "norm" or step["execution_mode"] != "in_house":
                continue
            family = process_catalog.get(step["process_code"]).formula_family
            (norm_steps if family else service_steps)[step["seq"]] = step

        given = [entry.get("route_seq") for entry in entries if isinstance(entry, dict)] + [
            line.get("route_seq") for line in service_lines if isinstance(line, dict)
        ]
        duplicates = sorted({seq for seq in given if given.count(seq) > 1})
        if duplicates:
            raise InvalidState(
                f"Шаги маршрута {duplicates} посчитаны дважды — операция была "
                f"бы оплачена повторно"
            )
        missing = sorted((set(norm_steps) | set(service_steps)) - set(given))
        if missing:
            all_steps = {**norm_steps, **service_steps}
            names = ", ".join(f"{seq} ({all_steps[seq]['process_code']})" for seq in missing)
            raise InvalidState(
                f"Не посчитаны шаги замороженного маршрута: {names}. Пропуск "
                f"уходит в цену нулём и невидим на экране."
            )

        items: list[dict[str, Any]] = []
        total_min = Decimal("0")
        total_cost = Decimal("0")
        for entry in entries:
            expected = {"route_seq", "batch", "params"}
            if not isinstance(entry, dict) or set(entry) != expected:
                raise InvalidState("Станочный шаг: route_seq, batch, params")
            seq = entry["route_seq"]
            step = norm_steps.get(seq)
            if step is None:
                raise InvalidState(
                    f"Шаг {seq} не станочный шаг замороженного маршрута — "
                    f"прочие работы идут в service_lines"
                )
            code = step["process_code"]
            family = process_catalog.get(code).formula_family
            rate_id = pack.park_entry(code)["rate_id"]
            if rate_id is None:
                raise InvalidRatePack(
                    f"Для «{code}» в парке не назначена ставка (NEEDS_RATE)"
                )
            rate = pack.rate(rate_id)
            if rate["kind"] != "scalar" or rate["tariff_unit"] != "per_hour":
                raise InvalidRatePack(
                    f"Станочная ставка «{rate_id}» должна быть скалярной "
                    f"per_hour — нормы времени умножаются на станко-час"
                )
            main = machine_minutes(pack, family, material["group"], dict(entry["params"] or {}))
            piece = piece_minutes(pack, main, quantity=quantity, batch=entry["batch"])
            minutes_shown = piece.pop("_t_total_min_dec")
            cost = qmoney(minutes_shown / Decimal(60) * rate["value"])
            total_min += minutes_shown
            total_cost += cost
            items.append(
                {
                    "cost_line_id": f"time:step-{seq}",
                    "route_seq": seq,
                    "process_code": code,
                    "formula_family": family,
                    "amount_kind": "INTERNAL_COST",
                    **piece,
                    "rate_id": rate_id,
                    "rate_value": public_number(rate["value"]),
                    "rate_source": rate["source"],
                    "cost_rub": public_number(cost),
                }
            )
        lines: list[dict[str, Any]] = []
        for line_raw in service_lines:
            allowed = {"route_seq", "axes", "note", "quantity_basis"} | set(QTY_FIELD_BY_UNIT.values())
            if not isinstance(line_raw, dict) or not {"route_seq", "note"} <= set(line_raw) or set(line_raw) - allowed:
                raise InvalidState(
                    "Строка прочих работ: route_seq, note, ровно одно поле "
                    "количества и axes для матричной ставки"
                )
            seq = line_raw["route_seq"]
            step = service_steps.get(seq)
            if step is None:
                raise InvalidState(
                    f"Шаг {seq} — не строка прочих работ зоны нормировщика"
                )
            rate_id = pack.park_entry(step["process_code"])["rate_id"]
            if rate_id is None:
                raise InvalidRatePack(
                    f"Для «{step['process_code']}» в парке не назначена ставка "
                    f"(NEEDS_RATE): заведите её или переведите шаг в подряд"
                )
            line = self._line_from_rate(
                pack,
                cost_line_id=f"time:svc-{seq}",
                rate_id=rate_id,
                item=line_raw,
                note=_note(line_raw.get("note"), "service line"),
                order_quantity=quantity,
                in_house_axis_max=pack.park_entry(step["process_code"]).get(
                    "in_house_axis_max"
                ),
            )
            line["route_seq"] = seq
            line["process_code"] = step["process_code"]
            total_cost += Decimal(str(line["subtotal_rub"]))
            lines.append(line)

        # Цепочка времени привязана к маршруту и МАТЕРИАЛУ, а не к digest
        # заготовки: цена металла (supply_confirm) на нормы не влияет, и
        # заставлять нормировщика пересчитывать то же самое после каждого
        # подтверждения снабжения значило бы наказывать за правильный порядок.
        result = {
            "pack_revision": pack.revision,
            "pack_fingerprint": pack.fingerprint,
            "material_code": blank["material_code"],
            "quantity": quantity,
            "route_digest": variant["digest"],
            "items": items,
            "service_lines": lines,
            "total_min": public_number(total_min.quantize(Decimal("0.01"))),
            "total_rub": public_number(qmoney(total_cost)),
        }
        result["digest"] = digest_json(result)

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(
                workflow, ("DETAILED_COSTING", "COSTING_COMPLETE"), "Нормы времени"
            )
            _, variant_locked = self._frozen_route(state)
            if variant_locked["digest"] != variant["digest"]:
                raise Conflict("Маршрут изменился во время расчёта норм")
            costing = self._costing(state)
            blank_locked = costing.get("blank")
            if not blank_locked or blank_locked["material_code"] != blank["material_code"]:
                raise Conflict("Заготовка изменилась во время расчёта норм")
            costing["time"] = result
            workflow["status"] = "COSTING_COMPLETE"
            state["status"] = "COSTING_COMPLETE"
            self._event(state, "time_costed")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "result": result}

    # ── front/book_machine: книга ────────────────────────────────────────

    def book_assemble(
        self,
        order_id: str,
        expected_revision: int,
        extras: dict[str, Any] | None = None,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Граница писателя книги — front/book_machine; суммы считает движок."""
        _require_role(actor_role, ("front", "book_machine"), "Сборка книги")
        validate_id(order_id, field="order_id")
        pack = self._resolve_pack()
        extras = extras or {}
        if set(extras) - {"packaging", "logistics"}:
            raise InvalidState("Unknown extras")

        _, state_now = self.registry.get(order_id)
        workflow_now = self._workflow(state_now)
        wf.require_status(workflow_now, ("COSTING_COMPLETE",), "Сборка книги")
        _, variant = self._frozen_route(state_now)
        costing = state_now.get("costing") or {}
        blank = costing.get("blank")
        time_part = costing.get("time")
        if not blank or not time_part:
            raise InvalidState("Для книги нужны посчитанные заготовка и нормы времени")
        for part_name, part in (("Заготовка", blank), ("Нормы времени", time_part)):
            self._require_same_pack(part.get("pack_fingerprint"), pack, part_name)
        if time_part["material_code"] != blank["material_code"]:
            raise Conflict(
                "Нормы времени посчитаны по другому материалу, чем заготовка — "
                "гибрид двух материалов в одну цену не собирается"
            )

        blank_total = Decimal(str(blank["total_rub"]))
        time_total = Decimal(str(time_part["total_rub"]))
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
                    "cost_line_id": f"extra:{code}",
                    "rate_id": f"extra:{code}",
                    "amount_kind": "INTERNAL_COST",
                    "quantity": public_number(quantity),
                    "rate_value": public_number(extra["rate_rub"]),
                    "rate_source": extra["rate_source"],
                    "subtotal_rub": public_number(subtotal),
                }
            )

        quotes = self._checked_contractor_quotes(state_now)
        outsource_steps = {
            str(step["seq"]): step
            for step in variant["steps"]
            if step["execution_mode"] == "outsource"
        }
        alien_quotes = sorted(set(quotes) - set(outsource_steps), key=int)
        if alien_quotes:
            raise Conflict(
                "КП подрядчика не относится к outsource-шагу маршрута: "
                + ", ".join(alien_quotes)
            )
        contractor_lines: list[dict[str, Any]] = []
        route_unpriced: list[dict[str, Any]] = []
        contractor_total = Decimal("0")
        for key, step in sorted(outsource_steps.items(), key=lambda item: int(item[0])):
            quote = quotes.get(key)
            stale_reasons = (
                self._contractor_quote_stale_reasons(
                    quote,
                    workflow=workflow_now,
                    variant=variant,
                    step=step,
                    pack=pack,
                )
                if quote is not None
                else []
            )
            current = quote is not None and not stale_reasons
            if quote is not None:
                contractor_lines.append(
                    deepcopy(quote)
                    | {
                        "current": current,
                        **({"stale_reasons": stale_reasons} if stale_reasons else {}),
                    }
                )
            if current:
                contractor_total += Decimal(str(quote["normalized_amount_rub"]))
                continue
            route_unpriced.append(
                {
                    "seq": step["seq"],
                    "process_code": step["process_code"],
                    "execution_mode": step["execution_mode"],
                    "note": step["note"],
                    "reason": (
                        "КП подрядчика не записано"
                        if quote is None
                        else "КП подрядчика устарело: " + ", ".join(stale_reasons)
                    ),
                    **(
                        {"actual_process_name": step["actual_process_name"]}
                        if "actual_process_name" in step
                        else {}
                    ),
                    **({"quote_digest": quote["digest"]} if quote is not None else {}),
                }
            )
        contractor_total = qmoney(contractor_total)

        cost_total = qmoney(blank_total + time_total + contractor_total + extra_total)
        pricing = pack.pricing
        percent = pricing["margin_percent"]
        vat_factor = Decimal(1) + pricing["vat_rate_pct"] / HUNDRED
        rates_include_vat = bool(pricing.get("rates_include_vat"))
        material_total = next(
            (
                Decimal(str(line["subtotal_rub"]))
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
                net_price = pricing_cost_total * (Decimal(1) + percent / HUNDRED)
            else:
                net_price = pricing_cost_total / (Decimal(1) - percent / HUNDRED)
        else:
            non_material_cost = pricing_cost_total - pricing_material_total
            if pricing["margin_basis"] == "on_cost":
                non_material_price = non_material_cost * (Decimal(1) + percent / HUNDRED)
            else:
                non_material_price = non_material_cost / (Decimal(1) - percent / HUNDRED)
            net_price = (
                non_material_price
                + pricing_material_total * (Decimal(1) + material_markup / HUNDRED)
            )
        price = net_price
        if pricing["vat_included"]:
            price *= Decimal(1) + pricing["vat_rate_pct"] / HUNDRED
        price = _round_price(price, pricing["rounding"])
        effective_net = (
            price / (Decimal(1) + pricing["vat_rate_pct"] / HUNDRED)
            if pricing["vat_included"]
            else price
        )
        net_shown = qmoney(effective_net)
        vat_shown = qmoney(price - net_shown)

        component_markups: dict[str, Any] = {}
        if material_markup is not None:
            material_markup_amount = qmoney(
                material_total * material_markup / HUNDRED
            )
            component_markups["material"] = {
                "base_rub": public_number(qmoney(material_total)),
                "percent": public_number(material_markup),
                "amount_rub": public_number(material_markup_amount),
                "sell_rub": public_number(qmoney(material_total + material_markup_amount)),
            }

        manual_review_required = [
            {
                "seq": step["seq"],
                "process_code": step["process_code"],
                "reason": pack.park_entry(step["process_code"]).get(
                    "note", "Для операции требуется ручная коммерческая сверка"
                ),
            }
            for step in variant["steps"]
            if step["execution_mode"] == "in_house"
            and pack.park_entry(step["process_code"]).get("requires_manual_review")
        ]
        for item in time_part.get("items", []):
            sources = [item.get("norm_source") or {}, item.get("overheads_source") or {}]
            if any("TEMPLATE" in str(source.get("ref", "")).upper() for source in sources):
                manual_review_required.append(
                    {
                        "seq": item["route_seq"],
                        "process_code": item["process_code"],
                        "reason": "Норма времени или накладные параметры помечены TEMPLATE",
                    }
                )

        price_valid_until = date.fromisoformat(utcnow()[:10]) + timedelta(
            days=int(pricing["valid_days"])
        )
        current_quote_validity = [
            date.fromisoformat(str(line["valid_until"]))
            for line in contractor_lines
            if line.get("current") is True
        ]
        if current_quote_validity:
            price_valid_until = min(price_valid_until, *current_quote_validity)

        result = {
            "pack_revision": pack.revision,
            "pack_fingerprint": pack.fingerprint,
            "route_digest": variant["digest"],
            "blank_digest": blank["digest"],
            "time_digest": time_part["digest"],
            "contractor_quotes_digest": digest_json(quotes),
            "calculation_revision": workflow_now["calculation_revision"],
            "route_unpriced": route_unpriced,
            "provisional": bool(blank.get("provisional")),
            "manual_review_required": manual_review_required,
            **({"component_markups": component_markups} if component_markups else {}),
            "cost": {
                "amount_kind": "INTERNAL_COST",
                "blank_rub": public_number(qmoney(blank_total)),
                "machining_rub": public_number(qmoney(time_total)),
                "contractor_quotes": contractor_lines,
                "contractor_rub": public_number(contractor_total),
                "extras": extra_lines,
                "extras_rub": public_number(qmoney(extra_total)),
                "total_rub": public_number(cost_total),
                "rates_include_vat": rates_include_vat,
                **(
                    {"net_equivalent_rub": public_number(qmoney(pricing_cost_total))}
                    if rates_include_vat
                    else {}
                ),
            },
            "price": {
                "amount_kind": "CUSTOMER_PRICE",
                "net_total_rub": public_number(net_shown),
                "vat_amount_rub": public_number(vat_shown),
                "total_rub": public_number(price),
                "currency": "RUB",
                "vat_included": pricing["vat_included"],
                "vat_rate_pct": public_number(pricing["vat_rate_pct"]),
                "valid_until": price_valid_until.isoformat(),
                "is_final": (
                    not route_unpriced
                    and not bool(blank.get("provisional"))
                ),
                **(
                    {
                        "status_note": (
                            "Сумма не окончательная, добавится сумма из КП"
                        )
                    }
                    if route_unpriced
                    else {}
                ),
            },
            "margin": {
                "basis": pricing["margin_basis"],
                "declared_percent": public_number(pricing["margin_percent"]),
                **(
                    {"material_markup_percent": public_number(material_markup)}
                    if material_markup is not None
                    else {}
                ),
                "effective_percent": public_number(
                    (
                        (effective_net - pricing_cost_total)
                        / (
                            pricing_cost_total
                            if pricing["margin_basis"] == "on_cost"
                            else effective_net
                        )
                        * HUNDRED
                    ).quantize(Decimal("0.01"))
                    if cost_total and effective_net
                    else Decimal(0)
                ),
            },
        }
        result["digest"] = digest_json(result)

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(workflow, ("COSTING_COMPLETE",), "Сборка книги")
            if workflow["calculation_revision"] != result["calculation_revision"]:
                raise Conflict("Ревизия расчёта сменилась во время сборки книги")
            costing_locked = state.get("costing") or {}
            for name, digest in (("blank", result["blank_digest"]), ("time", result["time_digest"])):
                part = costing_locked.get(name)
                if not part or part["digest"] != digest:
                    raise Conflict("Стадия изменилась во время сборки книги")
                self._require_same_pack(part.get("pack_fingerprint"), pack, name)
            if digest_json(self._checked_contractor_quotes(state)) != result[
                "contractor_quotes_digest"
            ]:
                raise Conflict("КП подрядчика изменилось во время сборки книги")
            state["book"] = result
            workflow["status"] = "BOOK_ASSEMBLED"
            state["status"] = "BOOK_ASSEMBLED"
            self._event(state, "book_assembled")

        revision, _ = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {"order_id": order_id, "revision": revision, "book": result}

    def calc_revision_open(
        self,
        order_id: str,
        expected_revision: int,
        reason: str,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Открыть пересчёт: новые данные предприятия или решение человека.

        Наследник pipeline_repack: расчёт и книга уходят в историю, маршрут
        остаётся замороженным (данные сменились — состав работ нет), статус
        возвращается к подробному расчёту, calculation_revision растёт.
        """
        _require_role(actor_role, ("front",), "Открытие пересчёта")
        reason_text = _note(reason, "reason")

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.require_status(
                workflow,
                (
                    "DETAILED_COSTING",
                    "COSTING_COMPLETE",
                    "BOOK_ASSEMBLED",
                    "QA_MECHANICAL_PASS",
                    "QA_TECHNOLOGICAL_PASS",
                    "READY_FOR_LD",
                ),
                "Открытие пересчёта",
            )
            wf.bump_revision(
                state,
                to_status="ROUTE_FROZEN",
                reason=f"revision_open: {reason_text}",
                by=str(actor_role),
                at=utcnow(),
            )
            state["status"] = state["workflow"]["status"]
            self._event(state, "revision_opened", reason_text)

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "calculation_revision": state["workflow"]["calculation_revision"],
            "status": state["workflow"]["status"],
        }

    # ── QA ───────────────────────────────────────────────────────────────

    def qa_run_mechanical(
        self,
        order_id: str,
        expected_revision: int,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Механический гейт: вердикт вычисляет движок, не вызывающий.

        Роль вызывающего лишь запускает проверку — PASS поставить руками
        нельзя ни одной роли, включая front. Это несущее свойство: первый
        гейт защищает от арифметики, и доверять его человеку значило бы
        проверять калькулятор глазами.
        """
        _require_role(
            actor_role, ("front", "qa", "book_machine"), "Механическая проверка"
        )
        from .calc_qa import mechanical_checks

        pack = self._resolve_pack()

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.gate_requirement("mechanical", workflow)
            checks = mechanical_checks(state, pack)
            failed = [check for check in checks if not check["ok"]]
            receipts = wf.qa_receipts(state)
            if not failed:
                receipts["mechanical"] = {
                    "verdict": "PASS",
                    "computed_by": "engine",
                    "at": utcnow(),
                    "checks": checks,
                }
                wf.apply_gate_pass(state, "mechanical")
            else:
                owners = sorted({check["adjust_owner"] for check in failed})
                receipts["mechanical"] = {
                    "verdict": "ADJUST",
                    "computed_by": "engine",
                    "at": utcnow(),
                    "adjust_owner": owners[0],
                    "checks": checks,
                }
                wf.bump_revision(
                    state,
                    to_status="DETAILED_COSTING",
                    reason="qa_mechanical_adjust",
                    by="engine",
                    at=utcnow(),
                )
            state["status"] = state["workflow"]["status"]
            self._event(state, f"qa_mechanical:{receipts['mechanical']['verdict']}")

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        workflow = state["workflow"]
        receipt = (state.get("qa") or {}).get(
            str(workflow["calculation_revision"]), {}
        ).get("mechanical") or self._last_archived_mechanical(state)
        return {
            "order_id": order_id,
            "revision": revision,
            "status": workflow["status"],
            "verdict": receipt,
        }

    @staticmethod
    def _last_archived_mechanical(state: dict[str, Any]) -> dict[str, Any] | None:
        history = state.get("workflow_history") or []
        for entry in reversed(history):
            receipt = (entry.get("qa") or {}).get("mechanical")
            if receipt:
                return receipt
        return None

    def qa_verdict(
        self,
        order_id: str,
        expected_revision: int,
        gate: str,
        verdict: str,
        reasons: list[str],
        verdict_by: str,
        adjust_owner: str | None = None,
        *,
        actor_role: str | None,
    ) -> dict[str, Any]:
        """Технологический и коммерческий гейты. Механический — только движок."""
        _require_role(actor_role, ("qa",), "Вердикт QA")
        if gate == "mechanical":
            raise InvalidState(
                "Механический гейт вычисляется движком (qa_run_mechanical) — "
                "вердикт руками не ставится"
            )
        if gate not in wf.QA_GATES:
            raise InvalidState(
                f"Неизвестный гейт «{gate}»; есть: {', '.join(wf.QA_GATES)}"
            )
        if verdict not in wf.QA_VERDICTS:
            raise InvalidState(
                f"Вердикт — один из: {', '.join(wf.QA_VERDICTS)}. NO_EVIDENCE "
                f"не равен PASS."
            )
        who = _note(verdict_by, "verdict_by")
        if not isinstance(reasons, list) or len(reasons) > 16 or (
            verdict != "PASS" and not reasons
        ):
            raise InvalidState(
                "reasons — список причин (обязателен для всего, кроме PASS)"
            )
        reason_texts = [_note(reason, "reason") for reason in reasons]
        if verdict == "ADJUST":
            if adjust_owner not in wf.ADJUST_OWNERS:
                raise InvalidState(
                    f"ADJUST требует adjust_owner из: {', '.join(wf.ADJUST_OWNERS)}. "
                    f"Возврат технологу — отдельный формальный route_return."
                )
        elif adjust_owner is not None:
            raise InvalidState("adjust_owner передаётся только с вердиктом ADJUST")
        # Недоступный пак — отказ, а не пропуск сверки: PASS без возможности
        # проверить действующие данные — это утверждение вслепую (паттерн v6
        # stage_approve; дыра со сменой пака между гейтами — Codex B3).
        active_pack = self._resolve_pack()

        def mutate(state: dict[str, Any]) -> None:
            workflow = self._workflow(state)
            wf.gate_requirement(gate, workflow)
            book = state.get("book")
            if not book or book.get("calculation_revision") != workflow["calculation_revision"]:
                raise Conflict(
                    "Книга не относится к текущей ревизии расчёта — соберите её заново"
                )
            if verdict == "PASS":
                self._require_same_pack(
                    book.get("pack_fingerprint"), active_pack, "Книга"
                )
            if gate == "commercial" and verdict == "PASS" and book.get("provisional"):
                raise InvalidState(
                    "Книга собрана по предварительной цене металла (звёздочка). "
                    "Сначала supply_confirm, пересборка книги и заново QA."
                )
            if gate == "commercial" and verdict == "PASS":
                _, frozen = self._frozen_route(state)
                current_quotes = self._checked_contractor_quotes(state)
                quote_gaps = [
                    step["seq"]
                    for step in frozen.get("steps", [])
                    if step.get("execution_mode") == "outsource"
                    and (
                        str(step["seq"]) not in current_quotes
                        or self._contractor_quote_stale_reasons(
                            current_quotes[str(step["seq"])],
                            workflow=workflow,
                            variant=frozen,
                            step=step,
                            pack=active_pack,
                        )
                    )
                ]
                if quote_gaps:
                    raise InvalidState(
                        "КП подрядчика отсутствует, устарело или истекло для шагов: "
                        + ", ".join(str(seq) for seq in quote_gaps)
                    )
            if gate == "commercial" and verdict == "PASS" and book.get(
                "route_unpriced"
            ):
                raise InvalidState(
                    "В книге есть операции через КП без цены. Получите КП и "
                    "пересоберите книгу; коммерческий PASS с неполной суммой запрещён."
                )
            if (
                gate == "commercial"
                and verdict == "PASS"
                and book.get("manual_review_required")
                and not self._current_manual_review_receipt(
                    state, book, active_pack
                )
            ):
                raise InvalidState(
                    "Ручная коммерческая сверка текущей книги не закрыта "
                    "действующей QA-квитанцией."
                )
            receipts = wf.qa_receipts(state)
            receipt = {
                "verdict": verdict,
                "by": who,
                "actor_role": actor_role,
                "at": utcnow(),
                "reasons": reason_texts,
            }
            if verdict == "PASS":
                receipts[gate] = receipt
                wf.apply_gate_pass(state, gate)
            elif verdict == "ADJUST":
                receipt["adjust_owner"] = adjust_owner
                receipts[gate] = receipt
                drop = {
                    "supply": ("blank", "book"),
                    "norm": ("time", "book"),
                    "front": ("book",),
                }[str(adjust_owner)]
                wf.bump_revision(
                    state,
                    to_status=(
                        "DETAILED_COSTING" if adjust_owner in {"supply", "norm"} else "COSTING_COMPLETE"
                    ),
                    reason=f"qa_{gate}_adjust:{adjust_owner}",
                    by=who,
                    at=utcnow(),
                    drop=drop,
                )
            elif verdict == "BLOCK":
                receipt["blocked"] = True
                receipts[gate] = receipt
                workflow["status"] = "BLOCK_FOR_TECH_REVIEW"
            else:  # NO_EVIDENCE — зафиксировано, перехода нет
                receipts[gate] = receipt
            state["status"] = state["workflow"]["status"]
            self._event(state, f"qa_{gate}:{verdict}", who)

        revision, state = self.registry.mutate(
            order_id, mutate, expected_revision=expected_revision
        )
        return {
            "order_id": order_id,
            "revision": revision,
            "status": state["workflow"]["status"],
            "gate": gate,
            "verdict": verdict,
        }
