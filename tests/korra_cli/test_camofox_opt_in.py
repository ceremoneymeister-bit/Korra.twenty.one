"""K21 fix12: встроенный Camofox запускается только по выбору владельца."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import (  # noqa: F401
    UnixServer, choose_browser, data, in_profile, install,
)


def test_no_choice_never_probes_or_starts_camofox(install, data):
    from tools import browser_camofox as bc, browser_tool as bt

    with patch.object(emb, "health") as health, patch.object(emb, "recover") as recover:
        assert not emb.is_enabled()
        assert bc.get_camofox_url() == ""
        assert not bc.is_camofox_mode()
        with patch.object(bt, "_run_browser_command", return_value={"success": True}) as chromium:
            result = json.loads(bt.browser_navigate("https://example.com", task_id="opt-in"))
        assert result["success"] and "note" not in result
        chromium.assert_called()
    health.assert_not_called()
    recover.assert_not_called()


def test_last_explicit_choice_controls_the_service(install, data):
    assert not emb.is_enabled()
    with in_profile(data, "a"):
        choose_browser("Camofox")
    assert emb.is_enabled()
    with in_profile(data, "b"):
        choose_browser("Camofox")
    with in_profile(data, "a"):
        choose_browser("Local Browser")
    assert emb.is_enabled()
    with in_profile(data, "b"):
        choose_browser("Local Browser")
    assert not emb.is_enabled()


def test_existing_explicit_selection_survives_startup_sync(install, data):
    config = data / "config.yaml"
    config.write_text("browser:\n  cloud_provider: camofox\n")
    before = config.read_bytes()
    assert emb.main(["sync"]) == 0
    assert emb.is_enabled()
    assert config.read_bytes() == before


def test_startup_removes_an_old_marker_without_enabling_implicit_profiles(install, data):
    folder = data / "camofox"
    folder.mkdir()
    (folder / "enabled").write_text("1\n")
    with patch.object(emb, "_s6_svc") as signal:
        assert emb.main(["sync"]) == 0
    signal.assert_not_called()  # служба сверяет себя до запуска Node
    assert not emb.is_enabled()


def test_subscription_view_does_not_advertise_unselected_camofox(install, data):
    from korra_cli.nous_subscription import get_nous_subscription_features

    assert get_nous_subscription_features({}).browser.current_provider != "Camofox"


def test_unused_service_is_a_normal_state_with_enable_instructions(install, data):
    info = emb.status()
    assert info["state"] == "unused"
    assert "korra tools" in info["next"] and "Camofox" in info["next"]


def test_another_profiles_process_credentials_cannot_disable_an_explicit_choice(install, data, monkeypatch):
    with in_profile(data, "a"):
        choose_browser("Camofox")
    with in_profile(data, "b"):
        monkeypatch.setenv("CAMOFOX_URL", "http://external.invalid")
        monkeypatch.setenv("BROWSER_USE_API_KEY", "key-of-b")
        choose_browser("Local Browser")
    assert emb.is_enabled()


def test_deleting_last_consumer_stops_the_service(install, data):
    from korra_cli import profiles

    with in_profile(data, "scratch"):
        choose_browser("Camofox")
    assert emb.is_enabled()
    profiles.delete_profile("scratch", yes=True)
    assert not emb.is_enabled()


def test_picker_describes_opt_in_without_a_default_claim(install, data):
    from korra_cli import tools_config as tc
    from korra_cli.config import load_config

    rows = tc._visible_providers(tc.TOOL_CATEGORIES["browser"], load_config())
    row = next(r for r in rows if r.get("browser_provider") == "camofox")
    assert "для сайтов, которые не пускают роботов" in row["tag"]
    assert "по умолчанию" not in row["badge"] and "рекомендуется" not in row["badge"]
    assert not tc._is_provider_active(row, load_config())
    choose_browser("Camofox")
    assert tc._is_provider_active(row, load_config())


def test_profile_without_a_choice_stays_on_chromium_when_another_uses_camofox(install, data):
    from tools import browser_camofox as bc

    with in_profile(data, "chosen"):
        choose_browser("Camofox")
    with in_profile(data, "plain"), patch.object(emb, "health") as health:
        assert bc.get_camofox_url() == ""
        assert not bc.is_camofox_mode()
    health.assert_not_called()
    assert emb.is_enabled()


def test_external_server_does_not_start_the_embedded_service(install, data):
    from korra_cli.config import remove_env_value, save_env_value

    save_env_value("CAMOFOX_URL", "http://external.invalid", update_process=False)
    choose_browser("Camofox")
    assert not emb.is_enabled()
    remove_env_value("CAMOFOX_URL", update_process=False)
    assert emb.is_enabled()
    save_env_value("CAMOFOX_URL", "http://external.invalid", update_process=False)
    assert not emb.is_enabled()


def test_config_set_and_raw_yaml_follow_the_same_service_switch(install, data):
    from korra_cli.config import atomic_config_write, set_config_value

    set_config_value("browser.cloud_provider", "camofox")
    assert emb.is_enabled()
    atomic_config_write(data / "config.yaml", {})
    assert not emb.is_enabled()


def test_legacy_gateway_selection_does_not_start_camofox(install, data):
    from korra_cli.config import save_config
    from tools import browser_camofox as bc

    save_config({"browser": {"cloud_provider": "camofox", "use_gateway": True}})
    assert not bc.is_camofox_mode()
    assert not emb.is_enabled()


def test_imported_and_cloned_explicit_choices_enable_the_service(install, data, tmp_path):
    from korra_cli import profiles

    with in_profile(data, "source"):
        choose_browser("Camofox")
    archive = profiles.export_profile("source", str(tmp_path / "profile.tar.gz"))
    profiles.delete_profile("source", yes=True)
    assert not emb.is_enabled()
    profiles.import_profile(str(archive), name="imported")
    assert emb.is_enabled()
    with patch.object(profiles, "create_wrapper_script"):
        profiles.create_profile("cloned", clone_from="imported", no_alias=True)
    profiles.delete_profile("imported", yes=True)
    assert emb.is_enabled()
    with in_profile(data, "cloned"):
        choose_browser("Local Browser")
    assert not emb.is_enabled()


def test_panel_endpoint_selects_only_the_requested_profile(install, data, monkeypatch):
    from fastapi.testclient import TestClient
    from korra_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    with in_profile(data, "a"), in_profile(data, "b"):
        pass
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    for profile, provider, enabled in (
        ("a", "Camofox", True), ("b", "Local Browser", True), ("a", "Local Browser", False),
    ):
        response = client.put(
            "/api/tools/toolsets/browser/provider", json={"provider": provider, "profile": profile},
        )
        assert response.status_code == 200, response.text
        assert emb.is_enabled() == enabled


def test_real_service_script_waits_without_node_until_selection(install, data, tmp_path):
    """Настоящие sh → sync → serve; меняются лишь пути образа и бинарник Node."""
    root = Path(__file__).resolve().parents[2]
    script = (root / "docker/s6-rc.d/camofox/run").read_text()
    script = script.replace("/opt/data", str(data))
    script = script.replace("/opt/hermes/.venv/bin/activate", str(root / ".venv/bin/activate"))
    script = script.replace("sleep 3", f"touch '{tmp_path / 'waiting'}'; sleep 0.05")
    run = tmp_path / "run"
    run.write_text(script)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    setuid = binaries / "s6-setuidgid"
    setuid.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    setuid.chmod(0o755)
    node = binaries / "node"
    node.write_text(f"#!/bin/sh\ntouch '{tmp_path / 'node-started'}'\n")
    node.chmod(0o755)
    env = {**os.environ, "PATH": f"{binaries}:{Path(sys.executable).parent}:{os.environ['PATH']}",
           "PYTHONPATH": str(root)}
    process = subprocess.Popen(["sh", str(run)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "waiting").exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.05)
        assert (tmp_path / "waiting").exists()
        assert not (tmp_path / "node-started").exists()
        assert not emb.is_enabled()
        choose_browser("Camofox")
        _, error = process.communicate(timeout=15)
        assert process.returncode == 0, error.decode()
        assert (tmp_path / "node-started").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
