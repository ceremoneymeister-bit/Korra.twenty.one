"""MCP-поверхность расчётчика. Нарезка инструментов — ПО РОЛИ, в сервере.

Роль приходит из ``env.METAL_CALC_ROLE`` профиля (front / book_machine /
tech / supply / norm / qa). Env выбран не случайно: managed-политика
накладывается на конфиг профиля через deep-merge, в котором список
``tools.include`` заменяется целиком, а словарь ``env`` — сливается по
ключам. Профильное значение роли переживает политику, и граница ролей
перестаёт зависеть от порядка слияния конфигов (финдинг F10 от 28.08).

Оборона двухслойная: здесь роль решает, какие инструменты вообще
зарегистрированы (агент чужого не видит), а в service3 каждый write ещё раз
проверяет ``actor_role`` (агент чужое не запишет, даже если поверхность
протекла — например, через устаревший кэш схем).

Пустая роль — legacy-набор v6 (17 инструментов) для совместимости канарейки
и дозакрытия старых заказов; права на V3-write у неё нет ни одного.
"""
from __future__ import annotations

import base64
import os
from collections.abc import Callable
from typing import Any, Literal

from .errors import InvalidState, MetalCalcError, OrderScopeDenied
from .intake import SourceManifestEntry
from .order_scope import (
    AUTONOMOUS_STAGE_SCOPE,
    load_scope_secret,
    verify_order_scope,
    verify_workflow_scope_state,
)
from .service import MetalCalcService
from .util import SHA256_RE, sha256_bytes, validate_id

TOOL_NAMES = (
    "ingest_attachment",
    "analyze_drawing",
    "calculate_quote",
    "render_quote_xlsx",
    "order_upsert",
    "order_get",
    "order_list",
    "order_stats",
)

# Конвейер v6 (запись 40). Для V3-заказов эти инструменты отказывают:
# service2 проверяет отсутствие state["workflow"] в каждом мутаторе.
PIPELINE_TOOL_NAMES = (
    "rates_catalog",
    "pipeline_status",
    "blank_cost",
    "supply_confirm",
    "route_propose",
    "stage_approve",
    "pipeline_repack",
    "time_calc",
    "quote_build",
)

# Конвейер V3 — схема разделения V2 методолога (28.08.2026).
V3_TOOL_NAMES = (
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
)

#: Что видит каждая роль. Автономные профили не получают глобальный список
#: заказов, а каждое order-bound чтение/изменение требует подписанную сессию.
_V3_COMMON = (
    "workflow_status",
    "process_catalog",
    "rates_catalog",
    "order_get",
    "order_list",
)
_V3_AUTONOMOUS_COMMON = tuple(name for name in _V3_COMMON if name != "order_list")

ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    # Фронт — человек-оркестратор с полным контуром; ему же — legacy-набор,
    # чтобы дозакрыть старые заказы, и служебные инструменты v1.
    "front": _V3_COMMON
    + (
        "order_upsert",
        "order_stats",
        "ingest_attachment",
        "analyze_drawing",
        "calculate_quote",
        "render_quote_xlsx",
        "pipeline_status",
        "blank_cost",
        "supply_confirm",
        "route_propose",
        "stage_approve",
        "pipeline_repack",
        "time_calc",
        "quote_build",
        "input_freeze",
        "report_get",
        "render_book_xlsx",
        "book_assemble",
        "calc_revision_open",
        "qa_run_mechanical",
    ),
    "book_machine": (
        "workflow_status",
        "order_get",
        "book_assemble",
        "qa_run_mechanical",
    ),
    "tech": _V3_AUTONOMOUS_COMMON
    + ("bom_upsert", "route_variants_propose", "route_freeze"),
    "supply": _V3_AUTONOMOUS_COMMON + ("blank_drivers_set", "supply_confirm"),
    "norm": _V3_AUTONOMOUS_COMMON + ("time_norms_set",),
    "qa": _V3_COMMON + ("qa_run_mechanical",),
}


