"""Отложенная проверка доступности инструмента — это не авария.

При unresolved multiplex profile scope ``get_secret`` обязан отказать: без
scope он не знает, чей это профиль, и отдал бы то, что оказалось в окружении.
Отказ правильный, а вот его оформление — нет: availability-проверки Discord и
Home Assistant печатали в чистый boot полный traceback ``UnscopedSecretError``.
Владелец видит аварию там, где система сработала штатно, а настоящие ошибки
теряются среди этого шума.
"""

import logging

import pytest

from agent.secret_scope import UnscopedSecretError
import tools.registry as registry


def _unscoped():
    raise UnscopedSecretError("HASS_TOKEN read without a profile scope")


def _broken():
    raise RuntimeError("проверка сломалась по-настоящему")


def test_unscoped_secret_is_unavailable_without_a_traceback(caplog):
    caplog.set_level(logging.DEBUG, logger="tools.registry")
    assert registry._run_check_fn_uncached(_unscoped, unresolved_scope=True) is False
    assert not [record for record in caplog.records if record.exc_info]


def test_a_real_failure_still_shows_its_traceback(caplog):
    caplog.set_level(logging.DEBUG, logger="tools.registry")
    assert registry._run_check_fn_uncached(_broken, unresolved_scope=True) is False
    assert [record for record in caplog.records if record.exc_info]


def test_unscoped_secret_is_quiet_on_the_cached_path_too(caplog, monkeypatch):
    """Реальный boot идёт через кэш: при нерезолвнутом scope он обходит кэш."""
    monkeypatch.setattr(
        registry, "check_fn_cache_scope", lambda: registry.CHECK_FN_CACHE_BYPASS
    )
    caplog.set_level(logging.DEBUG, logger="tools.registry")
    assert registry._check_fn_cached(_unscoped) is False
    assert not [record for record in caplog.records if record.exc_info]


def test_the_tool_stays_unavailable_until_the_scope_arrives():
    """Запрет читать секрет без scope снимать нельзя — только шум."""
    with pytest.raises(UnscopedSecretError):
        _unscoped()
    assert registry._run_check_fn_uncached(_unscoped) is False
