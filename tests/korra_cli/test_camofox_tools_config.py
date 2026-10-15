"""K21-336: выбор браузера в `korra tools` ведёт встроенный Camofox по настройкам профилей."""

from unittest.mock import patch

import pytest

from korra_cli import camofox_embedded as emb
from korra_cli import tools_config as tc
from tests.fakes.camofox_embedded import (  # noqa: F401  (фикстуры)
    TcpSpy,
    UnixServer,
    choose_browser,
    chromium_root,
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
    assert "для сайтов, которые не пускают роботов" in _row("Camofox")["tag"]


def test_choosing_camofox_enables_the_service(install, data):
    choose_browser("Camofox")
    assert emb.is_enabled()
    assert emb.endpoint() == emb.BASE_URL
    assert not emb.is_disabled()


@pytest.mark.parametrize("other", ["Local Browser", "Lightpanda", "Camofox"])
def test_service_follows_the_selected_browser(install, data, other):
    choose_browser("Camofox")
    choose_browser(other)
    assert emb.is_enabled() == (other == "Camofox")


def test_service_stays_on_while_another_profile_uses_it(install, data):
    with in_profile(data, "a"):
        choose_browser("Camofox")
    with in_profile(data, "b"):
        choose_browser("Local Browser")
    assert emb.is_enabled()
    with in_profile(data, "a"):
        choose_browser("Lightpanda")
    assert not emb.is_enabled()
    with in_profile(data, "b"):
        choose_browser("Camofox")
    assert emb.is_enabled()


def test_deleting_a_profile_does_not_touch_the_server(install, data, monkeypatch):
    from korra_cli import profiles

    with in_profile(data, "scratch"):
        choose_browser("Camofox")
    emb.disable(signal=False)
    profiles.delete_profile("scratch", yes=True)
    assert emb.is_disabled()  # решение владельца профилями не меняется
    assert not (data / "profiles" / "scratch").exists()


def test_write_does_nothing_without_the_bundled_server(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "data"))
    config = {}
    tc._write_provider_config(_row("Camofox"), config, managed_feature=None)
    assert config["browser"]["cloud_provider"] == "camofox"
    assert not (tmp_path / "data" / "camofox").exists()


def test_post_setup_in_the_image_does_not_run_npm_and_waits_for_health(install, data, capsys):
    choose_browser("Camofox")
    with patch("subprocess.run") as run, patch(
        "korra_constants.find_node_executable"
    ) as find_node, UnixServer(emb.socket_path()):
        tc._run_post_setup("camofox")
    run.assert_not_called()
    find_node.assert_not_called()
    assert emb.is_enabled()
    assert "работает" in capsys.readouterr().out


def test_post_setup_does_not_switch_on_a_server_the_owner_switched_off(install, data, capsys):
    emb.disable(signal=False)
    with patch("subprocess.run") as run, UnixServer(emb.socket_path()):
        tc._run_post_setup("camofox")
    run.assert_not_called()
    assert emb.is_disabled()
    out = capsys.readouterr().out
    assert "выключен" in out and "korra tools camofox on" in out


def test_post_setup_reports_a_slow_start_without_failing(install, data, capsys):
    ticks = iter([0, 1, 100])
    with patch("time.monotonic", side_effect=lambda: next(ticks)), patch("time.sleep"):
        tc._run_post_setup("camofox")
    out = capsys.readouterr().out
    assert "ещё запускается" in out and "korra doctor" in out


def test_post_setup_with_an_external_url_checks_that_server(
    install, data, monkeypatch, capsys
):
    with TcpSpy() as spy:
        monkeypatch.setenv("CAMOFOX_URL", f"http://127.0.0.1:{spy.port}")
        tc._run_post_setup("camofox")
        assert spy.seen == [None]  # опрошен именно внешний сервер
    out = capsys.readouterr().out
    assert "внешний сервер" in out.lower() and "отвечает" in out


def test_post_setup_with_a_dead_external_url_names_it(install, data, monkeypatch, capsys):
    monkeypatch.setenv("CAMOFOX_URL", "http://127.0.0.1:1")
    tc._run_post_setup("camofox")
    out = capsys.readouterr().out
    assert "не отвечает" in out and "http://127.0.0.1:1" in out


def test_post_setup_outside_the_image_still_uses_npm_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    with patch("korra_constants.find_node_executable", return_value=None):
        tc._run_post_setup("camofox")


def test_readiness_is_ready_in_the_image_without_any_env(install, data):
    provider = {**_row("Camofox"), "env_vars": []}
    assert tc.provider_readiness_status(provider, {}) == "ready"


def test_browser_feature_is_available_when_bundled_server_is_selected_and_on(install, data):
    from korra_cli import nous_subscription as ns

    config = {"browser": {"cloud_provider": "camofox"}}
    features = ns.get_nous_subscription_features(config)
    assert features.browser.current_provider == "Camofox"
    assert features.browser.available is True
    emb.disable(signal=False)
    assert ns.get_nous_subscription_features(config).browser.available is False


# ── Camofox — по явному выбору владельца ────────────────────────────────


def _rows(config=None):
    cat = tc.TOOL_CATEGORIES["browser"]
    with patch.object(tc, "_plugin_browser_providers", return_value=[]):
        return tc._visible_providers(cat, config or {})


