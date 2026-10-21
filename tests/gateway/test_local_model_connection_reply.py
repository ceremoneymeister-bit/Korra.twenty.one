"""Regression tests for #86570: gateway provider error connection messaging."""

import pytest

from gateway.run import (
    _GATEWAY_CONNECTION_ERROR_RE,
    _gateway_provider_error_reply,
    _looks_like_gateway_provider_error,
)


class TestGatewayConnectionErrorReply:
    def test_connection_error_strings_produce_specific_reply(self):
        samples = [
            "openai.APIConnectionError",
            "httpx.ConnectError: connection refused",
            "ConnectionError: [WinError 10061] No connection could be made",
            "Errno 111 Connection refused",
            "All connection attempts failed: Connection refused",
        ]
        for text in samples:
            assert _looks_like_gateway_provider_error(text), text
            reply = _gateway_provider_error_reply(text)
            assert "не отвечает" in reply.lower(), text
            assert 'выключен или недоступен' in reply, text

    def test_broad_connection_phrases_still_map_once_classified(self):
        """Reply selector keeps the full phrase set; the gate does not."""
        for text in (
            "cannot connect to http://127.0.0.1:8033/v1",
            "failed to establish a new connection",
        ):
            reply = _gateway_provider_error_reply(text)
            assert 'выключен или недоступен' in reply, text

    def test_prose_cannot_connect_is_not_a_provider_error(self):
        text = (
            "cannot connect to the office VPN from this cafe, "
            "so I used the backup notes instead"
        )
        assert not _looks_like_gateway_provider_error(text)

    def test_other_errors_keep_generic_reply(self):
        for text in (
            "RuntimeError: model returned empty content",
            "Exception: unknown provider",
            "HTTP 500 internal server error",
        ):
            if _looks_like_gateway_provider_error(text):
                reply = _gateway_provider_error_reply(text)
                assert "выключен или недоступен" not in reply, text

    def test_connection_regex_does_not_match_non_connection_error(self):
        assert not _GATEWAY_CONNECTION_ERROR_RE.search("Rate limited after 3 retries")
        assert not _GATEWAY_CONNECTION_ERROR_RE.search("Provider authentication failed")

    def test_auth_and_rate_limit_preserved(self):
        assert 'не удалось войти' in _gateway_provider_error_reply(
            "provider authentication failed"
        ).lower()
        assert 'ограничил частоту' in _gateway_provider_error_reply(
            "rate limited after 3 retries"
        ).lower()


class TestGatewaySubscriptionLimitReply:
    @pytest.mark.parametrize("text,reset", [
        ("API call failed after 3 retries: HTTP 429: You have hit your usage limit. Try again in 2 hours.", "2 hours"),
        ("Error code: 429 - insufficient_quota: You exceeded your current quota", None),
        ("HTTP 429: usage_limit_reached, resets at 14:30 UTC", "14:30 UTC"),
        ("HTTP 429: usage_limit_reached, resets at 2026-10-19T14:30:00Z", "2026-10-19T14:30:00Z"),
    ])
    def test_quota_exhaustion_is_reported_as_subscription_limit(self, text, reset):
        assert _looks_like_gateway_provider_error(text)
        reply = _gateway_provider_error_reply(text)
        assert "Лимит подписки модели исчерпан" in reply
        assert "Немного подождите" not in reply
        if reset:
            assert reset in reply

    def test_plain_rate_limit_keeps_short_wait_reply(self):
        reply = _gateway_provider_error_reply("rate limited after 3 retries")
        assert "ограничил частоту" in reply.lower()
        assert "Лимит подписки" not in reply

    def test_subscription_service_outage_is_not_quota_exhaustion(self):
        reply = _gateway_provider_error_reply("HTTP 503: subscription service unavailable")
        assert "Лимит подписки" not in reply

    def test_reset_notice_does_not_repeat_provider_instructions(self):
        reply = _gateway_provider_error_reply(
            "HTTP 429: usage_limit_reached. Try again after contacting private-support"
        )
        assert "Лимит подписки" in reply
        assert "private-support" not in reply
