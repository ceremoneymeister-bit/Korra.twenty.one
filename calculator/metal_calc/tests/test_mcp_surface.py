from __future__ import annotations

import asyncio

import pytest

from metal_calc.mcp_server import (
    PIPELINE_TOOL_NAMES,
    ROLE_TOOLS,
    TOOL_NAMES,
    V3_TOOL_NAMES,
    _stage_rendered_book_xlsx,
    build_mcp,
    read_role_from_env,
)
from metal_calc.packs2 import PipelinePackStore
from metal_calc.securefs import SecureRoot
from metal_calc.service2 import PipelineService
from metal_calc.service3 import WorkflowService


def _names(mcp) -> set[str]:
    return {tool.name for tool in asyncio.run(mcp.list_tools())}


def _pipeline(service) -> PipelineService:
    return PipelineService(
        service.registry,
        PipelinePackStore(SecureRoot(service.settings.rates_root, writable=False)),
    )


def _workflow(service) -> WorkflowService:
    return WorkflowService(
        service.registry,
        PipelinePackStore(SecureRoot(service.settings.rates_root, writable=False)),
    )


def test_exact_eight_mcp_tools(service) -> None:
    names = _names(build_mcp(service))
    assert names == set(TOOL_NAMES)
    assert len(names) == 8


def test_pipeline_adds_exact_nine_tools(service) -> None:
    names = _names(build_mcp(service, _pipeline(service)))
    assert names == set(TOOL_NAMES) | set(PIPELINE_TOOL_NAMES)
    assert len(names) == 17


def test_legacy_surface_gets_no_v3_tools(service) -> None:
    """Пустая роль = ровно v6-набор: канарейка без ролей не меняется."""
    names = _names(build_mcp(service, _pipeline(service), _workflow(service), role=None))
    assert names == set(TOOL_NAMES) | set(PIPELINE_TOOL_NAMES)
    assert not names & (set(V3_TOOL_NAMES) - {"supply_confirm"})


@pytest.mark.parametrize("role", sorted(ROLE_TOOLS))
def test_role_surface_is_exact(service, role: str) -> None:
    names = _names(build_mcp(service, _pipeline(service), _workflow(service), role=role))
    assert names == set(ROLE_TOOLS[role])


def test_input_freeze_exposes_typed_attachment_identity(service) -> None:
    tools = asyncio.run(
        build_mcp(service, _pipeline(service), _workflow(service), role="front").list_tools()
    )
    schema = next(tool for tool in tools if tool.name == "input_freeze").inputSchema
    manifest = schema["properties"]["source_manifest"]
    reference = manifest["items"]["$ref"].split("/")[-1]
    entry = schema["$defs"][reference]
    assert set(entry["required"]) == {"source_file_id", "name", "sha256"}
    assert set(entry["properties"]) == {"source_file_id", "name", "sha256"}
    assert all(field["type"] == "string" for field in entry["properties"].values())


def test_v9_report_and_export_are_front_only_typed_tools(service) -> None:
    front_mcp = build_mcp(service, _pipeline(service), _workflow(service), role="front")
    tools = {tool.name: tool for tool in asyncio.run(front_mcp.list_tools())}
    assert {"report_get", "render_book_xlsx"}.issubset(tools)
    assert "qa_verdict" not in tools
    mode = tools["report_get"].inputSchema["properties"]["mode"]
    assert set(mode["enum"]) == {"internal", "client"}
    digest = tools["render_book_xlsx"].inputSchema["properties"]["expected_book_digest"]
    assert digest["type"] == "string"
    for role in ("tech", "supply", "norm", "qa"):
        names = _names(build_mcp(service, _pipeline(service), _workflow(service), role=role))
        assert not names & {"report_get", "render_book_xlsx"}


