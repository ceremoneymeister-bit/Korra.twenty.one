"""Release K21-001: real native ZIP import, isolated DATA and process holders."""
import hashlib
import os
from argparse import Namespace
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import zipfile

import pytest

from korra_cli import backup


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_ino, stat.S_IMODE(p.stat().st_mode),
            p.stat().st_uid, p.stat().st_gid,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in [root, *root.rglob("*")]}


def database(path, value="archived"):
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user"
    target = user / ".hermes"
    target.mkdir(parents=True)
    (target / "config.yaml").write_text("model: original\n")
    (target / "untouched.txt").write_text("keep")
    monkeypatch.setenv("HERMES_HOME", str(target))
    monkeypatch.setattr(Path, "home", lambda: user)
    # Actual importer must never ask the operating system to install/start.
    import korra_cli.gateway as gateway
    monkeypatch.setattr(gateway, "ensure_gateway_service",
                        lambda **kw: pytest.fail("offline import auto-started a service"))
    monkeypatch.setattr(gateway, "_is_service_running", lambda: False)
    return user, target


def archive(tmp_path, files, compression=zipfile.ZIP_STORED):
    path = tmp_path / "backup.zip"
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def restore(path, **kwargs):
    backup.run_import(Namespace(zipfile=str(path), force=True, **kwargs))


def rejected(path, root, **kwargs):
    before = snapshot(root)
    with pytest.raises(SystemExit) as exc:
        restore(path, **kwargs)
    assert exc.value.code != 0
    assert snapshot(root) == before


def require_host_proc():
    if sys.platform != "linux" or os.geteuid() != 0:
        pytest.skip("real full-/proc acceptance requires Linux host root")
    if os.readlink("/proc/1/ns/pid") != "pid:[4026531836]":
        pytest.skip("real holder acceptance requires initial host PID namespace")


@contextmanager
def holder(path, kind="fd"):
    code = """import os, sqlite3, sys
p, kind = sys.argv[1:]
if kind == 'cwd':
    os.chdir(p)
elif kind == 'sqlite':
    conn=sqlite3.connect(p)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("INSERT INTO sample VALUES ('uncommitted')")
else:
    handle=open(p, 'rb')
print('ready', flush=True)
sys.stdin.read()
"""
    proc = subprocess.Popen([sys.executable, "-c", code, str(path), kind],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        yield proc
    finally:
        proc.communicate("", timeout=10)
        assert proc.returncode == 0


@pytest.mark.parametrize("kind", ["fd", "cwd", "sqlite"])
def test_any_real_holder_refuses_before_target_write(tmp_path, isolated, kind):
    require_host_proc()
    user, target = isolated
    db = target / "state.db"
    database(db, "original")
    path = archive(tmp_path, {"config.yaml": "replacement", "state.db": database(tmp_path / "source.db")})
    held = target if kind == "cwd" else db
    with holder(held, kind):
        rejected(path, user)


def test_same_process_tracked_connection_blocks(tmp_path, isolated):
    require_host_proc()
    from korra_cli.sqlite_safe_read import connect_tracked
    user, target = isolated
    db = target / "state.db"
    database(db)
    path = archive(tmp_path, {"config.yaml": "replacement"})
    before = snapshot(user)
    conn = connect_tracked(db)
    try:
        with pytest.raises(SystemExit):
            restore(path)
    finally:
        conn.close()
    assert snapshot(user) == before


def test_named_profile_and_custom_home_do_not_touch_root_or_siblings(tmp_path, isolated, monkeypatch):
    require_host_proc()
    user, root = isolated
    profile = root / "profiles" / "coder"
    sibling = root / "profiles" / "writer"
    for directory in (profile, sibling):
        directory.mkdir(parents=True)
        (directory / "config.yaml").write_text("old")
    parent_file = snapshot(root)["config.yaml"]
    other = snapshot(sibling)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    path = archive(tmp_path, {"config.yaml": "new", "state.db": database(tmp_path / "source.db")})
    restore(path)
    assert (profile / "config.yaml").read_text() == "new"
    assert snapshot(root)["config.yaml"] == parent_file
    assert snapshot(sibling) == other
    custom = user / "custom-DATA"
    custom.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(custom))
    root_before = snapshot(root)
    restore(path)
    assert (custom / "config.yaml").read_text() == "new"
    assert snapshot(root) == root_before


