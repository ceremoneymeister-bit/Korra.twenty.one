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


def run():
    issues = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        doctor.check_embedded_camofox(issues)
    return out.getvalue(), issues


def test_silent_outside_the_image(tmp_path, monkeypatch, data):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    assert run() == ("", [])


def test_running(install, data):
    choose_browser("Camofox")
    emb.ensure_state()
    with UnixServer(emb.socket_path()):
        out, issues = run()
    assert "работает" in out
    assert issues == []


def test_not_responding_gives_next_step_and_never_the_key(install, data):
    choose_browser("Camofox")
    emb.ensure_state()
    out, issues = run()
    assert "не отвечает" in out
    assert "s6-svc -u /run/service/camofox" in out and "s6-svc -t /run/service/camofox" in out
    assert len(issues) == 1 and "не отвечает" in issues[0]
    assert emb.access_key() not in out + "".join(issues)


def test_switched_off_names_the_command_and_the_effect_and_is_not_a_failure(install, data):
    emb.disable(signal=False)
    out, issues = run()
    assert "выключен на этой установке" in out
    assert "Chromium" in out and "ошибку" in out
    assert "korra tools camofox on" in out
    assert issues == []  # решение владельца, а не поломка


def test_doctor_explains_how_to_enable_an_unused_browser(chromium_root, install, data):
    out, issues = run()
    assert "служба спит" in out and "запасной режим: включён" in out and issues == []
    with in_profile(data, "work"):
        choose_browser("Camofox")
    with UnixServer(emb.socket_path()):
        out, issues = run()
    assert "работает" in out and issues == []


def test_doctor_has_no_fix_for_the_server(install, data):
    import inspect

    assert "should_fix" not in inspect.signature(doctor.check_embedded_camofox).parameters
