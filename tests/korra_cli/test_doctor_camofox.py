"""K21-336: korra doctor называет состояние встроенного Camofox и следующий шаг."""

import contextlib
import io

import korra_cli.doctor as doctor
from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import (  # noqa: F401  (фикстуры)
    UnixServer,
    choose_browser,
    chromium_root,
    data,
    in_profile,
    install,
)


def run(should_fix=False):
    issues = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        doctor.check_embedded_camofox(issues, should_fix=should_fix)
    return out.getvalue(), issues


def test_silent_outside_the_image(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    assert run() == ("", [])


def test_disabled_because_every_profile_chose_chromium_is_a_note(chromium_root, install, data):
    out, issues = run()
    assert "выключен" in out and "другой браузер" in out
    assert "korra tools" in out
    assert issues == []


def test_profile_without_a_browser_choice_needs_the_server_and_fix_turns_it_on(install, data):
    out, issues = run()
    assert "нужен профилям: default" in out and "через Chromium" in out
    assert len(issues) == 1 and "korra doctor --fix" in issues[0]

    out, issues = run(should_fix=True)
    assert issues == []
    assert "включён заново" in out
    assert emb.is_enabled()


def test_selected_but_off_is_a_warning_and_fix_turns_it_back_on(install, data):
    with in_profile(data, None):
        choose_browser("Camofox")
    emb._enabled_marker().unlink()  # маркер потерян, настройки остались
    out, issues = run()
    assert "нужен профилям" in out and "сервер выключен" in out
    assert "korra doctor --fix" in out
    assert len(issues) == 1 and "korra doctor --fix" in issues[0]

    out, issues = run(should_fix=True)
    assert issues == []
    assert "включён заново" in out and "вход на сайтах сохранён" in out
    assert emb.is_enabled()


def test_enabled_without_consumers_is_a_warning_and_fix_turns_it_off(chromium_root, install, data):
    emb.enable()
    out, issues = run()
    assert "ни одному профилю он не нужен" in out
    assert len(issues) == 1 and "korra doctor --fix" in issues[0]

    out, issues = run(should_fix=True)
    assert issues == []
    assert not emb.is_enabled()


def test_running_lists_the_profiles_that_use_it(install, data):
    with in_profile(data, "work"):
        choose_browser("Camofox")
    with UnixServer(emb.socket_path()):
        out, issues = run()
    assert "работает" in out and "work" in out
    assert issues == []


def test_not_responding_gives_next_step_and_never_the_key(install, data):
    with in_profile(data, None):
        choose_browser("Camofox")
    out, issues = run()
    assert "не отвечает" in out
    assert "s6-svc -u /run/service/camofox" in out
    assert len(issues) == 1 and "не отвечает" in issues[0]
    assert emb.access_key() not in out + "".join(issues)


def test_fix_does_not_pretend_to_repair_a_silent_server(install, data):
    with in_profile(data, None):
        choose_browser("Camofox")
    out, issues = run(should_fix=True)
    assert "не отвечает" in out
    assert len(issues) == 1