@pytest.mark.parametrize("bad_name", ["../escape", "/absolute", "a/../../escape", "a\\..\\escape"])
def test_traversal_rejects_whole_archive(tmp_path, isolated, bad_name):
    user, target = isolated
    path = archive(tmp_path, {"config.yaml": "replacement", bad_name: "bad"})
    rejected(path, user)


@pytest.mark.parametrize("variant", ["crc", "sqlite", "ratio", "symlink", "duplicate"])
def test_full_preflight_has_zero_target_writes(tmp_path, isolated, variant):
    user, target = isolated
    files = {"config.yaml": "replacement", "late.txt": "unique-corrupt-payload"}
    if variant == "sqlite":
        files["state.db"] = b"SQLite format 3\x00" + b"broken" * 30
    if variant == "ratio":
        files["large.txt"] = b"0" * (2 * 1024 * 1024)
    path = archive(tmp_path, files, zipfile.ZIP_DEFLATED if variant == "ratio" else zipfile.ZIP_STORED)
    if variant == "crc":
        payload = path.read_bytes()
        path.write_bytes(payload.replace(b"unique-corrupt-payload", b"Unique-corrupt-payload", 1))
    if variant in {"symlink", "duplicate"}:
        with zipfile.ZipFile(path, "a") as zf:
            info = zipfile.ZipInfo("link" if variant == "symlink" else "config.yaml")
            if variant == "symlink":
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "../other")
    rejected(path, user)


@pytest.mark.parametrize("limit", ["_IMPORT_MAX_FILES", "_IMPORT_MAX_FILE_BYTES", "_IMPORT_MAX_TOTAL_BYTES"])
def test_archive_limits_are_checked_before_target_creation(tmp_path, isolated, monkeypatch, limit):
    user, target = isolated
    monkeypatch.setattr(backup, limit, 1, raising=False)
    path = archive(tmp_path, {"config.yaml": "replacement", "late.txt": "data"})
    rejected(path, user)


def test_publish_failure_rolls_back_bytes_inodes_and_created_paths(tmp_path, isolated, monkeypatch):
    require_host_proc()
    user, target = isolated
    path = archive(tmp_path, {"config.yaml": "new", "new/deep/file": "new", "fail.txt": "new"})
    original_replace = backup.atomic_replace
    def fail(tmp, dst):
        if Path(dst) == target / "fail.txt":
            raise OSError("synthetic publication failure")
        return original_replace(tmp, dst)
    monkeypatch.setattr(backup, "atomic_replace", fail)
    rejected(path, user)


def test_default_migration_preserves_target_identity_runtime_and_sidecars(tmp_path, isolated):
    require_host_proc()
    user, target = isolated
    (target / ".ssh").mkdir(mode=0o700)
    (target / ".ssh" / "id_ed25519_host").write_text("inert target identity")
    (target / "install_id").write_text("target-id")
    identities = snapshot(target / ".ssh")
    install = snapshot(target)["install_id"]
    path = archive(tmp_path, {
        "config.yaml": "new", ".ssh/id_ed25519_host": "inert archived identity",
        "install_id": "archive-id", ".install_id.lock": "foreign",
        "gateway.sock": "foreign", ".backup.lock": "foreign", "gateway.drain": "foreign",
        "profiles/coder/gateway_state.json": "foreign",
        "state.db-wal": "foreign", "state.db-shm": "foreign", "state.db-journal": "foreign",
    })
    restore(path)
    assert snapshot(target / ".ssh") == identities
    assert snapshot(target)["install_id"] == install
    assert set(p.name for p in target.iterdir()) == {".ssh", "install_id", "config.yaml", "untouched.txt"}


