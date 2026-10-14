"""K21-207: visible-message paging through real profile stores and HTTP routes."""

from pathlib import Path

import pytest

from korra_state import SessionDB


@pytest.fixture
def homes(tmp_path, monkeypatch):
    # Exercise the real HOME-anchored profile resolver, not a mocked DB opener.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default = tmp_path / ".hermes"
    paths = {"default": default, "worker": default / "profiles" / "worker"}
    for home in paths.values():
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(default))
    return paths


@pytest.fixture
def client(homes, monkeypatch):
    from starlette.testclient import TestClient

    import korra_state
    from korra_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(korra_state, "DEFAULT_DB_PATH", homes["default"] / "state.db")
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    yield client
    client.close()


def _turn(tag, number, steps):
    rows = [{"role": "user", "content": f"{tag} question {number}"}]
    for step in range(steps):
        call_id = f"{tag}-{number}-{step}"
        rows.extend([
            {"role": "assistant", "content": f"checking {step}", "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "terminal", "arguments": "{}"},
            }]},
            {"role": "tool", "content": f"result {step}", "tool_call_id": call_id},
        ])
    rows.append({"role": "assistant", "content": f"{tag} answer {number}"})
    # Equal and regressing clocks must not affect the insertion-order cursor.
    for row in rows:
        row["timestamp"] = 1_700_000_000 - number
    return rows


def _seed(home, tag, steps, turns=80):
    with SessionDB(home / "state.db") as db:
        db.create_session("paged", source="dashboard")
        db.create_session("unrelated", source="dashboard")
        for number in range(turns):
            db.append_messages_batch("paged", _turn(tag, number, steps))
            # Global row IDs have gaps; they are not session-relative offsets.
            db.append_message("unrelated", "user", f"other {number}")
        return db.get_messages("paged")


def _page(client, before=None, profile=None):
    params = {"display_limit": 70}
    if before is not None:
        params["before_id"] = before
    if profile is not None:
        params["profile"] = profile
    response = client.get("/api/sessions/paged/messages", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_page_integrity(page, before):
    rows, pagination = page["messages"], page["pagination"]
    ids = [row["id"] for row in rows]
    assert ids == sorted(set(ids))
    assert pagination["display_limit"] == 70
    assert pagination["returned"] == len(rows)
    assert pagination["before_id"] == ids[0]
    assert all(row["session_id"] == "paged" and row["active"] == 1 for row in rows)
    if before is not None:
        assert ids[-1] < before
    calls = {call["id"] for row in rows for call in row.get("tool_calls") or []}
    assert all(row["tool_call_id"] in calls for row in rows if row["role"] == "tool")


@pytest.mark.parametrize("steps", [0, 3, 13])
def test_seventy_visible_messages_keep_complete_turns_and_all_older_rows(client, homes, steps):
    """160 bubbles, up to 2240 physical rows: each full page has 35 whole turns."""
    expected = _seed(homes["default"], "default", steps)
    pages, before = [], None
    for start, end in ((45, 80), (10, 45), (0, 10)):
        page = _page(client, before)
        _assert_page_integrity(page, before)
        rows = page["messages"]
        assert rows[0]["role"] == "user"
        assert rows[-1]["role"] == "assistant" and not rows[-1]["tool_calls"]
        assert [row["content"] for row in rows if row["role"] == "user"] == [
            f"default question {number}" for number in range(start, end)
        ]
        assert [row["content"] for row in rows if row["role"] == "assistant" and not row["tool_calls"]] == [
            f"default answer {number}" for number in range(start, end)
        ]
        assert len(rows) == (end - start) * (2 + steps * 2)
        assert page["pagination"]["has_more"] is (start > 0)
        pages.append(page)
        before = page["pagination"]["before_id"]
    # Compare every stored row, not just questions or final answers.
    assert [row for page in reversed(pages) for row in page["messages"]] == expected
    empty = _page(client, before)
    assert empty["messages"] == []
    assert empty["pagination"]["before_id"] is None
    assert empty["pagination"]["has_more"] is False
    assert empty["pagination"]["archived_before"] is False


def test_backward_cursor_stays_stable_when_new_turns_are_appended(client, homes):
    expected = _seed(homes["default"], "default", steps=7)
    first = _page(client)
    before = first["pagination"]["before_id"]
    with SessionDB(homes["default"] / "state.db") as db:
        db.append_messages_batch("paged", _turn("default", 80, steps=7))
    pages = [first]
    for _ in range(3):
        page = _page(client, before)
        _assert_page_integrity(page, before)
        pages.append(page)
        before = page["pagination"]["before_id"]
        if not page["pagination"]["has_more"]:
            break
    else:
        pytest.fail("backward pagination did not reach the start")
    assert [row for page in reversed(pages) for row in page["messages"]] == expected
    latest = _page(client)
    assert latest["messages"][-1]["content"] == "default answer 80"


def test_same_session_and_cursor_ids_do_not_mix_profile_histories(client, homes):
    expected = {name: _seed(home, name, steps=7) for name, home in homes.items()}
    # Both stores intentionally contain the same session and physical row IDs.
    assert [row["id"] for row in expected["default"]] == [row["id"] for row in expected["worker"]]
    pages = {None: [], "default": [], "worker": []}
    cursors = dict.fromkeys(pages)
    for _ in range(3):
        for profile in pages:
            page = _page(client, cursors[profile], profile)
            _assert_page_integrity(page, cursors[profile])
            pages[profile].append(page)
            cursors[profile] = page["pagination"]["before_id"]
    for profile, history in pages.items():
        assert history[-1]["pagination"]["has_more"] is False
        assert [row for page in reversed(history) for row in page["messages"]] == expected[profile or "default"]
    # A valid default-only session must not be borrowed on a named-profile miss.
    with SessionDB(homes["default"] / "state.db") as db:
        db.create_session("default-only", source="dashboard")
        db.append_message("default-only", "user", "private default question")
    assert client.get("/api/sessions/default-only/messages", params={
        "profile": "worker", "display_limit": 70,
    }).status_code == 404
    assert client.get("/api/sessions/paged/messages", params={
        "profile": "missing", "display_limit": 70,
    }).status_code == 404


def test_row_safety_cap_still_pages_every_tool_step_without_orphan_results(client, homes):
    """The intentional 1000-row cap can split turns, but must never lose rows."""
    expected = _seed(homes["default"], "default", steps=20)
    pages, before = [], None
    for _ in range(len(expected)):
        page = _page(client, before)
        _assert_page_integrity(page, before)
        assert len(page["messages"]) <= 1000
        assert page["messages"][0]["role"] in ("user", "assistant")
        pages.append(page)
        before = page["pagination"]["before_id"]
        if not page["pagination"]["has_more"]:
            break
    else:
        pytest.fail("capped pagination did not reach the start")
    assert pages[0]["messages"][0]["role"] == "assistant"
    assert sum(row["role"] == "user" for row in pages[0]["messages"]) < 35
    assert [row for page in reversed(pages) for row in page["messages"]] == expected
