"""Korra 0.21.16's retired integrations stay out of the shipped UI/runtime."""
from pathlib import Path

import yaml

RETIRED_CHANNELS = frozenset(
    "a2a buzz dingtalk feishu google_chat homeassistant irc line matrix "
    "mattermost ntfy photon raft simplex wecom wecom_callback".split()
)
RETIRED_PLUGINS = {
    "spotify", "google_meet", "teams_pipeline", "cron_providers/chronos",
    "observability/langfuse", "video_gen/deepinfra", "video_gen/fal", "video_gen/xai",
} | {f"platforms/{name}" for name in RETIRED_CHANNELS}
RETIRED_TOOLSETS = {"spotify", "homeassistant", "feishu_doc", "feishu_drive", "video_gen", "langfuse"}


def test_bundled_discovery_preserves_other_integrations():
    from korra_cli.plugins_cmd import _discover_all_plugins

    bundled = {key for _, _, _, source, _, key in _discover_all_plugins() if source == "bundled"}
    assert not bundled & RETIRED_PLUGINS
    assert {
        "platforms/telegram", "platforms/max", "platforms/discord", "platforms/slack",
        "platforms/teams", "platforms/whatsapp", "platforms/email",
        "image_gen/openai-codex", "image_gen/fal", "image_gen/deepinfra", "image_gen/xai",
        "web/ddgs", "web/exa", "web/parallel", "browser/browser_use",
    } <= bundled


def test_channels_catalog_does_not_offer_historical_enum_members():
    from gateway.config import Platform
    from korra_cli.web_server import _messaging_platform_catalog

    # Historical values must still deserialize, but not become setup cards.
    for channel in RETIRED_CHANNELS:
        assert Platform(channel).value == channel
    ids = {row["id"] for row in _messaging_platform_catalog()}
    assert not ids & RETIRED_CHANNELS
    assert {"telegram", "max", "whatsapp", "discord", "slack", "teams", "email"} <= ids


def test_setup_catalogs_keep_video_analysis_and_image_generation():
    from korra_cli.config import OPTIONAL_ENV_VARS
    from korra_cli.platforms import get_all_platforms
    from korra_cli.tools_config import TOOL_CATEGORIES, _get_effective_configurable_toolsets
    from toolsets import TOOLSETS

    assert not RETIRED_TOOLSETS & TOOL_CATEGORIES.keys()
    assert not RETIRED_TOOLSETS & TOOLSETS.keys()
    assert not RETIRED_TOOLSETS & {row[0] for row in _get_effective_configurable_toolsets()}
    assert not RETIRED_CHANNELS & get_all_platforms().keys()
    assert "video_analyze" in TOOLSETS["video"]["tools"]
    assert "image_generate" in TOOLSETS["image_gen"]["tools"]
    assert not {"HASS_TOKEN", "MATRIX_ACCESS_TOKEN", "HERMES_LANGFUSE_PUBLIC_KEY"} & OPTIONAL_ENV_VARS.keys()


def test_dashboard_api_offers_only_retained_bundled_integrations():
    from starlette.testclient import TestClient
    from korra_cli import web_server

    web_server._invalidate_plugins_hub_cache()
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    response = client.get("/api/dashboard/plugins/hub")
    assert response.status_code == 200
    rows = response.json()["plugins"]
    bundled_root = Path(__file__).resolve().parents[2] / "plugins"
    keys = {
        str(Path(row["path"]).relative_to(bundled_root))
        for row in rows if row["source"] == "bundled"
    }
    assert not keys & RETIRED_PLUGINS
    for provider in ("deepinfra", "fal", "xai"):
        assert f"image_gen/{provider}" in keys
        assert len([row for row in rows if row["name"] == provider]) == 1

    response = client.get("/api/messaging/platforms")
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["platforms"]}
    assert not ids & RETIRED_CHANNELS
    assert {"telegram", "max", "whatsapp", "discord", "slack", "teams", "email"} <= ids
    for channel in RETIRED_CHANNELS:
        response = client.put(f"/api/messaging/platforms/{channel}", json={"enabled": True})
        assert response.status_code == 404, channel

    response = client.get("/api/tools/toolsets")
    assert response.status_code == 200
    toolsets = {row["name"]: row for row in response.json()}
    assert not toolsets.keys() & RETIRED_TOOLSETS
    assert "video_analyze" in toolsets["video"]["tools"]
    assert "image_generate" in toolsets["image_gen"]["tools"]


def test_retired_cron_webhook_is_not_exposed():
    from korra_cli.web_server import app
    from gateway.platforms.api_server import APIServerAdapter
    from gateway.config import PlatformConfig

    assert "/api/cron/fire" not in {getattr(route, "path", "") for route in app.routes}
    adapter = APIServerAdapter(PlatformConfig())
    assert not any(path == "/api/cron/fire" for _, path, _ in adapter._http_route_table())


def test_previous_settings_are_preserved_and_builtin_cron_stays_available(tmp_path):
    from korra_constants import get_hermes_home
    from korra_cli.config import load_config, save_config
    from cron.scheduler_provider import resolve_cron_scheduler

    path = get_hermes_home() / "config.yaml"
    saved = {
        "platforms": {"matrix": {"enabled": True, "extra": {"homeserver": "https://example.invalid"}}},
        "plugins": {"enabled": ["spotify", "observability/langfuse"]},
        "video_gen": {"provider": "fal"},
        "cron": {"provider": "chronos"},
    }
    path.write_text(yaml.safe_dump(saved), encoding="utf-8")
    cfg = load_config()
    save_config(cfg)
    after = yaml.safe_load(path.read_text())
    for key, value in saved.items():
        assert after[key] == value
    scheduler = resolve_cron_scheduler()
    assert scheduler.name == "builtin"


def test_user_installed_plugin_is_not_deleted_or_hidden(tmp_path):
    from korra_constants import get_hermes_home
    from korra_cli.plugins_cmd import _discover_all_plugins

    plugin = get_hermes_home() / "plugins" / "spotify"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: spotify\nversion: '1.0'\n", encoding="utf-8")
    (plugin / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    rows = [row for row in _discover_all_plugins() if row[0] == "spotify"]
    assert len(rows) == 1
    assert rows[0][3] == "user"
    assert Path(rows[0][4]) == plugin
