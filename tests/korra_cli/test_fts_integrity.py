"""K21-055/K21-039: повреждение индекса FTS5 должно замечаться само.

У Виктории (12.09.2026) `state.db` работающего контура уже был повреждён по
обратному индексу FTS5: беседы читались, сообщения писались, контур выглядел
здоровым — узнали об этом только при импорте в новый контур, где preflight
импорта отбил архив по `quick_check`. Соседняя беда K21-039 — тот же дефект в
архивной копии, из-за которого восстановление требовало ручной пересборки.

Здесь проверяется, что повреждение называется само, что «индекс можно
пересобрать» отличается от «повреждены данные», и что пересборка не теряет
сообщений.
"""
from __future__ import annotations

import random
import sqlite3
import uuid
import zipfile
from argparse import Namespace
from pathlib import Path

from korra_cli import fts_integrity


def _state_db(path: Path, messages: int = 120) -> int:
    """Настоящий state.db движка с непустым индексом FTS5."""
    from korra_state import SessionDB

    db = SessionDB(db_path=path)
    session = db.create_session(session_id=str(uuid.uuid4()), source="cli")
    for index in range(messages):
        db.append_message(session, role="user", content=f"чертёж балки номер {index}")
        db.append_message(session, role="assistant", content=f"нормоконтроль {index}")
    db.close()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    finally:
        conn.close()


def _damage_fts_index(path: Path) -> None:
    """Обнулить сегмент обратного индекса, не трогая ``messages``."""
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT id FROM messages_fts_trigram_data "
            "WHERE block IS NOT NULL AND length(block) > 100 ORDER BY id"
        ).fetchall()
        assert rows, "фикстуре нужен непустой индекс FTS5"
        conn.execute(
            "UPDATE messages_fts_trigram_data SET block = zeroblob(length(block)) "
            "WHERE id = ?",
            (rows[-1][0],),
        )
        conn.commit()
    finally:
        conn.close()


def _damage_pages(path: Path) -> None:
    """Испортить страницу b-дерева — это уже потеря данных, не индекс."""
    random.seed(20260912)
    with path.open("r+b") as handle:
        handle.seek(4096 * 3)
        handle.write(bytes(random.randrange(256) for _ in range(4096)))


def test_healthy_state_db_passes(tmp_path):
    path = tmp_path / "state.db"
    _state_db(path)
    assert fts_integrity.check_state_db_fts(path).status == fts_integrity.OK


def test_damaged_index_names_the_table_and_calls_itself_rebuildable(tmp_path):
    path = tmp_path / "state.db"
    _state_db(path)
    _damage_fts_index(path)
    report = fts_integrity.check_state_db_fts(path)
    assert report.status == fts_integrity.INDEX_DAMAGED
    assert report.rebuildable
    assert report.tables == ("messages_fts_trigram",)
    assert "сообщения целы" in report.detail


def test_verdict_does_not_depend_on_the_quick_check_wording(tmp_path):
    """Одно повреждение — разные слова у разных сборок SQLite.

    Первая версия модуля разбирала строку регулярным выражением и на CI
    (SQLite 3.53.1) объявляла ребилдабельный индекс потерей данных, хотя на
    хостовом 3.45 тот же тест был зелёным. Приговор должен приходить от пробной
    пересборки, а не от текста, поэтому здесь он сверяется с ответом
    собственной команды FTS5 — той, что одинакова на всех версиях.
    """
    path = tmp_path / "state.db"
    _state_db(path)
    _damage_fts_index(path)

    report = fts_integrity.check_state_db_fts(path)

    # Сырой текст сохранён для оператора, но в классификации не участвует:
    # на 3.45 это «malformed inverted index…», на 3.53 — «fts5: corruption
    # found reading blob…».
    assert report.problems
    assert fts_integrity.OK not in report.problems

    copy = tmp_path / "copy.db"
    copy.write_bytes(path.read_bytes())
    conn = sqlite3.connect(str(copy))
    try:
        failing = fts_integrity._failing_fts5_tables(
            conn, fts_integrity._fts5_tables(conn)
        )
    finally:
        conn.close()
    assert failing == ("messages_fts_trigram",)
    assert report.tables == failing


