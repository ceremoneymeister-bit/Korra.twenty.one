"""No network/model calls: enforce explicit, subscription-only live test entry."""

import base64
import json
from pathlib import Path
import time

import pytest


@pytest.fixture
def live(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import designer_live_acceptance
    return designer_live_acceptance


def jwt(expires):
    claims = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).decode().rstrip("=")
    return f"synthetic.{claims}.signature"


def test_accepts_access_only_subscription_login(live):
    token = jwt(time.time() + 3600)
    assert live.subscription_token({"auth_mode": "chatgpt", "access_token": token}) == token


@pytest.mark.parametrize("payload", [
    {"auth_mode": "apikey", "access_token": "sk-synthetic"},
    {"auth_mode": "chatgpt", "access_token": "sk-synthetic"},
    {"auth_mode": "chatgpt", "access_token": jwt(0)},
    {"auth_mode": "chatgpt", "access_token": jwt(time.time() + 100)},
    {"auth_mode": "chatgpt", "access_token": jwt(time.time() + 3600), "refresh_token": "do-not-copy"},
    {"auth_mode": "chatgpt", "access_token": jwt(time.time() + 3600), "OPENAI_API_KEY": "do-not-use"},
])
def test_refuses_api_keys_refresh_material_and_short_lived_access(live, payload):
    with pytest.raises(ValueError):
        live.subscription_token(payload)


def test_live_requires_explicit_opt_in_before_reading_credentials(live, monkeypatch, tmp_path):
    monkeypatch.delenv("KORRA_DESIGNER_LIVE", raising=False)
    monkeypatch.setattr(live.sys, "argv", ["designer_live_acceptance", "--out", str(tmp_path / "unused")])
    with pytest.raises(SystemExit) as exc:
        live.main()
    assert exc.value.code == 2
    assert not (tmp_path / "unused").exists()


def test_offline_probe_reports_missing_dependencies(live, monkeypatch):
    import designer_preflight
    monkeypatch.setattr(designer_preflight.importlib.util, "find_spec", lambda name: object() if name == "PIL" else None)
    monkeypatch.setattr(designer_preflight.shutil, "which", lambda name: None)
    assert designer_preflight.dependencies() == {
        "python_pptx": False, "pillow": True, "soffice": False,
        "pdftoppm": False, "pdftotext": False,
    }


def test_refuses_out_of_scope_stand_before_live_calls(live, monkeypatch, tmp_path):
    monkeypatch.delenv("KORRA_WRITE_SAFE_ROOT", raising=False)
    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="write-safe root"):
        live.validate_output_scope(tmp_path / "elsewhere")
    live.validate_output_scope(tmp_path / "data" / "presentation")


def test_canonical_write_scope_takes_precedence(live, monkeypatch, tmp_path):
    monkeypatch.setenv("KORRA_WRITE_SAFE_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(tmp_path / "old"))
    with pytest.raises(ValueError, match="write-safe root"):
        live.validate_output_scope(tmp_path / "old" / "presentation")
    live.validate_output_scope(tmp_path / "data" / "presentation")


def test_uses_native_multiple_write_roots(live, monkeypatch, tmp_path):
    import os
    monkeypatch.setenv("KORRA_WRITE_SAFE_ROOT", os.pathsep.join([str(tmp_path / "first"), str(tmp_path / "second")]))
    live.validate_output_scope(tmp_path / "second" / "presentation")
    with pytest.raises(ValueError):
        live.validate_output_scope(tmp_path / "third")


def test_shared_transcript_excludes_private_provider_reasoning(live):
    source = {"final_response": "The file is ready", "reasoning_tokens": 10,
              "last_reasoning": "private", "messages": [
                  {"role": "assistant", "content": "Visible", "reasoning_content": "private",
                   "codex_reasoning_items": [{"encrypted_content": "private"}]},
                  {"role": "tool", "content": "Created file"},
              ], "items": [{"type": "reasoning", "text": "private"}, {"type": "text", "text": "Visible"}]}
    assert live.export_visible_response(source) == {
        "final_response": "The file is ready", "reasoning_tokens": 10,
        "messages": [{"role": "assistant", "content": "Visible"}, {"role": "tool", "content": "Created file"}],
        "items": [{"type": "text", "text": "Visible"}],
    }
