"""Разговор из панели помечается в истории как разговор, а не как автоматизация.

Панель разговаривает с движком по тому же ``/v1/chat/completions``, что и любой
сторонний OpenAI-совместимый клиент, поэтому строка сессии получала источник
``api_server``. Вкладка «Чаты» в разделе «История» отбрасывает источники
автоматизации (``cron``, ``tool``, ``api_server``, …), и клиент, открыв историю
своих же разговоров, видел «Сессий пока нет» под счётчиком «3 Всего».

Механика уже была в движке: ``KORRA_SESSION_SOURCE`` в session-контексте
перекрывает источник, а ``_normalize_session_source`` держит список допустимых
значений (``dashboard`` в нём есть). Не хватало одного — панель никак не
называла себя. Теперь называет заголовком ``X-Korra-Session-Source``.
"""

import pytest

from gateway.session_context import clear_session_vars, get_session_env
from gateway.platforms.api_server import (
    SESSION_SOURCE_HEADER,
    APIServerAdapter,
)
from run_agent import _session_source_for_agent


class TestNormalizeSessionSource:
    def test_dashboard_is_accepted_as_is(self):
        assert APIServerAdapter._normalize_session_source("dashboard") == "dashboard"

    def test_browser_canonicalizes(self):
        assert (
            APIServerAdapter._normalize_session_source("browser") == "hermes_browser"
        )

    def test_unknown_value_falls_back_to_api_server(self):
        assert APIServerAdapter._normalize_session_source("что угодно") == "api_server"


class TestBindApiServerSession:
    def test_default_keeps_platform_as_source(self):
        """Без заголовка поведение прежнее: источник берётся из платформы."""
        tokens = APIServerAdapter._bind_api_server_session(
            chat_id="s1", session_key="s1", session_id="s1"
        )
        try:
            assert get_session_env("KORRA_SESSION_SOURCE", "") == ""
            assert _session_source_for_agent("api_server") == "api_server"
        finally:
            clear_session_vars(tokens)

    def test_declared_source_reaches_the_session_row(self):
        """Именно это значение ``run_agent`` кладёт в ``sessions.source``."""
        tokens = APIServerAdapter._bind_api_server_session(
            chat_id="s2",
            session_key="s2",
            session_id="s2",
            session_source="dashboard",
        )
        try:
            assert get_session_env("KORRA_SESSION_SOURCE", "") == "dashboard"
            assert _session_source_for_agent("api_server") == "dashboard"
            # Транспорт не меняется: правила доставки по-прежнему платформенные.
            assert get_session_env("KORRA_SESSION_PLATFORM", "") == "api_server"
        finally:
            clear_session_vars(tokens)


class TestDashboardProxyDeclaresItself:
    def test_proxy_header_name_matches_the_engine(self):
        """Одна договорённость на два процесса — имена обязаны совпадать."""
        from korra_cli.web_server import (
            _CHAT_SESSION_SOURCE,
            _CHAT_SESSION_SOURCE_HEADER,
        )

        assert _CHAT_SESSION_SOURCE_HEADER == SESSION_SOURCE_HEADER
        assert (
            APIServerAdapter._normalize_session_source(_CHAT_SESSION_SOURCE)
            == _CHAT_SESSION_SOURCE
        )

    def test_proxy_value_is_not_an_automation_source(self):
        """Значение обязано лежать вне списка автоматизации панели."""
        from korra_cli.web_server import _CHAT_SESSION_SOURCE

        automation = {
            "cron",
            "tool",
            "api_server",
            "acp",
            "hermes_flow",
            "vulcan_delegate",
            "webhook",
        }
        assert _CHAT_SESSION_SOURCE not in automation


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
