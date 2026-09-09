"""Tests for the password (non-redirect) dashboard-auth login flow.

Covers the protocol extension (``supports_password`` +
``complete_password_login``), the ``/auth/password-login`` route end-to-end
through the REAL ``gated_auth_middleware`` (session-cookie mint →
authenticated request → transparent refresh), the login-page credential
form rendering, and the route's rate limiter.

The E2E harness mirrors ``test_dashboard_auth_401_reauth.py``: register a
provider, flip ``app.state.auth_required = True``, drive a ``TestClient``.
"""

from __future__ import annotations

import time

import pytest

from fastapi.testclient import TestClient

from korra_cli import web_server
from korra_cli.dashboard_auth import (
    DashboardAuthProvider,
    InvalidCredentialsError,
    ProviderError,
    Session,
    assert_protocol_compliance,
    clear_providers,
    register_provider,
)
from korra_cli.dashboard_auth.cookies import SESSION_AT_COOKIE, SESSION_RT_COOKIE
from korra_cli.dashboard_auth.login_page import render_login_html
from korra_cli.dashboard_auth.routes import _reset_password_rate_limit
from tests.korra_cli.conftest_dashboard_auth import StubAuthProvider


# ---------------------------------------------------------------------------
# Test password provider — minimal, in-memory, signed tokens.
# ---------------------------------------------------------------------------


def _sign(secret: bytes, sub: str, kind: str, ttl: int) -> str:
    import base64
    import hashlib
    import hmac
    import json

    raw = json.dumps(
        {"sub": sub, "kind": kind, "exp": int(time.time()) + ttl},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + sig).decode()


def _unsign(secret: bytes, token: str):
    import base64
    import hashlib
    import hmac
    import json

    try:
        blob = base64.urlsafe_b64decode(token.encode())
        raw, sig = blob[:-32], blob[-32:]
        if not hmac.compare_digest(
            sig, hmac.new(secret, raw, hashlib.sha256).digest()
        ):
            return None
        return json.loads(raw)
    except Exception:
        return None


class PasswordProvider(DashboardAuthProvider):
    """In-test username/password provider (admin / hunter2)."""

    name = "testpw"
    display_name = "Test Password"
    supports_password = True

    def __init__(self, *, ttl: int = 3600, secret: bytes = b"test-secret-1234567890"):
        self._ttl = ttl
        self._secret = secret
        self.unreachable = False  # flip to simulate a ProviderError

    def start_login(self, *, redirect_uri: str):
        raise NotImplementedError

    def complete_login(self, **kwargs):
        raise NotImplementedError

    def complete_password_login(self, *, username: str, password: str) -> Session:
        if self.unreachable:
            raise ProviderError("backing store down")
        if username != "admin" or password != "hunter2":
            raise InvalidCredentialsError("bad creds")
        exp = int(time.time()) + self._ttl
        return Session(
            user_id="admin",
            email="",
            display_name="admin",
            org_id="",
            provider=self.name,
            expires_at=exp,
            access_token=_sign(self._secret, "admin", "access", self._ttl),
            refresh_token=_sign(self._secret, "admin", "refresh", 30 * 86400),
        )

    def verify_session(self, *, access_token: str):
        p = _unsign(self._secret, access_token)
        if not p or p.get("kind") != "access" or p["exp"] <= int(time.time()):
            return None
        return Session(
            user_id=p["sub"], email="", display_name=p["sub"], org_id="",
            provider=self.name, expires_at=p["exp"],
            access_token=access_token, refresh_token="",
        )

    def refresh_session(self, *, refresh_token: str) -> Session:
        from korra_cli.dashboard_auth import RefreshExpiredError

        p = _unsign(self._secret, refresh_token)
        if not p or p.get("kind") != "refresh" or p["exp"] <= int(time.time()):
            raise RefreshExpiredError("dead rt")
        exp = int(time.time()) + self._ttl
        return Session(
            user_id=p["sub"], email="", display_name=p["sub"], org_id="",
            provider=self.name, expires_at=exp,
            access_token=_sign(self._secret, p["sub"], "access", self._ttl),
            refresh_token=_sign(self._secret, p["sub"], "refresh", 30 * 86400),
        )

    def revoke_session(self, *, refresh_token: str) -> None:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pw_provider():
    return PasswordProvider()


@pytest.fixture
def gated_app(pw_provider):
    clear_providers()
    register_provider(pw_provider)
    _reset_password_rate_limit()
    prev_host = getattr(web_server.app.state, "bound_host", None)
    prev_port = getattr(web_server.app.state, "bound_port", None)
    prev_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.bound_host = "fly-app.fly.dev"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True
    client = TestClient(web_server.app, base_url="https://fly-app.fly.dev")
    yield client
    clear_providers()
    _reset_password_rate_limit()
    web_server.app.state.bound_host = prev_host
    web_server.app.state.bound_port = prev_port
    web_server.app.state.auth_required = prev_required


