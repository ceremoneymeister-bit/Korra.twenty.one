import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from korra_cli import web_server
from korra_cli.web_routers.sessions import search_router
from korra_state import SessionDB


class _FakeSessionDB:
    """Fake backing the /api/sessions/search endpoint.

    The endpoint surfaces direct session-id matches first, then FTS message
    matches, deduping both by compression lineage root. This fake has no
    compression chains (get_session returns no parent), so each session is its
    own lineage root.
    """

    closed = False
    opened_read_only = None
    requested_fields = None

    def __init__(self, *args, **kwargs):
        type(self).opened_read_only = kwargs.get("read_only")

    @staticmethod
    def _source_allowed(row, source=None, sources=None, exclude_sources=None):
        row_source = row.get("source")
        if source and row_source != source:
            return False
        if sources and row_source not in sources:
            return False
        if exclude_sources and row_source in exclude_sources:
            return False
        return True

    def search_sessions_by_id(
        self,
        query,
        limit=20,
        include_archived=True,
        source=None,
        sources=None,
        exclude_sources=None,
    ):
        assert query == "20260603"
        assert include_archived is True
        rows = [
            {
                "id": "20260603_090200_exact",
                "preview": "ID match preview",
                "source": "cli",
                "model": "claude",
                "started_at": 100,
            }
        ]
        return [
            row
            for row in rows
            if self._source_allowed(
                row, source=source, sources=sources, exclude_sources=exclude_sources
            )
        ][:limit]

    def search_messages(
        self,
        query,
        source_filter=None,
        exclude_sources=None,
        limit=20,
        fields=None,
    ):
        assert query == "20260603*"
        type(self).requested_fields = fields
        rows = [
            {
                "session_id": "20260603_090200_exact",
                "snippet": "duplicate content hit should not replace ID hit",
                "role": "user",
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]
        return [
            row
            for row in rows
            if self._source_allowed(
                row, sources=source_filter, exclude_sources=exclude_sources
            )
        ][:limit]

    def get_session(self, session_id):
        # No compression chains in this fixture — every session is its own root.
        return {"id": session_id, "parent_session_id": None}

    def list_sessions_rich(self, **kwargs):
        return []

    def get_compression_tip(self, session_id):
        return session_id

    def close(self):
        self.closed = True


def test_desktop_session_search_merges_id_matches_before_content_matches(monkeypatch):
    _FakeSessionDB.opened_read_only = None
    _FakeSessionDB.requested_fields = None
    monkeypatch.setattr("korra_state.SessionDB", _FakeSessionDB)

    response = asyncio.run(web_server.search_sessions(q="20260603", limit=2))

    assert _FakeSessionDB.requested_fields is not None
    assert "context" not in _FakeSessionDB.requested_fields
    # ID match surfaces first; the content hit on the SAME session is deduped
    # by lineage root (not double-listed); the unrelated content hit follows.
    assert response == {
        "results": [
            {
                "id": "20260603_090200_exact",
                "session_id": "20260603_090200_exact",
                "lineage_root": "20260603_090200_exact",
                "snippet": "ID match preview",
                "role": None,
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "id": "content_session",
                "session_id": "content_session",
                "lineage_root": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]
    }
    assert _FakeSessionDB.opened_read_only is True


@pytest.fixture
def real_search(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("KORRA_HOME", str(home))
    paths = {"default": home / "state.db", "designer": home / "profiles/designer/state.db"}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        with SessionDB(db_path=path):
            pass
    app = FastAPI()
    app.include_router(search_router)
    with TestClient(app) as client:
        yield client, paths


def _chat(db, sid, title, content="Обсудим сроки", source="dashboard"):
    db.create_session(sid, source=source)
    db.set_session_title(sid, title)
    db.append_message(sid, "user", content)


def test_real_search_finds_renamed_cyrillic_title_outside_first_page(real_search):
    client, paths = real_search
    with SessionDB(db_path=paths["default"]) as db:
        _chat(db, "older", "Начальное название")
        for i in range(60):
            _chat(db, f"recent-{i}", f"Недавний разговор {i}")
        assert "older" not in {row["id"] for row in db.list_sessions_rich(limit=50)}
        db.set_session_title("older", "ВЕСЕННЯЯ КОЛЛЕКЦИЯ — запуск")
    response = client.get("/api/sessions/search", params={"q": "весенняя", "profile": "default"})
    assert response.status_code == 200
    rows = response.json()["results"]
    assert [row["id"] for row in rows] == ["older"]
    assert rows[0]["title"] == "ВЕСЕННЯЯ КОЛЛЕКЦИЯ — запуск"
    assert rows[0]["role"] is None


def test_real_search_combines_title_and_content_without_duplicates(real_search):
    client, paths = real_search
    with SessionDB(db_path=paths["default"]) as db:
        _chat(db, "both", "Коллекция", "Весенняя коллекция готова")
        _chat(db, "content-only", "Встреча с командой", "Обсудим весеннюю коллекцию")
    rows = client.get("/api/sessions/search", params={"q": "коллекц", "profile": "default"}).json()["results"]
    assert {row["id"] for row in rows} == {"both", "content-only"}
    assert len(rows) == 2
    hit = next(row for row in rows if row["id"] == "content-only")
    assert "коллекци" in hit["snippet"]
    assert hit["role"] == "user"


def test_real_search_scopes_titles_and_messages_and_respects_source_filter(real_search):
    client, paths = real_search
    for profile, path in paths.items():
        with SessionDB(db_path=path) as db:
            _chat(db, f"{profile}-title", "Уникальный поиск")
            _chat(db, f"{profile}-message", "Другая тема", "Уникальный поиск внутри")
            _chat(db, f"{profile}-cli", "Уникальный CLI", source="cli")
    rows = client.get("/api/sessions/search", params={"q": "уникальный", "profile": "designer", "source": "dashboard"}).json()["results"]
    assert {row["id"] for row in rows} == {"designer-title", "designer-message"}
    assert client.get("/api/sessions/search", params={"q": "поиск", "profile": "missing"}).status_code == 404


def test_real_search_reloads_after_rename_and_delete(real_search):
    client, paths = real_search
    with SessionDB(db_path=paths["default"]) as db:
        _chat(db, "changing", "Старое название")
    params = {"q": "название", "profile": "default"}
    assert len(client.get("/api/sessions/search", params=params).json()["results"]) == 1
    with SessionDB(db_path=paths["default"]) as db:
        db.set_session_title("changing", "Новое название")
    assert client.get("/api/sessions/search", params=params).json()["results"][0]["title"] == "Новое название"
    with SessionDB(db_path=paths["default"]) as db:
        db.delete_session("changing")
    assert client.get("/api/sessions/search", params=params).json()["results"] == []


@pytest.mark.parametrize("child_before_close", [False, True])
def test_real_title_search_keeps_compression_lineage_and_branch_distinct(real_search, child_before_close):
    client, paths = real_search
    with SessionDB(db_path=paths["default"]) as db:
        _chat(db, "root", "КОЛЛЕКЦИЯ: оригинал")
        if not child_before_close:
            db.end_session("root", "compression")
        db.create_session("tip", source="dashboard", parent_session_id="root")
        if child_before_close:
            db.end_session("root", "compression")
        db.append_message("tip", "user", "коллекция продолжается")
        db.create_session("branch", source="dashboard", parent_session_id="root", model_config={"_branched_from": "root"})
        db.set_session_title("branch", "Коллекция: другой вариант")
        db.append_message("branch", "user", "коллекция в другом варианте")
    rows = client.get("/api/sessions/search", params={"q": "коллекц", "profile": "default"}).json()["results"]
    assert {row["id"] for row in rows} == {"tip", "branch"}
    assert len(rows) == 2


@pytest.mark.parametrize("query,title", [("%", "Скидка 25%"), ("_", "Код a_b"), ("ЗАПУСК", "Запуск сайта"), ("an94", "Поставка AN-94")])
def test_real_title_search_handles_unicode_and_literal_wildcards(real_search, query, title):
    client, paths = real_search
    with SessionDB(db_path=paths["default"]) as db:
        _chat(db, "match", title)
        _chat(db, "unrelated", "Посторонний разговор")
    response = client.get("/api/sessions/search", params={"q": query, "profile": "default"})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == ["match"]
