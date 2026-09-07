"""The runner must address the role inside the shared, authenticated gateway."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def runner(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("calc21_runner", Path(__file__).with_name("runner.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "PROFILES_ROOT", tmp_path / "profiles")
    (tmp_path / ".env").write_text("API_SERVER_PORT=8661\nAPI_SERVER_KEY=local-test-key-at-least-16\n")
    monkeypatch.setenv("METAL_CALC_GATEWAY_PORT", "8661")
    return module


def test_role_uses_shared_port_and_root_key(runner):
    assert runner.profile_api("raschet-route") == (
        "http://127.0.0.1:8661/p/raschet-route", "local-test-key-at-least-16"
    )


def test_foreign_profile_cannot_become_delivery_target(runner):
    with pytest.raises(runner.TargetConfigurationError):
        runner.profile_api("other-agent")


def test_missing_shared_key_cannot_fall_back_to_other_profile(runner):
    (runner.PROFILES_ROOT.parent / ".env").write_text("API_SERVER_PORT=8661\n")
    with pytest.raises(runner.TargetConfigurationError):
        runner.profile_api("raschet-route")
