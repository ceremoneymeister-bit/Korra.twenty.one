"""K21-336: обёртка Node переводит единственный listen сервера на Unix-сокет (настоящий node)."""
from __future__ import annotations

import json
import http.client
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "docker" / "camofox" / "unix-socket.mjs"

@pytest.fixture(scope="module", autouse=True)
def require_node_register_hooks():
    node = shutil.which("node")
    if node is None:
        pytest.skip("нужен Node.js с node:module.registerHooks")
    probe = subprocess.run(
        [node, "--input-type=module", "-e",
         "import { registerHooks } from 'node:module'; registerHooks({}).deregister();"],
        capture_output=True, text=True, timeout=10,
    )
    if probe.returncode:
        pytest.skip(
            "локальный Node.js не поддерживает node:module.registerHooks "
            "(нужен Node 22.15+, 23.5+ или 24+): " + probe.stderr.strip()
        )

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


WATCHDOG_SERVER = """
import http from 'node:http';
import { spawn } from 'node:child_process';
const child = spawn(process.env.FAKE_BROWSER, JSON.parse(process.env.FAKE_ARGS || '["30"]'), { stdio: 'ignore' });
const health = { ok: true, browserRunning: true, activeTabs: Number(process.env.FAKE_TABS), activeSessions: 1 };
const server = http.createServer((req, res) => res.end(JSON.stringify(health)));
server.listen(9377, '127.0.0.1');
setTimeout(() => {
  console.log('browser-alive ' + (child.exitCode === null && child.signalCode === null));
  child.kill('SIGKILL');
  process.exit(0);
}, 2500);
"""


def _run_watchdog_server(run_dir: Path, *, tabs: int, limit_ms: str, program: str = "sleep", args: str = '["30"]'):
    sock = run_dir / "camofox" / "server.sock"
    script = run_dir / "watchdog-server.mjs"
    script.write_text(WATCHDOG_SERVER)
    browser = run_dir / "camoufox-bin"
    shutil.copy(shutil.which(program), browser)
    proc = subprocess.Popen(
        ["node", str(WRAPPER), str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "CAMOFOX_UNIX_SOCKET": str(sock),
            "FAKE_BROWSER": str(browser),
            "FAKE_TABS": str(tabs),
            "FAKE_ARGS": args,
            "KORRA_CAMOFOX_STUCK_LIMIT_MS": limit_ms,
        },
    )
    out, err = proc.communicate(timeout=15)
    alive = "browser-alive true" in out
    assert "browser-alive" in out
    return alive, err


def test_watchdog_kills_a_browser_that_stays_loaded_without_tabs(run_dir):
    alive, err = _run_watchdog_server(run_dir, tabs=0, limit_ms="500")
    assert not alive
    assert "принудительное завершение Firefox (1 процессов)" in err


def test_watchdog_leaves_a_browser_with_open_tabs_alone(run_dir):
    alive, err = _run_watchdog_server(run_dir, tabs=1, limit_ms="500")
    assert alive
    assert "принудительное завершение" not in err


def test_watchdog_can_be_disabled_with_zero_limit(run_dir):
    alive, err = _run_watchdog_server(run_dir, tabs=0, limit_ms="0")
    assert alive
    assert "принудительное завершение" not in err


def test_watchdog_kills_the_whole_browser_tree_including_stopped_children(run_dir):
    pidfile = run_dir / "child.pid"
    script = f"sleep 6 & echo $! > {pidfile}; wait"
    alive, err = _run_watchdog_server(
        run_dir, tabs=0, limit_ms="500", program="sh", args=json.dumps(["-c", script])
    )
    assert not alive
    child = int(pidfile.read_text())
    try:
        state = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        state = "Z"
    assert state in ("Z", "X"), "потомок Firefox пережил принудительное завершение"
    assert "(2 процессов)" in err


