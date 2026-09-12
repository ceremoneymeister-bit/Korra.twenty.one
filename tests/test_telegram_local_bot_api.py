"""Медиа с собственного сервера Bot API читается с диска, а не по HTTP.

Сервер `telegram-bot-api --local` на запрос файла отвечает абсолютным путём на
своём диске и по HTTP файлы не отдаёт. Контур, который всё же идёт за ними по
HTTP, получает 404 — владелец видит «InvalidToken», и повтор не помогает. Так
на трёх установках девять дней молча терялись все голосовые и фотографии.

Решение делится надвое: host-kit монтирует каталог сервера в контур только на
чтение и называет его в `KORRA_TELEGRAM_LOCAL_ROOT`, а адаптер по этому
признаку включает чтение с диска. Здесь проверяется вторая половина.
"""

import pytest

from plugins.platforms.telegram.adapter import resolve_telegram_local_mode


LOCAL_ROOT = "KORRA_TELEGRAM_LOCAL_ROOT"


def test_own_server_is_detected_without_config_edits(monkeypatch):
    monkeypatch.setenv(LOCAL_ROOT, "/opt/data/telegram-bot-api")
    assert resolve_telegram_local_mode({"base_url": "http://127.0.0.1:8081/bot"})


def test_cloud_telegram_keeps_http(monkeypatch):
    # Без своего сервера base_url не задан, и путь остаётся сетевым.
    monkeypatch.setenv(LOCAL_ROOT, "/opt/data/telegram-bot-api")
    assert not resolve_telegram_local_mode({})


def test_without_mounted_directory_nothing_changes(monkeypatch):
    # Каталог не примонтирован: читать с диска нечего, HTTP остаётся честнее.
    monkeypatch.delenv(LOCAL_ROOT, raising=False)
    assert not resolve_telegram_local_mode({"base_url": "http://127.0.0.1:8081/bot"})


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_announcement_is_not_a_mount(monkeypatch, value):
    monkeypatch.setenv(LOCAL_ROOT, value)
    assert not resolve_telegram_local_mode({"base_url": "http://127.0.0.1:8081/bot"})


def test_explicit_config_always_wins(monkeypatch):
    # Владелец мог выключить чтение с диска намеренно — например, когда сервер
    # бота стоит на другой машине и его каталог контуру не виден.
    monkeypatch.setenv(LOCAL_ROOT, "/opt/data/telegram-bot-api")
    assert not resolve_telegram_local_mode(
        {"base_url": "http://127.0.0.1:8081/bot", "local_mode": False}
    )
    monkeypatch.delenv(LOCAL_ROOT, raising=False)
    assert resolve_telegram_local_mode(
        {"base_url": "http://127.0.0.1:8081/bot", "local_mode": True}
    )
