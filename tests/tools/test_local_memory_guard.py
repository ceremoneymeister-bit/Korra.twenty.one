"""Exercise the actual local terminal paths, with small allocations and no model."""
import shlex
import sys

import psutil
import pytest

from tools.environments.local import LocalEnvironment
from tools.process_registry import ProcessRegistry

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux memory guard')


def allocation_command(tmp_path):
    # Two children exceed the budget together; neither does on its own.
    script = tmp_path / 'allocate.py'
    script.write_text('import time\na=bytearray(38*1024*1024)\ntime.sleep(2)\n')
    child = f'{shlex.quote(sys.executable)} {shlex.quote(str(script))}'
    return f'{child} & {child} & wait'


@pytest.fixture(autouse=True)
def small_budget(monkeypatch):
    monkeypatch.setenv('TERMINAL_LOCAL_MEMORY_MAX_MB', '64')
    monkeypatch.setattr('tools.process_registry._is_supervised_gateway_process', lambda: False)


def test_foreground_tree_stopped_but_next_command_works(tmp_path):
    env = LocalEnvironment(cwd=str(tmp_path))
    result = env.execute(allocation_command(tmp_path), timeout=15)
    assert result['returncode'] != 0
    assert result['resource_limit']['kind'] == 'memory'
    assert result['resource_limit']['limit_bytes'] == 64 * 1024**2
    assert 'памят' in result['output']
    following = env.execute('printf STILL_ALIVE', timeout=10)
    assert following['returncode'] == 0
    assert 'STILL_ALIVE' in following['output']


@pytest.mark.parametrize('use_pty', [False, True])
def test_background_tree_reports_memory_reason_and_preserves_sibling(tmp_path, use_pty):
    if use_pty:
        pytest.importorskip('ptyprocess')
    registry = ProcessRegistry()
    sibling = registry.spawn_local('sleep 30', cwd=str(tmp_path))
    job = registry.spawn_local(allocation_command(tmp_path), cwd=str(tmp_path), use_pty=use_pty)
    try:
        assert job._completion_event.wait(15)
        assert job.completion_reason == 'killed'
        assert job.termination_source == 'memory_limit'
        assert 'памят' in job.output_buffer
        assert not sibling.exited
        assert psutil.Process(sibling.pid).is_running()
    finally:
        registry.kill_process(job.id)
        registry.kill_process(sibling.id)


def test_ordinary_command_releases_guard(tmp_path):
    env = LocalEnvironment(cwd=str(tmp_path))
    proc = env._run_bash('printf OK')
    result = env._wait_for_process(proc, timeout=10)
    assert result['returncode'] == 0
    assert result['output'] == 'OK'
    assert 'resource_limit' not in result
    guard = proc._korra_memory_guard
    guard.thread.join(2)
    assert not guard.thread.is_alive()


def test_terminal_tool_exposes_resource_failure_without_retry(tmp_path, monkeypatch):
    import json
    from tools import terminal_tool as tool
    monkeypatch.setenv("TERMINAL_ENV", "local")
    script = tmp_path / "tool-allocate.py"
    starts = tmp_path / "starts.txt"
    script.write_text(f"from pathlib import Path\nimport time\n"
                      f"with Path({str(starts)!r}).open('a') as f: f.write('start\\n')\n"
                      "a=bytearray(80*1024*1024)\ntime.sleep(2)\n")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    result = json.loads(tool.terminal_tool(command, timeout=15, workdir=str(tmp_path),
                                            task_id="memory-test"))
    assert result["exit_code"] != 0
    assert result.get("resource_limit", {}).get("kind") == "memory", result
    assert "Не повторяйте" in result["output"]
    assert starts.read_text().splitlines() == ["start"]


def test_budget_reserves_gateway_memory_in_parent_cgroup(monkeypatch):
    from tools.local_memory import _worker_memory_max_bytes
    from pathlib import Path
    files = {"/proc/self/cgroup": "0::/parent/leaf\n",
             "/sys/fs/cgroup/parent/leaf/memory.max": "max",
             "/sys/fs/cgroup/parent/memory.max": str(512 * 1024**2)}
    def read(path, **_kwargs):
        if str(path) not in files:
            raise FileNotFoundError(path)
        return files[str(path)]
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setenv("TERMINAL_LOCAL_MEMORY_MAX_MB", "99999")
    assert _worker_memory_max_bytes() == 512 * 1024**2 * 3 // 5


def test_guard_never_signals_a_recycled_process(monkeypatch):
    from tools.local_memory import LocalMemoryGuard
    from unittest.mock import Mock
    root = Mock()
    root.is_running.return_value = False  # psutil identity mismatch
    monkeypatch.setattr(psutil, "Process", lambda _pid: root)
    stopped = []
    guard = LocalMemoryGuard(987654321, stopped.append)
    guard.start()
    guard.thread.join(2)
    assert not stopped
    root.children.assert_not_called()


def test_sampler_start_failure_does_not_retry_the_command(monkeypatch):
    from tools.local_memory import LocalMemoryGuard
    guard = LocalMemoryGuard(987654321, lambda _report: None)
    def unavailable():
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(guard.thread, "start", unavailable)
    guard.start()  # no exception into PTY fallback / terminal retry
    assert guard.report is None
