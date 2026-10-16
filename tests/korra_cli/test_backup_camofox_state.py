"""Входы встроенного Camofox не попадают в копию на Диск открытым текстом (R20-01, K21-349).

Данные синтетические. Пути берутся у ``camofox_embedded.server_env()``: это те
каталоги, которые реально получает сервер, а не ``browser_auth/``.
"""

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from korra_cli import camofox_embedded as emb
from korra_cli.backup_secrets import is_secret
from tests.korra_cli.test_backup_secret_leaks import (
    isolated, open_members_containing, put, secrets_zip, split,
)

MARKER = b"REVIEW-SYNTHETIC-SESSION-7c88f532"


def _storage_state() -> tuple[str, bytes]:
    base = Path(emb.server_env()["CAMOFOX_PROFILE_DIR"])
    raw = json.dumps({"cookies": [{"name": "sid", "value": MARKER.decode(),
                                   "domain": "example.invalid", "path": "/"}], "origins": []}).encode()
    return str(base / "hermes_review" / "storage-state.json"), raw


def _cookie_file() -> tuple[str, bytes]:
    base = Path(emb.server_env()["CAMOFOX_COOKIES_DIR"])
    raw = b"# Netscape HTTP Cookie File\nexample.invalid\tFALSE\t/\tTRUE\t0\tsid\t" + MARKER + b"\n"
    return str(base / "review.txt"), raw


@pytest.mark.parametrize("store", ["profiles", "cookies"])
def test_embedded_browser_logins_are_encrypted(isolated, store):
    absolute, raw = _storage_state() if store == "profiles" else _cookie_file()
    relative = str(Path(absolute).relative_to(isolated))
    put(isolated, relative, raw)
    put(isolated, "config.yaml", b"model: synthetic\n")
    private = X25519PrivateKey.generate()
    archive = split(isolated, private)
    leaks = open_members_containing(archive, MARKER)
    assert leaks == [], f"plain ZIP members contain browser login: {leaks}"
    with secrets_zip(archive, private) as inner:
        assert inner.read(relative) == raw


def test_every_embedded_camofox_store_is_secret_by_path():
    env = emb.server_env()
    root = emb.state_dir()
    for key in ("CAMOFOX_PROFILE_DIR", "CAMOFOX_COOKIES_DIR", "CAMOFOX_TRACES_DIR", "CAMOFOX_UPLOADS_DIR"):
        relative = Path(env[key]).relative_to(root.parent) / "x.bin"
        assert is_secret(relative.as_posix()), key
    assert is_secret("camofox/access.key")


def _camofox_files() -> dict[str, bytes]:
    return {
        "camofox/profiles/abc123/storage-state.json": _storage_state()[1],
        "camofox/profiles/abc123/extra/leveldb.bin": bytes(range(256)) * 3,
        "camofox/cookies/review.txt": _cookie_file()[1],
        "camofox/traces/t1.zip": b"PK-synthetic-trace-" + MARKER,
        "camofox/uploads/u1.dat": b"upload-" + MARKER,
        "camofox/access.key": b"synthetic-access-key-" + MARKER + b"\n",
    }


def test_camofox_state_survives_drive_copy_and_operator_import(isolated, tmp_path, monkeypatch):
    from argparse import Namespace
    from cryptography.hazmat.primitives import serialization
    from korra_cli import backup

    files = _camofox_files()
    for rel, raw in files.items():
        put(isolated, rel, raw)
    put(isolated, "camofox/enabled", b"1\n")
    put(isolated, "config.yaml", b"model: synthetic\n")
    private = X25519PrivateKey.generate()
    archive = split(isolated, private)
    assert open_members_containing(archive, MARKER) == []
    with secrets_zip(archive, private) as inner:
        assert {rel for rel in files} <= set(inner.namelist())

    archive_path = put(tmp_path, "drive-copy.zip", archive)
    key_path = put(tmp_path, "operator.pem", private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    destination = tmp_path / "imported"
    monkeypatch.setenv("HERMES_HOME", str(destination))
    monkeypatch.setenv("KORRA_HOME", str(destination))
    backup.run_import(Namespace(zipfile=str(archive_path), force=True, operator_key=str(key_path)))
    for rel, raw in files.items():
        assert (destination / rel).read_bytes() == raw, rel


def test_local_backup_keeps_camofox_state_in_plain_zip(isolated, tmp_path):
    import zipfile
    from argparse import Namespace
    from korra_cli import backup, backup_crypto as crypto

    files = _camofox_files()
    for rel, raw in files.items():
        put(isolated, rel, raw)
    put(isolated, "config.yaml", b"model: synthetic\n")
    out = tmp_path / "local.zip"
    backup.run_backup(Namespace(output=str(out)))
    with zipfile.ZipFile(out) as zf:
        assert crypto.SECRETS_MEMBER not in zf.namelist()
        for rel, raw in files.items():
            assert zf.read(rel) == raw, rel
