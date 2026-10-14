"""Real SQLite and scratch I/O witness adapted from upstream #133501."""
from pathlib import Path
import pytest
from korra_cli import kanban_db as kb

@pytest.mark.parametrize("status", ["running", "blocked", "review", "done"])
def test_last_child_keeps_live_parent_workspace(tmp_path, monkeypatch, status):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    kb.init_db()
    conn = kb.connect()
    try:
        parent = kb.create_task(conn, title="parent")
        child = kb.create_task(conn, title="child")
        kb.link_tasks(conn, parent, child)
        ws = tmp_path / "kanban" / "workspaces" / parent
        ws.mkdir(parents=True)
        artifact = ws / "работа.txt"
        artifact.write_text("result in progress", encoding="utf-8")
        conn.execute("UPDATE tasks SET status=?, workspace_kind='scratch',workspace_path=? WHERE id=?",
                     (status,str(ws),parent))
        conn.execute("UPDATE tasks SET status='archived' WHERE id=?",(child,))
        conn.commit()
        kb._try_cleanup_parent_workspaces(conn, child)
        assert artifact.exists() == (status != "done")
    finally:
        conn.close()