def test_same_host_identity_modes_owner_all_databases_and_repeat_restore(tmp_path, isolated):
    require_host_proc()
    user, target = isolated
    os.chown(target, 10000, 10000)
    data = database(tmp_path / "source.db")
    path = archive(tmp_path, {"config.yaml": "new", "state.db": data, "kanban.db": data,
        "memory.sqlite": data, ".ssh/id_ed25519_host": "inert fixture",
        ".ssh/known_hosts": "fixture", ".env": "FAKE_TOKEN=fixture",
        "_external/.honcho/config.json": "{}"})
    for _ in range(2):
        restore(path, same_host_restore=True)
        assert (target / ".ssh").stat().st_mode & 0o777 == 0o700
        for rel in (".ssh/id_ed25519_host", ".ssh/known_hosts", ".env"):
            st = (target / rel).stat()
            assert (st.st_mode & 0o777, st.st_uid, st.st_gid) == (0o600, 10000, 10000)
        for rel in ("state.db", "kanban.db", "memory.sqlite"):
            with closing(sqlite3.connect(target / rel)) as conn:
                assert conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
                assert conn.execute("SELECT value FROM sample").fetchall() == [("archived",)]
        assert (user / ".honcho/config.json").stat().st_mode & 0o777 == 0o600


def test_unknown_process_visibility_fails_closed(tmp_path, isolated, monkeypatch):
    user, target = isolated
    path = archive(tmp_path, {"config.yaml": "new"})
    real_readlink = os.readlink
    def denied(path, *args, **kwargs):
        if str(path).startswith("/proc/"):
            raise PermissionError("synthetic unreadable proc")
        return real_readlink(path, *args, **kwargs)
    monkeypatch.setattr(os, "readlink", denied)
    rejected(path, user)


