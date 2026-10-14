"""`korra backup --to-drive` against a fake Google Drive (local HTTP, no network).

The fake implements the parts of Drive v3 the backup relies on: about/quota,
folder lookup by appProperties, file listing/deleting, and the resumable upload
protocol (Content-Range, 308 + Range, ``bytes */*`` status queries).
"""

import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import threading
import zipfile
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from korra_cli import backup_crypto as crypto
from korra_cli import backup_drive
from korra_cli.backup_drive import DriveClient

GRAN = 256 * 1024


class FakeDrive:
    def __init__(self, quota_limit=None, quota_usage=0):
        self.quota = {"limit": str(quota_limit), "usage": str(quota_usage)} if quota_limit else {}
        self.files: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.plan: dict[int, str] = {}
        self.put_ranges: list[str] = []
        self.data_puts = 0
        self.deleted: list[str] = []
        self.corrupt_md5 = False
        self._seq = 0
        self._server = None

    def _id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq}"

    def add_file(self, name, parent, props, created):
        fid = self._id("f")
        self.files[fid] = {
            "id": fid, "name": name, "parents": [parent], "appProperties": props,
            "createdTime": created, "mimeType": "application/zip", "md5Checksum": "0" * 32,
            "webViewLink": f"https://drive.example/{fid}", "data": b"old",
        }
        return fid

    @staticmethod
    def _public(meta):
        return {k: v for k, v in meta.items() if k not in {"data", "parents"}}

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self):
        drive = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, payload=None, headers=None):
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                if payload is not None:
                    self.send_header("Content-Type", "application/json")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path == "/drive/v3/about":
                    return self._send(200, {"storageQuota": drive.quota})
                if parsed.path == "/drive/v3/files":
                    q = query["q"][0]
                    if "korra_backup_folder" in q:
                        found = [f for f in drive.files.values() if f.get("folder")]
                    else:
                        parent = re.search(r"'([^']+)' in parents", q).group(1)
                        install = re.search(r"korra_install_id' and value='([^']+)'", q).group(1)
                        found = [
                            f for f in drive.files.values()
                            if parent in f.get("parents", [])
                            and f["appProperties"].get("korra_backup") == "1"
                            and f["appProperties"].get("korra_install_id") == install
                        ]
                    return self._send(200, {"files": [FakeDrive._public(f) for f in found]})
                match = re.fullmatch(r"/drive/v3/files/(\w+)", parsed.path)
                if match and match.group(1) in drive.files:
                    return self._send(200, FakeDrive._public(drive.files[match.group(1)]))
                return self._send(404, {"error": "not found"})

            def do_DELETE(self):
                match = re.fullmatch(r"/drive/v3/files/(\w+)", urlparse(self.path).path)
                if match:
                    drive.files.pop(match.group(1), None)
                    drive.deleted.append(match.group(1))
                    return self._send(204)
                if self.path.startswith("/upload/session/"):
                    return self._send(499)
                return self._send(404)

            def do_POST(self):
                parsed = urlparse(self.path)
                body = self._body()
                if parsed.path == "/drive/v3/files":
                    meta = json.loads(body)
                    fid = drive._id("d")
                    drive.files[fid] = {**meta, "id": fid, "folder": True}
                    return self._send(200, {"id": fid})
                if parsed.path == "/upload/drive/v3/files":
                    meta = json.loads(body)
                    sid = drive._id("s")
                    drive.sessions[sid] = {"meta": meta, "data": bytearray(), "done": None}
                    return self._send(200, {}, {"Location": f"{drive.url}/upload/session/{sid}"})
                return self._send(404)

            def do_PUT(self):
                sid = self.path.rsplit("/", 1)[-1]
                session = drive.sessions.get(sid)
                if session is None:
                    return self._send(404)
                rng = self.headers.get("Content-Range", "")
                drive.put_ranges.append(rng)
                data = session["data"]
                if rng.startswith("bytes */"):
                    self._body()
                    if session["done"]:
                        return self._send(200, session["done"])
                    headers = {"Range": f"bytes=0-{len(data) - 1}"} if data else {}
                    return self._send(308, None, headers)
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\*|\d+)", rng)
                start, end, total = int(match.group(1)), int(match.group(2)), match.group(3)
                assert (end + 1 - start) % GRAN == 0 or total != "*", "non-final chunk must be 256 KiB aligned"
                drive.data_puts += 1
                action = drive.plan.get(drive.data_puts)
                if action == "503":
                    self._body()
                    return self._send(503)
                if action == "drop":
                    length = int(self.headers["Content-Length"])
                    part = self.rfile.read(length // 2)
                    if start == len(data):
                        data.extend(part[: len(part) // GRAN * GRAN])
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                body = self._body()
                if start != len(data):
                    return self._send(308, None, {"Range": f"bytes=0-{len(data) - 1}"} if data else {})
                data.extend(body)
                if total != "*" and end + 1 == int(total):
                    drive._seq += 1
                    md5 = hashlib.md5(bytes(data)).hexdigest()
                    if drive.corrupt_md5:
                        md5 = "f" * 32
                    fid = f"up{drive._seq}"
                    meta = {
                        **session["meta"], "id": fid, "size": str(len(data)), "md5Checksum": md5,
                        "createdTime": f"2026-10-14T12:00:{drive._seq % 60:02d}Z",
                        "webViewLink": f"https://drive.example/{fid}",
                    }
                    drive.files[fid] = {**meta, "data": bytes(data)}
                    session["done"] = FakeDrive._public(meta)
                    return self._send(200, session["done"])
                return self._send(308, None, {"Range": f"bytes=0-{len(data) - 1}"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def client(self):
        return DriveClient(
            lambda force=False: "test-token",
            api_base=f"{self.url}/drive/v3",
            upload_base=f"{self.url}/upload/drive/v3",
            sleep=lambda seconds: None,
            pauses=(0, 0, 0, 0),
        )

    def uploaded(self):
        return [f for f in self.files.values() if f.get("id", "").startswith("up")]


@pytest.fixture
def drive():
    server = FakeDrive().start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _small_chunks(monkeypatch):
    monkeypatch.setattr(backup_drive, "UPLOAD_CHUNK", GRAN)


@pytest.fixture(autouse=True)
def _offline_import_checks(monkeypatch):
    if (sys.platform != "linux" or os.geteuid() != 0
            or os.readlink("/proc/1/ns/pid") != "pid:[4026531836]"):
        monkeypatch.setattr("korra_cli.backup._assert_import_offline", lambda *a, **kw: {})
    import korra_cli.gateway as gateway_mod

    monkeypatch.setattr(gateway_mod, "ensure_gateway_service", lambda **kw: False)
    monkeypatch.setattr(gateway_mod, "_is_service_running", lambda: False)


SECRET_VALUES = [
    "sk-SECRET-ROOT-ENV-1111",
    "SECRET-AUTH-JSON-2222",
    "SECRET-GOOGLE-REFRESH-3333",
    "SECRET-PROFILE-ENV-4444",
    "SECRET-CODEX-AUTH-5555",
    "SECRET-MCP-TOKEN-6666",
    "SECRET-INLINE-BOT-7777",
    "SECRET-PEM-8888",
]


def _build_home(root: Path) -> None:
    (root / "config.yaml").write_text("model:\n  provider: openrouter\ntelegram:\n  bot_token: 'SECRET-INLINE-BOT-7777:abc'\n")
    (root / ".env").write_text("OPENROUTER_API_KEY=sk-SECRET-ROOT-ENV-1111\n")
    (root / "auth.json").write_text('{"access_token": "SECRET-AUTH-JSON-2222"}')
    (root / "google-workspace").mkdir()
    (root / "google-workspace" / "token.json").write_text('{"refresh_token": "SECRET-GOOGLE-REFRESH-3333"}')
    (root / "mcp-tokens").mkdir()
    (root / "mcp-tokens" / "srv.json").write_text('{"t": "SECRET-MCP-TOKEN-6666"}')
    (root / "keys").mkdir()
    (root / "keys" / "server.pem").write_text("-----BEGIN KEY-----\nSECRET-PEM-8888\n-----END KEY-----\n")
    profile = root / "profiles" / "work"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model:\n  provider: x\n")
    (profile / ".env").write_text("TELEGRAM_BOT_TOKEN=SECRET-PROFILE-ENV-4444\n")
    (profile / "memories").mkdir()
    (profile / "memories" / "MEMORY.md").write_text("Любимый цвет владельца — зелёный.\n")
    (profile / "home" / ".codex").mkdir(parents=True)
    (profile / "home" / ".codex" / "auth.json").write_text('{"k": "SECRET-CODEX-AUTH-5555"}')
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "skills" / "demo" / "SKILL.md").write_text("# Demo\n")
    (root / "plugins" / "jev").mkdir(parents=True)
    old = root / "plugins" / "jev" / "ancient.txt"
    old.write_text("mtime zero\n")
    os.utime(old, (0, 0))
    (root / "data").mkdir()
    (root / "data" / "blob.bin").write_bytes(os.urandom(1_200_000))
    state = root / "state"
    state.mkdir()
    conn = sqlite3.connect(root / "state.db")
    conn.execute("CREATE TABLE s (v TEXT)")
    conn.execute("INSERT INTO s VALUES ('plain-state')")
    conn.commit()
    conn.close()
    global _WAL_CONN
    _WAL_CONN = sqlite3.connect(state / "delivery.sqlite3")
    _WAL_CONN.execute("PRAGMA journal_mode=WAL")
    _WAL_CONN.execute("PRAGMA wal_autocheckpoint=0")
    _WAL_CONN.execute("CREATE TABLE t (v TEXT)")
    _WAL_CONN.execute("INSERT INTO t VALUES ('only-in-wal')")
    _WAL_CONN.commit()


_WAL_CONN = None


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "src" / ".hermes"
    root.mkdir(parents=True)
    _build_home(root)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "src")
    yield root
    _WAL_CONN.close()


@pytest.fixture
def operator(tmp_path):
    private = X25519PrivateKey.generate()
    key_file = tmp_path / "operator.pem"
    key_file.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    public = base64.b64encode(crypto.public_key_bytes(private.public_key())).decode()
    return key_file, {"backup": {"operator_public_key": public, "installation_name": "тест"}}


def _snapshot(root: Path) -> dict[str, bytes]:
    out = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.name.endswith(("-wal", "-shm")) and path.suffix != ".lock":
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def _run_to_drive(root, drive, config, **kwargs):
    backup_drive.run_backup_to_drive(root, client=drive.client(), config=config, **kwargs)


def _import(tmp_path, zip_path, **kwargs):
    from korra_cli.backup import run_import

    dst = tmp_path / "dest" / ".hermes"
    dst.mkdir(parents=True, exist_ok=True)
    return dst, kwargs, run_import


def _do_import(tmp_path, monkeypatch, zip_path, **kwargs):
    from korra_cli.backup import run_import

    dst = tmp_path / "dest" / ".hermes"
    dst.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(dst))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "dest")
    run_import(Namespace(zipfile=str(zip_path), force=True, **kwargs))
    return dst


def test_full_cycle_with_interrupted_upload(tmp_path, monkeypatch, home, drive, operator, capsys):
    key_file, config = operator
    drive.plan = {2: "drop", 3: "503"}
    source = _snapshot(home)

    _run_to_drive(home, drive, config)
    out = capsys.readouterr().out

    uploaded = drive.uploaded()
    assert len(uploaded) == 1
    meta = uploaded[0]
    assert re.fullmatch(r"korra-тест-\d{4}-\d{2}-\d{2}-\d{4}\.zip", meta["name"])
    assert meta["appProperties"]["korra_backup"] == "1"
    assert meta["appProperties"]["korra_install_id"]
    folder = [f for f in drive.files.values() if f.get("folder")]
    assert [f["name"] for f in folder] == ["Korra — резервные копии"]
    assert folder[0]["appProperties"] == {"korra_backup_folder": "1"}
    assert meta["parents"] == [folder[0]["id"]]
    assert "Копия на Google Диске готова" in out and meta["webViewLink"] in out
    assert "Секретов зашифровано:" in out

    # Interruption happened and the upload continued from the server's offset.
    assert any(r == "bytes */*" for r in drive.put_ranges)
    starts = [int(re.match(r"bytes (\d+)-", r).group(1)) for r in drive.put_ranges if not r.startswith("bytes */")]
    assert starts[0] == 0 and len(set(starts)) > 3
    assert drive.put_ranges[-1].endswith(f"/{len(meta['data'])}")

    archive = meta["data"]
    assert hashlib.md5(archive).hexdigest() == meta["md5Checksum"]
    # No planted secret appears in the archive bytes or in any open member.
    for value in SECRET_VALUES:
        assert value.encode() not in archive
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert crypto.SECRETS_MEMBER in zf.namelist()
        for name in zf.namelist():
            if name != crypto.SECRETS_MEMBER:
                content = zf.read(name)
                for value in SECRET_VALUES:
                    assert value.encode() not in content, name
        assert ".env" not in zf.namelist() and "profiles/work/.env" not in zf.namelist()
        assert "config.yaml" not in zf.namelist()  # inline bot token -> sealed
        assert "profiles/work/config.yaml" in zf.namelist()
        assert "plugins/jev/ancient.txt" in zf.namelist()
        assert not [n for n in zf.namelist() if n.endswith(("-wal", "-shm"))]
    assert not list(tmp_path.rglob("*.zip"))  # nothing written to disk

    zip_path = tmp_path / "from-drive.zip"
    zip_path.write_bytes(archive)

    # Without the key: clear Russian error, nothing restored.
    with pytest.raises(SystemExit):
        _do_import(tmp_path, monkeypatch, zip_path)
    err = capsys.readouterr().out
    assert "ключом команды Korra" in err and "--skip-secrets" in err
    assert not (tmp_path / "dest" / ".hermes" / "state.db").exists()

    # Wrong key.
    wrong = tmp_path / "wrong.pem"
    wrong.write_bytes(
        X25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    with pytest.raises(SystemExit):
        _do_import(tmp_path, monkeypatch, zip_path, operator_key=str(wrong))
    assert "не удалось расшифровать" in capsys.readouterr().out

    # Skip secrets: everything except the sealed files.
    dst = _do_import(tmp_path, monkeypatch, zip_path, skip_secrets=True)
    restored = _snapshot(dst)
    assert ".env" not in restored and "auth.json" not in restored
    assert "profiles/work/.env" not in restored and "google-workspace/token.json" not in restored
    assert restored["profiles/work/memories/MEMORY.md"] == source["profiles/work/memories/MEMORY.md"]
    assert restored["data/blob.bin"] == source["data/blob.bin"]
    assert "skip-secrets" in capsys.readouterr().out
    import shutil

    shutil.rmtree(dst)

    # With the key: byte-for-byte (databases by content).
    dst = _do_import(tmp_path, monkeypatch, zip_path, operator_key=str(key_file))
    restored = _snapshot(dst)
    expected = {k: v for k, v in source.items() if k != "install_id" and not k.endswith((".db", ".sqlite3"))}
    for rel, data in expected.items():
        assert restored.get(rel) == data, rel
    assert (dst / ".env").read_text() == "OPENROUTER_API_KEY=sk-SECRET-ROOT-ENV-1111\n"
    assert os.stat(dst / ".env").st_mode & 0o077 == 0
    assert os.path.getmtime(dst / "plugins" / "jev" / "ancient.txt") >= 0
    assert not (dst / "state" / "delivery.sqlite3-wal").exists()
    with sqlite3.connect(dst / "state" / "delivery.sqlite3") as check:
        assert check.execute("SELECT v FROM t").fetchall() == [("only-in-wal",)]
    with sqlite3.connect(dst / "state.db") as check:
        assert check.execute("SELECT v FROM s").fetchall() == [("plain-state",)]


def test_not_enough_space_means_no_upload(home, drive, operator, capsys):
    _, config = operator
    drive.quota = {"limit": str(1_000_000), "usage": str(900_000)}
    with pytest.raises(SystemExit) as exc:
        _run_to_drive(home, drive, config)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "мало места" in out and "свободно" in out
    assert drive.sessions == {} and not drive.files


def test_unlimited_quota_proceeds(home, drive, operator):
    _, config = operator
    drive.quota = {}
    _run_to_drive(home, drive, config)
    assert len(drive.uploaded()) == 1


def test_rotation_deletes_only_own_old_copies(home, drive, operator, capsys):
    from korra_cli.install_identity import read_or_create_install_id

    _, config = operator
    config["backup"]["drive_keep"] = 3
    install_id = read_or_create_install_id(home)
    folder = drive._id("d")
    drive.files[folder] = {
        "id": folder, "folder": True, "name": backup_drive.FOLDER_NAME, "appProperties": {"korra_backup_folder": "1"},
    }
    own = [
        drive.add_file(f"korra-old-{i}.zip", folder,
                       {"korra_backup": "1", "korra_install_id": install_id}, f"2026-10-0{i}T00:00:00Z")
        for i in range(1, 6)
    ]
    foreign = drive.add_file("korra-other.zip", folder,
                             {"korra_backup": "1", "korra_install_id": "other-install"}, "2026-01-01T00:00:00Z")
    unmarked = drive.add_file("korra-mine-but-unmarked.zip", folder, {}, "2026-01-02T00:00:00Z")
    elsewhere = drive.add_file("somewhere.zip", "root", {"korra_backup": "1", "korra_install_id": install_id},
                               "2026-01-03T00:00:00Z")

    _run_to_drive(home, drive, config)

    remaining = set(drive.files)
    assert {foreign, unmarked, elsewhere, folder} <= remaining
    # new + two newest old copies are kept (drive_keep=3); the three oldest go.
    assert set(own[3:]) <= remaining and not (set(own[:3]) & remaining)
    assert len(drive.uploaded()) == 1
    assert "Удалено старых копий: 3" in capsys.readouterr().out
    assert set(drive.deleted) == set(own[:3])


def test_existing_folder_is_reused(home, drive, operator):
    _, config = operator
    _run_to_drive(home, drive, config)
    _run_to_drive(home, drive, config)
    assert len([f for f in drive.files.values() if f.get("folder")]) == 1
    assert len(drive.uploaded()) == 2


def test_md5_mismatch_removes_new_file_and_keeps_old(home, drive, operator, capsys):
    from korra_cli.install_identity import read_or_create_install_id

    _, config = operator
    install_id = read_or_create_install_id(home)
    folder = drive._id("d")
    drive.files[folder] = {"id": folder, "folder": True, "name": "x", "appProperties": {"korra_backup_folder": "1"}}
    old = drive.add_file("korra-old.zip", folder, {"korra_backup": "1", "korra_install_id": install_id},
                         "2026-10-01T00:00:00Z")
    drive.corrupt_md5 = True
    with pytest.raises(SystemExit):
        _run_to_drive(home, drive, config)
    assert "контрольная сумма" in capsys.readouterr().out
    assert old in drive.files and not drive.uploaded()


def test_incomplete_copy_does_not_rotate(home, drive, operator, monkeypatch, capsys):
    from korra_cli import backup as backup_mod
    from korra_cli.install_identity import read_or_create_install_id

    _, config = operator
    config["backup"]["drive_keep"] = 1
    install_id = read_or_create_install_id(home)
    folder = drive._id("d")
    drive.files[folder] = {"id": folder, "folder": True, "name": "x", "appProperties": {"korra_backup_folder": "1"}}
    old = drive.add_file("korra-old.zip", folder, {"korra_backup": "1", "korra_install_id": install_id},
                         "2026-10-01T00:00:00Z")
    real = backup_mod._write_backup_member

    def flaky(zf, abs_path, arcname, *args):
        if arcname == "data/blob.bin":
            args[1].append(f"  {arcname}: Permission denied")
            return 0
        return real(zf, abs_path, arcname, *args)

    monkeypatch.setattr(backup_mod, "_write_backup_member", flaky)
    _run_to_drive(home, drive, config)
    out = capsys.readouterr().out
    assert "не полностью" in out and "blob.bin" in out
    assert old in drive.files


def test_google_not_connected_gives_russian_hint(home, monkeypatch, capsys):
    from korra_cli import google_workspace as gw

    def refuse(*args, **kwargs):
        raise gw.GoogleWorkspaceError("not_authenticated", "x", status_code=401)

    monkeypatch.setattr(gw, "_credentials", refuse)
    monkeypatch.setattr(gw, "_load_app", lambda: ("", {}))
    provider = backup_drive.google_token_provider()
    with pytest.raises(backup_drive.DriveBackupError) as exc:
        provider(False)
    assert "Ключи и доступы" in str(exc.value)


def test_cli_flag_goes_through_backup_lock(home, monkeypatch, capsys):
    from korra_cli import backup as backup_mod

    with backup_mod._backup_operation_lock(home):
        with pytest.raises(SystemExit) as exc:
            backup_mod.run_backup(Namespace(output=None, label=None, to_drive=True))
    assert exc.value.code == 2

    called = []
    monkeypatch.setattr(backup_drive, "run_backup_to_drive", lambda root: called.append(root))
    backup_mod.run_backup(Namespace(output=None, label=None, to_drive=True))
    assert called == [home.resolve()] or called == [home]

    with pytest.raises(SystemExit):
        backup_mod.run_backup(Namespace(output="x.zip", label=None, to_drive=True))
    assert "--to-drive" in capsys.readouterr().out


def test_local_backup_is_unchanged_and_not_encrypted(tmp_path, home):
    from korra_cli.backup import run_backup

    out = tmp_path / "local.zip"
    run_backup(Namespace(output=str(out)))
    with zipfile.ZipFile(out) as zf:
        assert ".env" in zf.namelist() and crypto.SECRETS_MEMBER not in zf.namelist()