def test_check_leaves_the_live_database_untouched(tmp_path):
    """Проба пересборки обязана идти на копии: боевую базу трогать нельзя."""
    path = tmp_path / "state.db"
    _state_db(path)
    _damage_fts_index(path)
    before = path.read_bytes()

    assert fts_integrity.check_state_db_fts(path).status == fts_integrity.INDEX_DAMAGED

    assert path.read_bytes() == before
    # Временная копия не должна пережить проверку.
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".korra-fts")] == []


def test_damaged_pages_are_not_called_rebuildable(tmp_path):
    path = tmp_path / "state.db"
    _state_db(path)
    _damage_pages(path)
    report = fts_integrity.check_state_db_fts(path)
    assert report.status == fts_integrity.DATA_DAMAGED
    assert not report.rebuildable
    assert "восстановление из резервной копии" in report.detail


def test_rebuild_restores_the_index_without_losing_messages(tmp_path):
    path = tmp_path / "state.db"
    expected = _state_db(path)
    _damage_fts_index(path)
    report = fts_integrity.check_state_db_fts(path)
    rebuilt, detail = fts_integrity.rebuild_fts_indexes(path, report.tables)
    assert rebuilt, detail
    assert fts_integrity.check_state_db_fts(path).status == fts_integrity.OK
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == expected
        assert conn.execute(
            "SELECT count(*) FROM messages_fts_trigram"
        ).fetchone()[0] == expected
    finally:
        conn.close()


def test_live_repair_path_rebuilds_and_keeps_every_message(tmp_path):
    """Штатный ремонт живой базы — korra_state.repair_state_db_schema."""
    from korra_state import repair_state_db_schema

    path = tmp_path / "state.db"
    expected = _state_db(path)
    _damage_fts_index(path)
    report = repair_state_db_schema(path)
    assert report["repaired"], report
    assert report["strategy"] == "rebuild_fts"
    assert fts_integrity.check_state_db_fts(path).status == fts_integrity.OK
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == expected
    finally:
        conn.close()


def test_container_boot_reports_the_damaged_index(tmp_path, monkeypatch, capsys):
    """K21-055: старт контура должен сам сказать про повреждённый индекс."""
    from korra_cli import container_boot

    scandir = tmp_path / "run-service"
    scandir.mkdir()
    profile = tmp_path / "profiles" / "worker"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model: test\n")
    _state_db(tmp_path / "state.db")
    _damage_fts_index(tmp_path / "state.db")
    _state_db(profile / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("S6_PROFILE_GATEWAY_SCANDIR", str(scandir))
    monkeypatch.setattr(container_boot, "_read_container_argv", lambda: ())
    monkeypatch.setattr(
        container_boot, "reconcile_profile_gateways", lambda **kwargs: []
    )

    assert container_boot.main() == 0

    out = capsys.readouterr().out
    assert "fts:" in out
    assert fts_integrity.INDEX_DAMAGED in out
    assert "messages_fts_trigram" in out
    assert f"{profile / 'state.db'} ok" in out


def test_backup_archive_survives_a_damaged_live_index(tmp_path, monkeypatch):
    """K21-039: архив не должен содержать базу, которую отвергнет импорт."""
    from korra_cli import backup

    home = tmp_path / "data"
    home.mkdir()
    (home / "config.yaml").write_text("model: test\n")
    expected = _state_db(home / "state.db")
    _damage_fts_index(home / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out_path = tmp_path / "archive.zip"

    backup.run_backup(Namespace(output=str(out_path), quick=False))

    with zipfile.ZipFile(out_path) as archive:
        (tmp_path / "restored.db").write_bytes(archive.read("state.db"))
    restored = tmp_path / "restored.db"
    assert fts_integrity.check_state_db_fts(restored).status == fts_integrity.OK
    conn = sqlite3.connect(str(restored))
    try:
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == expected
    finally:
        conn.close()