@pytest.mark.parametrize("valid", [False, True])
def test_real_cli_profile_has_no_bootstrap_writes_before_import(tmp_path, isolated, valid):
    require_host_proc()
    user, root = isolated
    profile = root / "profiles" / "coder"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model: old\n")
    files = {"config.yaml": "model: restored\n"}
    if not valid:
        files["late.db"] = b"corrupt fixture"
    path = archive(tmp_path, files)
    before = snapshot(user)
    env = os.environ.copy()
    env.update(HOME=str(user), HERMES_HOME=str(root), HERMES_SKIP_CHMOD="1",
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    proc = subprocess.run([sys.executable, "-m", "korra_cli.main", "-p", "coder",
                           "import", str(path), "--force"], env=env, capture_output=True, timeout=30)
    if valid:
        assert proc.returncode == 0, proc.stderr.decode()
        assert (profile / "config.yaml").read_text() == "model: restored\n"
        after = snapshot(user)
        assert set(after) == set(before), "import bootstrapped unrelated DATA"
        assert {k: v for k, v in after.items() if k != ".hermes/profiles/coder/config.yaml"} == {
            k: v for k, v in before.items() if k != ".hermes/profiles/coder/config.yaml"}
    else:
        assert proc.returncode != 0
        assert snapshot(user) == before


def test_identity_exclusions_follow_existing_safe_alias(tmp_path, isolated):
    require_host_proc()
    user, target = isolated
    ssh = target / ".ssh"
    ssh.mkdir(mode=0o700)
    (ssh / "id_ed25519_host").write_text("inert original")
    (target / "alias").symlink_to(ssh, target_is_directory=True)
    identity_before = snapshot(ssh)
    path = archive(tmp_path, {"config.yaml": "new", "alias/id_ed25519_host": "foreign"})
    restore(path)
    assert snapshot(ssh) == identity_before


def test_deleted_open_file_remains_a_data_holder(tmp_path, isolated):
    require_host_proc()
    user, target = isolated
    held = target / "worker.data"
    held.write_text("fixture")
    path = archive(tmp_path, {"config.yaml": "new"})
    with holder(held):
        held.unlink()
        rejected(path, user)


def test_data_change_during_staging_is_not_overwritten(tmp_path, isolated, monkeypatch):
    require_host_proc()
    user, target = isolated
    path = archive(tmp_path, {"config.yaml": "restored"})
    stage = backup._stage_import
    def concurrent_change(*args):
        stage(*args)
        (target / "config.yaml").write_text("external modification")
    monkeypatch.setattr(backup, "_stage_import", concurrent_change)
    with pytest.raises(SystemExit):
        restore(path)
    assert (target / "config.yaml").read_text() == "external modification"
    assert {p.name for p in target.iterdir()} == {"config.yaml", "untouched.txt"}


def test_private_pid_namespace_is_not_an_offline_witness(tmp_path, isolated, monkeypatch):
    user, target = isolated
    path = archive(tmp_path, {"config.yaml": "restored"})
    real_readlink = os.readlink
    monkeypatch.setattr(os, "readlink", lambda path, *a, **kw:
                        "pid:[9999999999]" if str(path) == "/proc/1/ns/pid"
                        else real_readlink(path, *a, **kw))
    rejected(path, user)


def test_timeout_is_not_an_offline_witness(tmp_path, isolated, monkeypatch):
    require_host_proc()
    user, target = isolated
    path = archive(tmp_path, {"config.yaml": "restored"})
    monkeypatch.setattr(backup, "_IMPORT_SCAN_SECONDS", -1)
    rejected(path, user)


def test_mmap_holder_survives_closed_original_fd(tmp_path, isolated):
    require_host_proc()
    import mmap
    user, target = isolated
    held = target / "mapped.data"
    held.write_bytes(b"mapped fixture")
    path = archive(tmp_path, {"config.yaml": "restored"})
    with held.open("rb") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        rejected(path, user)
    finally:
        mapping.close()


def test_real_cli_version_flag_does_not_restore(tmp_path, isolated):
    user, target = isolated
    path = archive(tmp_path, {"config.yaml": "restored"})
    before = snapshot(user)
    env = os.environ.copy()
    env.update(HOME=str(user), HERMES_HOME=str(target), HERMES_SKIP_CHMOD="1",
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    proc = subprocess.run([sys.executable, "-m", "korra_cli.main", "--version",
                           "import", str(path), "--force"], env=env, capture_output=True, timeout=30)
    assert proc.returncode == 0
    assert snapshot(user) == before


def test_failed_rollback_keeps_mapped_original_wal_for_manual_recovery(tmp_path, isolated, monkeypatch, capsys):
    require_host_proc()
    import json
    user, target = isolated
    db = target / "state.db"
    database(db, "original")
    # A crashed but now offline writer leaves a real committed WAL bundle.
    proc = subprocess.run([sys.executable, "-c",
        "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
        "c.execute('PRAGMA journal_mode=WAL'); "
        "c.execute(\"INSERT INTO sample VALUES ('committed WAL fixture')\"); "
        "c.commit(); os._exit(0)", str(db)], capture_output=True, timeout=10)
    assert proc.returncode == 0
    wal_before = Path(str(db) + "-wal").read_bytes()
    path = archive(tmp_path, {"config.yaml": "new", "state.db": database(tmp_path / "source.db")})
    original_replace = os.replace
    def fail(src, dst, *args, **kwargs):
        src, dst = Path(src), Path(dst)
        if src.name == "state.db-shm" and dst.parent.name.startswith(".korra-import-"):
            raise OSError("synthetic forward failure")
        if src.name.startswith("sidecar-") and dst.name == "state.db-wal":
            raise OSError("synthetic rollback failure")
        return original_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(SystemExit):
        restore(path)
    assert "откат неполный" in capsys.readouterr().out
    journals = list(target.glob(".korra-import-*"))
    assert journals
    mappings = [json.loads(p.read_text()) for journal in journals for p in journal.glob("*.json")]
    assert any(entry["target"] == "state.db-wal" for entry in mappings)
    saved_wals = [p for journal in journals for p in journal.glob("sidecar-*") if not p.name.endswith(".json")]
    assert any(p.read_bytes() == wal_before for p in saved_wals)
    assert all(journal.stat().st_mode & 0o777 == 0o700 for journal in journals)

@pytest.mark.parametrize("marker", [b"\xff", b"", b"   \n"])
def test_real_cli_uncertain_active_profile_refuses_before_any_write(tmp_path, isolated, marker):
    require_host_proc()
    user, root = isolated
    (root / "active_profile").write_bytes(marker)
    path = archive(tmp_path, {"config.yaml": "model: wrong-root-overwrite\n"})
    before = snapshot(user)
    env = os.environ.copy()
    env.update(HOME=str(user), HERMES_HOME=str(root), HERMES_SKIP_CHMOD="1",
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    proc = subprocess.run([sys.executable, "-m", "korra_cli.main",
                           "import", str(path), "--force"], env=env, capture_output=True, timeout=30)
    assert proc.returncode != 0
    assert snapshot(user) == before
