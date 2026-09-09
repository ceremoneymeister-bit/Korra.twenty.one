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
