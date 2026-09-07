"""Отказ провайдера в чате панели объясняется словами владельца, не текстом движка.

Живой случай 07.09.2026 на первом клиентском контуре: выбран ``openai-codex``,
подписка ещё не подключена, и на первое сообщение чат показал «No Codex
credentials stored. Run `hermes auth` to authenticate. Run `hermes model` to
re-authenticate.» — английский текст с командами CLI, которые в панели выполнить
негде. Ветка отказа в ``_run_agent`` брала ``str(exc)`` как есть.

Движок заворачивает ``AuthError`` в ``RuntimeError`` с сохранённой цепочкой
``from`` (``gateway/run.py``), и панель добавляет свою обёртку. Здесь закреплено,
что панель находит исходную ошибку по цепочке и отвечает по её коду: подсказка,
где нажать, — а не команда, которую некуда ввести.
"""

from korra_cli.auth import AuthError
from gateway.platforms.api_server import (
    _describe_provider_auth_failure,
    _ProviderAuthResolutionError,
)


def _chain(auth: AuthError) -> _ProviderAuthResolutionError:
    """Та же обёртка, что на живом пути: AuthError → RuntimeError → ошибка панели."""
    try:
        try:
            raise auth
        except AuthError as inner:
            raise RuntimeError(f"{inner} Run `hermes model` to re-authenticate.") from inner
    except RuntimeError as wrapped:
        try:
            raise _ProviderAuthResolutionError(str(wrapped)) from wrapped
        except _ProviderAuthResolutionError as outer:
            return outer


class TestProviderAuthFailureText:
    def test_codex_without_login_points_to_keys_section(self):
        exc = _chain(AuthError(
            "No Codex credentials stored. Run `hermes auth` to authenticate.",
            provider="openai-codex", code="codex_auth_missing", relogin_required=True,
        ))
        text = _describe_provider_auth_failure(exc)
        assert "Подписка ChatGPT / Codex" in text
        assert "Ключи" in text and "Войти" in text
        assert "hermes" not in text.lower()
        assert "Run" not in text

    def test_codex_expired_refresh_token_is_the_same_hint(self):
        exc = _chain(AuthError(
            "Codex auth is missing refresh_token. Run `hermes auth` to re-authenticate.",
            provider="openai-codex", code="codex_auth_missing_refresh_token", relogin_required=True,
        ))
        assert "Подписка ChatGPT / Codex" in _describe_provider_auth_failure(exc)

    def test_no_provider_configured_keeps_engine_russian_text(self):
        exc = _chain(AuthError(
            "Провайдер ответа не настроен: добавьте ключ в разделе «Ключи».",
            code="no_provider_configured",
        ))
        assert _describe_provider_auth_failure(exc) == (
            "Провайдер ответа не настроен: добавьте ключ в разделе «Ключи»."
        )

    def test_other_provider_relogin_names_provider_and_keys(self):
        exc = _chain(AuthError(
            "xAI OAuth state is missing tokens. Re-authenticate with `hermes model`.",
            provider="xai-oauth", code="xai_oauth_missing", relogin_required=True,
        ))
        text = _describe_provider_auth_failure(exc)
        assert "«xai-oauth»" in text and "Ключи" in text
        assert "hermes" not in text.lower()

    def test_non_auth_failure_is_passed_through(self):
        exc = _ProviderAuthResolutionError("Failed to recreate closed OpenAI client")
        assert _describe_provider_auth_failure(exc) == "Failed to recreate closed OpenAI client"