# Зависшее закрытие сессии переживает убийство Firefox: апстрим уходит в перезапуск и /health
# остаётся «recovering» (так было на живом стенде). Здесь /health после убийства либо зависает
# в этом состоянии, либо восстанавливается.
HUNG_RESTART_SERVER = """
import http from 'node:http';
import { spawn } from 'node:child_process';
const child = spawn(process.env.FAKE_BROWSER, ['30'], { stdio: 'ignore' });
const display = spawn(process.env.FAKE_DISPLAY, ['30'], { stdio: 'ignore' });
console.log('display-pid ' + display.pid);
let killed = false;
child.on('exit', () => { killed = true; });
const server = http.createServer((req, res) => {
  if (killed && process.env.FAKE_RECOVERS !== '1') {
    res.statusCode = 503;
    return res.end(JSON.stringify({ ok: false, engine: 'camoufox', recovering: true }));
  }
  res.end(JSON.stringify({ ok: true, browserRunning: !killed, activeTabs: 0, activeSessions: 1 }));
});
server.listen(9377, '127.0.0.1');
setTimeout(() => process.exit(0), 6000);
"""


def _run_hung_restart(run_dir: Path, *, recovers: bool):
    sock = run_dir / "camofox" / "server.sock"
    script = run_dir / "hung-server.mjs"
    script.write_text(HUNG_RESTART_SERVER)
    browser = run_dir / "camoufox-bin"
    shutil.copy(shutil.which("sleep"), browser)
    display = run_dir / "Xvfb"
    shutil.copy(shutil.which("sleep"), display)
    proc = subprocess.Popen(
        ["node", str(WRAPPER), str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "CAMOFOX_UNIX_SOCKET": str(sock),
            "FAKE_BROWSER": str(browser),
            "FAKE_DISPLAY": str(display),
            "FAKE_RECOVERS": "1" if recovers else "0",
            "KORRA_CAMOFOX_STUCK_LIMIT_MS": "500",
            "KORRA_CAMOFOX_STUCK_RECOVERY_MS": "800",
        },
    )
    out, err = proc.communicate(timeout=15)
    display_pid = int(out.split("display-pid ")[1].split()[0])
    return proc.returncode, err, display_pid


def _is_dead(pid: int, wait: float = 3.0) -> bool:
    # SIGKILL доходит не мгновенно: под нагрузкой параллельного прогона процесс
    # ещё секунду-другую виден как работающий. Ждём ограниченно, а не однажды.
    deadline = time.monotonic() + wait
    while True:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if state in ("Z", "X"):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def test_watchdog_restarts_the_whole_process_when_the_server_stays_in_recovery(run_dir):
    code, err, display_pid = _run_hung_restart(run_dir, recovers=False)
    assert code == 1
    assert "принудительное завершение Firefox" in err
    assert "перезапуск процесса" in err
    assert _is_dead(display_pid), "Xvfb пережил перезапуск и занял бы дисплей"


def test_watchdog_keeps_the_process_when_the_server_recovers_after_the_kill(run_dir):
    code, err, display_pid = _run_hung_restart(run_dir, recovers=True)
    assert code == 0
    assert "принудительное завершение Firefox" in err
    assert "перезапуск процесса" not in err


CONTEXT_HEALTH_SERVER = """
import http from 'node:http';
let created = 0;
let closed = 0;
const hang = () => new Promise(() => {});
const browser = process.env.FAKE_STAGE === 'idle' ? null : {
  isConnected: () => true,
  async newContext() {
    created++;
    if (process.env.FAKE_STAGE === 'context') return hang();
    return {
      async newPage() { if (process.env.FAKE_STAGE === 'page') return hang(); },
      async close() { closed++; if (process.env.FAKE_STAGE === 'close') return hang(); },
    };
  },
};
let handler;
const app = { get(route, fn) { handler = fn; } };
app.get('/health', (req, res) => {
  res.json({ ok: true, browserRunning: !!browser, created, closed });
});
http.createServer((req, res) => {
  res.status = code => { res.statusCode = code; return res; };
  res.json = data => res.end(JSON.stringify(data));
  if (req.url === '/stats') return res.json({ created, closed });
  handler(req, res);
}).listen(9377);
"""


