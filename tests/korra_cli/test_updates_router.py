"""Раздел «Обновления»: панель показывает и принимает просьбу, но не обновляет.

Главное свойство маршрутов — они безопасны для контура: ни один из них не
может ни перезапустить контейнер, ни изменить образ. Тесты закрепляют именно
это, а заодно поведение экрана в неприятных случаях: чужие данные в карточке
выпуска, устаревшая просьба, битые файлы состояния.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from korra_cli import web_server
from korra_cli.web_routers import updates as updates_routes


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Свой HERMES_HOME: тесты не должны трогать домашний каталог машины."""
    monkeypatch.setattr(updates_routes, "get_hermes_home", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    return TestClient(web_server.app)


@pytest.fixture
def auth():
    return {"X-Hermes-Session-Token": web_server._SESSION_TOKEN}


RELEASE = {
    "release_id": "K21-2026.09.09",
    "title": "Тестовый выпуск",
    "published_at": "09.09.2026",
    "summary": "Одно предложение.",
    "sections": [{"heading": "Что нового", "items": [{"title": "Пункт", "detail": "Пояснение"}]}],
    "pause": "около минуты",
}


def announce(client, auth, **body):
    response = client.post("/api/updates/announce", headers=auth, json=body)
    assert response.status_code == 200, response.text
    return response


def state(client, auth):
    response = client.get("/api/updates/state", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_state_describes_installed_release_without_any_announcement(home, client, auth):
    body = state(client, auth)
    # Что стоит — известно из заметок внутри образа, без обращения наружу.
    assert body["installed"]["release_id"].startswith("K21-")
    assert body["installed"]["version"]
    assert body["available"] is None and body["up_to_date"] is True
    assert body["request"] is None and body["progress"] is None
    assert [step["key"] for step in body["steps"]] == ["fetch", "drain", "backup", "switch", "check"]


def test_announced_release_becomes_available(home, client, auth):
    announce(client, auth, release=RELEASE)
    body = state(client, auth)
    assert body["available"]["release_id"] == "K21-2026.09.09"
    assert body["available"]["sections"][0]["items"][0]["title"] == "Пункт"
    assert body["up_to_date"] is False


def test_announced_release_equal_to_installed_is_not_offered(home, client, auth):
    installed = state(client, auth)["installed"]["release_id"]
    announce(client, auth, release={**RELEASE, "release_id": installed})
    body = state(client, auth)
    assert body["available"] is None and body["up_to_date"] is True


def test_announcement_is_cleaned_before_it_reaches_the_screen(home, client, auth):
    announce(client, auth, release={
        "release_id": "K21-2026.09.09",
        "title": "x" * 900,
        "summary": {"не": "строка"},
        "sections": [{"heading": "Раздел", "items": ["строкой", {"title": "y" * 900, "detail": 5}, 42]}],
        "лишнее": "поле",
    })
    available = state(client, auth)["available"]
    assert len(available["title"]) == 200
    assert available["summary"] == ""
    items = available["sections"][0]["items"]
    assert items[0] == {"title": "строкой", "detail": ""}
    assert len(items[1]["title"]) == 200 and items[1]["detail"] == ""
    assert len(items) == 2  # число выброшено
    assert "лишнее" not in available


def test_oversized_announcement_is_refused(home, client, auth):
    response = client.post("/api/updates/announce", headers=auth,
                           json={"release": {"release_id": "K21-X", "summary": "д" * 70000}})
    assert response.status_code == 413
    assert not (home / "update-center" / "available.json").exists()


def test_request_is_saved_and_visible(home, client, auth):
    announce(client, auth, release=RELEASE)
    response = client.post("/api/updates/request", headers=auth, json={"release_id": "K21-2026.09.09"})
    assert response.status_code == 200
    saved = json.loads((home / "update-center" / "request.json").read_text(encoding="utf-8"))
    assert saved["release_id"] == "K21-2026.09.09" and saved["source"] == "panel"
    assert state(client, auth)["request"]["release_id"] == "K21-2026.09.09"


def test_request_without_available_release_is_refused(home, client, auth):
    response = client.post("/api/updates/request", headers=auth, json={})
    assert response.status_code == 409
    assert "недоступно" in response.json()["detail"]


def test_request_for_another_release_is_refused(home, client, auth):
    announce(client, auth, release=RELEASE)
    response = client.post("/api/updates/request", headers=auth, json={"release_id": "K21-чужой"})
    assert response.status_code == 409
    assert not (home / "update-center" / "request.json").exists()


def test_request_during_a_running_operation_is_refused(home, client, auth):
    announce(client, auth, release=RELEASE, progress={"status": "running", "phase": "backup"})
    response = client.post("/api/updates/request", headers=auth, json={})
    assert response.status_code == 409 and "уже идёт" in response.json()["detail"]


def test_stale_request_is_marked_when_the_release_moved_on(home, client, auth):
    announce(client, auth, release=RELEASE)
    client.post("/api/updates/request", headers=auth, json={})
    announce(client, auth, release={**RELEASE, "release_id": "K21-2026.09.10"})
    assert state(client, auth)["request"]["stale"] is True


def test_expired_request_disappears(home, client, auth):
    announce(client, auth, release=RELEASE)
    client.post("/api/updates/request", headers=auth, json={})
    path = home / "update-center" / "request.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["requested_at"] = time.time() - updates_routes.REQUEST_TTL_SECONDS - 1
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert state(client, auth)["request"] is None


def test_request_can_be_cancelled(home, client, auth):
    announce(client, auth, release=RELEASE)
    client.post("/api/updates/request", headers=auth, json={})
    assert client.delete("/api/updates/request", headers=auth).status_code == 200
    assert state(client, auth)["request"] is None


def test_dispatching_clears_the_request(home, client, auth):
    announce(client, auth, release=RELEASE)
    client.post("/api/updates/request", headers=auth, json={})
    announce(client, auth, progress={"status": "running", "phase": "fetch"}, clear_request=True)
    body = state(client, auth)
    assert body["request"] is None and body["progress"]["status"] == "running"


@pytest.mark.parametrize(
    "phase,step",
    [("fetch", "fetch"), ("remote_draining", "drain"), ("backup", "backup"),
     ("schema_rehearsal", "backup"), ("recreate", "switch"), ("smoke", "check"),
     ("rollback_restore", "switch"), ("небывалая", "fetch")],
)
def test_updater_phases_map_to_owner_visible_steps(home, client, auth, phase, step):
    announce(client, auth, progress={"status": "running", "phase": phase})
    assert state(client, auth)["progress"]["step"] == step


def test_progress_says_when_the_target_is_already_installed(home, client, auth):
    installed = state(client, auth)["installed"]["release_id"]
    announce(client, auth, progress={"status": "succeeded", "phase": "complete", "release_id": installed})
    progress = state(client, auth)["progress"]
    assert progress["final"] is True and progress["installed_target"] is True
    assert progress["message"] == "Готово. Установлена новая версия."


def test_unknown_status_is_ignored_rather_than_shown(home, client, auth):
    announce(client, auth, progress={"status": "чтотовроде", "phase": "fetch"})
    assert state(client, auth)["progress"] is None


def test_broken_state_files_do_not_break_the_screen(home, client, auth):
    directory = home / "update-center"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("available.json", "request.json", "progress.json"):
        (directory / name).write_text("{это не json", encoding="utf-8")
    body = state(client, auth)
    assert body["available"] is None and body["request"] is None and body["progress"] is None
    assert body["installed"]["release_id"]


def test_routes_are_closed_without_the_panel_session(home, client):
    assert client.get("/api/updates/state").status_code == 401
    assert client.post("/api/updates/request", json={}).status_code == 401
    assert client.post("/api/updates/announce", json={}).status_code == 401
