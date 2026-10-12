"""K21-288 (д): второй Telegram-бот профиля ходит на тот же локальный Bot API, что и корневой."""
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner


@pytest.fixture
def make(monkeypatch):
    def build(root_extra, own_extra):
        root = PlatformConfig(enabled=True, token="root-token", extra=dict(root_extra))
        own = PlatformConfig(enabled=True, token="own-token", extra=dict(own_extra))
        runner = GatewayRunner.__new__(GatewayRunner)
        runner.config = GatewayConfig(platforms={Platform.TELEGRAM: root})
        monkeypatch.setattr(
            "gateway.platform_registry.platform_registry.is_registered",
            lambda name: True,
        )
        monkeypatch.setattr(
            "gateway.platform_registry.platform_registry.create_adapter",
            lambda name, cfg: SimpleNamespace(config=cfg),
        )
        return runner, root, own

    return build


def test_secondary_bot_inherits_local_server_from_root(make):
    runner, _root, own = make(
        {"base_url": "http://127.0.0.1:8081/bot", "base_file_url": "http://127.0.0.1:8081/file/bot", "local_mode": True},
        {},
    )
    adapter = runner._create_adapter(Platform.TELEGRAM, own)
    assert adapter.config.extra["base_url"] == "http://127.0.0.1:8081/bot"
    assert adapter.config.extra["base_file_url"] == "http://127.0.0.1:8081/file/bot"
    assert adapter.config.extra["local_mode"] is True
    assert adapter.config.token == "own-token"


def test_secondary_bot_with_own_server_keeps_its_transport(make):
    runner, _root, own = make(
        {"base_url": "http://127.0.0.1:8081/bot", "local_mode": True},
        {"base_url": "http://other:9000/bot"},
    )
    adapter = runner._create_adapter(Platform.TELEGRAM, own)
    assert adapter.config.extra["base_url"] == "http://other:9000/bot"
    assert "local_mode" not in adapter.config.extra


def test_explicit_local_mode_false_of_secondary_is_respected(make):
    runner, _root, own = make(
        {"base_url": "http://127.0.0.1:8081/bot", "local_mode": True},
        {"local_mode": False},
    )
    adapter = runner._create_adapter(Platform.TELEGRAM, own)
    assert adapter.config.extra["base_url"] == "http://127.0.0.1:8081/bot"
    assert adapter.config.extra["local_mode"] is False


def test_root_without_local_server_changes_nothing(make):
    runner, _root, own = make({}, {})
    adapter = runner._create_adapter(Platform.TELEGRAM, own)
    assert "base_url" not in adapter.config.extra
    assert "local_mode" not in adapter.config.extra


def test_other_platforms_are_not_touched(make):
    runner, _root, own = make({"base_url": "http://127.0.0.1:8081/bot"}, {})
    adapter = runner._create_adapter(Platform.DISCORD, own)
    assert "base_url" not in adapter.config.extra