@pytest.mark.parametrize("stage", ["context", "page", "close", "healthy", "idle"])
def test_health_checks_context_and_page_with_one_deadline_without_starting_idle_browser(run_dir, stage):
    sock = run_dir / "server.sock"
    script = run_dir / "server.js"
    script.write_text(CONTEXT_HEALTH_SERVER)
    proc = subprocess.Popen(
        ["node", str(WRAPPER), str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "CAMOFOX_UNIX_SOCKET": str(sock), "FAKE_STAGE": stage},
    )

    def health(route="/health"):
        conn = http.client.HTTPConnection("camofox.invalid", timeout=8)
        conn.sock = socket.socket(socket.AF_UNIX)
        conn.sock.settimeout(8)
        conn.sock.connect(str(sock))
        try:
            conn.request("GET", route)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    try:
        _wait_for(sock)
        if stage == "context":
            with ThreadPoolExecutor(max_workers=2) as pool:
                answers = list(pool.map(lambda _: health(), range(2)))
            assert answers[0] == answers[1]
            status, body = answers[0]
            assert health("/stats")[1]["created"] == 1
        else:
            status, body = health()
        if stage in ("healthy", "idle"):
            assert status == 200 and body["ok"] is True
            assert body["created"] == body["closed"] == (1 if stage == "healthy" else 0)
        else:
            assert status == 503 and body["ok"] is False
            assert body["contextReady"] is False
            # После таймаута не накапливаем новые зависшие фабрики.
            assert health() == (status, body)
    finally:
        proc.kill()
        proc.communicate(timeout=5)


def test_changed_upstream_health_route_refuses_to_start_instead_of_reporting_false_readiness(run_dir):
    script = run_dir / "node_modules" / "@askjo" / "camofox-browser" / "server.js"
    script.parent.mkdir(parents=True)
    script.write_text(CONTEXT_HEALTH_SERVER.replace("app.get('/health',", 'app.get("/health",'))
    done = subprocess.run(
        ["node", str(WRAPPER), str(script)], capture_output=True, text=True, timeout=10,
        env={**os.environ, "CAMOFOX_UNIX_SOCKET": str(run_dir / "server.sock")},
    )
    assert done.returncode == 1
    assert "проверка context/page не установлена" in done.stderr


# Виртуальный экран: Xvfb из camoufox-js не должен слушать абстрактный сокет, который
# виден всем контейнерам в сети хоста. Каталог пакета повторяет раскладку
# node_modules/camoufox-js/dist/virtdisplay.js, а аргументы Xvfb — дословно из camoufox-js 0.11.5.
VIRTDISPLAY_FIXTURE = """
import { spawn } from 'node:child_process';
export class VirtualDisplay {
    proc = null;
    get xvfb_args() {
        return [
            "-screen",
            "0",
            "1x1x24",
            "-ac",
            "-nolisten",
            "tcp",
            "-extension",
            "RENDER",
            "-nocursor",
            "-br",
        ];
    }
    async get() {
        this.proc = spawn('Xvfb', ['-displayfd', '3', ...this.xvfb_args], {
            stdio: ['ignore', 'ignore', 'ignore', 'pipe'],
            detached: true,
        });
        let buf = '';
        for await (const chunk of this.proc.stdio[3]) {
            buf += chunk;
            if (buf.includes('\\n')) break;
        }
        return ':' + Number.parseInt(buf, 10);
    }
    kill() { try { process.kill(-this.proc.pid, 'SIGKILL'); } catch {} }
}
"""

# Класс сервера (DefaultVirtualDisplay) наследует базовый и меняет только размер экрана.
DISPLAY_SERVER = """
import fs from 'node:fs';
import { VirtualDisplay } from 'camoufox-js/dist/virtdisplay.js';
class DefaultVirtualDisplay extends VirtualDisplay {
  get xvfb_args() {
    const patched = [...super.xvfb_args];
    patched[patched.indexOf('0') + 1] = '1280x720x24';
    return patched;
  }
}
const display = new DefaultVirtualDisplay();
console.log('args ' + JSON.stringify(display.xvfb_args));
if (process.env.FAKE_START_XVFB === '1') {
  const name = await display.get();
  // Сокеты именно этого Xvfb: на хосте теста могут быть чужие экраны с тем же номером.
  const inodes = new Set();
  for (const fd of fs.readdirSync('/proc/' + display.proc.pid + '/fd')) {
    try {
      const match = /^socket:\\[(\\d+)\\]$/.exec(fs.readlinkSync('/proc/' + display.proc.pid + '/fd/' + fd));
      if (match) inodes.add(match[1]);
    } catch {}
  }
  const listening = fs.readFileSync('/proc/net/unix', 'utf8').split('\\n')
    .map((line) => line.trim().split(/\\s+/))
    .filter((cols) => inodes.has(cols[6]) && cols[7] && cols[7].includes('X11-unix'))
    .map((cols) => cols[7]);
  const abstract = listening.some((name) => name.startsWith('@'));
  const file = listening.some((name) => name.startsWith('/'));
  console.log('sockets abstract=' + abstract + ' file=' + file + ' ' + JSON.stringify(listening));
  display.kill();
}
process.exit(0);
"""


