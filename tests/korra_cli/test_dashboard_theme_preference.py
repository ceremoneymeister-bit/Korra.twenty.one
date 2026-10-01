import copy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from korra_cli import web_server as ws


@pytest.fixture
def pref_state(monkeypatch):
    state = {"dashboard": {}}
    writes = []
    monkeypatch.setattr(ws, "load_config", lambda: copy.deepcopy(state))
    def save(config, **_kwargs):
        writes.append(copy.deepcopy(config))
        state.clear()
        state.update(copy.deepcopy(config))
    monkeypatch.setattr(ws, "save_config", save)
    monkeypatch.setattr(ws, "get_install_id", lambda: "a" * 32)
    return state, writes


@pytest.mark.parametrize("value,expected", [(None, "light"), ("default", "light"),
    ("unknown", "light"), ("dark", "dark"), ("midnight", "dark")])
def test_theme_bootstrap_and_get_are_authoritative_without_healing(pref_state, value, expected):
    import asyncio
    state, writes = pref_state
    if value is not None:
        state["dashboard"]["theme"] = value
    before = copy.deepcopy(state)
    response = asyncio.run(ws.get_dashboard_themes())
    assert response["preference"]["theme"] == expected
    assert response["preference"]["installation_id"] == "a" * 32
    css = ws._render_active_theme_bootstrap_css()
    assert f"--background-base:{'#212121' if expected == 'dark' else '#e8e8e8'};" in css
    assert writes == [] and state == before


def test_theme_write_is_acknowledged_and_stale_revision_cannot_overwrite(pref_state):
    import asyncio
    from korra_cli.web_models import ThemeSetBody
    from fastapi import HTTPException
    state, writes = pref_state
    current = asyncio.run(ws.get_dashboard_themes())["preference"]
    ack = asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="dark", revision=current["revision"])))
    assert ack["preference"]["theme"] == "dark"
    assert ack["preference"]["revision"] != current["revision"]
    with pytest.raises(HTTPException) as err:
        asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="light", revision=current["revision"])))
    assert err.value.status_code == 409
    assert state["dashboard"]["theme"] == "dark" and len(writes) == 1


def test_actual_spa_contains_prepaint_server_preference(pref_state, tmp_path, monkeypatch):
    state, _ = pref_state
    state["dashboard"]["theme"] = "dark"
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<html><head></head><body><div id="root"></div></body></html>')
    monkeypatch.setattr(ws, "WEB_DIST", tmp_path)
    app = FastAPI()
    ws.mount_spa(app)
    response = TestClient(app).get("/", headers={"X-Forwarded-Prefix": "/c/test"})
    head = response.text.split("</head>")[0]
    assert "--background-base:#212121;" in head
    assert "window.__KORRA_THEME_PREF__=" in head
    assert '"theme":"dark"' in head
    assert '"base_path":"/c/test"' in head


@pytest.mark.parametrize("color", ["#5275D9", "#d95791", "#ffffee", "#000000"])
def test_color_round_trip_real_config_and_prepaint(tmp_path, monkeypatch, color):
    import yaml
    from korra_cli.dashboard_theme import color_theme
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ws, "get_install_id", lambda: "a" * 32)
    app = FastAPI()
    app.add_api_route("/api/dashboard/theme", ws.set_dashboard_theme, methods=["PUT"])
    app.add_api_route("/api/dashboard/themes", ws.get_dashboard_themes, methods=["GET"])
    assets = tmp_path / "dist"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text('<html><head></head><body><div id="root"></div></body></html>')
    monkeypatch.setattr(ws, "WEB_DIST", assets)
    ws.mount_spa(app)
    with TestClient(app) as client:
        before = client.get("/api/dashboard/themes").json()["preference"]
        saved = client.put("/api/dashboard/theme", json={"name": "color", "color": color, "revision": before["revision"]})
        assert saved.status_code == 200
        ack = saved.json()["preference"]
        assert ack["color"] == color.lower() and ack["theme"] == "color"
        assert ack["revision"] != before["revision"]
        assert yaml.safe_load((tmp_path / "config.yaml").read_text())["dashboard"]["theme_color"] == color.lower()
        # Another browser gets the same durable choice without a cache/cookie.
        with TestClient(app) as phone:
            assert phone.get("/api/dashboard/themes").json()["preference"] == ack
            html = phone.get("/", headers={"X-Forwarded-Prefix": "/c/test"}).text
        head = html.split("</head>")[0]
        tokens = color_theme(color)["neumorphism"]
        assert f'--neo-surface:{tokens["surface"]};' in head
        assert f'--neo-accent:{tokens["accent"]};' in head
        assert "linear-gradient(" in head and '"color":"' + color.lower() + '"' in head
        assert "color-scheme:light;" in head
        assert client.put("/api/dashboard/theme", json={"name": "color", "color": "#aabbcc", "revision": before["revision"]}).status_code == 409
        # An old client can still choose a neutral theme; retain its last color.
        assert client.put("/api/dashboard/theme", json={"name": "light"}).status_code == 200
        neutral = client.get("/api/dashboard/themes").json()["preference"]
        assert neutral["theme"] == "light" and neutral["color"] == color.lower()
        assert "linear-gradient(" not in client.get("/").text


@pytest.mark.parametrize("color", ["red", "#fff", "#12345678", "#12345g", "</style><script>alert(1)</script>", 42])
def test_rejects_invalid_color_payloads(color):
    from korra_cli.web_models import ThemeSetBody
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ThemeSetBody(name="color", color=color)


def test_manual_color_edits_invalidate_revision_and_invalid_config_is_safe():
    from korra_cli.dashboard_theme import preference, bootstrap_css, DEFAULT_COLOR
    config = {"dashboard": {"theme": "color", "theme_color": "#5275d9"}}
    before = preference(config, None, "owner")
    config["dashboard"]["theme_color"] = "#d95791"
    assert preference(config, None, "owner")["revision"] != before["revision"]
    config["dashboard"]["theme_color"] = "</style><script>alert(1)</script>"
    after = preference(config, None, "owner")
    assert after["color"] == DEFAULT_COLOR
    assert "<script>" not in bootstrap_css(after)


def test_managed_policy_cannot_ack_dropped_color(pref_state, monkeypatch):
    import asyncio
    from korra_cli.web_models import ThemeSetBody
    state, _ = pref_state
    def strip_color(config, **kwargs):
        state.clear()
        state.update(copy.deepcopy(config))
        state["dashboard"].pop("theme_color", None)
    monkeypatch.setattr(ws, "save_config", strip_color)
    with pytest.raises(ws.HTTPException) as error:
        asyncio.run(ws.set_dashboard_theme(ThemeSetBody(name="color", color="#d95791")))
    assert error.value.status_code == 409
