"""The deletion dialog must match real database, memory and file behavior."""
from fastapi.testclient import TestClient

from korra_state import SessionDB


def test_delete_removes_only_target_history_and_keeps_saved_memory_and_files(tmp_path, monkeypatch):
    from korra_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    home = tmp_path / "home"
    worker = home / "profiles" / "worker"
    other = home / "profiles" / "other"
    for profile in (home, worker, other):
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "config.yaml").write_text("{}")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("korra_constants.get_default_hermes_root", lambda: home)
    for profile in (worker, other):
        with SessionDB(db_path=profile / "state.db") as db:
            for session in ("delete-me", "keep-me"):
                db.create_session(session, source="api_server", profile_name=profile.name)
                db.append_message(session, role="user", content="Уникальная договорённость")
    preserved = {
        worker / "memories" / "MEMORY.md": "Сохранённый факт",
        worker / "memories" / "USER.md": "Предпочтения владельца",
        worker / "workspace" / "result.txt": "Отдельно сохранённый документ",
        worker / "sessions" / "delete-me.jsonl": '{"content":"technical transcript"}',
    }
    for path, content in preserved.items():
        path.parent.mkdir(exist_ok=True)
        path.write_text(content)

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    params = {"profile": "worker", "q": "договорённость"}
    try:
        before = client.get("/api/sessions/search", params=params)
        assert before.status_code == 200, before.text
        assert {row["id"] for row in before.json()["results"]} == {"delete-me", "keep-me"}
        result = client.delete("/api/sessions/delete-me", params={"profile": "worker"})
        assert result.status_code == 200, result.text
        after = client.get("/api/sessions/search", params=params)
        assert {row["id"] for row in after.json()["results"]} == {"keep-me"}
        listing = client.get("/api/sessions", params={"profile": "worker"})
        assert {row["id"] for row in listing.json()["sessions"]} == {"keep-me"}
        with SessionDB(db_path=worker / "state.db") as db:
            assert db.get_session("delete-me") is None
            assert db.get_messages("delete-me") == []
            assert db.get_session("keep-me") is not None
        with SessionDB(db_path=other / "state.db") as db:
            assert db.get_session("delete-me") is not None
        assert {path: path.read_text() for path in preserved} == preserved
    finally:
        client.close()
