"""K21-294: кнопка «Отменить» под сообщением «Учёл…» в веб-чате."""

import json

import pytest

from agent.learning_receipt import DISPLAY_KIND, RECEIPT_KEY, build_review_receipt, snapshot_memory

SKILL_V1 = "---\nname: weekly-report\ndescription: weekly report\n---\n\n# Report\n\nVersion one.\n"
SKILL_V2 = SKILL_V1.replace("Version one.", "Version two.")


@pytest.fixture
def web(monkeypatch, _isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import korra_state
    from agent import skill_utils
    from korra_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app
    from korra_constants import get_hermes_home
    from tools import memory_tool, skill_ledger, skill_manager_tool, skill_usage

    home = get_hermes_home()
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(korra_state, "DEFAULT_DB_PATH", home / "state.db")
    monkeypatch.setattr(skill_ledger, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_usage, "get_hermes_home", lambda: home)
    monkeypatch.setattr(memory_tool, "get_hermes_home", lambda: home)
    monkeypatch.setattr(skill_manager_tool, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_utils, "get_all_skills_dirs", lambda: [skills_dir])
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return {"client": client, "skill_md": skills_dir / "weekly-report" / "SKILL.md"}


def _skill_notice(action, content):
    from tools.skill_manager_tool import skill_manage

    raw = skill_manage(action=action, name="weekly-report", content=content)
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {
            "name": "skill_manage",
            "arguments": json.dumps({"action": action, "name": "weekly-report"}),
        }}]},
        {"role": "tool", "tool_call_id": "c", "content": raw},
    ]
    return build_review_receipt(messages, [], snapshot_memory())


def _post_notice(receipt):
    from korra_state import SessionDB

    db = SessionDB()
    try:
        db.create_session("web-undo", source="api_server")
        return db.append_message(
            "web-undo", "assistant", "✅ Учёл", display_kind=DISPLAY_KIND,
            display_metadata={RECEIPT_KEY: receipt},
        )
    finally:
        db.close()


def _undo(web, row_id):
    return web["client"].post(f"/api/sessions/web-undo/messages/{row_id}/learning-undo")


def test_undo_restores_the_previous_skill_and_repeat_is_harmless(web):
    from tools.skill_manager_tool import skill_manage

    skill_manage(action="create", name="weekly-report", content=SKILL_V1)
    row_id = _post_notice(_skill_notice("edit", SKILL_V2))
    assert "Version two." in web["skill_md"].read_text()

    first = _undo(web, row_id)
    assert first.status_code == 200 and first.json()["status"] == "undone"
    assert "Version one." in web["skill_md"].read_text()

    again = _undo(web, row_id)
    assert again.json()["status"] == "already_undone"
    assert "Version one." in web["skill_md"].read_text()

    history = web["client"].get("/api/sessions/web-undo/messages").json()["messages"]
    assert history[0]["display_metadata"][RECEIPT_KEY]["undone"] is True


def test_undo_of_a_superseded_change_explains_and_keeps_newer_work(web):
    from tools.skill_manager_tool import skill_manage

    skill_manage(action="create", name="weekly-report", content=SKILL_V1)
    row_id = _post_notice(_skill_notice("edit", SKILL_V2))
    skill_manage(action="edit", name="weekly-report", content=SKILL_V2.replace("two", "three"))

    body = _undo(web, row_id).json()
    assert body["status"] == "conflict" and "Ничего не изменено" in body["message"]
    assert "three" in web["skill_md"].read_text()


def test_undo_of_unknown_message_is_a_clean_answer(web):
    from korra_state import SessionDB

    db = SessionDB()
    db.create_session("web-undo", source="api_server")
    row_id = db.append_message("web-undo", "assistant", "обычный ответ")
    db.close()
    body = _undo(web, row_id).json()
    assert body["ok"] is False and body["message"]
    assert web["client"].post("/api/sessions/nope/messages/1/learning-undo").status_code == 404
