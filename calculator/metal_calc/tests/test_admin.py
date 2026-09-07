from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from metal_calc import admin, packs2, service as service_module, service3
from metal_calc.errors import InvalidState


class _Service:
    closed = False

    def __init__(self, _settings: Any) -> None:
        self.registry = object()
        self.rates_root = object()

    def close(self) -> None:
        self.closed = True


def test_qa_verdict_cli_uses_trusted_qa_role(monkeypatch, capsys) -> None:
    seen: dict[str, Any] = {}
    service = _Service(object())

    class _Workflow:
        def __init__(self, registry: Any, packs: Any) -> None:
            seen["registry"] = registry
            seen["packs"] = packs

        def qa_verdict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            seen["args"] = args
            seen["kwargs"] = kwargs
            return {"status": "READY_FOR_LD"}

    monkeypatch.setattr(service_module, "MetalCalcService", lambda _settings: service)
    monkeypatch.setattr(packs2, "PipelinePackStore", lambda root: ("packs", root))
    monkeypatch.setattr(service3, "WorkflowService", _Workflow)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "metal-calc-admin",
            "qa-verdict",
            "--order-id",
            "34232-P01",
            "--expected-revision",
            "17",
            "--gate",
            "commercial",
            "--verdict",
            "PASS",
            "--actor",
            "panel-user",
        ],
    )
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": __import__("io").BytesIO(b"{}")})())

    admin.main()

    assert json.loads(capsys.readouterr().out) == {"ok": True, "status": "READY_FOR_LD"}
    assert seen["args"] == (
        "34232-P01",
        17,
        "commercial",
        "PASS",
        [],
        "panel-user",
        None,
    )
    assert seen["kwargs"] == {"actor_role": "qa"}
    assert service.closed is True


def test_qa_verdict_cli_fails_closed_and_closes(monkeypatch, capsys) -> None:
    service = _Service(object())

    class _Workflow:
        def __init__(self, _registry: Any, _packs: Any) -> None:
            pass

        def qa_verdict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise InvalidState("wrong gate")

    monkeypatch.setattr(service_module, "MetalCalcService", lambda _settings: service)
    monkeypatch.setattr(packs2, "PipelinePackStore", lambda root: ("packs", root))
    monkeypatch.setattr(service3, "WorkflowService", _Workflow)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "metal-calc-admin",
            "qa-verdict",
            "--order-id",
            "34232-P01",
            "--expected-revision",
            "17",
            "--gate",
            "technological",
            "--verdict",
            "PASS",
            "--actor",
            "panel-user",
        ],
    )
    monkeypatch.setattr(sys, "stdin", type("Stdin", (), {"buffer": __import__("io").BytesIO(b"{}")})())

    with pytest.raises(SystemExit) as exc:
        admin.main()

    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "InvalidState"
    assert service.closed is True


def test_contractor_quote_cli_uses_trusted_panel_actor(monkeypatch, capsys) -> None:
    seen: dict[str, Any] = {}
    service = _Service(object())

    class _Workflow:
        def __init__(self, registry: Any, packs: Any) -> None:
            seen["registry"] = registry
            seen["packs"] = packs

        def contractor_quote_set(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            seen["args"] = args
            seen["kwargs"] = kwargs
            return {"status": "COSTING_COMPLETE", "calculation_revision": 2}

    monkeypatch.setattr(service_module, "MetalCalcService", lambda _settings: service)
    monkeypatch.setattr(packs2, "PipelinePackStore", lambda root: ("packs", root))
    monkeypatch.setattr(service3, "WorkflowService", _Workflow)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "metal-calc-admin",
            "contractor-quote-set",
            "--order-id",
            "34232-P01",
            "--expected-revision",
            "17",
            "--route-seq",
            "4",
            "--actor",
            "panel-user",
        ],
    )
    body = {
        "amount_rub": "120.50",
        "basis": "per_piece",
        "vat_included": True,
        "quoted_at": "2026-09-04",
        "valid_until": "2026-10-04",
        "source": "ООО Покраска",
        "reference": "КП-42",
    }
    monkeypatch.setattr(
        sys,
        "stdin",
        type("Stdin", (), {"buffer": __import__("io").BytesIO(json.dumps(body).encode())})(),
    )

    admin.main()

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "status": "COSTING_COMPLETE",
        "calculation_revision": 2,
    }
    assert seen["args"] == (
        "34232-P01",
        17,
        4,
        "120.50",
        "per_piece",
        True,
        "2026-09-04",
        "2026-10-04",
        "ООО Покраска",
        "КП-42",
        "panel-user",
    )
    assert seen["kwargs"] == {"actor_role": "panel"}
    assert service.closed is True


@pytest.mark.parametrize(
    ("command", "stdin_body", "method", "expected_args"),
    [
        (
            [
                "manual-review-complete",
                "--order-id",
                "34232-P01",
                "--expected-revision",
                "17",
                "--expected-book-digest",
                "a" * 64,
                "--actor",
                "Методолог",
            ],
            {
                "reviews": [
                    {
                        "item_index": 0,
                        "item_digest": "b" * 64,
                        "evidence": "Ставка сверена",
                        "reference": "Карта-42",
                    }
                ]
            },
            "manual_review_complete",
            ("34232-P01", 17, "a" * 64, "reviews", "Методолог"),
        ),
        (
            [
                "route-return",
                "--order-id",
                "34232-P01",
                "--expected-revision",
                "17",
                "--actor",
                "Методолог",
            ],
            {"reason": "Маршрут не соответствует КД"},
            "route_return",
            (
                "34232-P01",
                17,
                "Маршрут не соответствует КД",
                "Методолог",
            ),
        ),
    ],
)
def test_human_review_admin_commands_use_trusted_qa_role(
    monkeypatch,
    capsys,
    command,
    stdin_body,
    method,
    expected_args,
) -> None:
    seen: dict[str, Any] = {}
    service = _Service(object())

    class _Workflow:
        def __init__(self, _registry: Any, _packs: Any) -> None:
            pass

        def manual_review_complete(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            seen["args"] = args
            seen["kwargs"] = kwargs
            return {"status": "QA_TECHNOLOGICAL_PASS"}

        def route_return(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            seen["args"] = args
            seen["kwargs"] = kwargs
            return {"status": "ROUTE_OPTIONS_READY"}

    monkeypatch.setattr(service_module, "MetalCalcService", lambda _settings: service)
    monkeypatch.setattr(packs2, "PipelinePackStore", lambda root: ("packs", root))
    monkeypatch.setattr(service3, "WorkflowService", _Workflow)
    monkeypatch.setattr(sys, "argv", ["metal-calc-admin", *command])
    monkeypatch.setattr(
        sys,
        "stdin",
        type(
            "Stdin",
            (),
            {"buffer": __import__("io").BytesIO(json.dumps(stdin_body).encode())},
        )(),
    )

    admin.main()

    actual_args = list(seen["args"])
    if method == "manual_review_complete":
        assert actual_args[:3] == list(expected_args[:3])
        assert actual_args[3] == stdin_body["reviews"]
        assert actual_args[4] == expected_args[4]
    else:
        assert tuple(actual_args) == expected_args
    assert seen["kwargs"] == {"actor_role": "qa"}
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert service.closed is True