def read_role_from_env() -> str | None:
    """Роль профиля. Неизвестное значение — ошибка старта, не «как legacy».

    Опечатка в роли, тихо давшая полный legacy-набор, была бы обходом всей
    нарезки — профиль выглядел бы настроенным и имел чужие инструменты.
    """
    raw = (os.environ.get("METAL_CALC_ROLE") or "").strip().lower()
    if not raw:
        return None
    from .service3 import ROLES

    if raw not in ROLES:
        raise RuntimeError(
            f"METAL_CALC_ROLE={raw!r} не роль движка; допустимы: "
            f"{', '.join(ROLES)} или пусто (legacy)"
        )
    return raw


def read_profile_from_env(role: str | None) -> str | None:
    """Require the exact autonomous profile identity at MCP process start."""
    raw = (os.environ.get("METAL_CALC_PROFILE") or "").strip()
    expected = next(
        (profile for scoped_role, profile in AUTONOMOUS_STAGE_SCOPE.values()
         if scoped_role == role),
        None,
    )
    if expected is not None and raw != expected:
        raise RuntimeError(
            f"METAL_CALC_PROFILE must be {expected!r} for role {role!r}"
        )
    return raw or None


def _invoke(function: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return function(*args, **kwargs)
    except MetalCalcError as exc:
        return {"error": {"code": exc.code, "message": exc.public_message}}
    except Exception:
        return {"error": {"code": "InternalError", "message": "Operation failed"}}


def _stage_rendered_book_xlsx(
    service: MetalCalcService, rendered: dict[str, Any]
) -> dict[str, Any]:
    """Publish a verified client-only render into the gateway delivery jail.

    The workflow renderer remains a pure, byte-stable function. This MCP
    boundary strips base64 from the model-visible result and exposes only a
    validated MEDIA path. Replays adopt the same deterministic file only when
    its bytes still match the renderer digest.
    """
    try:
        payload = base64.b64decode(rendered["content_base64"], validate=True)
        expected_bytes = int(rendered["bytes"])
        expected_sha256 = str(rendered["sha256"])
        order_id = str(rendered["order_id"])
        file_name = str(rendered["file_name"])
        book_digest = str(rendered["book_digest"])
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidState("Некорректный результат рендера книги") from error
    validate_id(order_id, field="order_id")
    if (
        expected_bytes != len(payload)
        or not SHA256_RE.fullmatch(expected_sha256)
        or not SHA256_RE.fullmatch(book_digest)
        or sha256_bytes(payload) != expected_sha256
        or file_name != f"quote_{order_id}_{book_digest[:12]}.xlsx"
    ):
        raise InvalidState("Результат рендера книги не прошёл проверку целостности")

    relative = f"{order_id}/{file_name}"
    service.delivery.ensure_dir(order_id)
    existed = service.delivery.exists(relative)
    idempotent = False
    if existed:
        current = service.delivery.read_bytes(relative, limit=expected_bytes)
        idempotent = len(current) == expected_bytes and sha256_bytes(current) == expected_sha256
        # Повторный рендер — это явная выдача новой TTL-квитанции. Даже при
        # тех же байтах atomic_replace освежает mtime; если FINAL успел стать
        # PRELIMINARY при том же book digest, старый файл заменяется текущим.
        service.delivery.atomic_replace(relative, payload)
    else:
        service.delivery.atomic_write(relative, payload)

    public = service.settings.delivery_public_root / order_id / file_name
    return {
        key: value
        for key, value in rendered.items()
        if key != "content_base64"
    } | {
        "media_path": str(public),
        "idempotent": idempotent,
        "restaged": existed,
        "delivery_staged": True,
    }


def build_mcp(
    service: MetalCalcService,
    pipeline: Any | None = None,
    workflow: Any | None = None,
    role: str | None = None,
    profile: str | None = None,
):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("mcp package is required") from exc

    if role is not None:
        if role not in ROLE_TOOLS:
            raise RuntimeError(f"Unknown role: {role}")
        if workflow is None:
            raise RuntimeError("Role requires the V3 workflow service")
        allowed = set(ROLE_TOOLS[role])
    else:
        allowed = set(TOOL_NAMES) | (set(PIPELINE_TOOL_NAMES) if pipeline is not None else set())

    def enabled(name: str) -> bool:
        if name in PIPELINE_TOOL_NAMES and pipeline is None:
            return False
        if name in V3_TOOL_NAMES and workflow is None:
            return False
        return name in allowed

    mcp = FastMCP("metal_calc", instructions="Deterministic drawing costing tools")

    autonomous_stage = next(
        (stage for stage, scope in AUTONOMOUS_STAGE_SCOPE.items() if scope[0] == role),
        None,
    )
    autonomous_profile = (
        AUTONOMOUS_STAGE_SCOPE[autonomous_stage][1]
        if autonomous_stage is not None
        else None
    )
    runtime_profile = profile or autonomous_profile
    if autonomous_profile is not None and runtime_profile != autonomous_profile:
        raise RuntimeError("Autonomous role/profile mismatch")

    def invoke_order_scoped(
        order_id: str,
        hermes_session_context: str | None,
        function: Callable[..., dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        def authorized_call() -> dict[str, Any]:
            if autonomous_stage is not None:
                try:
                    secret = load_scope_secret()
                except RuntimeError as exc:
                    raise OrderScopeDenied(
                        "Автономный доступ к заказу не настроен"
                    ) from exc
                claims = verify_order_scope(
                    hermes_session_context or "",
                    secret,
                    order_id=order_id,
                    expected_role=str(role),
                    expected_profile=str(runtime_profile),
                )
                # The order hash is authenticated before this lookup, so a
                # token for A cannot probe whether B exists. The latest exact
                # trigger event invalidates replayed sessions after a re-run.
                _, state = workflow.registry.get(order_id)
                verify_workflow_scope_state(claims, state, order_id)
            return function(*args, **kwargs)

        return _invoke(authorized_call)

    # ── v1: заказы и вложения ────────────────────────────────────────────

    if enabled("ingest_attachment"):

        @mcp.tool()
        def ingest_attachment(order_id: str, cache_name: str) -> dict[str, Any]:
            """Import one native Telegram document-cache attachment into an order."""
            return _invoke(service.ingest_attachment, order_id, cache_name)

    if enabled("analyze_drawing"):

        @mcp.tool()
        def analyze_drawing(
            order_id: str,
            source_file_ids: list[str],
            rates_revision: str,
            material_code: str | None = None,
            units_hint: str | None = None,
        ) -> dict[str, Any]:
            """Run packaged cadkit and persist deterministic geometry and mass."""
            return _invoke(
                service.analyze_drawing,
                order_id,
                source_file_ids,
                units_hint,
                rates_revision,
                material_code,
            )

    if enabled("calculate_quote"):

        @mcp.tool()
        def calculate_quote(
            order_id: str,
            geometry_revision: str,
            rates_revision: str,
            material_code: str,
            approved_manual_fact_ids: list[str] | None = None,
        ) -> dict[str, Any]:
            """Calculate cost, price and margin using a read-only rate pack."""
            return _invoke(
                service.calculate_quote,
                order_id,
                geometry_revision,
                rates_revision,
                material_code,
                approved_manual_fact_ids or [],
            )

    if enabled("render_quote_xlsx"):

        @mcp.tool()
        def render_quote_xlsx(
            order_id: str, calculation_sha256: str, template: str = "default"
        ) -> dict[str, Any]:
            """Render and stage an XLSX from an exact saved calculation digest."""
            return _invoke(service.render_quote_xlsx, order_id, calculation_sha256, template)

    if enabled("order_upsert"):

        @mcp.tool()
        def order_upsert(
            order_id: str, expected_revision: int, patch: dict[str, Any]
        ) -> dict[str, Any]:
            """Create or update allowlisted order fields with optimistic locking."""
            return _invoke(service.order_upsert, order_id, expected_revision, patch)

    if enabled("order_get"):

        @mcp.tool()
        def order_get(
            order_id: str, hermes_session_context: str | None = None
        ) -> dict[str, Any]:
            """Read one assembled typed order."""
            return invoke_order_scoped(
                order_id,
                hermes_session_context,
                service.order_get,
                order_id,
            )

    if enabled("order_list"):

        @mcp.tool()
        def order_list(
            statuses: list[str] | None = None,
            limit: int = 20,
            offset: int = 0,
            sort: str = "updated_desc",
        ) -> dict[str, Any]:
            """List orders using typed filters and allowlisted sorting."""
            return _invoke(service.order_list, statuses, limit, offset, sort)

    if enabled("order_stats"):

        @mcp.tool()
        def order_stats(
            period_from: str,
            period_to: str,
            group_by: str = "status",
            statuses: list[str] | None = None,
        ) -> dict[str, Any]:
            """Aggregate order counts and financial totals without raw SQL."""
            return _invoke(service.order_stats, period_from, period_to, group_by, statuses)

    # ── v6: legacy-конвейер (дозакрытие старых заказов) ──────────────────

    if enabled("rates_catalog"):

        @mcp.tool()
        def rates_catalog() -> dict[str, Any]:
            """What company data is published: codes to use in the other tools.

            Call this BEFORE naming a material or an operation. The codes are
            defined by the methodologist, not by convention. `norms_missing`
            lists machine/material-group pairs that have no time norm.
            """
            return _invoke(pipeline.rates_catalog)

    if enabled("pipeline_status"):

        @mcp.tool()
        def pipeline_status(order_id: str) -> dict[str, Any]:
            """Read LEGACY (v6) pipeline stage statuses for one order."""
            return _invoke(pipeline.pipeline_status, order_id)

    if enabled("blank_cost"):

        @mcp.tool()
        def blank_cost(
            order_id: str,
            expected_revision: int,
            material_code: str,
            quantity: int,
            mass_kg: float | str,
            mass_basis: str,
            mass_note: str,
            items: list[dict[str, Any]],
        ) -> dict[str, Any]:
            """LEGACY v6 stage 1. Refused for V2-scheme orders (workflow_status)."""
            return _invoke(
                pipeline.blank_cost,
                order_id,
                expected_revision,
                material_code,
                quantity,
                mass_kg,
                mass_basis,
                mass_note,
                items,
            )

    if enabled("supply_confirm"):

        @mcp.tool()
        def supply_confirm(
            order_id: str,
            expected_revision: int,
            rate_rub_per_kg: float | str,
            confirmed_by: str,
            source_ref: str,
            rate_includes_vat: bool | None = None,
            hermes_session_context: str | None = None,
        ) -> dict[str, Any]:
            """Replace the provisional metal price with the confirmed supply price.

            Works on both pipelines: V2-scheme orders update the blank costing
            (QA receipts of the current revision are archived — the gates run
            again on the new numbers), legacy orders update the blank stage.
            """

            def dispatch() -> dict[str, Any]:
                if workflow is not None:
                    _, state = workflow.registry.get(order_id)
                    if "workflow" in state:
                        return workflow.supply_confirm(
                            order_id,
                            expected_revision,
                            rate_rub_per_kg,
                            confirmed_by,
                            source_ref,
                            rate_includes_vat,
                            actor_role=role,
                        )
                if pipeline is None:
                    raise MetalCalcError("Legacy pipeline is not available")
                return pipeline.supply_confirm(
                    order_id, expected_revision, rate_rub_per_kg, confirmed_by, source_ref
                )

            return invoke_order_scoped(
                order_id, hermes_session_context, dispatch
            )

    if enabled("route_propose"):

        @mcp.tool()
        def route_propose(
            order_id: str,
            expected_revision: int,
            steps: list[dict[str, Any]],
        ) -> dict[str, Any]:
            """LEGACY v6 stage 2. Refused for V2-scheme orders."""
            return _invoke(pipeline.route_propose, order_id, expected_revision, steps)

    if enabled("stage_approve"):

        @mcp.tool()
        def stage_approve(
            order_id: str, expected_revision: int, stage_name: str, approved_by: str
        ) -> dict[str, Any]:
            """LEGACY v6 human approval. Refused for V2-scheme orders."""
            return _invoke(
                pipeline.stage_approve, order_id, expected_revision, stage_name, approved_by
            )

    if enabled("pipeline_repack"):

        @mcp.tool()
        def pipeline_repack(
            order_id: str,
            expected_revision: int,
            stage_name: str,
            requested_by: str,
        ) -> dict[str, Any]:
            """LEGACY v6 reopen-for-recalculation. V2 orders: calc_revision_open."""
            return _invoke(
                pipeline.pipeline_repack,
                order_id,
                expected_revision,
                stage_name,
                requested_by,
            )

    if enabled("time_calc"):

        @mcp.tool()
        def time_calc(
            order_id: str,
            expected_revision: int,
            material_code: str,
            entries: list[dict[str, Any]],
        ) -> dict[str, Any]:
            """LEGACY v6 stage 3. Refused for V2-scheme orders."""
            return _invoke(
                pipeline.time_calc,
                order_id,
                expected_revision,
                material_code,
                entries,
            )

    if enabled("quote_build"):

        @mcp.tool()
        def quote_build(
            order_id: str,
            expected_revision: int,
            extras: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """LEGACY v6 quote assembly. V2-scheme orders use book_assemble."""
            return _invoke(pipeline.quote_build, order_id, expected_revision, extras)

    # ── V3: схема разделения V2 ──────────────────────────────────────────

    if enabled("workflow_status"):

        @mcp.tool()
        def workflow_status(
            order_id: str, hermes_session_context: str | None = None
        ) -> dict[str, Any]:
            """Read status, variants, costing, contractor quotes, and QA.

            The status names are the scheme's own terms (INPUT_FROZEN …
            READY_FOR_LD) — use them verbatim when talking to the human,
            with the Russian title from `status_title`. Contractor quotes are
            read-only here: only an authenticated panel operator can record one.
            """
            return invoke_order_scoped(
                order_id,
                hermes_session_context,
                workflow.workflow_status,
                order_id,
            )

    if enabled("report_get"):

        @mcp.tool()
        def report_get(
            order_id: str, mode: Literal["internal", "client"]
        ) -> dict[str, Any]:
            """Front: read the deterministic V9 report without changing state.

            mode=internal returns frozen input, workflow, exact current book
            including INTERNAL_COST, current-revision QA receipts and blockers.
            mode=client returns a strict public allowlist with CUSTOMER_PRICE;
            it contains no internal cost or rate/source metadata and always
            says FINAL or PRELIMINARY. FINAL requires three existing QA PASS
            receipts for the current calculation revision.
            """
            return _invoke(workflow.report_get, order_id, mode, actor_role=role)

    if enabled("render_book_xlsx"):

        @mcp.tool()
        def render_book_xlsx(
            order_id: str, expected_book_digest: str
        ) -> dict[str, Any]:
            """Front: render and stage the exact current V9 client XLSX.

            Pass book.digest from report_get(mode=internal). A mismatched,
            corrupt, stale-revision or stale-rate book is rejected. Output is
            a client-only byte-stable XLSX in the delivery jail plus MEDIA
            path; unchanged retries adopt the same verified file. The tool
            does not send it by itself.
            """
            return _invoke(
                lambda: _stage_rendered_book_xlsx(
                    service,
                    workflow.render_book_xlsx(
                        order_id,
                        expected_book_digest,
                        actor_role=role,
                    ),
                )
            )

    if enabled("process_catalog"):

        @mcp.tool()
        def process_catalog() -> dict[str, Any]:
            """Global process catalog AND this company's park.

            The catalog says what exists (stable process_code identities);
            the park says what THIS company performs, with its rates. A code
            outside the park is not an error — it is outsourcing
            (execution_mode=outsource), never a silent guess.
            """
            return _invoke(workflow.process_catalog_tool)

    if enabled("input_freeze"):

        @mcp.tool()
        def input_freeze(
            order_id: str,
            expected_revision: int,
            quantity: int,
            kd_revision: str,
            source_manifest: list[SourceManifestEntry],
            note: str,
        ) -> dict[str, Any]:
            """Front: freeze the order input — quantity, KD revision, sources.

            `quantity` is stated ONCE for the whole order and enforced across
            every stage. Each source must exactly match an attachment already
            ingested into this order by source_file_id, name and sha256. The
            manifest must cover all current attachments without duplicates.
            """
            return _invoke(
                workflow.input_freeze,
                order_id,
                expected_revision,
                quantity,
                kd_revision,
                source_manifest,
                note,
                actor_role=role,
            )

    if enabled("bom_upsert"):

        @mcp.tool()
        def bom_upsert(
            order_id: str,
            expected_revision: int,
            nodes: list[dict[str, Any]],
            hermes_session_context: str | None = None,
        ) -> dict[str, Any]:
            """Tech: declare the BOM — one manufactured part + purchased items.

            Node: {bom_node_id, parent_bom_id (null for the part),
            node_type: MANUFACTURED_PART|PURCHASED_ITEM, make_or_buy:
            MAKE|BUY, quantity, note}.
            """
            return invoke_order_scoped(
                order_id,
                hermes_session_context,
                workflow.bom_upsert,
                order_id,
                expected_revision,
                nodes,
                actor_role=role,
            )

    if enabled("route_variants_propose"):

        @mcp.tool()
        def route_variants_propose(
            order_id: str,
            expected_revision: int,
            steps: list[dict[str, Any]],
            hermes_session_context: str | None = None,
        ) -> dict[str, Any]:
            """Tech: propose a route variant. No money in it by construction.

            Step: {seq (contiguous from 1), process_code (from the catalog —
            call process_catalog first), execution_mode: in_house|outsource,
            note}. in_house requires the code in the company park.
            OTHER.SPECIFIED is outsource-only and needs actual_process_name.
            """
            return invoke_order_scoped(
                order_id,
                hermes_session_context,
                workflow.route_variants_propose,
                order_id,
                expected_revision,
                steps,
                actor_role=role,
            )

    if enabled("route_freeze"):

        @mcp.tool()
        def route_freeze(
            order_id: str,
            expected_revision: int,
            variant_id: str,
            hermes_session_context: str | None = None,
        ) -> dict[str, Any]:
            """Tech: freeze the chosen variant. MANDATORY before any costing.

            Detailed costing works only on the frozen variant. There is ONE
            formal route return after freezing; the second stops the order
            for a human technological review.
            """
            return invoke_order_scoped(
                order_id,
                hermes_session_context,
                workflow.route_freeze,
                order_id,
                expected_revision,
                variant_id,
                actor_role=role,
            )

    if enabled("blank_drivers_set"):

        @mcp.tool()
        def blank_drivers_set(
            order_id: str,
            expected_revision: int,
            material_code: str,
            mass_kg: float | str,
            mass_basis: str,
            mass_note: str,
            items: list[dict[str, Any]],
            hermes_session_context: str | None = None,
        ) -> dict[str, Any]:
            """Supply (Agent 2): material and blank-zone steps of the frozen route.

            You CHOOSE the approved rate (via the park); the engine applies
            its value. Every in_house blank-zone step must be priced exactly
            once per assigned rate: item {route_seq, optional rate_id, note,
            exactly one quantity field the tariff unit requires
            (cuts / pierces_per_piece / bends_per_piece / length_m / hours / …), axes for
            matrix rates}. Use process_catalog `park[].rates` to see every
            required rate, quantity field and axis. Continuous physical drivers
            additionally require quantity_basis=per_piece|order_total; the engine
            applies order quantity only for per_piece. `mass_basis`: per_piece or total. Purchased
            metal stays provisional until supply_confirm.
            """
            return invoke_order_scoped(
                order_id,
                hermes_session_context,
                workflow.blank_drivers_set,
                order_id,
                expected_revision,
                material_code,
                mass_kg,
                mass_basis,
                mass_note,
                items,
                actor_role=role,
            )

    if enabled("time_norms_set"):

        @mcp.tool()
        def time_norms_set(
            order_id: str,
            expected_revision: int,
            entries: list[dict[str, Any]],
            service_lines: list[dict[str, Any]] | None = None,
            hermes_session_context: str | None = None,
        ) -> dict[str, Any]:
            """Norm (Agent 3): time norms for machining steps of the frozen route.

            entries — machining steps with a formula family: {route_seq,
            batch, params}. Params for turning: diameter_mm, length_mm,
            stock_mm; milling: length_mm, stock_mm; edm/grinding:
            surface_cm2. Units are FIXED: lengths in mm, surfaces in cm2.
            service_lines — non-formula steps of your zone (fitter, welding,
            rolling …): {route_seq, note, one quantity field, axes for
            matrix rates}; continuous physical drivers also require
            quantity_basis=per_piece|order_total. You never compute money — the engine prices your
            minutes with the approved machine-hour rate.
            """
            return invoke_order_scoped(
                order_id,
                hermes_session_context,
                workflow.time_norms_set,
                order_id,
                expected_revision,
                entries,
                service_lines,
                actor_role=role,
            )

    if enabled("book_assemble"):

        @mcp.tool()
        def book_assemble(
            order_id: str,
            expected_revision: int,
            extras: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Front/book_machine: assemble the book inside its writer boundary.

            Requires COSTING_COMPLETE. Fails when any stage was priced under
            a different company-data revision (PackChanged). The result keeps
            INTERNAL_COST and CUSTOMER_PRICE strictly apart and lists
            route_unpriced (outsourced steps priced nowhere) — always show
            them to the human next to the total.
            """
            return _invoke(
                workflow.book_assemble, order_id, expected_revision, extras, actor_role=role
            )

    if enabled("calc_revision_open"):

        @mcp.tool()
        def calc_revision_open(
            order_id: str, expected_revision: int, reason: str
        ) -> dict[str, Any]:
            """Front: reopen costing under new company data (rates changed).

            The route stays frozen — data changed, the work scope did not.
            Costing, book and QA receipts go to history; calculation_revision
            grows. Call it when the human explicitly asks to recalculate.
            """
            return _invoke(
                workflow.calc_revision_open,
                order_id,
                expected_revision,
                reason,
                actor_role=role,
            )

    if enabled("qa_run_mechanical"):

        @mcp.tool()
        def qa_run_mechanical(order_id: str, expected_revision: int) -> dict[str, Any]:
            """Run the mechanical QA gate. The ENGINE computes the verdict.

            Nobody — no role, no human — can set this PASS by hand: the gate
            recomputes sums, digests, rate resolution and the route/payment
            bijection. A failed check yields ADJUST with the owner and a new
            calculation_revision.
            """
            return _invoke(
                workflow.qa_run_mechanical, order_id, expected_revision, actor_role=role
            )

    return mcp


def main() -> None:
    import argparse
    from pathlib import Path
    from .config import Settings
    from .packs2 import PipelinePackStore
    from .securefs import SecureRoot
    from .service2 import PipelineService
    from .service3 import WorkflowService

    parser = argparse.ArgumentParser(prog="metal-calc-mcp")
    parser.add_argument("--intake-only", action="store_true")
    parser.add_argument("--analysis-only", action="store_true")
    parser.add_argument("--session-db", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    role = read_role_from_env()
    profile = read_profile_from_env(role)
    if args.analysis_only:
        if args.intake_only or args.session_db is not None or role != "front" or os.environ.get("METAL_CALC_PROFILE") != "intake-analysis":
            raise RuntimeError("--analysis-only requires the internal analysis profile")
        from .intake_analysis import AnalysisStore
        from .intake_handoffs import IntakeHandoffs
        from .analysis_mcp import build_mcp as build_analysis_mcp
        build_analysis_mcp(AnalysisStore(IntakeHandoffs(settings.orders_root))).run(transport="stdio")
        return
    if args.intake_only:
        if role != "front":
            raise RuntimeError("--intake-only requires the front role")
        from .intake_handoffs import IntakeHandoffs
        from .intake_mcp import build_mcp as build_intake_mcp
        build_intake_mcp(IntakeHandoffs(settings.orders_root, session_db=args.session_db)).run(transport="stdio")
        return
    if args.session_db is not None:
        raise RuntimeError("--session-db requires --intake-only")
    service = MetalCalcService(settings)
    packs = PipelinePackStore(SecureRoot(settings.rates_root, writable=False))
    pipeline = PipelineService(service.registry, packs)
    workflow = WorkflowService(service.registry, packs)
    try:
        build_mcp(
            service, pipeline, workflow, role=role, profile=profile
        ).run(transport="stdio")
    finally:
        service.close()


if __name__ == "__main__":
    main()