def _run_display_server(run_dir: Path, fixture: str, *, start_xvfb: bool = False):
    package = run_dir / "node_modules" / "camoufox-js"
    (package / "dist").mkdir(parents=True)
    (package / "package.json").write_text('{"name":"camoufox-js","type":"module","main":"dist/index.js"}')
    (package / "dist" / "virtdisplay.js").write_text(fixture)
    script = run_dir / "display-server.mjs"
    script.write_text(DISPLAY_SERVER)
    done = subprocess.run(
        ["node", str(WRAPPER), str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "CAMOFOX_UNIX_SOCKET": str(run_dir / "camofox" / "server.sock"),
            "FAKE_START_XVFB": "1" if start_xvfb else "0",
        },
    )
    return done


def test_xvfb_arguments_of_the_server_display_class_stop_listening_on_the_abstract_socket(run_dir):
    done = _run_display_server(run_dir, VIRTDISPLAY_FIXTURE)
    assert done.returncode == 0, done.stderr
    args = json.loads(next(l for l in done.stdout.splitlines() if l.startswith("args ")).removeprefix("args "))
    pairs = [tuple(args[i : i + 2]) for i in range(len(args) - 1)]
    assert ("-nolisten", "local") in pairs
    assert ("-nolisten", "tcp") in pairs
    assert "1280x720x24" in args


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="нужен Xvfb")
def test_real_xvfb_has_only_the_file_socket(run_dir):
    done = _run_display_server(run_dir, VIRTDISPLAY_FIXTURE, start_xvfb=True)
    assert done.returncode == 0, done.stderr
    assert "sockets abstract=false file=true" in done.stdout


def test_wrapper_refuses_to_start_when_the_xvfb_block_changed(run_dir):
    changed = VIRTDISPLAY_FIXTURE.replace('"-nolisten",\n            "tcp",', '"-listen",\n            "inet",')
    assert changed != VIRTDISPLAY_FIXTURE
    done = _run_display_server(run_dir, changed)
    assert done.returncode == 1
    assert "не найден блок аргументов Xvfb" in done.stderr
    assert "args " not in done.stdout


LIFECYCLE_SERVER = """
let browser = { isConnected: () => true };
const sessions = new Map();
const sessionCreations = new Map();
const order = [];
async function ensureBrowser() {
  clearBrowserIdleTimer();
  if (!browser) browser = { isConnected: () => true };
  return browser;
}
function clearBrowserIdleTimer() {}
async function closeBrowserFully(reason) { order.push(reason); browser = null; }
async function closeSession(userId, session, {
  reason = 'session_closed',
} = {}) {
  const key = userId;
  order.push('persist:' + key);
  await new Promise(r => setTimeout(r, 50));
  await session.context.close().catch(() => {});
  sessions.delete(key);
}
const context = { async close() { order.push('context-close'); } };
const a = { context }, b = { context };
sessions.set('a', a); sessions.set('b', b);
await closeSession('a', a);
console.log('other-session-browser=' + !!browser);
const closing = closeSession('b', b);
// A new request during persistence must wait for teardown and start fresh.
const next = ensureBrowser().then(() => order.push('next-browser'));
await Promise.all([closing, next]);
console.log(JSON.stringify(order));
await closeSession('missing', { context });
console.log('empty-browser=' + !!browser);
"""


