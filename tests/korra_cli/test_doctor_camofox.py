"""K21-336: korra doctor называет состояние встроенного Camofox и следующий шаг."""

import contextlib
import io

import pytest

import korra_cli.doctor as doctor
from korra_cli import camofox_embedded as emb


@pytest.fixture
def image(tmp_path, monkeypatch):
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
    monkeypatch.setattr("tools.tool_backend_helpers.read_selection", lambda section: None)
    return data


def run():
    issues = []
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        doctor.check_embedded_camofox(issues)
    return out.getvalue(), issues


def test_silent_outside_the_image(tmp_path, monkeypatch):
    monkeypatch.setenv("KORRA_CAMOFOX_HOME", str(tmp_path / "missing"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "data"))
    assert run() == ("", [])


def test_disabled_is_a_note_with_how_to_enable(image):
    out, issues = run()
    assert "выключен" in out
    assert "korra tools" in out
    assert issues == []


def test_selected_but_off_is_a_warning_with_a_fix(image, monkeypatch):
    monkeypatch.setattr("tools.tool_backend_helpers.read_selection", lambda section: "camofox")
    out, issues = run()
    assert "выбран в настройках, но сервер выключен" in out
    assert len(issues) == 1 and "korra tools" in issues[0]


def test_running(image, monkeypatch):
    emb.enable()
    monkeypatch.setattr(emb, "health", lambda timeout=2.0: True)
    out, issues = run()
    assert "работает" in out
    assert issues == []


def test_not_responding_gives_next_step_and_never_the_key(image, monkeypatch):
    emb.enable()
    monkeypatch.setattr(emb, "health", lambda timeout=2.0: False)
    out, issues = run()
    assert "не отвечает" in out
    assert "s6-svc -t /run/service/camofox" in out
    assert len(issues) == 1 and "не отвечает" in issues[0]
    assert emb.access_key() not in out + "".join(issues)