# ---------------------------------------------------------------------------
# Protocol extension
# ---------------------------------------------------------------------------


class TestProtocolExtension:
    def test_password_provider_is_protocol_compliant(self):
        assert assert_protocol_compliance(PasswordProvider) is None

    def test_default_supports_password_is_false(self):
        # OAuth providers (the Stub) inherit the False default.
        assert StubAuthProvider.supports_password is False

    def test_default_complete_password_login_raises_not_implemented(self):
        # A provider that doesn't override the method (the Stub) raises,
        # rather than silently accepting any credentials.
        with pytest.raises(NotImplementedError):
            StubAuthProvider().complete_password_login(
                username="x", password="y"
            )


# ---------------------------------------------------------------------------
# /api/auth/providers exposes the supports_password flag
# ---------------------------------------------------------------------------


class TestProviderListFlag:
    def test_providers_endpoint_reports_supports_password(self, gated_app):
        resp = gated_app.get("/api/auth/providers")
        assert resp.status_code == 200
        prov = {p["name"]: p for p in resp.json()["providers"]}
        assert prov["testpw"]["supports_password"] is True

    def test_password_provider_html_redirects_to_login_form(self, gated_app):
        resp = gated_app.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login?next=%2F"

        login = gated_app.get(resp.headers["location"])
        assert login.status_code == 200
        assert '<form class="provider-form" data-provider="testpw"' in login.text
        assert "/auth/password-login" in login.text


    def test_oauth_provider_reports_false(self):
        clear_providers()
        register_provider(StubAuthProvider())
        prev = getattr(web_server.app.state, "auth_required", None)
        web_server.app.state.auth_required = True
        try:
            client = TestClient(
                web_server.app, base_url="https://fly-app.fly.dev"
            )
            resp = client.get("/api/auth/providers")
            prov = {p["name"]: p for p in resp.json()["providers"]}
            assert prov["stub"]["supports_password"] is False
        finally:
            clear_providers()
            web_server.app.state.auth_required = prev


# ---------------------------------------------------------------------------
# /auth/password-login — end-to-end through the real middleware
# ---------------------------------------------------------------------------