def test_last_session_unloads_browser_after_persistence_and_before_next_launch(run_dir):
    script = run_dir / 'lifecycle.mjs'
    script.write_text(LIFECYCLE_SERVER)
    done = subprocess.run(
        ['node', str(WRAPPER), str(script)], capture_output=True, text=True, timeout=10,
        env={**os.environ, 'CAMOFOX_UNIX_SOCKET':str(run_dir / 'server.sock')},
    )
    assert done.returncode == 0, done.stderr
    assert 'other-session-browser=true' in done.stdout
    assert 'empty-browser=false' in done.stdout
    order = json.loads(next(line for line in done.stdout.splitlines() if line.startswith('[')))
    assert order.index('persist:b') < order.index('idle_shutdown') < order.index('next-browser')


@pytest.mark.parametrize('stage', ['context', 'teardown'])
def test_lifecycle_deadline_terminates_instead_of_leaving_a_pending_factory(run_dir, stage):
    prefix = LIFECYCLE_SERVER.split('const context = {')[0]
    prefix += """
import { spawn } from 'node:child_process';
const child = spawn(process.execPath, ['-e', "process.title = 'firefox'; console.log('ready'); setInterval(() => {}, 1000)"]);
await new Promise(resolve => child.stdout.once('data', resolve));
console.log('child-pid=' + child.pid);
"""
    if stage == 'context':
        prefix += """
browser.newContext = () => new Promise(() => {});
async function create() { const b = browser; const context = await b.newContext(contextOptions); }
const contextOptions = {};
setInterval(() => {}, 1000);
await create();
"""
    else:
        prefix += """
const session = { context: { close: () => new Promise(() => {}) } };
sessions.set('a', session);
setInterval(() => {}, 1000);
await closeSession('a', session);
"""
    script = run_dir / 'deadline.mjs'
    script.write_text(prefix)
    done = subprocess.run(
        ['node', str(WRAPPER), str(script)], capture_output=True, text=True, timeout=9,
        env={**os.environ, 'CAMOFOX_UNIX_SOCKET':str(run_dir / 'server.sock')},
    )
    pid = int(next(line.split('=')[1] for line in done.stdout.splitlines() if line.startswith('child-pid=')))
    status = Path(f'/proc/{pid}/status')
    for _ in range(100):
        try:
            alive = '\nState:\tZ' not in status.read_text()
        except (FileNotFoundError, ProcessLookupError):
            alive = False
        if not alive:
            break
        time.sleep(0.01)
    if alive:
        os.kill(pid, 9)
    assert not alive, 'deadline left the owned Firefox process running'
    assert done.returncode == 1
    assert 'exceeded 5s; terminating service and browser tree' in done.stderr
    assert 'ReferenceError' not in done.stderr


def test_reaper_preserves_last_page_until_storage_is_saved(run_dir):
    # Execute the same reaper operations on a context whose storage needs a live page.
    script = run_dir / 'reap.mjs'
    prefix = LIFECYCLE_SERVER.split('const context = {')[0]
    prefix = prefix.replace("order.push('persist:' + key);", "if (!session.page.alive) throw Error('storage lost'); order.push('persist:' + key);")
    script.write_text(prefix + """
const tabState = { page: { alive: true } };
const tabId = 'tab';
const group = new Map([[tabId, tabState]]);
const session = { page: tabState.page, tabGroups: new Map([['group', group]]),
  context: { async close() { tabState.page.alive = false; } } };
sessions.set('user', session);
function safePageClose(page) { page.alive = false; }
function hasActivePageLeases() { return false; }
            safePageClose(tabState.page);
            group.delete(tabId);
session.tabGroups.clear();
await closeSession('user', session);
console.log('storage-saved-and-browser-unloaded=' + !browser);
""")
    done = subprocess.run(
        ['node', str(WRAPPER), str(script)], capture_output=True, text=True, timeout=9,
        env={**os.environ, 'CAMOFOX_UNIX_SOCKET':str(run_dir / 'server.sock')},
    )
    assert done.returncode == 0, done.stderr
    assert 'storage-saved-and-browser-unloaded=true' in done.stdout
