"""K21-336: обёртка Node переводит единственный listen сервера на Unix-сокет (настоящий node)."""
from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "docker" / "camofox" / "unix-socket.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")

FAKE_SERVER = """
import http from 'node:http';
const server = http.createServer((req, res) => res.end('ok'));
server.listen(9377, process.env.CAMOFOX_BIND_HOST || undefined, () => console.log('listening ' + server.address()));
setTimeout(() => {
  try { http.createServer().listen(0, '127.0.0.1'); console.log('second-tcp-allowed'); }
  catch (error) { console.log('second-tcp-blocked'); }
}, 200);
setTimeout(() => process.exit(0), 1500);
"""


@pytest.fixture
def run_dir():
    path = Path(tempfile.mkdtemp(prefix="kw", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _start(run_dir: Path, sock: Path, *, umask: int = 0o022):
    script = run_dir / "fake-server.mjs"
    script.write_text(FAKE_SERVER)
    env = {**os.environ, "CAMOFOX_UNIX_SOCKET": str(sock), "CAMOFOX_BIND_HOST": "127.0.0.1"}
    return subprocess.Popen(
        ["node", str(WRAPPER), str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        preexec_fn=lambda: os.umask(umask),
    )


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} did not appear")


def _get(sock: Path) -> bytes:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(sock))
        client.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        data = b""
        while chunk := client.recv(4096):
            data += chunk
    return data


def test_listen_is_redirected_to_a_private_socket_in_a_private_directory(run_dir):
    sock = run_dir / "camofox" / "server.sock"
    proc = _start(run_dir, sock, umask=0o022)
    try:
        _wait_for(sock)
        assert stat.S_IMODE(sock.stat().st_mode) == 0o600
        assert stat.S_IMODE(sock.parent.stat().st_mode) == 0o700
        assert _get(sock).endswith(b"ok")
        out, err = proc.communicate(timeout=10)
    finally:
        proc.kill()
    assert f"listening {sock}" in out
    assert "second-tcp-blocked" in out and "second-tcp-allowed" not in out


def test_server_never_opens_a_tcp_port(run_dir):
    sock = run_dir / "camofox" / "server.sock"
    proc = _start(run_dir, sock)
    try:
        _wait_for(sock)
        with socket.socket() as probe:
            probe.settimeout(0.5)
            assert probe.connect_ex(("127.0.0.1", 9377)) != 0
        proc.communicate(timeout=10)
    finally:
        proc.kill()


def test_a_stale_socket_file_does_not_stop_the_start(run_dir):
    sock = run_dir / "camofox" / "server.sock"
    sock.parent.mkdir()
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(sock))
    stale.close()
    assert sock.exists()
    proc = _start(run_dir, sock)
    try:
        time.sleep(0.5)
        assert _get(sock).endswith(b"ok")
        proc.communicate(timeout=10)
    finally:
        proc.kill()


def test_wrapper_refuses_to_start_without_a_socket_path(run_dir):
    script = run_dir / "x.mjs"
    script.write_text("console.log('should not run')")
    env = {k: v for k, v in os.environ.items() if k != "CAMOFOX_UNIX_SOCKET"}
    done = subprocess.run(["node", str(WRAPPER), str(script)], capture_output=True, text=True, env=env, timeout=10)
    assert done.returncode == 1
    assert "should not run" not in done.stdout


PREWARM_SERVER = """
import http from 'node:http';
let launched = 0;
async function ensureBrowser() { launched += 1; }
function log(level, message) { console.log(message); }
function scheduleBrowserIdleShutdown() {}
const server = http.createServer((req, res) => res.end('launched=' + launched));
server.listen(9377, '127.0.0.1', async () => {
  try {
    const start = Date.now();
    await ensureBrowser();
    log('info', 'browser pre-warmed', { ms: Date.now() - start });
    scheduleBrowserIdleShutdown();
  } catch (err) {
    log('error', 'pre-warm failed');
  }
  console.log('ready');
});
setTimeout(() => process.exit(0), 1500);
"""


def _run_prewarm_server(run_dir: Path, source: str):
    sock = run_dir / "camofox" / "server.sock"
    script = run_dir / "prewarm-server.mjs"
    script.write_text(source)
    proc = subprocess.Popen(
        ["node", str(WRAPPER), str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "CAMOFOX_UNIX_SOCKET": str(sock)},
    )
    try:
        _wait_for(sock)
        time.sleep(0.3)
        body = _get(sock).decode()
        out, err = proc.communicate(timeout=10)
    finally:
        proc.kill()
    return body, out, err


def test_browser_prewarm_at_start_is_skipped_without_touching_the_package(run_dir):
    body, out, err = _run_prewarm_server(run_dir, PREWARM_SERVER)
    assert body.endswith("launched=0")
    assert "ready" in out
    assert "pre-warm skipped" in out and "browser pre-warmed" not in out
    assert "не найден" not in err
    assert (run_dir / "prewarm-server.mjs").read_text() == PREWARM_SERVER


def test_changed_package_keeps_working_with_prewarm_and_says_so(run_dir):
    changed = PREWARM_SERVER.replace("await ensureBrowser();", "await ensureBrowser(); /* новая версия */")
    body, out, err = _run_prewarm_server(run_dir, changed)
    assert body.endswith("launched=1")
    assert "блок прогрева в server.js не найден" in err
