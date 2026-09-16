"""Best-effort Linux local-command guard; container cgroups are the hard boundary.

RSS of a command tree is sampled, so a rapid allocation or detached daemon can
escape this guard. Never advertise it as isolation from neighbouring containers.
No shell command or user data is included in resource-limit logs.
"""
import logging
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional

import psutil

logger = logging.getLogger(__name__)

_MIN_WORKER_MEMORY_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_WORKER_MEMORY_MAX_BYTES = 1024 * 1024 * 1024
_WORKER_MEMORY_MAX_CAP_BYTES = 4 * 1024 * 1024 * 1024


def _worker_memory_max_bytes() -> int:
    """Return a finite per-worker cgroup limit without widening host risk.

    The internal override is populated from terminal.local_memory_max_mb.
    It can only tighten the safe bound. Reserve 40% of the enclosing cgroup
    for the control plane; otherwise use half of physical RAM, capped at 4 GiB.  This keeps the
    sibling worker outside the gateway cgroup while ensuring the worker cannot
    consume memory up to the enclosing user slice or host limit.
    """
    override_bound: Optional[int] = None
    override = os.getenv("TERMINAL_LOCAL_MEMORY_MAX_MB", "").strip()
    if override:
        override_valid = False
        try:
            parsed = int(override) * 1024 * 1024
            if parsed >= _MIN_WORKER_MEMORY_MAX_BYTES:
                override_bound = parsed
                override_valid = True
        except ValueError:
            pass
        if not override_valid:
            logger.warning(
                "Ignoring invalid TERMINAL_LOCAL_MEMORY_MAX_MB=%r; "
                "expected an integer representing at least %d MiB",
                override,
                _MIN_WORKER_MEMORY_MAX_BYTES // (1024 * 1024),
            )

    candidates: List[int] = []
    cgroup_root = Path("/sys/fs/cgroup")
    paths = [cgroup_root / "memory.max",
             cgroup_root / "memory/memory.limit_in_bytes"]
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            if line.startswith("0::"):
                relative = Path(line.partition("::")[2].lstrip("/"))
                if ".." not in relative.parts:
                    scope = cgroup_root / relative
                    # A child may say "max" while an ancestor is constrained.
                    while scope != cgroup_root:
                        paths.append(scope / "memory.max")
                        scope = scope.parent
                break
    except OSError:
        pass
    for path in paths:
        try:
            raw_limit = path.read_text(encoding="utf-8").strip()
            if raw_limit.isdigit() and int(raw_limit) >= _MIN_WORKER_MEMORY_MAX_BYTES:
                candidates.append(max(_MIN_WORKER_MEMORY_MAX_BYTES, int(raw_limit) * 3 // 5))
        except OSError:
            continue

    try:
        physical_bytes = int(os.sysconf("SC_PHYS_PAGES")) * int(
            os.sysconf("SC_PAGE_SIZE")
        )
        physical_bound = min(
            _WORKER_MEMORY_MAX_CAP_BYTES,
            max(_MIN_WORKER_MEMORY_MAX_BYTES, physical_bytes // 2),
        )
        candidates.append(physical_bound)
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    safe_bound = min(candidates) if candidates else _DEFAULT_WORKER_MEMORY_MAX_BYTES
    return min(override_bound, safe_bound) if override_bound else safe_bound


class LocalMemoryGuard:
    """Watch a single owned process tree without signalling recycled PIDs."""

    def __init__(self, pid, on_exceeded):
        self.limit = _worker_memory_max_bytes()
        self.report = None
        self._stop = threading.Event()
        self._on_exceeded = on_exceeded
        self._process = None
        if sys.platform == "linux" and isinstance(pid, int) and pid != os.getpid():
            try:
                self._process = psutil.Process(pid)
                self._process.create_time()  # capture identity before PID reuse
            except psutil.Error:
                pass
        self.thread = threading.Thread(target=self._watch, daemon=True,
                                       name="korra-local-memory")

    def start(self):
        try:
            self.thread.start()
        except RuntimeError:
            # A monitoring failure must never retry an already-spawned command.
            logger.warning("Cannot start local memory sampler; container limits remain required")

    def stop(self):
        # Never join here: the kill callback can itself finish a registry job.
        self._stop.set()

    def _watch(self):
        root = self._process
        if root is None:
            return
        while not self._stop.is_set():
            try:
                if not root.is_running() or root.status() == psutil.STATUS_ZOMBIE:
                    return
                processes = [root, *root.children(recursive=True)]
                rss = 0
                for process in processes:
                    try:
                        if process.is_running():
                            rss += process.memory_info().rss
                    except psutil.Error:
                        continue
                if rss > self.limit:
                    # Recheck ownership and cancellation after the process scan.
                    if self._stop.is_set() or not root.is_running():
                        return
                    self.report = {
                        "kind": "memory", "limit_bytes": self.limit,
                        "observed_bytes": rss,
                        "message": (
                            "Команда остановлена: превышен лимит памяти "
                            f"({self.limit // (1024 * 1024)} МиБ). "
                            "Используйте более лёгкую модель или обработайте данные частями. "
                            "Не повторяйте ту же команду без изменений."
                        ),
                    }
                    logger.warning("Local command memory limit exceeded: rss=%d limit=%d", rss, self.limit)
                    self._on_exceeded(self.report)
                    return
            except psutil.NoSuchProcess:
                return
            except psutil.Error as exc:
                logger.debug("Local memory sample unavailable: %s", type(exc).__name__)
            except Exception:
                logger.exception("Local command memory guard failed")
                return
            self._stop.wait(0.1)
