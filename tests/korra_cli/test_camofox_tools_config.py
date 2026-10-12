"""K21-336: выбор браузера в `korra tools` ведёт встроенный Camofox по настройкам профилей."""

from unittest.mock import patch

import pytest

from korra_cli import camofox_embedded as emb
from korra_cli import tools_config as tc
from tests.fakes.camofox_embedded import (  # noqa: F401  (фикстуры)
    TcpSpy,
    UnixServer,
    choose_browser,
    data,
    in_profile,
    install,
)


def _row(name):
    return next(p for p in tc.TOOL_CATEGORIES["browser"]["providers"] if p["name"] == name)


def test_no_url_prompt_inside_the_image(install, data):
    assert tc._camofox_env_vars() == []


def test_url_prompt_stays_outside_the_image(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    assert [v["key"] for v in tc._camofox_env_vars()] == ["CAMOFOX_URL"]


def test_row_text_is_russian_and_about_blocks():
    assert "защитой от блокировок" in _row("Camofox")["tag"]


def test_choosing_camofox_enables_the_bundled_server(install, data):
    choose_browser("Camofox")
    assert emb.is_enabled()
    assert emb.endpoint() == emb.BASE_URL
    assert emb.consumers() == ["default"]


def test_choosing_another_browser_in_the_only_profile_switches_the_server_off(install, data):
    choose_browser("Camofox")
    choose_browser("Local Browser")
    assert not emb.is_enabled()
    assert (data / "camofox" / "access.key").exists()


@pytest.mark.parametrize("other", ["Local Browser", "Lightpanda"])
def test_other_browser_rows_do_not_leave_the_server_running(install, data, other):
    choose_browser("Camofox")
    choose_browser(other)
    assert not emb.is_enabled()


def test_server_belongs_to_the_installation_not_to_one_profile(install, data):
    with in_profile(data, "a"):
        choose_browser("Camofox")
    with in_profile(data, "b"):
        choose_browser("Camofox")
    assert sorted(emb.consumers()) == ["a", "b"]
    with in_profile(data, "b"):
        choose_browser("Local Browser")
    assert emb.is_enabled()  # профиль A ещё пользуется сервером
    with in_profile(data, "a"):
        choose_browser("Lightpanda")
    assert not emb.is_enabled()


def test_root_and_named_profile_both_count(install, data):
    with in_profile(data, None):
        choose_browser("Camofox")
    with in_profile(data, "work"):
        choose_browser("Camofox")
    assert sorted(emb.consumers()) == ["default", "work"]
    with in_profile(data, "work"):
        choose_browser("Local Browser")
    assert emb.is_enabled()


def test_explicit_external_url_in_process_env_does_not_enable_the_embedded_server(install, data, monkeypatch):
    monkeypatch.setenv("CAMOFOX_URL", "http://external.example:9377")
    choose_browser("Camofox")
    assert not emb.is_enabled()
    assert emb.consumers() == []
    assert not (data / "camofox").exists()


def test_explicit_external_url_in_the_profile_env_does_not_enable_the_embedded_server(install, data):
    with in_profile(data, "ext"):
        (data / "profiles" / "ext" / ".env").write_text("CAMOFOX_URL=http://external.example:9377\n")
        choose_browser("Camofox")
    assert not emb.is_enabled()
    with in_profile(data, "emb"):
        choose_browser("Camofox")
    assert emb.is_enabled()
    assert emb.consumers() == ["emb"]  # внешний адрес профиля ext не засчитывается


def test_the_current_profiles_env_url_does_not_hide_other_profiles(install, data, monkeypatch):
    # Загруженный .env текущего профиля лежит в окружении процесса и не должен
    # считаться явным адресом чужих профилей.
    with in_profile(data, "emb"):
        choose_browser("Camofox")
    with in_profile(data, "ext"):
        (data / "profiles" / "ext" / ".env").write_text("CAMOFOX_URL=http://external.example:9377\n")
        monkeypatch.setenv("CAMOFOX_URL", "http://external.example:9377")
        choose_browser("Camofox")
        assert emb.consumers() == ["emb"]
    assert emb.is_enabled()


def test_deleted_profile_stops_counting_and_the_server_goes_off_with_the_last_one(install, data):
    from korra_constants import mark_named_profile_deleted

    with in_profile(data, "gone"):
        choose_browser("Camofox")
    assert emb.is_enabled()
    mark_named_profile_deleted(data / "profiles" / "gone")
    assert emb.consumers() == []
    assert emb.sync() is False
    assert not emb.is_enabled()


def test_deleting_the_last_consumer_profile_switches_the_server_off(install, data, monkeypatch):
    from korra_cli import profiles

    with in_profile(data, "scratch"):
        choose_browser("Camofox")
    assert emb.is_enabled()
    profiles.delete_profile("scratch", yes=True)
    assert not emb.is_enabled()


def test_write_does_nothing_without_the_bundled_server(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "data"))
    config = {}
    tc._write_provider_config(_row("Camofox"), config, managed_feature=None)
    assert config["browser"]["cloud_provider"] == "camofox"
    assert not (tmp_path / "data" / "camofox").exists()


def test_post_setup_in_the_image_does_not_run_npm_and_waits_for_health(install, data, capsys):
    with patch("subprocess.run") as run, patch(
        "korra_constants.find_node_executable"
    ) as find_node, UnixServer(emb.socket_path()):
        tc._run_post_setup("camofox")
    run.assert_not_called()
    find_node.assert_not_called()
    assert emb.is_enabled()
    assert "работает" in capsys.readouterr().out


def test_post_setup_reports_a_slow_start_without_failing(install, data, capsys):
    ticks = iter([0, 1, 100])
    with patch("time.monotonic", side_effect=lambda: next(ticks)), patch("time.sleep"):
        tc._run_post_setup("camofox")
    out = capsys.readouterr().out
    assert "ещё запускается" in out and "korra doctor" in out


def test_post_setup_with_an_external_url_checks_that_server_and_leaves_embedded_off(
    install, data, monkeypatch, capsys
):
    with TcpSpy() as spy:
        monkeypatch.setenv("CAMOFOX_URL", f"http://127.0.0.1:{spy.port}")
        tc._run_post_setup("camofox")
        assert spy.seen == [None]  # опрошен именно внешний сервер
    out = capsys.readouterr().out
    assert "внешний сервер" in out.lower() and "отвечает" in out
    assert not emb.is_enabled()


def test_post_setup_with_a_dead_external_url_names_it(install, data, monkeypatch, capsys):
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:1")
    tc._run_post_setup("camofox")
    out = capsys.readouterr().out
    assert "не отвечает" in out and "http://127.0.0.1:1" in out
    assert not emb.is_enabled()


def test_post_setup_outside_the_image_still_uses_npm_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    with patch("korra_constants.find_node_executable", return_value=None):
        tc._run_post_setup("camofox")


def test_readiness_is_ready_in_the_image_without_any_env(install, data):
    provider = {**_row("Camofox"), "env_vars": []}
    assert tc.provider_readiness_status(provider, {}) == "ready"


def test_browser_feature_is_available_when_bundled_server_is_selected_and_on(install, data):
    from korra_cli import nous_subscription as ns

    emb.enable()
    config = {"browser": {"cloud_provider": "camofox"}}
    features = ns.get_nous_subscription_features(config)
    assert features.browser.current_provider == "Camofox"
    assert features.browser.available is True