class TestPasswordLoginRoute:
    def test_valid_credentials_set_session_cookies_and_return_next(
        self, gated_app
    ):
        resp = gated_app.post(
            "/auth/password-login",
            json={
                "provider": "testpw",
                "username": "admin",
                "password": "hunter2",
                "next": "/sessions",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "next": "/sessions"}
        set_cookie = resp.headers.get("set-cookie", "")
        # HTTPS request → __Host- prefixed access-token cookie is set.
        assert SESSION_AT_COOKIE in set_cookie
        assert SESSION_RT_COOKIE in set_cookie

    def test_valid_credentials_prefix_post_login_landing(self, gated_app):
        resp = gated_app.post(
            "/auth/password-login",
            headers={"x-forwarded-prefix": "/hermes"},
            json={
                "provider": "testpw",
                "username": "admin",
                "password": "hunter2",
                "next": "/sessions",
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "next": "/hermes/sessions"}
        assert "Path=/hermes" in resp.headers.get("set-cookie", "")

    def test_prefixed_landing_with_query_is_not_double_prefixed(self, gated_app):
        resp = gated_app.post(
            "/auth/password-login",
            headers={"x-forwarded-prefix": "/hermes"},
            json={
                "provider": "testpw",
                "username": "admin",
                "password": "hunter2",
                "next": "/hermes?tab=1",
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "next": "/hermes?tab=1"}

    @pytest.mark.parametrize(
        "unsafe_next",
        [r"/\\evil.example", "%2F%5Cevil.example", "/%0A/evil.example"],
    )
    def test_valid_credentials_reject_open_redirect_landing(
        self, gated_app, unsafe_next
    ):
        resp = gated_app.post(
            "/auth/password-login",
            json={
                "provider": "testpw",
                "username": "admin",
                "password": "hunter2",
                "next": unsafe_next,
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "next": "/"}

    def test_session_cookie_then_grants_authenticated_access(self, gated_app):
        # Log in, then hit an auth-required endpoint with the cookie jar
        # the TestClient retains — proving the minted session is accepted
        # by the real gated_auth_middleware.
        login = gated_app.post(
            "/auth/password-login",
            json={"provider": "testpw", "username": "admin", "password": "hunter2"},
        )
        assert login.status_code == 200
        me = gated_app.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user_id"] == "admin"
        assert me.json()["provider"] == "testpw"

    def test_wrong_password_returns_generic_401(self, gated_app):
        resp = gated_app.post(
            "/auth/password-login",
            json={"provider": "testpw", "username": "admin", "password": "WRONG"},
        )
        assert resp.status_code == 401
        # Generic detail — no user-vs-password distinction.
        assert resp.json()["detail"] == "Invalid credentials"
        assert "set-cookie" not in {k.lower() for k in resp.headers}




# ---------------------------------------------------------------------------
# Transparent refresh — expired access token, live refresh token
# ---------------------------------------------------------------------------


class TestPasswordSessionRefresh:
    def test_expired_access_token_refreshes_via_rt_cookie(self):
        # TTL=0 → access token born expired; the RT cookie should drive a
        # transparent refresh on the next request (the same machinery the
        # OAuth provider uses).
        clear_providers()
        provider = PasswordProvider(ttl=0)
        register_provider(provider)
        _reset_password_rate_limit()
        prev = getattr(web_server.app.state, "auth_required", None)
        web_server.app.state.auth_required = True
        try:
            client = TestClient(
                web_server.app, base_url="https://fly-app.fly.dev"
            )
            login = client.post(
                "/auth/password-login",
                json={"provider": "testpw", "username": "admin", "password": "hunter2"},
            )
            assert login.status_code == 200
            # Give the provider a live TTL so the refreshed token verifies.
            provider._ttl = 3600
            me = client.get("/api/auth/me")
            assert me.status_code == 200
            assert me.json()["user_id"] == "admin"
        finally:
            clear_providers()
            _reset_password_rate_limit()
            web_server.app.state.auth_required = prev


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_repeated_failures_eventually_429(self, gated_app):
        # The limiter caps attempts per IP per window (default 10). After
        # the budget is exhausted, even a VALID credential gets 429.
        last = None
        for _ in range(15):
            last = gated_app.post(
                "/auth/password-login",
                json={"provider": "testpw", "username": "admin", "password": "WRONG"},
            )
        assert last.status_code == 429
        # Even correct creds are throttled once the window is saturated.
        good = gated_app.post(
            "/auth/password-login",
            json={"provider": "testpw", "username": "admin", "password": "hunter2"},
        )
        assert good.status_code == 429


# ---------------------------------------------------------------------------
# Login page rendering
# ---------------------------------------------------------------------------


class TestLoginPageRender:
    def test_password_provider_renders_credential_form_and_script(self):
        clear_providers()
        register_provider(PasswordProvider())
        try:
            html = render_login_html(next_path="/sessions")
            assert '<html lang="ru">' in html
            assert "Korra21" in html
            from korra_cli.dashboard_auth.login_page import current_greeting
            assert current_greeting() in html
            # Слово «контур» с входа снято по решению владельца 09.09.2026.
            assert "Контур" not in html
            # Слева знак и сменяющиеся строки, справа область входа —
            # композиция входа Claude, к которой владелец вернулся 09.09.2026.
            for line in (
                "Чтобы твои идеи не оставались идеями",
                "Дай своим идеям место в реальности",
                "Реальность ждёт твоих творений",
                "Мир ещё не видел того, что ты создашь",
                "То, чего ещё нет, начинается с тебя",
            ):
                assert line in html, f"пропала строка слогана: {line}"
            # Ротация — на CSS: страница входа обязана обходиться без скриптов,
            # иначе OAuth-вариант (он рендерится script-free) остался бы с
            # пятью наложенными фразами.
            assert "@keyframes tagline" in html
            # Запрет анимаций гасит движение, но фразы обязаны продолжать
            # сменяться: иначе на «Уменьшить движение» страница навсегда
            # застывала бы на первой строке.
            assert "@keyframes tagline-plain" in html
            assert "animation-name: tagline-plain" in html
            assert "prefers-reduced-motion" in html
            assert 'class="login-shell"' in html
            assert 'class="identity"' in html
            assert 'class="card"' in html
            assert '<form class="provider-form" data-provider="testpw"' in html
            assert 'name="username"' in html
            assert 'name="password"' in html
            assert "Логин" in html
            assert "Пароль" in html
            assert ">Войти</button>" in html
            # Подписи-дубля над формой больше нет: минималистичный вход
            # обходится знаком, заголовком и placeholder-ами полей.
            assert "Вход по логину и паролю" not in html
            assert 'placeholder="Логин"' in html
            assert 'placeholder="Пароль"' in html
            assert "Проверьте логин и пароль" in html
            assert 'aria-describedby="login-error-testpw"' in html
            assert 'id="login-error-testpw" role="alert"' in html
            assert 'value="/sessions"' in html
            assert "<script>" in html
            assert "/auth/password-login" in html
        finally:
            clear_providers()

    def test_login_page_is_light_only_and_wears_the_brand_lockup(self):
        """Решение владельца 09.09.2026: светлая тема по умолчанию в любом
        интерфейсе, а «KORRA 21» на входе — наши знаки картинками. Вход не
        имеет переключателя тем, поэтому тёмных токенов тут быть не должно."""
        clear_providers()
        register_provider(PasswordProvider())
        try:
            html = render_login_html()

            assert "color-scheme: light" in html
            assert "--canvas: #e8e8e8" in html
            assert "color-scheme: dark" not in html
            assert "--canvas: #212121" not in html
            assert 'content="#e8e8e8"' in html
            assert '<img class="brand-word" src="/brand/korra-wordmark.png"' in html
            assert '<img class="brand-number" src="/brand/korra-21.png"' in html
            assert '<span class="brand-word">KORRA</span>' not in html
        finally:
            clear_providers()

    def test_provider_setup_page_is_light_and_uses_the_same_lockup(self):
        """Страница «Настройте доступ» (провайдеров нет) — та же светлая тема:
        именно её видит владелец контура, если конфиг входа ещё не собран."""
        clear_providers()
        html = render_login_html()

        assert "Настройте доступ" in html
        assert "color-scheme: light" in html
        assert "color-scheme: dark" not in html
        assert 'src="/brand/korra-wordmark.png"' in html

    def test_oauth_only_page_stays_script_free(self):
        clear_providers()
        register_provider(StubAuthProvider())
        try:
            html = render_login_html()
            assert "provider-btn" in html
            assert "Продолжить через Stub" in html
            assert "<script>" not in html
            # No password FORM element rendered (the .provider-form CSS
            # rule lives in the template's <style> block unconditionally;
            # what must be absent is an actual rendered form + its script).
            assert '<form class="provider-form"' not in html
            assert "/auth/password-login" not in html
        finally:
            clear_providers()

    def test_password_form_uses_proxy_prefix_for_submit_and_font(self):
        clear_providers()
        register_provider(PasswordProvider())
        try:
            html = render_login_html(base_path="/cabinet/client")
            assert 'action="/cabinet/client/auth/password-login"' in html
            assert "url('/cabinet/client/fonts/Onest-Variable.woff2')" in html
            assert "fetch(form.action" in html
        finally:
            clear_providers()

    @pytest.mark.parametrize("unsafe_prefix", [r"/\evil.example", "/&#92;evil.example"])
    def test_password_form_prefix_cannot_escape_the_current_origin(
        self, unsafe_prefix
    ):
        import re
        from html import unescape
        from urllib.parse import urljoin, urlsplit

        clear_providers()
        register_provider(PasswordProvider())
        try:
            page = render_login_html(base_path=unsafe_prefix)
            action_match = re.search(r'action="([^"]+)"', page)
            assert action_match is not None
            action = unescape(action_match.group(1))
            resolved = urlsplit(urljoin("https://korra.example/login", action))
            assert resolved.netloc == "korra.example"
        finally:
            clear_providers()


# ---------------------------------------------------------------------------
# Приветствие по времени суток (выбор владельца 09.09.2026)
# ---------------------------------------------------------------------------


class TestGreeting:
    @pytest.mark.parametrize(
        ("hour", "expected"),
        [
            (0, "Доброй ночи"), (4, "Доброй ночи"),
            (5, "Доброе утро"), (11, "Доброе утро"),
            (12, "Добрый день"), (17, "Добрый день"),
            (18, "Добрый вечер"), (22, "Добрый вечер"),
            (23, "Доброй ночи"),
        ],
    )
    def test_boundaries(self, hour, expected):
        from korra_cli.dashboard_auth.login_page import greeting_for_hour

        assert greeting_for_hour(hour) == expected

    def test_greeting_follows_the_contour_timezone_not_the_container_clock(
        self, monkeypatch
    ):
        """Контейнер живёт в UTC, а владелец — в своём поясе. Приветствие обязано
        идти от korra_time (HERMES_TIMEZONE / timezone в конфиге), иначе рабочее
        утро клиента встречает «Доброй ночи»."""
        import korra_time
        from korra_cli.dashboard_auth import login_page

        monkeypatch.setenv("HERMES_TIMEZONE", "Asia/Novosibirsk")
        korra_time.reset_cache()
        try:
            from datetime import datetime, timezone

            # 23:30 UTC — 06:30 следующего дня в Новосибирске.
            monkeypatch.setattr(
                korra_time,
                "now",
                lambda: datetime(2026, 9, 9, 23, 30, tzinfo=timezone.utc).astimezone(
                    korra_time.get_timezone()
                ),
            )
            assert login_page.current_greeting() == "Доброе утро"
        finally:
            korra_time.reset_cache()

    def test_broken_clock_never_breaks_the_login(self, monkeypatch):
        import korra_time
        from korra_cli.dashboard_auth import login_page

        def boom():
            raise RuntimeError("clock unavailable")

        monkeypatch.setattr(korra_time, "now", boom)

        assert login_page.current_greeting() in {
            "Доброе утро", "Добрый день", "Добрый вечер", "Доброй ночи",
        }
