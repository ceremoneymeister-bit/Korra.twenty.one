"""K21-336: выбор Camofox в `korra tools` внутри образа включает встроенный сервер."""

from unittest.mock import patch

import pytest

from korra_cli import camofox_embedded as emb
from korra_cli import tools_config as tc


@pytest.fixture
def image(tmp_path, monkeypatch):
    """Контейнер с Camofox: сервер установлен, данные во временном каталоге."""
    home = tmp_path / "opt-camofox"
    server = home.joinpath(*emb._SERVER_RELPATH)
    server.parent.mkdir(parents=True)
    server.write_text("")
    (home / "browser").mkdir()
    (home / "browser" / "camoufox-bin").write_text("")
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(home))
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(data))
    return data


def _row(name):
    return next(p for p in tc.TOOL_CATEGORIES["browser"]["providers"] if p["name"] == name)


def test_no_url_prompt_inside_the_image(image):
    assert tc._camofox_env_vars() == []


def test_url_prompt_stays_outside_the_image(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    assert [v["key"] for v in tc._camofox_env_vars()] == ["CAMOFOX_URL"]


def test_row_text_is_russian_and_about_blocks():
    tag = _row("Camofox")["tag"]
    assert "защитой от блокировок" in tag


def test_choosing_camofox_enables_the_bundled_server(image):
    config = {}
    tc._write_provider_config({**_row("Camofox"), "env_vars": []}, config, managed_feature=None)
    assert config["browser"]["cloud_provider"] == "camofox"
    assert emb.is_enabled()
    assert emb.endpoint().startswith("http://127.0.0.1:")


@pytest.mark.parametrize("other", ["Local Browser", "Lightpanda"])
def test_choosing_another_browser_switches_the_bundled_server_off(image, other):
    emb.enable()
    config = {}
    tc._write_provider_config(_row(other), config, managed_feature=None)
    assert not emb.is_enabled()
    assert (image / "camofox" / "access.key").exists()


def test_write_does_nothing_without_the_bundled_server(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "data"))
    config = {}
    tc._write_provider_config(_row("Camofox"), config, managed_feature=None)
    assert config["browser"]["cloud_provider"] == "camofox"
    assert not (tmp_path / "data" / "camofox").exists()


def test_post_setup_in_the_image_does_not_run_npm_and_waits_for_health(image, capsys):
    with patch("subprocess.run") as run, patch.object(emb, "health", return_value=True), patch(
        "korra_constants.find_node_executable"
    ) as find_node:
        tc._run_post_setup("camofox")
    run.assert_not_called()
    find_node.assert_not_called()
    assert emb.is_enabled()
    assert "работает" in capsys.readouterr().out


def test_post_setup_reports_a_slow_start_without_failing(image, capsys):
    ticks = iter([0, 1, 100])
    with patch.object(emb, "health", return_value=False), patch(
        "time.monotonic", side_effect=lambda: next(ticks)
    ), patch("time.sleep"):
        tc._run_post_setup("camofox")
    out = capsys.readouterr().out
    assert "ещё запускается" in out and "korra doctor" in out


def test_post_setup_outside_the_image_still_uses_npm_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    with patch("korra_constants.find_node_executable", return_value=None):
        tc._run_post_setup("camofox")


def test_readiness_is_ready_in_the_image_without_any_env(image):
    provider = {**_row("Camofox"), "env_vars": []}
    assert tc.provider_readiness_status(provider, {}) == "ready"


def test_browser_feature_is_available_when_bundled_server_is_selected_and_on(image, monkeypatch):
    from korra_cli import nous_subscription as ns

    monkeypatch.delenv("CAMOFOX_URL", raising=False)
    emb.enable()
    config = {"browser": {"cloud_provider": "camofox"}}
    features = ns.get_nous_subscription_features(config)
    assert features.browser.current_provider == "Camofox"
    assert features.browser.available is True
