"""Runtime IPC must not turn a complete backup into a false data-loss alarm."""
import logging
import os
import socket
import tempfile
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from korra_cli import backup


@pytest.fixture
def contour(monkeypatch):
    # AF_UNIX pathname length is bounded even when pytest's per-test name is long.
    with tempfile.TemporaryDirectory(prefix="k82-") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        (home / "config.yaml").write_text("model: {}\n")
        user = home / "workspace"
        user.mkdir()
        (user / "gateway.sock").write_text("ordinary user file, despite its name")
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(Path, "home", lambda: root)
        monkeypatch.setattr(backup, "_collect_memory_provider_external_paths", lambda: [])
        yield root, home


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="requires Unix sockets")
@pytest.mark.parametrize("automatic", [False, True])
def test_closed_socket_excluded_but_same_named_regular_file_preserved(contour, automatic, capsys, caplog):
    root, home = contour
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as ipc:
        ipc.bind(str(home / "gateway.sock"))
    assert (home / "gateway.sock").is_socket()
    archive = root / "backup.zip"
    caplog.set_level(logging.DEBUG, logger="korra_cli.backup")
    if automatic:
        assert backup._write_full_zip_backup(archive, home) == archive
    else:
        backup.run_backup(Namespace(output=str(archive)))
        assert "не полностью" not in capsys.readouterr().out
    with zipfile.ZipFile(archive) as saved:
        assert "gateway.sock" not in saved.namelist()
        assert saved.read("workspace/gateway.sock") == b"ordinary user file, despite its name"
    assert "Skipping gateway.sock" not in caplog.text


@pytest.mark.parametrize("automatic", [False, True])
def test_regular_file_read_error_remains_visible(contour, monkeypatch, automatic, capsys, caplog):
    root, home = contour
    archive = root / "backup.zip"
    previous = b"previous verified archive"
    if automatic:
        archive.write_bytes(previous)
    real_write = zipfile.ZipFile.write

    def unreadable(zf, filename, *args, **kwargs):
        if Path(filename) == home / "workspace" / "gateway.sock":
            raise PermissionError("test read denied")
        return real_write(zf, filename, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", unreadable)
    if automatic:
        assert backup._write_full_zip_backup(archive, home) is None
        assert "test read denied" in caplog.text
        assert archive.read_bytes() == previous
    else:
        backup.run_backup(Namespace(output=str(archive)))
        output = capsys.readouterr().out
        assert "не полностью" in output
        assert "test read denied" in output


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="requires Unix sockets")
def test_external_memory_socket_is_excluded(contour, monkeypatch, capsys):
    root, home = contour
    external = root / "provider"
    external.mkdir()
    (external / "memory.txt").write_text("preserved")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as ipc:
        ipc.bind(str(external / "server.sock"))
    monkeypatch.setattr(backup, "_collect_memory_provider_external_paths", lambda: [external])
    archive = root / "backup.zip"
    backup.run_backup(Namespace(output=str(archive)))
    assert "не полностью" not in capsys.readouterr().out
    with zipfile.ZipFile(archive) as saved:
        assert saved.read("_external/provider/memory.txt") == b"preserved"
        assert not any(name.endswith("server.sock") for name in saved.namelist())


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires named pipes")
def test_fifo_is_filtered_before_archive_open(contour):
    root, home = contour
    fifo = home / "worker.pipe"
    os.mkfifo(fifo)
    # Opening a FIFO without a writer would hang; assert the scan boundary
    # before exercising the real backup writer.
    assert backup._should_skip_backup_file(fifo, fifo.relative_to(home), root / "backup.zip")
    assert backup._write_full_zip_backup(root / "backup.zip", home) is not None