def test_in_the_image_camofox_is_an_optional_browser(install, data):
    rows = _rows()
    camofox = next(r for r in rows if r["name"] == "Camofox")
    assert "по умолчанию" not in camofox["badge"] and "рекомендуется" not in camofox["badge"]
    assert "для сайтов, которые не пускают роботов" in camofox["tag"]
    assert rows[0]["name"] == "Local Browser"


def test_outside_the_image_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    rows = _rows()
    assert rows[0]["name"] == "Local Browser"
    assert "★" in rows[0]["badge"]
    assert "по умолчанию" not in next(r for r in rows if r["name"] == "Camofox")["badge"]


def test_unconfigured_profile_does_not_show_camofox_as_active(install, data):
    from korra_cli.config import load_config

    config = load_config()
    camofox = next(r for r in _rows() if r["name"] == "Camofox")
    assert not tc._is_provider_active(camofox, config)


def test_chromium_is_one_action_and_the_row_follows_it(install, data):
    from korra_cli.config import load_config

    choose_browser("Local Browser")
    config = load_config()
    assert config["browser"]["cloud_provider"] == "local"
    rows = _rows(config)
    active = [r["name"] for r in rows if tc._is_provider_active(r, config)]
    assert active == ["Local Browser"]
    assert not emb.is_enabled()
    choose_browser("Camofox")
    assert emb.is_enabled()


def test_a_switched_off_server_is_not_shown_as_the_active_default(install, data):
    from korra_cli.config import load_config

    emb.disable(signal=False)
    rows = _rows()
    config = load_config()
    assert not tc._is_provider_active(next(r for r in rows if r["name"] == "Camofox"), config)


# ── Сценарии находок повторного ревью (review2): после упрощения невозможны ──


def _no_foreign_env(monkeypatch):
    for key in ("BROWSER_USE_API_KEY", "BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID", "AGENT_BROWSER_ENGINE"):
        monkeypatch.delenv(key, raising=False)


def test_new_profile_keeps_the_previous_browser(install, data, monkeypatch):
    from tools import browser_camofox as bc
    from korra_cli import profiles

    _no_foreign_env(monkeypatch)
    with patch.object(profiles, "create_wrapper_script"):
        profiles.create_profile("fresh", no_alias=True, no_skills=True)
    assert not emb.is_enabled()
    with in_profile(data, "fresh"):
        assert bc.get_camofox_url() == ""


def test_review2_finding_1_cloud_key_of_another_profile_changes_nothing(install, data, monkeypatch):
    from tools import browser_camofox as bc

    _no_foreign_env(monkeypatch)
    with in_profile(data, "chosen"):
        choose_browser("Camofox")
    with in_profile(data, "cloudy") as home:
        (home / ".env").write_text("BROWSER_USE_API_KEY=key-of-cloudy\n")
        choose_browser("Local Browser")
    with in_profile(data, "chosen"):
        assert bc.get_camofox_url() == emb.BASE_URL
    assert emb.is_enabled()


def test_review2_finding_2_published_profile_enables_the_service(install, data, monkeypatch):
    from tools import browser_camofox as bc
    from korra_cli import profiles
    from korra_cli.profile_creation import publish_profile

    _no_foreign_env(monkeypatch)

    def populate(stage):
        profiles.create_profile("from-template", no_alias=True, no_skills=True, _staging_dir=stage)
        (stage / "config.yaml").write_text("browser:\n  cloud_provider: camofox\n")
        assert not emb.is_enabled()  # staging ещё не профиль
        return {"ok": True}, {}

    with patch.object(profiles, "create_wrapper_script"):
        publish_profile("review-template-0001", "from-template", {}, populate)
    with in_profile(data, "from-template"):
        assert bc.get_camofox_url() == emb.BASE_URL
    assert emb.is_enabled()


def test_review2_finding_3_create_profile_waits_for_the_creation_lock(install, data, monkeypatch):
    # create_profile() снова стоит под profile_creation_lock(): читатель, держащий
    # блокировку, не видит неполный профиль, а создание ждёт его.
    import threading

    from korra_cli import profiles

    _no_foreign_env(monkeypatch)
    entered_seed = threading.Event()
    release_seed = threading.Event()
    original_seed = profiles._seed_model_config
    errors: list = []

    def paused_seed(*args, **kwargs):
        entered_seed.set()
        if not release_seed.wait(5):
            raise RuntimeError("seed timeout")
        return original_seed(*args, **kwargs)

    def create():
        try:
            profiles.create_profile("incomplete", no_alias=True, no_skills=True, soul="expected identity")
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    with patch.object(profiles, "create_wrapper_script"), patch.object(
        profiles, "_seed_model_config", side_effect=paused_seed
    ):
        with profiles.profile_creation_lock():
            worker = threading.Thread(target=create)
            worker.start()
            try:
                bypassed = entered_seed.wait(1.5)
                visible = dict(profiles.profiles_to_serve(multiplex=True))
            finally:
                release_seed.set()
        worker.join(10)
    assert bypassed is False  # создание не прошло мимо блокировки читателя
    assert "incomplete" not in visible
    assert not worker.is_alive() and errors == []
    assert (data / "profiles" / "incomplete" / "SOUL.md").exists()