def test_v9_mcp_export_stages_verified_bytes_without_base64(service) -> None:
    payload = b"synthetic-xlsx"
    import base64
    import hashlib

    rendered = {
        "order_id": "order-test-1",
        "book_digest": "a" * 64,
        "file_name": "quote_order-test-1_aaaaaaaaaaaa.xlsx",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "rows": 1,
        "content_base64": base64.b64encode(payload).decode("ascii"),
    }
    first = _stage_rendered_book_xlsx(service, rendered)
    second = _stage_rendered_book_xlsx(service, rendered)
    assert "content_base64" not in first
    assert first["media_path"] == "/opt/data/delivery/order-test-1/quote_order-test-1_aaaaaaaaaaaa.xlsx"
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert first["restaged"] is False
    assert second["restaged"] is True
    assert (service.settings.delivery_root / "order-test-1" / rendered["file_name"]).read_bytes() == payload

    (service.settings.delivery_root / "order-test-1" / rendered["file_name"]).write_bytes(b"corrupt")
    replaced = _stage_rendered_book_xlsx(service, rendered)
    assert replaced["idempotent"] is False
    assert replaced["restaged"] is True
    assert (service.settings.delivery_root / "order-test-1" / rendered["file_name"]).read_bytes() == payload


def test_book_machine_surface_is_minimal_and_has_no_human_verdict(service) -> None:
    names = _names(
        build_mcp(service, _pipeline(service), _workflow(service), role="book_machine")
    )
    assert names == {
        "workflow_status",
        "order_get",
        "book_assemble",
        "qa_run_mechanical",
    }
    assert not names & {
        "qa_verdict",
        "route_return",
        "report_get",
        "render_book_xlsx",
        "order_upsert",
        "stage_approve",
    }


def test_front_surface_has_no_human_qa_write(service) -> None:
    names = _names(
        build_mcp(service, _pipeline(service), _workflow(service), role="front")
    )
    assert not names & {"qa_verdict", "route_return", "manual_review_complete"}


def test_human_only_writes_are_absent_from_every_model_surface(service) -> None:
    human_only = {
        "contractor_quote_set",
        "qa_verdict",
        "route_return",
        "manual_review_complete",
    }
    assert not set(V3_TOOL_NAMES) & human_only
    assert "panel" not in ROLE_TOOLS
    for role in ROLE_TOOLS:
        names = _names(
            build_mcp(service, _pipeline(service), _workflow(service), role=role)
        )
        assert not names & human_only


def test_agents_never_see_alien_writes(service) -> None:
    """F10: заготовка не видит qa/маршрут, маршрутчик — деньги, нормировщик — книгу."""
    supply = _names(build_mcp(service, _pipeline(service), _workflow(service), role="supply"))
    tech = _names(build_mcp(service, _pipeline(service), _workflow(service), role="tech"))
    norm = _names(build_mcp(service, _pipeline(service), _workflow(service), role="norm"))
    assert not supply & {"route_variants_propose", "route_freeze", "qa_verdict", "book_assemble"}
    assert not tech & {"blank_drivers_set", "time_norms_set", "book_assemble", "qa_verdict"}
    assert not norm & {"book_assemble", "qa_verdict", "route_freeze", "supply_confirm"}
    # Механический гейт недоступен рабочим ролям даже на запуск.
    assert "qa_run_mechanical" not in supply | tech | norm


@pytest.mark.parametrize("role", ["tech", "supply", "norm"])
def test_autonomous_surface_has_no_order_list_and_all_order_tools_take_scope(
    service, role: str
) -> None:
    tools = {
        tool.name: tool
        for tool in asyncio.run(
            build_mcp(service, _pipeline(service), _workflow(service), role=role).list_tools()
        )
    }
    assert "order_list" not in tools
    order_bound = set(tools) - {"process_catalog", "rates_catalog"}
    assert order_bound
    for name in order_bound:
        properties = tools[name].inputSchema["properties"]
        assert "order_id" in properties, name
        assert "hermes_session_context" in properties, name


def test_unknown_role_env_fails_closed(service, monkeypatch) -> None:
    monkeypatch.setenv("METAL_CALC_ROLE", "admin")
    with pytest.raises(RuntimeError, match="не роль движка"):
        read_role_from_env()
    monkeypatch.setenv("METAL_CALC_ROLE", "  ")
    assert read_role_from_env() is None
    monkeypatch.setenv("METAL_CALC_ROLE", "SUPPLY")
    assert read_role_from_env() == "supply"
    monkeypatch.setenv("METAL_CALC_ROLE", "book_machine")
    assert read_role_from_env() == "book_machine"
