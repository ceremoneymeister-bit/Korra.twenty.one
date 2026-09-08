from __future__ import annotations

import io
import urllib.error
from types import SimpleNamespace

import pytest

from korra_cli import gateway_enroll


def _args(**overrides):
    values = {
        "token": None,
        "connector_url": None,
        "gateway_id": None,
        "wake_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_managed_install_message_is_russian_and_uses_korra(
    monkeypatch, capsys
):
    monkeypatch.setattr("korra_cli.config.is_managed", lambda: True)

    with pytest.raises(SystemExit) as exc_info:
        gateway_enroll.cmd_gateway_enroll(_args())

    output = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "управляемой установке" in output
    assert "`korra gateway enroll`" in output
    assert "hermes" not in output.lower()


def test_missing_enrollment_token_message_is_russian(monkeypatch, capsys):
    monkeypatch.setattr("korra_cli.config.is_managed", lambda: False)
    monkeypatch.delenv("GATEWAY_RELAY_ENROLL_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        gateway_enroll.cmd_gateway_enroll(_args())

    output = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "Нет токена подключения" in output
    assert "Одноразовый токен" in output
    assert "hermes" not in output.lower()


def test_relogin_guidance_is_russian_and_uses_korra(monkeypatch, capsys):
    from korra_cli.auth import AuthError

    monkeypatch.setattr("korra_cli.config.is_managed", lambda: False)
    monkeypatch.setattr(
        gateway_enroll,
        "_resolve_identity_token",
        lambda: (_ for _ in ()).throw(
            AuthError("expired", provider="nous", relogin_required=True)
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        gateway_enroll.cmd_gateway_enroll(
            _args(token="once", connector_url="https://relay.example")
        )

    output = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "Вы не вошли в Nous Portal" in output
    assert "`korra setup`" in output
    assert "`korra auth add nous`" in output
    assert "hermes" not in output.lower()


def test_connector_401_message_is_russian_and_uses_korra(monkeypatch):
    error = urllib.error.HTTPError(
        "https://relay.example/relay/enroll",
        401,
        "Unauthorized",
        {},
        io.BytesIO(b"{}"),
    )
    monkeypatch.setattr(
        gateway_enroll.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        gateway_enroll._post_enroll(
            connector_base_url="https://relay.example",
            access_token="access",
            enrollment_token="once",
            gateway_id="gateway-1",
        )

    message = str(exc_info.value)
    assert "Коннектор отклонил данные входа" in message
    assert "`korra auth add nous`" in message
    assert "hermes" not in message.lower()


def test_success_message_is_russian_and_keeps_secrets_hidden(
    tmp_path, monkeypatch, capsys
):
    saved = {}
    monkeypatch.setattr("korra_cli.config.is_managed", lambda: False)
    monkeypatch.setattr("korra_cli.config.save_env_value", saved.__setitem__)
    monkeypatch.setattr("korra_cli.config.get_env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(gateway_enroll, "_resolve_identity_token", lambda: "access")
    monkeypatch.setattr(
        gateway_enroll,
        "_post_enroll",
        lambda **_kwargs: {
            "secret": "top-secret",
            "deliveryKey": "delivery-secret",
            "tenant": "client-1",
            "gatewayId": "gateway-1",
        },
    )
    monkeypatch.setattr(
        gateway_enroll, "_warn_if_secondary_multiplex_profile", lambda: False
    )

    gateway_enroll.cmd_gateway_enroll(
        _args(token="once", connector_url="https://relay.example")
    )

    output = capsys.readouterr().out
    assert "Шлюз «gateway-1» подключён для клиента client-1" in output
    assert "Перезапустите шлюз" in output
    assert "top-secret" not in output
    assert "delivery-secret" not in output
    assert "<скрыто>" in output
    assert "hermes" not in output.lower()
    assert saved["GATEWAY_RELAY_SECRET"] == "top-secret"
    assert saved["GATEWAY_RELAY_DELIVERY_KEY"] == "delivery-secret"
