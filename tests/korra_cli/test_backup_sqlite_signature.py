"""SQLite databases of any name: snapshot by signature, no live sidecars (review of K21-349)."""

import io
import sqlite3
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from korra_cli import backup, backup_crypto as crypto


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("KORRA_HOME", str(root))
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.delenv("KORRA_PROFILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: root)
    monkeypatch.setattr(backup, "_scan_external_files", lambda: ([], []))
    monkeypatch.setattr(backup, "_assert_import_offline", lambda *a, **kw: {})
    return root


def put(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def split(root, key):
    sink = io.BytesIO()
    files, _ = backup._scan_backup_files(root)
    stats = backup._write_split_archive(sink, files, [], key.public_key(), root.parent)
    assert not stats.errors, stats.errors
    return sink.getvalue()


def open_members_containing(archive: bytes, marker: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return [name for name in zf.namelist()
                if name != crypto.SECRETS_MEMBER and marker in zf.read(name)]


def secrets_zip(archive: bytes, private) -> zipfile.ZipFile:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        inner = io.BytesIO()
        crypto.decrypt_stream(io.BytesIO(zf.read(crypto.SECRETS_MEMBER)), private, inner)
    return zipfile.ZipFile(inner)


def pem(private) -> bytes:
    return private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())


def make_wal_session(path: Path, marker: bytes) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE sessions(auth_key BLOB)")
    connection.execute("INSERT INTO sessions VALUES (?)", (marker,))
    connection.commit()
    assert Path(str(path) + "-wal").stat().st_size > 0
    return connection


@pytest.mark.parametrize("prefix", ["", "profiles/work/"])
def test_session_database_is_snapshotted_inside_the_secret_block(isolated, tmp_path, monkeypatch, prefix):
    marker = b"SYNTHETIC-TELEGRAM-AUTH-KEY-9e381d0c"
    connection = make_wal_session(isolated / prefix / "telegram.session", marker)
    try:
        private = X25519PrivateKey.generate()
        archive = split(isolated, private)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in zf.namelist())
        assert open_members_containing(archive, marker) == []
        with secrets_zip(archive, private) as inner:
            assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in inner.namelist())
            snapshot = put(tmp_path, "snapshot.sqlite", inner.read(prefix + "telegram.session"))
        with sqlite3.connect(snapshot) as restored:
            assert restored.execute("SELECT auth_key FROM sessions").fetchall() == [(marker,)]

        archive_path = put(tmp_path, "session-backup.zip", archive)
        key_path = put(tmp_path, "operator.pem", pem(private))
        destination = tmp_path / "imported"
        monkeypatch.setenv("HERMES_HOME", str(destination))
        monkeypatch.setenv("KORRA_HOME", str(destination))
        backup.run_import(Namespace(zipfile=str(archive_path), force=True, operator_key=str(key_path)))
        assert not any(p.name.endswith(("-wal", "-shm", "-journal"))
                       for p in destination.rglob("*") if p.is_file())
        restored = sqlite3.connect(destination / prefix / "telegram.session")
        try:
            assert restored.execute("SELECT auth_key FROM sessions").fetchall() == [(marker,)]
        finally:
            restored.close()
    finally:
        connection.close()


def test_sqlite_is_recognised_by_signature_not_by_extension(isolated):
    connection = make_wal_session(isolated / "plugin-state", b"x")
    try:
        assert backup._is_sqlite_database(isolated / "plugin-state")
        assert not backup._is_sqlite_database(put(isolated, "notes.sqlite3", b"not a database"))
        files, _ = backup._scan_backup_files(isolated)
        names = [rel.name for _, rel in files]
        assert "plugin-state" in names
        assert "plugin-state-wal" not in names and "plugin-state-shm" not in names
    finally:
        connection.close()


def test_sidecar_without_database_is_kept(isolated):
    put(isolated, "orphan.bin-wal", b"not a sidecar of anything")
    files, _ = backup._scan_backup_files(isolated)
    assert "orphan.bin-wal" in [rel.name for _, rel in files]


def test_automatic_backup_snapshots_signature_databases(isolated, tmp_path):
    connection = make_wal_session(isolated / "state" / "custom-name", b"auto-wal-only")
    try:
        out = tmp_path / "automatic.zip"
        assert backup._write_full_zip_backup(out, isolated) == out
        with zipfile.ZipFile(out) as zf:
            assert not any(name.endswith(("-wal", "-shm")) for name in zf.namelist())
            snapshot = put(tmp_path, "snap", zf.read("state/custom-name"))
        with sqlite3.connect(snapshot) as restored:
            assert restored.execute("SELECT auth_key FROM sessions").fetchall() == [(b"auto-wal-only",)]
    finally:
        connection.close()
