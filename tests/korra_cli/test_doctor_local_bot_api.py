"""Doctor называет прямо, почему медиа из Telegram не доходит до агента.

Сервер `telegram-bot-api --local` отвечает абсолютным путём на своём диске и по
HTTP файлы не отдаёт. Контур без этого каталога получает 404, который владелец
видит как «InvalidToken»: сообщение о поломке есть, а причины в нём нет. На
трёх установках так молча терялись все голосовые и фото девять дней, поэтому
проверка обязана называть и каталог, и способ починки.
"""

import contextlib
import io
import sys
import types

import pytest

import korra_cli.doctor as doctor


LOCAL_ROOT = "KORRA_TELEGRAM_LOCAL_ROOT"
OWN_SERVER = {"base_url": "http://127.0.0.1:8081/bot"}


@pytest.fixture
def config(monkeypatch):
    """Подменяет korra_cli.config.load_config, который doctor импортирует внутри."""
    holder = {"value": {}}

    def install(telegram_extra):
        holder["value"] = {"telegram": {"extra": telegram_extra}}

    module = types.ModuleType("korra_cli.config")
    module.load_config = lambda: holder["value"]
    monkeypatch.setitem(sys.modules, "korra_cli.config", module)
    return install


def run(issues=None):
    issues = [] if issues is None else issues
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        doctor.check_local_bot_api(issues)
    return output.getvalue(), issues


def test_missing_mount_is_a_failure_that_names_the_symptom(config, monkeypatch):
    config(OWN_SERVER)
    monkeypatch.delenv(LOCAL_ROOT, raising=False)
    text, issues = run()
    assert "не примонтирован" in text
    assert "голосовые" in text
    assert issues and "update.sh" in issues[0]


def test_mounted_directory_reports_the_path(config, monkeypatch, tmp_path):
    config(OWN_SERVER)
    monkeypatch.setenv(LOCAL_ROOT, str(tmp_path))
    text, issues = run()
    assert str(tmp_path) in text
    assert issues == []


def test_announced_but_absent_directory_fails(config, monkeypatch, tmp_path):
    config(OWN_SERVER)
    monkeypatch.setenv(LOCAL_ROOT, str(tmp_path / "нет-такого"))
    text, issues = run()
    assert "недоступен" in text
    assert issues


def test_cloud_telegram_says_nothing(config, monkeypatch):
    # Обычный облачный Telegram: проверять нечего, и тишина здесь уместнее
    # лишней строки в отчёте.
    config({})
    monkeypatch.setenv(LOCAL_ROOT, "")
    text, issues = run()
    assert text.strip() == ""
    assert issues == []


def test_remote_own_server_is_not_our_business(config, monkeypatch):
    config({"base_url": "https://bot-api.example.org/bot"})
    monkeypatch.delenv(LOCAL_ROOT, raising=False)
    text, issues = run()
    assert text.strip() == ""
    assert issues == []


def test_owner_switched_disk_reading_off(config, monkeypatch):
    config({**OWN_SERVER, "local_mode": False})
    monkeypatch.delenv(LOCAL_ROOT, raising=False)
    text, issues = run()
    assert "выключено" in text
    assert issues == []


def test_unreadable_config_does_not_break_doctor(monkeypatch):
    module = types.ModuleType("korra_cli.config")

    def explode():
        raise RuntimeError("конфиг не читается")

    module.load_config = explode
    monkeypatch.setitem(sys.modules, "korra_cli.config", module)
    text, issues = run()
    assert text.strip() == ""
    assert issues == []
