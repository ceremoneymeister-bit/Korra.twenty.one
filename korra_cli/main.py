#!/usr/bin/env python3
"""
Hermes CLI - Main entry point.

Usage:
    hermes                     # Interactive chat (default)
    hermes chat                # Interactive chat
    hermes gateway             # Run gateway in foreground
    hermes gateway start       # Start gateway as service
    hermes gateway stop        # Stop gateway service
    hermes gateway status      # Show gateway status
    hermes gateway install     # Install gateway service
    hermes gateway uninstall   # Uninstall gateway service
    hermes setup               # Interactive setup wizard
    hermes logout              # Clear stored authentication
    hermes status              # Show status of all components
    hermes cron                # Manage cron jobs
    hermes cron list           # List cron jobs
    hermes cron status         # Check if cron scheduler is running
    hermes doctor              # Check configuration and dependencies
    hermes honcho setup                    # Configure Honcho AI memory integration
    hermes honcho status                   # Show Honcho config and connection status
    hermes honcho sessions                 # List directory → session name mappings
    hermes honcho map <name>               # Map current directory to a session name
    hermes honcho peer                     # Show peer names and dialectic settings
    hermes honcho peer --user NAME         # Set user peer name
    hermes honcho peer --ai NAME           # Set AI peer name
    hermes honcho peer --reasoning LEVEL   # Set dialectic reasoning level
    hermes honcho mode                     # Show current memory mode
    hermes honcho mode [hybrid|honcho|local]  # Set memory mode
    hermes honcho tokens                   # Show token budget settings
    hermes honcho tokens --context N       # Set session.context() token cap
    hermes honcho tokens --dialectic N     # Set dialectic result char cap
    hermes honcho identity                 # Show AI peer identity representation
    hermes honcho identity <file>          # Seed AI peer identity from a file (SOUL.md etc.)
    hermes honcho migrate                  # Step-by-step migration guide: OpenClaw native → Hermes + Honcho
    hermes --version           Show version and update status
    hermes update              Update to latest version
    hermes uninstall           Uninstall Hermes Agent
    hermes acp                 Run as an ACP server for editor integration
    hermes sessions browse     Interactive session picker with search

    hermes claw migrate --dry-run  # Preview migration without changes
"""

# IMPORTANT: korra_bootstrap must be the very first import — it sets up
# UTF-8 stdio on Windows so print()/subprocess children don't hit
# UnicodeEncodeError with non-ASCII characters.  No-op on POSIX.
#
# Guarded against ModuleNotFoundError because ``korra_bootstrap`` is a
# top-level module registered via pyproject.toml's ``py-modules`` list.
# When the user upgrades code via ``git pull`` (or ``hermes update``
# crashes between ``git reset --hard`` and ``uv pip install -e .``), the
# new code references ``korra_bootstrap`` but the editable install's
# ``.pth`` file still points at the old set of top-level modules.  Without
# this guard, hermes crashes on import and the user can't run
# ``hermes update`` to recover.  Missing the bootstrap means UTF-8 stdio
# setup is skipped on Windows — degraded, not broken.  POSIX is unaffected.
try:
    import korra_bootstrap  # noqa: F401
except ModuleNotFoundError:
    pass

# Windows: neutralize CPython's ``platform._syscmd_ver`` before anything else
# imports — it shells out ``cmd /c ver`` (shell=True, no CREATE_NO_WINDOW), so
# any dependency touching ``platform.uname()`` at import time flashes a
# visible console when this process is windowless (pythonw gateway + every
# kanban worker).  No-op on POSIX; never raises.
from korra_cli._subprocess_compat import suppress_platform_ver_console
from korra_cli.cli_output import line_input
from korra_constants import korra_env, korra_env_present, korra_env_set, korra_env_pop, korra_env_setdefault, korra_env_expand

suppress_platform_ver_console()

import os
import sys

# ── Startup fast-path bootstrap ─────────────────────────────────────────
# Two lines of inline path math so ``python korra_cli/main.py`` (script
# mode — sys.path[0] is korra_cli/, not the repo root) can import the
# canonical helpers; everything else lives in korra_cli._startup_fast.
_bootstrap_root = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
if _bootstrap_root not in sys.path:
    sys.path.insert(0, _bootstrap_root)
from korra_cli import _startup_fast  # noqa: E402

# Early venv self-heal — MUST run before any third-party import below.  When
# a prior ``hermes update`` left a recovery marker and a core package's import
# files were wiped (#57828 — failed lazy backend refresh), the module-level
# ``from korra_cli.env_loader import ...`` / ``from korra_cli.config import
# ...`` imports further down would crash before ``main()`` ever reaches
# ``_recover_from_interrupted_install()``.  ``_early_recovery`` is stdlib-only
# (safe to import on a corrupted venv), repairs just enough for this module to
# finish importing, and leaves the marker lifecycle to the full recovery path.
# The module import itself is unguarded on purpose: it lives in this same
# package directory, so if IT can't import, nothing else in korra_cli can
# either. It is also the canonical home of the probe/repair tables reused by
# the full recovery path below.
from korra_cli import _early_recovery as _early_recovery_mod

try:
    _early_recovery_mod.recover_if_needed()
except Exception:
    pass


def _exit_after_oneshot(rc: object) -> None:
    """Exit one-shot mode without letting late native finalizers change rc.

    The SIGABRT this guards against (#30387, #43055) fires in a
    native-extension finalizer during CPython's ``Py_FinalizeEx``, *after*
    the response has printed. Flush streams, shut down file logging, then
    ``os._exit`` past interpreter finalization. The ``atexit`` chain is
    deliberately skipped — several handlers re-enter native code that may
    be the abort source. Stateful cleanup is handled in ``_run_agent`` and
    ``_cleanup_oneshot_runtime``.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        logging.shutdown()
    except Exception:
        pass
    if rc is None:
        exit_code = 0
    elif isinstance(rc, int):
        exit_code = rc
    else:
        exit_code = 1
    os._exit(exit_code)


_oneshot_cleanup_done = False


def _cleanup_oneshot_runtime() -> None:
    """Best-effort process-global cleanup before one-shot hard exit.

    ``run_oneshot`` owns the agent-local cleanup (memory provider, agent.close,
    session_db.close — all in ``_run_agent``'s finally block). This mirrors the
    process-global pieces from ``cli.py:_run_cleanup()`` that would otherwise
    be skipped by ``os._exit``.
    """
    global _oneshot_cleanup_done
    if _oneshot_cleanup_done:
        return
    _oneshot_cleanup_done = True
    try:
        from tools.terminal_tool import cleanup_all_environments
        cleanup_all_environments()
    except Exception:
        pass
    try:
        from tools.async_delegation import interrupt_all
        interrupt_all(reason="oneshot shutdown")
    except Exception:
        pass
    try:
        from tools.browser_tool import _emergency_cleanup_all_sessions
        _emergency_cleanup_all_sessions()
    except Exception:
        pass
    try:
        from tools.mcp_tool import shutdown_mcp_servers
        shutdown_mcp_servers()
    except BaseException:
        pass
    try:
        from agent.auxiliary_client import shutdown_cached_clients
        shutdown_cached_clients()
    except Exception:
        pass


def _run_and_exit_oneshot(
    prompt: str,
    *,
    model: object = None,
    provider: object = None,
    toolsets: object = None,
    skills: object = None,
    usage_file: object = None,
) -> None:
    try:
        from korra_cli.oneshot import run_oneshot

        rc = run_oneshot(
            prompt,
            model=model,
            provider=provider,
            toolsets=toolsets,
            skills=skills,
            usage_file=usage_file,
        )
    except KeyboardInterrupt:
        rc = 130
    except SystemExit as exc:
        if exc.code is not None and not isinstance(exc.code, int):
            print(exc.code, file=sys.stderr)
            rc = 1
        else:
            rc = exc.code
    except BaseException:
        # Defense-in-depth. ``run_oneshot`` already converts agent failures
        # into an int return code and only re-raises KeyboardInterrupt /
        # SystemExit (handled above). Anything still escaping here means
        # ``run_oneshot`` itself malfunctioned — surface it on stderr but never
        # fall through to normal interpreter teardown, which is the exact path
        # that aborts with SIGABRT on AL2023 (the bug this routine fixes).
        import traceback
        try:
            traceback.print_exc()
        except Exception:
            pass
        rc = 1
    try:
        _cleanup_oneshot_runtime()
    finally:
        # The hard exit is the safety boundary for #43055. Even an interrupt
        # during best-effort cleanup must not fall back into interpreter
        # finalization, where the reported native SIGABRT occurs.
        _exit_after_oneshot(rc)


def _project_root_str_fast() -> str:
    return _startup_fast.project_root_str()


def _ensure_project_root_on_path_fast() -> None:
    _startup_fast.ensure_project_root_on_path()


def _set_process_title() -> None:
    """Set the process title to 'hermes' so tools like 'ps', 'top', and
    'htop' show the app name instead of 'python3.xx'.

    Purely cosmetic — non-fatal on any platform.

    Strategy (try in order):
      1. ``setproctitle`` (opt-in dep — installed via ``hermes tools`` or
         ``pip install setproctitle``, or bundled in a future release).
      2. ctypes ``prctl(PR_SET_NAME)`` (Linux only, 15-char limit).
      3. ctypes ``pthread_setname_np`` (macOS only, kernel thread name —
         changes lldb/top but not ``ps aux``).
      4. No-op on Windows (the .exe name is already ``hermes.exe``).
    """
    # Strategy 1: setproctitle (best — works on macOS, Linux, BSD)
    try:
        import setproctitle  # type: ignore[import-untyped]

        setproctitle.setproctitle("hermes")
        return
    except ImportError:
        pass

    # Strategy 2/3: platform-specific ctypes fallback
    import ctypes
    import platform

    try:
        system = platform.system()
        if system == "Linux":
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(15, b"hermes", 0, 0, 0)  # PR_SET_NAME = 15
        elif system == "Darwin":
            libc = ctypes.CDLL("libc.dylib", use_errno=True)
            libc.pthread_setname_np(b"hermes")
        # Windows: the .exe name is already ``hermes.exe`` — nothing to do.
    except Exception:
        pass


# Cheap, dependency-free read of `display.interface` from config.yaml for the
# earliest hot-path decisions (mouse-residue suppression, Termux fast launch)
# that run *before* korra_cli.config is importable. Mirrors the explicit
# precedence used everywhere else: `--cli` always wins, then `--tui`/env, then
# this config value. Cached so the multiple early callers don't re-parse YAML.
_EARLY_INTERFACE_CACHE: "list | None" = None


def _config_default_interface_early() -> str:
    """Return the configured default interface ("cli"/"tui") via a minimal
    YAML read. Best-effort: any error falls back to "cli" (legacy behavior)."""
    global _EARLY_INTERFACE_CACHE
    if _EARLY_INTERFACE_CACHE is not None:
        return _EARLY_INTERFACE_CACHE[0]
    value = "cli"
    try:
        home = korra_env("KORRA_HOME")
        if home:
            cfg_path = os.path.join(home, "config.yaml")
        else:
            cfg_path = os.path.join(os.path.expanduser("~"), ".hermes", "config.yaml")
        if os.path.exists(cfg_path):
            import yaml as _yaml_iface

            with open(cfg_path, encoding="utf-8") as _f:
                raw = _yaml_iface.load(
                    _f, Loader=getattr(_yaml_iface, "CSafeLoader", None) or _yaml_iface.SafeLoader
                ) or {}
            disp = raw.get("display", {})
            if isinstance(disp, dict):
                iface = disp.get("interface")
                if isinstance(iface, str) and iface.strip().lower() == "tui":
                    value = "tui"
    except Exception:
        value = "cli"  # best-effort — default to classic REPL on any error
    _EARLY_INTERFACE_CACHE = [value]
    return value


def _wants_tui_early(argv: "list[str] | None" = None) -> bool:
    """Earliest TUI decision, usable before argparse/config imports.

    Precedence: explicit ``--cli`` wins (forces classic REPL), then
    explicit ``--tui``/``HERMES_TUI=1``, then a real-TTY gate (a
    non-interactive stdio can't host the Ink UI, so ambient config never
    boots it there), then ``display.interface`` in config.

    The TTY gate is load-bearing for headless spawners — kanban workers,
    cron jobs, pipes run ``hermes … chat -q`` with stdio on a pipe. This
    is the earliest launch decision (it runs before ``cmd_chat`` /
    ``_resolve_use_tui``), so a ``display.interface: tui`` default used to
    boot the TUI here — whose no-TTY bail-out exits 0 without doing the
    task → "protocol violation" on every attempt. An explicit ``--tui``
    still reaches the informative bail-out.
    """
    if argv is None:
        argv = sys.argv[1:]
    if "--cli" in argv:
        return False
    if korra_env("KORRA_TUI") == "1" or "--tui" in argv:
        return True
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:
        return False
    return _config_default_interface_early() == "tui"


# Mouse-tracking residue suppression — runs BEFORE every other import on the
# TUI hot path so the terminal stops emitting SGR/X10 mouse reports while the
# Python launcher is still doing imports (≈100–300ms in cooked + echo mode,
# before the Node TUI takes stdin into raw mode). During that window any
# incoming bytes are echoed straight back to the user's shell scrollback as
# ``^[[<…M`` text. The TUI itself runs `resetTerminalModes()` again in
# `entry.tsx`; this is just the earlier cousin. ``HERMES_TUI_NO_EARLY_DISABLE``
# escapes the behaviour for diagnostics.
def _suppress_mouse_residue_early() -> None:
    if korra_env("KORRA_TUI_NO_EARLY_DISABLE") == "1":
        return
    if not _wants_tui_early():
        return
    try:
        # Skip when stdout is redirected (`hermes --tui … >log`, CI capture):
        # the bytes can't reach the terminal anyway and would just pollute
        # the log with raw CSI.
        if not os.isatty(1):
            return
        # Disable every mouse-tracking variant we know about. Idempotent and
        # safe to send even when no tracking is currently asserted.
        os.write(
            1,
            b"\x1b[?1003l\x1b[?1002l\x1b[?1001l\x1b[?1000l\x1b[?9l"
            b"\x1b[?1006l\x1b[?1005l\x1b[?1015l\x1b[?1016l\x1b[?2029l",
        )
    except OSError:
        pass


_suppress_mouse_residue_early()


def _is_termux_startup_environment_fast() -> bool:
    """Tiny Termux check for pre-import startup shortcuts."""
    return _startup_fast.is_termux_env()


def _is_termux_fast_version_argv(argv: list[str]) -> bool:
    return _startup_fast.is_termux_fast_version_argv(argv)


def _is_global_fast_version_argv(argv: list[str]) -> bool:
    return _startup_fast.is_global_fast_version_argv(argv)


def _is_container_startup_environment_fast() -> bool:
    return _startup_fast.is_container_startup_environment()


def _active_profile_may_override_home_fast(hermes_root: str) -> bool:
    return _startup_fast.active_profile_may_override_home(hermes_root)


def _container_mode_may_be_active_fast() -> bool:
    return _startup_fast.container_mode_may_be_active()


def _read_openai_version_fast() -> str | None:
    """Read OpenAI SDK version without importing ``importlib.metadata``."""
    return _startup_fast.read_openai_version()


def _print_fast_version_info() -> None:
    _startup_fast.print_fast_version_info()


def _try_ultrafast_version() -> bool:
    """Handle ``hermes --version`` before config/logging imports."""
    return _startup_fast.try_fast_version()


def _try_termux_ultrafast_version() -> bool:
    """Backward-compatible test hook for the Termux startup fast path."""
    if not _is_termux_startup_environment_fast():
        return False
    return _try_ultrafast_version()


_ensure_project_root_on_path_fast()

if _try_ultrafast_version():
    raise SystemExit(0)

import argparse
import hashlib
import json
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


import functools as _functools

from korra_cli.subcommands._shared import add_accept_hooks_flag as _add_accept_hooks_flag
from korra_cli.subcommands.cron import build_cron_parser
from korra_cli.subcommands.sync import build_sync_parser
from korra_cli.subcommands.gateway import build_gateway_parser
from korra_cli.subcommands.profile import build_profile_parser
from korra_cli.subcommands.model import build_model_parser
from korra_cli.subcommands.setup import build_setup_parser

from korra_cli.subcommands.whatsapp import build_whatsapp_parser
from korra_cli.subcommands.slack import build_slack_parser
from korra_cli.subcommands.login import build_login_parser
from korra_cli.subcommands.logout import build_logout_parser
from korra_cli.subcommands.auth import build_auth_parser
from korra_cli.subcommands.status import build_status_parser
from korra_cli.subcommands.pause import build_pause_parser
from korra_cli.subcommands.webhook import build_webhook_parser
from korra_cli.subcommands.hooks import build_hooks_parser
from korra_cli.subcommands.doctor import build_doctor_parser
from korra_cli.subcommands.verify import build_verify_parser
from korra_cli.subcommands.security import build_security_parser
from korra_cli.subcommands.approvals import build_approvals_parser
from korra_cli.subcommands.dump import build_dump_parser
from korra_cli.subcommands.debug import build_debug_parser
from korra_cli.subcommands.backup import build_backup_parser
from korra_cli.subcommands.import_cmd import build_import_cmd_parser
from korra_cli.subcommands.import_agent import build_import_agent_parser
from korra_cli.subcommands.config import build_config_parser
from korra_cli.subcommands.skin import build_skin_parser
from korra_cli.subcommands.console import build_console_parser
from korra_cli.subcommands.update import build_update_parser
from korra_cli.subcommands.uninstall import build_uninstall_parser
from korra_cli.subcommands.dashboard import build_dashboard_parser
from korra_cli.subcommands.gui import build_gui_parser
from korra_cli.subcommands.logs import build_logs_parser
from korra_cli.subcommands.prompt_size import build_prompt_size_parser
from korra_cli.subcommands.memory import build_memory_parser
from korra_cli.subcommands.acp import build_acp_parser
from korra_cli.subcommands.tools import build_tools_parser
from korra_cli.subcommands.insights import build_insights_parser
from korra_cli.subcommands.monitoring import build_monitoring_parser
from korra_cli.subcommands.skills import build_skills_parser
from korra_cli.subcommands.pairing import build_pairing_parser
from korra_cli.subcommands.plugins import build_plugins_parser
from korra_cli.subcommands.mcp import build_mcp_parser
from korra_cli.subcommands.claw import build_claw_parser


def _require_tty(command_name: str) -> None:
    """Exit with a clear error if stdin is not a terminal.

    Interactive TUI commands (hermes tools, hermes setup, hermes model) use
    curses or input() prompts that spin at 100% CPU when stdin is a pipe.
    This guard prevents accidental non-interactive invocation.
    """
    if not sys.stdin.isatty():
        print(
            f'Команду korra {command_name} нужно запускать напрямую в интерактивном терминале. Запуск через конвейер или подпроцесс без ввода не поддерживается.',
            file=sys.stderr,
        )
        sys.exit(1)


# Add project root to path
PROJECT_ROOT = Path(_project_root_str_fast())
_ensure_project_root_on_path_fast()


# ---------------------------------------------------------------------------
# Profile override — MUST happen before any hermes module import.
#
# Many modules cache HERMES_HOME at import time (module-level constants).
# We intercept --profile/-p from sys.argv here and set the env var so that
# every subsequent ``korra_env("HERMES_HOME", ...)`` resolves correctly.
# The flag is stripped from sys.argv so argparse never sees it.
# Falls back to ~/.hermes/active_profile for sticky default.
# ---------------------------------------------------------------------------
def _apply_profile_override() -> None:
    """Pre-parse --profile/-p and set HERMES_HOME before imports."""
    argv = sys.argv[1:]
    profile_name = None
    consume = 0
    profile_index = None

    def _inside_mcp_add_args(index: int) -> bool:
        """True once argv reaches `hermes mcp add ... --args <command argv>`.

        ``mcp add --args`` is command-argv passthrough. Flags after that point
        belong to the child MCP command (for example Docker MCP Toolkit's
        ``--profile``), not to Hermes' own profile selector.
        """
        try:
            mcp_index = argv.index("mcp", 0, index)
            argv.index("add", mcp_index + 1, index)
        except ValueError:
            return False
        return True

    def _resolve_sudo_user_profile_env(name: str) -> str | None:
        """Resolve `sudo hermes -p <name>` against the invoking user's home.

        `_apply_profile_override()` runs before argparse, so `--run-as-user`
        is not available yet. For sudo invocations, the best available signal
        is SUDO_USER: root is only doing the privileged install/start action,
        while the profile store normally belongs to the user who invoked sudo.
        """
        if name == "default":
            return None
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return None
        sudo_user = os.environ.get("SUDO_USER", "").strip()
        if not sudo_user or sudo_user == "root":
            return None

        try:
            import pwd

            home = Path(pwd.getpwnam(sudo_user).pw_dir)
        except Exception:
            return None

        candidate = home / ".hermes" / "profiles" / name
        try:
            if candidate.is_dir():
                return str(candidate)
        except OSError:
            return None
        return None

    # 1. Check for explicit -p / --profile flag. Historically this worked even
    # after the subcommand (`hermes chat -p coder`), so keep scanning broadly.
    # The exception is command-argv passthrough regions such as `mcp add --args`.
    from korra_cli._parser import top_level_value_flag_sets

    value_flags, optional_value_flags = top_level_value_flag_sets()
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            break
        if arg == "--args" and _inside_mcp_add_args(i):
            break
        if arg in {"--profile", "-p"} and i + 1 < len(argv):
            profile_name = argv[i + 1]
            consume = 2
            profile_index = i
            break
        if arg.startswith("--profile="):
            profile_name = arg.split("=", 1)[1]
            consume = 1
            profile_index = i
            break
        if "=" not in arg and arg in value_flags and i + 1 < len(argv):
            i += 2
        elif (
            "=" not in arg
            and arg in optional_value_flags
            and i + 1 < len(argv)
            and not argv[i + 1].startswith("-")
        ):
            i += 2
        else:
            i += 1

    # 1b. Reject values that can't be valid profile names (e.g. pytest's
    # "-p no:xdist" would be misread as profile "no:xdist" otherwise).
    # Mirrors korra_cli.profiles._PROFILE_ID_RE so we never call
    # resolve_profile_env() with a value it must reject + sys.exit on.
    if profile_name is not None and consume == 2:
        import re as _re

        if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", profile_name):
            profile_name = None
            consume = 0
            profile_index = None

    # 1.5 If HERMES_HOME is already set and no explicit flag was given, trust it
    # only when it already points to a specific profile directory.  The
    # distinguishing heuristic: a profile path has "profiles" as its immediate
    # parent directory name (e.g. ~/.hermes/profiles/coder or
    # /opt/data/profiles/coder).  If HERMES_HOME points to the hermes root
    # instead (e.g. systemd hardcodes HERMES_HOME=/root/.hermes), we must
    # still read active_profile — the user may have switched profiles via
    # `hermes profile use` and the gateway should honour that choice.
    # See issue #22502.
    hermes_home_env = korra_env("KORRA_HOME", "")
    if profile_name is None and hermes_home_env:
        if Path(hermes_home_env).parent.name == "profiles":
            return

    # 2. If no flag, check active_profile in the hermes root.
    #
    # EXCEPTION: a supervisor-launched gateway child must NOT follow the
    # sticky active_profile. Each supervised slot has a fixed profile
    # identity: named slots pass ``-p <name>`` explicitly (handled in step 1
    # above) or pin ``HERMES_HOME`` to the profile directory (step 1.5), and
    # a bare invocation means "the root HERMES_HOME profile". If a supervised
    # default-profile child read active_profile here, switching the active
    # profile (e.g. via the dashboard or ``hermes profile use``) would
    # silently redirect the default gateway into that profile — the default
    # gateway then assumes the other profile's identity/credentials (logs
    # under the other profile's tree, connects with its Telegram bot token)
    # and double-polls a token already owned by that profile's own gateway.
    # See issue #74872 and the "Docker & Profiles & Dashboard" report.
    #
    # Supervisor markers honored (see gateway/restart.py
    # ``is_gateway_supervisor_process`` for the sibling detection used by
    # restart routing):
    #   - HERMES_SUPERVISED_CHILD: generalized marker exported by the
    #     generated systemd unit, launchd plist, and Windows Scheduled-Task
    #     launchers (#74872).
    #   - HERMES_S6_SUPERVISED_CHILD: legacy s6 container marker (back-compat;
    #     exported by S6ServiceManager's run-script).
    #   - INVOCATION_ID: set by systemd for service children only (never in
    #     interactive shells) — covers already-installed gateway units that
    #     predate the HERMES_SUPERVISED_CHILD marker. Consulted ONLY for
    #     gateway commands: INVOCATION_ID is inherited by every descendant of
    #     a systemd-launched process (self-hosted CI runners, user services
    #     running unrelated hermes commands), so honoring it globally would
    #     silently disable the sticky active_profile for those.
    #   - HERMES_GATEWAY_EXTERNAL_SUPERVISOR: explicit external-supervisor
    #     opt-in (``hermes gateway run --external-supervisor``).
    #
    # XPC_SERVICE_NAME is deliberately NOT consulted here: interactive macOS
    # terminals set it too, and a false positive would silently break the
    # sticky active_profile for every interactive command.
    def _under_gateway_supervisor() -> bool:
        if korra_env("KORRA_SUPERVISED_CHILD"):
            return True
        if korra_env("KORRA_S6_SUPERVISED_CHILD"):
            return True
        is_gateway_cmd = next(
            (a for a in argv if not a.startswith("-")), None
        ) == "gateway"
        if is_gateway_cmd and os.environ.get("INVOCATION_ID"):
            return True
        return korra_env(
            "KORRA_GATEWAY_EXTERNAL_SUPERVISOR", ""
        ).strip().lower() in {"1", "true", "yes", "on"}

    if profile_name is None and not _under_gateway_supervisor():
        try:
            from korra_constants import get_default_hermes_root

            active_path = get_default_hermes_root() / "active_profile"
            if active_path.exists():
                name = active_path.read_text(encoding="utf-8").strip()
                if name and name != "default":
                    profile_name = name
                    consume = 0  # don't strip anything from argv
        except (UnicodeDecodeError, OSError):
            pass  # corrupted file, skip

    # 3. If we found a profile, resolve and set HERMES_HOME
    if profile_name is not None:
        try:
            from korra_cli.profiles import resolve_profile_env

            hermes_home = resolve_profile_env(profile_name)
        except FileNotFoundError as exc:
            hermes_home = _resolve_sudo_user_profile_env(profile_name)
            if not hermes_home:
                print(f'Ошибка: {exc}', file=sys.stderr)
                sys.exit(1)
        except ValueError as exc:
            print(f'Ошибка: {exc}', file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            # A bug in profiles.py must NEVER prevent hermes from starting
            print(
                f'Не удалось выбрать профиль: {exc}. Используется профиль по умолчанию.',
                file=sys.stderr,
            )
            return
        korra_env_set(os.environ, "KORRA_HOME", hermes_home)
        # Strip the flag from argv so argparse doesn't choke
        if consume > 0 and profile_index is not None:
            start = profile_index + 1  # +1 because argv is sys.argv[1:]
            sys.argv = sys.argv[:start] + sys.argv[start + consume :]


_apply_profile_override()

# Windows launcher self-heal — the ``hermes`` command users run is a COPY of
# the venv console script, staged into the managed binary dir (the default
# Hermes root's ``bin``, next to the managed uv) by install.ps1. That dir
# lives OUTSIDE the git checkout precisely because an earlier layout staged
# the copies at ``<checkout>\bin``, where ``hermes update``'s autostash
# (``git stash push --include-untracked``) swept them off disk; with the
# desktop updater's ``--keep-stash`` nothing restored them and ``hermes``
# stopped resolving in every new terminal (venv\Scripts itself must stay off
# PATH — it shadows the user's ``python``, #83797). Re-staging at process
# start reaches already-broken installs through the one channel that still
# works there: the desktop app spawning its backend via
# ``python -m korra_cli.main``. Costs a few stat calls when healthy; gates
# fail toward inaction so source checkouts are untouched. Sits AFTER the
# profile override on purpose — no hermes module may be imported before
# profiles resolve. The launcher dir itself is per-machine (the helper
# anchors on the DEFAULT root, not HERMES_HOME), so profile sessions heal
# the same shared dir.
if sys.platform == "win32":
    try:
        from korra_cli import _install_repair as _install_repair_mod

        _install_repair_mod.ensure_windows_bin_launchers(_bootstrap_root)
    except Exception:
        pass

# Load .env from ~/.hermes/.env first, then project root as dev fallback.
# User-managed env files should override stale shell exports on restart.
from korra_cli.config import get_hermes_home
from korra_cli.env_loader import load_hermes_dotenv

# Updating dependencies must not import optional secret-manager libraries into
# the updater process before ``uv`` replaces the environment.  On Windows,
# Bitwarden's cryptography import maps ``_rust.pyd`` and the parent updater then
# prevents its own child installer from replacing that file (#73381).  Profile
# flags have already been stripped above, so the first remaining argument is
# the authoritative argparse subcommand.  Dotenv/managed config still loads;
# only external secret fetches are unnecessary for installation maintenance.
load_hermes_dotenv(
    project_env=PROJECT_ROOT / ".env",
    load_external_secrets=sys.argv[1:2] != ["update"],
)

# Bridge security.redact_secrets from config.yaml → HERMES_REDACT_SECRETS env
# var BEFORE korra_logging imports agent.redact (which snapshots the flag at
# module-import time). Without this, config.yaml's toggle is ignored because
# the setup_logging() call below imports agent.redact, which reads the env var
# exactly once. Env var in .env still wins — this is config.yaml fallback only.
#
# We also read network.force_ipv4 from the same yaml load to avoid two
# separate config.yaml reads (saves ~17ms on every CLI startup — the second
# `load_config()` was doing a full deep-merge for one boolean lookup).
_FORCE_IPV4_EARLY = False
try:
    # Reuse read_raw_config()'s (mtime, size)-keyed cache instead of a bespoke
    # yaml.load — the SAME parse then serves korra_logging's
    # _read_logging_config and any later raw reads in this process, collapsing
    # 3-4 config.yaml parses per invocation into one.
    from korra_cli.config import read_raw_config as _read_raw_early

    _cfg_path = get_hermes_home() / "config.yaml"
    if _cfg_path.exists():
        _early_cfg_raw = _read_raw_early() or {}
        # Managed scope: overlay administrator-pinned values so a managed
        # security.redact_secrets / network.force_ipv4 wins here too. This early
        # bridge reads config.yaml directly (before load_config is usable), so
        # without the overlay a managed redact_secrets toggle would be ignored.
        # Fail-open via the shared helper.
        try:
            from korra_cli import managed_scope
            _early_cfg_raw = managed_scope.apply_managed_overlay(_early_cfg_raw)
        except Exception:
            pass
        if not korra_env_present("KORRA_REDACT_SECRETS"):
            _early_sec_cfg = _early_cfg_raw.get("security", {})
            if isinstance(_early_sec_cfg, dict):
                _early_redact = _early_sec_cfg.get("redact_secrets")
                if _early_redact is not None:
                    korra_env_set(os.environ, "KORRA_REDACT_SECRETS", str(_early_redact).lower())
        _early_net_cfg = _early_cfg_raw.get("network", {})
        if isinstance(_early_net_cfg, dict) and _early_net_cfg.get("force_ipv4"):
            _FORCE_IPV4_EARLY = True
        del _early_cfg_raw
    del _cfg_path
except Exception:
    pass  # best-effort — redaction stays at default (enabled) on config errors

# Initialize centralized file logging early — all `hermes` subcommands
# (chat, setup, gateway, config, etc.) write to agent.log + errors.log.
# Dashboard entrypoints bootstrap with GUI mode so gui.log is always present
# during GUI testing, including pre-dispatch startup failures.
try:
    from korra_logging import setup_logging as _setup_logging

    _setup_logging(
        mode=(
            "gui"
            if next((arg for arg in sys.argv[1:] if not arg.startswith("-")), "")
            in {"dashboard", "serve", "gui", "desktop"}
            else "cli"
        )
    )
except Exception:
    pass  # best-effort — don't crash the CLI if logging setup fails

# Apply IPv4 preference early, before any HTTP clients are created.
# We already determined whether to force IPv4 from the raw yaml read above —
# this just calls the toggle without a redundant load_config() round trip.
if _FORCE_IPV4_EARLY:
    try:
        from korra_constants import apply_ipv4_preference as _apply_ipv4

        _apply_ipv4(force=True)
    except Exception:
        pass  # best-effort — don't crash if korra_constants not importable yet

import logging
import threading
import time as _time
from datetime import datetime

from korra_cli import __version__, __release_date__

# Provider model-selection wizard flows extracted to korra_cli/model_setup_flows.py
# (god-file decomposition Phase 2). Re-imported here so select_provider_and_model and
# existing test monkeypatches (korra_cli.main._model_flow_*) keep resolving unchanged.
from korra_cli.model_setup_flows import (
    _prompt_auth_credentials_choice,
    _model_flow_openrouter,
    _model_flow_nous,
    _model_flow_openai_codex,
    _model_flow_xai_oauth,
    _model_flow_qwen_oauth,
    _model_flow_minimax_oauth,
    _model_flow_custom,
    _model_flow_azure_foundry,
    _model_flow_named_custom,
    _model_flow_copilot,
    _model_flow_copilot_acp,
    _model_flow_kimi,
    _model_flow_stepfun,
    _model_flow_bedrock_api_key,
    _model_flow_bedrock,
    _model_flow_vertex,
    _model_flow_api_key_provider,
    _model_flow_anthropic,
    _model_flow_moa,
    _model_flow_ai_gateway,
)
logger = logging.getLogger(__name__)


def _is_termux_startup_environment(env: dict[str, str] | None = None) -> bool:
    """Import-safe Termux check for cold-start-sensitive CLI paths."""
    check = env or os.environ
    prefix = str(check.get("PREFIX", ""))
    return bool(
        check.get("TERMUX_VERSION")
        or "com.termux/files/usr" in prefix
        or prefix.startswith("/data/data/com.termux/")
    )


def _read_packed_ref(common_dir: Path, ref: str) -> str | None:
    """Look up a ref in .git/packed-refs without spawning git.

    packed-refs lines look like ``<sha> <ref>`` with optional ``^<sha>``
    peel lines and ``#``-prefixed comments / ``# pack-refs with:`` header.
    """
    try:
        text = (common_dir / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return parts[0].strip()
    return None


def _read_git_revision_fingerprint(repo_root: Path) -> str | None:
    """Return a cheap checkout fingerprint without spawning git."""
    git_dir = repo_root / ".git"
    try:
        if git_dir.is_file():
            for line in git_dir.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "gitdir" and value.strip():
                    git_dir = (repo_root / value.strip()).resolve()
                    break
        # Worktrees point HEAD at a per-worktree gitdir but pack their refs
        # in the main repo's gitdir (referenced via ``commondir``). Resolve
        # that up front so packed-refs lookups hit the right file.
        common_dir = git_dir
        commondir_file = git_dir / "commondir"
        if commondir_file.exists():
            try:
                rel = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
                if rel:
                    common_dir = (git_dir / rel).resolve()
            except OSError:
                pass
        head_file = git_dir / "HEAD"
        head = head_file.read_text(encoding="utf-8", errors="replace").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            # Loose refs may live in the worktree gitdir OR the common dir
            # (branches created via `git worktree add` typically live in the
            # common dir's refs/heads/).
            for candidate in (git_dir, common_dir):
                ref_file = candidate / ref
                if ref_file.exists():
                    return f"git:{ref}:{ref_file.read_text(encoding='utf-8', errors='replace').strip()}"
            packed_sha = _read_packed_ref(common_dir, ref)
            if packed_sha:
                return f"git:{ref}:{packed_sha}"
            # Ref name is known but unresolved — still stable across launches,
            # and the version/release fallback in the caller will invalidate
            # after `hermes update`.
            return f"git:{ref}:unresolved"
        return f"git:HEAD:{head}"
    except OSError:
        return None


def _termux_bundled_skills_fingerprint() -> str:
    """Cheap invalidation key for Termux bundled-skill startup sync."""
    git_fp = _read_git_revision_fingerprint(PROJECT_ROOT)
    if git_fp:
        return git_fp
    skills_dir = PROJECT_ROOT / "skills"
    try:
        stat = skills_dir.stat()
        return f"skills:{__version__}:{__release_date__}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return f"skills:{__version__}:{__release_date__}:missing"


def _termux_bundled_skills_stamp_path() -> Path:
    return get_hermes_home() / "skills" / ".termux_bundled_sync_stamp"


def _termux_bundled_skills_sync_needed() -> bool:
    if not _is_termux_startup_environment():
        return True
    if korra_env("KORRA_TERMUX_FORCE_SKILLS_SYNC") == "1":
        return True
    try:
        stamp = _termux_bundled_skills_stamp_path()
        return stamp.read_text(encoding="utf-8").strip() != _termux_bundled_skills_fingerprint()
    except OSError:
        return True


def _mark_termux_bundled_skills_synced() -> None:
    if not _is_termux_startup_environment():
        return
    try:
        stamp = _termux_bundled_skills_stamp_path()
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(_termux_bundled_skills_fingerprint() + "\n", encoding="utf-8")
    except OSError:
        pass


def _sync_bundled_skills_for_startup() -> bool:
    """Sync bundled skills, but skip unchanged Termux checkouts cheaply.

    Hashing every bundled skill is safe but expensive on older Android
    storage. The git/ref stamp keeps post-update correctness: a changed
    checkout revision forces one real sync, then later starts skip it.
    """
    if _is_termux_startup_environment() and not _termux_bundled_skills_sync_needed():
        return False

    from tools.skills_sync import sync_skills

    sync_skills(quiet=True)
    _mark_termux_bundled_skills_synced()
    return True


def _termux_should_prefetch_update_check() -> bool:
    if not _is_termux_startup_environment():
        return True
    return korra_env("KORRA_TERMUX_PREFETCH_UPDATES") == "1"


def _relative_time(ts) -> str:
    """Format a timestamp as relative time (e.g., '2h ago', 'yesterday').

    Thin wrapper kept for backward compatibility; the implementation lives
    in :mod:`korra_cli.timefmt` so lightweight consumers don't have to
    import the whole CLI surface.
    """
    from korra_cli.timefmt import relative_time

    return relative_time(ts)


def _has_any_provider_configured() -> bool:
    """Check if at least one inference provider is usable."""
    from korra_cli.config import get_env_path, get_hermes_home, load_config
    from korra_cli.auth import get_auth_status

    # Determine whether Hermes itself has been explicitly configured (model
    # in config that isn't the hardcoded default). Used below to gate external
    # tool credentials (Claude Code, Codex CLI) that shouldn't silently skip
    # the setup wizard on a fresh install.
    from korra_cli.config import DEFAULT_CONFIG

    _DEFAULT_MODEL = DEFAULT_CONFIG.get("model", "")
    cfg = load_config()
    model_cfg = cfg.get("model")
    if isinstance(model_cfg, dict):
        _default = model_cfg.get("default")
        if isinstance(_default, dict):
            from korra_cli.config import split_model_config_default
            _model_name, _ = split_model_config_default(_default)
        else:
            _model_name = (_default or "")
        _model_name = (str(_model_name) if not isinstance(_model_name, str) else _model_name).strip()
    elif isinstance(model_cfg, str):
        _model_name = model_cfg.strip()
    else:
        _model_name = ""
    _has_hermes_config = _model_name and _model_name != _DEFAULT_MODEL

    # Check env vars (may be set by .env or shell).
    # OPENAI_BASE_URL alone counts — local models (vLLM, llama.cpp, etc.)
    # often don't require an API key.
    from korra_cli.auth import PROVIDER_REGISTRY

    # Collect all provider env vars
    provider_env_vars = {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "OPENAI_BASE_URL",
    }
    for pconfig in PROVIDER_REGISTRY.values():
        if pconfig.auth_type == "api_key":
            provider_env_vars.update(pconfig.api_key_env_vars)
    if any(os.getenv(v) for v in provider_env_vars):
        return True

    # Check .env file for keys
    env_file = get_env_path()
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                key, _, val = line.partition("=")
                val = val.strip().strip("'\"")
                if key.strip() in provider_env_vars and val:
                    return True
        except Exception:
            pass

    # Cheap local checks first: auth.json and config.yaml are on-disk lookups,
    # while the PROVIDER_REGISTRY sweep below spawns subprocesses (gh) and can
    # take 15-20s — long enough that desktop setup.status calls time out.

    # Check for Nous Portal OAuth credentials
    auth_file = get_hermes_home() / "auth.json"
    if auth_file.exists():
        try:
            import json

            auth = json.loads(auth_file.read_text(encoding="utf-8-sig"))
            active = auth.get("active_provider")
            if active:
                status = get_auth_status(active)
                if status.get("logged_in"):
                    return True
        except Exception:
            pass

    # Check config.yaml — if model is a dict with an explicit provider set,
    # the user has gone through setup (fresh installs have model as a plain
    # string).  Also covers custom endpoints that store api_key/base_url in
    # config rather than .env.
    if isinstance(model_cfg, dict):
        cfg_provider = (model_cfg.get("provider") or "").strip()
        cfg_base_url = (model_cfg.get("base_url") or "").strip()
        cfg_api_key = (model_cfg.get("api_key") or "").strip()
        if cfg_provider or cfg_base_url or cfg_api_key:
            return True

    # Check provider-specific auth fallbacks (for example, Copilot via gh auth).
    try:
        for provider_id, pconfig in PROVIDER_REGISTRY.items():
            if pconfig.auth_type != "api_key":
                continue
            status = get_auth_status(provider_id)
            if status.get("logged_in"):
                return True
    except Exception:
        pass

    # Check for Claude Code OAuth credentials (~/.claude/.credentials.json)
    # Only count these if Hermes has been explicitly configured — Claude Code
    # being installed doesn't mean the user wants Hermes to use their tokens.
    if _has_hermes_config:
        try:
            from agent.anthropic_adapter import (
                read_claude_code_credentials,
                is_claude_code_token_valid,
            )

            creds = read_claude_code_credentials()
            if creds and (
                is_claude_code_token_valid(creds) or creds.get("refreshToken")
            ):
                return True
        except Exception:
            pass

    return False


def _confirm_startup_expensive_model_override(args) -> None:
    """Guard startup -m/--provider overrides before the first API call."""
    explicit_model = (getattr(args, "model", None) or "").strip()
    explicit_provider = (getattr(args, "provider", None) or "").strip()
    if not explicit_model and not explicit_provider:
        return

    try:
        from korra_cli.config import load_config
        from korra_cli.model_selection_guards import (
            combined_message,
            selection_warnings,
        )
    except Exception as exc:
        logger.warning("startup model cost guard unavailable: %s", exc)
        return

    try:
        config = load_config()
    except Exception as exc:
        logger.warning("startup model cost guard could not load config: %s", exc)
        config = {}
    if not isinstance(config, dict):
        config = {}
    model_cfg = config.get("model") or {}
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    security_cfg = config.get("security") or {}
    if not isinstance(security_cfg, dict):
        security_cfg = {}

    model = explicit_model or (model_cfg.get("default") or "").strip()
    if not model:
        return
    provider = (explicit_provider or model_cfg.get("provider") or "").strip()
    try:
        # Unified registry: cost guard + id-keyed guards (e.g. the
        # data-training-tier warning) all fire at startup too.
        warnings = selection_warnings(
            model,
            provider=provider,
            base_url=(model_cfg.get("base_url") or ""),
            api_key=(model_cfg.get("api_key") or ""),
        )
    except Exception as exc:
        logger.warning("startup model cost guard failed for %s/%s: %s", provider, model, exc)
        return
    if not warnings:
        return

    # Cost and provider-routing confirmation is intentionally independent of
    # --yolo / --accept-hooks: those flags approve local command/tool risk, not
    # paid aggregator spend or a surprising provider route.
    is_interactive = sys.stdin.isatty()
    allow_unattended_data_training = (
        security_cfg.get("allow_data_training_tiers_noninteractive") is True
    )
    if not is_interactive and allow_unattended_data_training:
        acknowledged = [
            warning for warning in warnings if warning.kind == "data_policy"
        ]
        if acknowledged:
            sys.stderr.write(combined_message(acknowledged) + "\n")
            sys.stderr.write(
                'Продолжаем без интерактивного ввода: включено security.allow_data_training_tiers_noninteractive.'
            )
            warnings = [
                warning for warning in warnings if warning.kind != "data_policy"
            ]
            if not warnings:
                return

    message = combined_message(warnings)
    if not is_interactive:
        sys.stderr.write(message + "\n")
        if any(warning.kind == "data_policy" for warning in warnings):
            sys.stderr.write(
                'Чтобы разрешить тарифы с использованием данных для обучения при запуске без ввода, задайте security.allow_data_training_tiers_noninteractive: true в config.yaml.'
            )
        sys.stderr.write(
            'Эту замену модели нужно подтвердить в интерактивном терминале. Без подтверждения запуск отменён.'
        )
        raise SystemExit(1)

    sys.stderr.write(message + "\n")
    try:
        reply = input('Использовать эту модель для текущего запуска? [y/N] ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        reply = ""
    if reply not in {"y", "yes"}:
        sys.stderr.write('Замена модели отменена.')
        raise SystemExit(1)


def _session_status_tag(status: Optional[str]) -> str:
    """Short fixed-width tag for a session lifecycle status."""
    return {
        "complete": "done",
        "interrupted": "intr",
        "error": "err",
        "empty": "empty",
    }.get(status or "", "-")


def _annotate_session_statuses(sessions: list, session_db) -> None:
    """Attach a ``_status`` key to each session row (best-effort, cheap).

    Uses ``SessionDB.session_lifecycle_statuses`` — one indexed last-message
    lookup per listed session, never a transcript scan. On any failure the
    rows simply stay untagged and the picker renders '-' for status.
    """
    if session_db is None or not sessions:
        return
    try:
        statuses = session_db.session_lifecycle_statuses(
            [s.get("id") for s in sessions]
        )
    except Exception:
        return
    for s in sessions:
        s["_status"] = statuses.get(s.get("id"), "")


def _session_browse_picker(sessions: list, session_db=None) -> Optional[str]:
    """Interactive curses-based session browser with live search filtering.

    Shows lifecycle status (done / intr / err / empty) and message count per
    session when *session_db* is provided. With a live *session_db*, pressing
    ``d`` on a row (while the search filter is empty) prompts y/n and deletes
    the session via ``SessionDB.delete_session``.

    Returns the selected session ID, or None if cancelled.
    """
    if not sessions:
        print('Беседы не найдены.')
        return None

    _annotate_session_statuses(sessions, session_db)

    def _delete_session(session_id: str) -> bool:
        if session_db is None:
            return False
        try:
            sessions_dir = get_hermes_home() / "sessions"
        except Exception:
            sessions_dir = None
        try:
            return bool(
                session_db.delete_session(session_id, sessions_dir=sessions_dir)
            )
        except Exception:
            return False

    # Try curses-based picker first
    try:
        import curses

        result_holder = [None]

        # Layout: [arrow 3] [title/preview flexible] [status 5] [msgs 5]
        #         [active 12] [src 6] [id 18]
        _FIXED_COLS = 3 + 5 + 2 + 5 + 2 + 12 + 6 + 18 + 6

        def _format_row(s, max_x):
            """Format a session row for display."""
            title = (s.get("title") or "").strip()
            preview = (s.get("preview") or "").strip()
            source = s.get("source", "")[:6]
            last_active = _relative_time(s.get("last_active"))
            sid = s["id"][:18]
            status = _session_status_tag(s.get("_status"))
            msgs = s.get("message_count")
            msgs_str = str(msgs) if isinstance(msgs, int) else "-"

            name_width = max(20, max_x - _FIXED_COLS)

            if title:
                name = title[:name_width]
            elif preview:
                name = preview[:name_width]
            else:
                name = sid

            return (
                f"{name:<{name_width}}  {status:<5}  {msgs_str:>5}  "
                f"{last_active:<10}  {source:<5} {sid}"
            )

        def _match(s, query):
            """Check if a session matches the search query (case-insensitive)."""
            q = query.lower()
            return (
                q in (s.get("title") or "").lower()
                or q in (s.get("preview") or "").lower()
                or q in s.get("id", "").lower()
                or q in (s.get("source") or "").lower()
            )

        def _curses_browse(stdscr):
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)  # selected
                curses.init_pair(2, curses.COLOR_YELLOW, -1)  # header
                curses.init_pair(3, curses.COLOR_CYAN, -1)  # search
                curses.init_pair(4, 8 if curses.COLORS > 8 else curses.COLOR_WHITE, -1)  # dim
                curses.init_pair(5, curses.COLOR_RED, -1)  # error/delete

            cursor = 0
            scroll_offset = 0
            search_text = ""
            confirm_delete = None  # session dict pending y/n confirmation
            flash = ""  # one-frame notice (e.g. "deleted <title>")
            filtered = list(sessions)

            def _status_attr(status):
                if not curses.has_colors():
                    return curses.A_NORMAL
                return {
                    "complete": curses.color_pair(1),
                    "interrupted": curses.color_pair(2),
                    "error": curses.color_pair(5),
                    "empty": curses.color_pair(4),
                }.get(status or "", curses.A_NORMAL)

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()
                if max_y < 5 or max_x < 40:
                    # Terminal too small
                    try:
                        stdscr.addstr(0, 0, 'Окно терминала слишком маленькое')
                    except curses.error:
                        pass
                    stdscr.refresh()
                    stdscr.getch()
                    return

                # Header line
                if search_text:
                    header = f'  Беседы — поиск: {search_text}█'
                    header_attr = curses.A_BOLD
                    if curses.has_colors():
                        header_attr |= curses.color_pair(3)
                else:
                    header = (
                        '  Беседы: ↑↓ выбор, Enter открыть, ввод — поиск, Esc выход'
                    )
                    header_attr = curses.A_BOLD
                    if curses.has_colors():
                        header_attr |= curses.color_pair(2)
                try:
                    stdscr.addnstr(0, 0, header, max_x - 1, header_attr)
                except curses.error:
                    pass

                # Column header line
                name_width = max(20, max_x - _FIXED_COLS)
                col_header = (
                    f"   {'Название / начало':<{name_width}}  {'Стат.':<5}  "
                    f"{'Сообщ':>5}  {'Активность':<10}  {'Источник':<5} {'ID'}"
                )
                try:
                    dim_attr = (
                        curses.color_pair(4) if curses.has_colors() else curses.A_DIM
                    )
                    stdscr.addnstr(1, 0, col_header, max_x - 1, dim_attr)
                except curses.error:
                    pass

                # Compute visible area
                visible_rows = max_y - 4  # header + col header + blank + footer
                visible_rows = max(visible_rows, 1)

                # Clamp cursor and scroll
                if not filtered:
                    try:
                        msg = '  Подходящих бесед нет.'
                        stdscr.addnstr(3, 0, msg, max_x - 1, curses.A_DIM)
                    except curses.error:
                        pass
                else:
                    if cursor >= len(filtered):
                        cursor = len(filtered) - 1
                    cursor = max(cursor, 0)
                    if cursor < scroll_offset:
                        scroll_offset = cursor
                    elif cursor >= scroll_offset + visible_rows:
                        scroll_offset = cursor - visible_rows + 1

                    for draw_i, i in enumerate(
                        range(
                            scroll_offset,
                            min(len(filtered), scroll_offset + visible_rows),
                        )
                    ):
                        y = draw_i + 3
                        if y >= max_y - 1:
                            break
                        s = filtered[i]
                        arrow = " → " if i == cursor else "   "
                        row = arrow + _format_row(s, max_x - 3)
                        attr = curses.A_NORMAL
                        if i == cursor:
                            attr = curses.A_BOLD
                            if curses.has_colors():
                                attr |= curses.color_pair(1)
                        try:
                            stdscr.addnstr(y, 0, row, max_x - 1, attr)
                            if i != cursor:
                                # Recolor the status tag column in place.
                                status = s.get("_status")
                                tag = _session_status_tag(status)
                                tag_x = 3 + max(20, (max_x - 3) - _FIXED_COLS) + 2
                                if tag_x + 5 < max_x - 1:
                                    stdscr.addnstr(
                                        y, tag_x, f"{tag:<5}", 5, _status_attr(status)
                                    )
                        except curses.error:
                            pass

                # Footer
                footer_y = max_y - 1
                footer_attr = (
                    curses.color_pair(4) if curses.has_colors() else curses.A_DIM
                )
                if confirm_delete is not None:
                    label = (
                        (confirm_delete.get("title") or "").strip()
                        or (confirm_delete.get("preview") or "").strip()
                        or confirm_delete["id"]
                    )
                    if len(label) > 40:
                        label = label[:37] + "..."
                    footer = f'  Удалить беседу «{label}»? [y/N]'
                    footer_attr = curses.A_BOLD
                    if curses.has_colors():
                        footer_attr |= curses.color_pair(5)
                elif flash:
                    footer = f"  {flash}"
                    flash = ""
                else:
                    if filtered:
                        footer = f'  Беседы: {cursor + 1}/{len(filtered)}'
                        if len(filtered) < len(sessions):
                            footer += f' (отобрано из {len(sessions)})'
                    else:
                        footer = f'  Беседы: 0/{len(sessions)}'
                    if session_db is not None and not search_text:
                        footer += '   d — удалить'
                try:
                    stdscr.addnstr(footer_y, 0, footer, max_x - 1, footer_attr)
                except curses.error:
                    pass

                stdscr.refresh()
                key = stdscr.getch()

                if confirm_delete is not None:
                    # y/n confirmation mode — only an explicit 'y' deletes.
                    target = confirm_delete
                    confirm_delete = None
                    if key in {ord("y"), ord("Y")}:
                        if _delete_session(target["id"]):
                            sessions[:] = [
                                s for s in sessions if s["id"] != target["id"]
                            ]
                            filtered = (
                                [s for s in sessions if _match(s, search_text)]
                                if search_text
                                else list(sessions)
                            )
                            flash = 'Удалено.'
                            if not sessions:
                                return
                        else:
                            flash = 'Не удалось удалить.'
                    continue

                if key in {curses.KEY_UP,}:
                    if filtered:
                        cursor = (cursor - 1) % len(filtered)
                elif key in {curses.KEY_DOWN,}:
                    if filtered:
                        cursor = (cursor + 1) % len(filtered)
                elif key in {curses.KEY_ENTER, 10, 13}:
                    if filtered:
                        result_holder[0] = filtered[cursor]["id"]
                    return
                elif key == 27:  # Esc
                    if search_text:
                        # First Esc clears the search
                        search_text = ""
                        filtered = list(sessions)
                        cursor = 0
                        scroll_offset = 0
                    else:
                        # Second Esc exits
                        return
                elif key in {curses.KEY_BACKSPACE, 127, 8}:
                    if search_text:
                        search_text = search_text[:-1]
                        if search_text:
                            filtered = [s for s in sessions if _match(s, search_text)]
                        else:
                            filtered = list(sessions)
                        cursor = 0
                        scroll_offset = 0
                elif key == ord("q") and not search_text:
                    return
                elif (
                    key == ord("d")
                    and not search_text
                    and session_db is not None
                    and filtered
                ):
                    # 'd' only acts as delete when the filter is empty —
                    # while a search is active it types into the query below.
                    confirm_delete = filtered[cursor]
                elif 32 <= key <= 126:
                    # Printable character → add to search filter
                    search_text += chr(key)
                    filtered = [s for s in sessions if _match(s, search_text)]
                    cursor = 0
                    scroll_offset = 0

        curses.wrapper(_curses_browse)
        return result_holder[0]

    except Exception:
        pass

    # Fallback: numbered list (Windows without curses, etc.). Shows the same
    # status/message-count columns but has no delete support.
    print('  Выбор беседы: введите номер для продолжения или q для отмены')
    for i, s in enumerate(sessions):
        title = (s.get("title") or "").strip()
        preview = (s.get("preview") or "").strip()
        label = title or preview or s["id"]
        if len(label) > 50:
            label = label[:47] + "..."
        last_active = _relative_time(s.get("last_active"))
        src = s.get("source", "")[:6]
        status = _session_status_tag(s.get("_status"))
        msgs = s.get("message_count")
        msgs_str = str(msgs) if isinstance(msgs, int) else "-"
        print(
            f"  {i + 1:>3}. {label:<50}  {status:<5}  {msgs_str:>5}  "
            f"{last_active:<10}  {src}"
        )

    while True:
        try:
            val = input(f'  Выберите [1–{len(sessions)}]: ').strip()
            if not val or val.lower() in {"q", "quit", "exit"}:
                return None
            idx = int(val) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]["id"]
            print(f'  Неверный выбор. Введите число от 1 до {len(sessions)} или q для отмены.')
        except ValueError:
            print('  Неверный ввод. Введите число или q для отмены.')
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def _resolve_workspace_key() -> Optional[str]:
    """The current workspace identity for cwd-scoped resume.

    Git repo root when CWD is inside a repo (so all sessions across its
    subdirs/worktrees group together), else the CWD itself. Returns None when
    neither can be determined — callers fall back to the global MRU then.
    """
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.abspath(result.stdout.strip())
    except Exception:
        pass
    try:
        return os.getcwd()
    except Exception:
        return None


def _resolve_last_session(source: str = "cli") -> Optional[str]:
    """Look up the most recently-used session ID for a source.

    Scoped to the current workspace first (git repo root, else cwd) so
    ``hermes -c`` from repo A continues repo A's last session rather than the
    global MRU. Falls back to the unscoped MRU when no session matches the
    current workspace, preserving the old behaviour for fresh directories.
    """
    db = None
    try:
        from korra_state import SessionDB

        db = SessionDB()
        ws_key = _resolve_workspace_key()
        if ws_key:
            sessions = db.search_sessions(source=source, limit=1, workspace_key=ws_key)
            if sessions:
                return sessions[0]["id"]
        # Fallback: global MRU for this source.
        sessions = db.search_sessions(source=source, limit=1)
        return sessions[0]["id"] if sessions else None
    except Exception:
        pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return None


def _probe_container(cmd: list, backend: str, via_sudo: bool = False):
    """Run a container inspect probe, returning the CompletedProcess.

    Catches TimeoutExpired specifically for a human-readable message;
    all other exceptions propagate naturally.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    except subprocess.TimeoutExpired:
        label = f"sudo {backend}" if via_sudo else backend
        print(
            f'Не дождались ответа {label}. Возможно, служба {backend} не отвечает или ещё запускается.',
            file=sys.stderr,
        )
        sys.exit(1)


def _exec_in_container(container_info: dict, cli_args: list):
    """Replace the current process with a command inside the managed container.

    Probes whether sudo is needed (rootful containers), then os.execvp
    into the container. On success the Python process is replaced entirely
    and the container's exit code becomes the process exit code (OS semantics).
    On failure, OSError propagates naturally.

    Args:
        container_info: dict with backend, container_name, exec_user, hermes_bin
        cli_args: the original CLI arguments (everything after 'hermes')
    """

    backend = container_info["backend"]
    container_name = container_info["container_name"]
    exec_user = container_info["exec_user"]
    hermes_bin = container_info["hermes_bin"]

    runtime = shutil.which(backend)
    if not runtime:
        print(
            f'Ошибка: {backend} не найден в PATH. Не удалось подключиться к контейнеру.',
            file=sys.stderr,
        )
        sys.exit(1)

    # Rootful containers (NixOS systemd service) are invisible to unprivileged
    # users — Podman uses per-user namespaces, Docker needs group access.
    # Probe whether the runtime can see the container; if not, try via sudo.
    sudo_path = None
    probe = _probe_container(
        [runtime, "inspect", "--format", "ok", container_name],
        backend,
    )
    if probe.returncode != 0:
        sudo_path = shutil.which("sudo")
        if sudo_path:
            probe2 = _probe_container(
                [sudo_path, "-n", runtime, "inspect", "--format", "ok", container_name],
                backend,
                via_sudo=True,
            )
            if probe2.returncode != 0:
                print(
                    f"""Контейнер «{container_name}» не найден через {backend}. Возможно, он работает от root: {backend} разделяет контейнеры по пользователям. Разрешите sudo без пароля для {backend}; нужен флаг -n, иначе запрос пароля заблокирует конвейер. В NixOS задайте security.sudo.extraRules для пользователя «{os.getenv('USER', 'your-user')}» и команды «{runtime}» с options = [ "NOPASSWD" ]. Или запустите: sudo korra {' '.join(cli_args)}""",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            print(
                f"Контейнер «{container_name}» не найден через {backend}. Возможно, он работает от root. Попробуйте: sudo korra {' '.join(cli_args)}",
                file=sys.stderr,
            )
            sys.exit(1)

    is_tty = sys.stdin.isatty()
    tty_flags = ["-it"] if is_tty else ["-i"]

    env_flags = []
    for var in ("TERM", "COLORTERM", "LANG", "LC_ALL"):
        val = os.environ.get(var)
        if val:
            env_flags.extend(["-e", f"{var}={val}"])

    cmd_prefix = [sudo_path, "-n", runtime] if sudo_path else [runtime]
    exec_cmd = (
        cmd_prefix
        + ["exec"]
        + tty_flags
        + ["-u", exec_user]
        + env_flags
        + [container_name, hermes_bin]
        + cli_args
    )

    os.execvp(exec_cmd[0], exec_cmd)


def _resolve_session_by_name_or_id(name_or_id: str) -> Optional[str]:
    """Resolve a session name (title) or ID to a session ID.

    - If it looks like a session ID (contains underscore + hex), try direct lookup first.
    - Otherwise, treat it as a title and use resolve_session_by_title (auto-latest).
    - Falls back to the other method if the first doesn't match.
    - If the resolved session is a compression root, follow the chain forward
      to the latest continuation. Users who remember the old root ID (e.g.
      from an exit summary printed before the bug fix, or from notes) get
      resumed at the live tip instead of a stale parent with no messages.
    """
    db = None
    try:
        from korra_state import SessionDB

        db = SessionDB()

        # Try as exact session ID first
        session = db.get_session(name_or_id)
        resolved_id: Optional[str] = None
        if session:
            resolved_id = session["id"]
        else:
            # Try as title (with auto-latest for lineage)
            resolved_id = db.resolve_session_by_title(name_or_id)

        if resolved_id:
            # Project forward through compression chain so resumes land on
            # the live tip instead of a dead compressed parent.
            try:
                resolved_id = db.get_compression_tip(resolved_id) or resolved_id
            except Exception:
                pass

        return resolved_id
    except Exception:
        pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return None


def _create_titled_session(title: str) -> Optional[str]:
    """Create a fresh session with the given title; return its session id.

    Used by ``chat -c <title> --create-if-missing`` (#86794): programmatic
    callers (plugins, scripts) that want "send to this named thread, making
    it if needed" get a deterministic outcome instead of a silent no-op.

    The session id follows the same timestamp+uuid shape the CLI uses for a
    brand-new session; the title is recorded with user provenance so
    auto-titling never overwrites it.
    """
    db = None
    try:
        import uuid as _uuid

        from korra_state import SessionDB

        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        short_uuid = _uuid.uuid4().hex[:6]
        new_session_id = f"{timestamp_str}_{short_uuid}"

        db = SessionDB()
        db.create_session(new_session_id, source="cli")
        db.set_session_title(new_session_id, title)
        return new_session_id
    except Exception:
        # Programmatic callers (the #86794 use case) rely on --create-if-missing
        # being deterministic; swallow the failure to keep the error path simple,
        # but log the underlying cause so it lands in errors.log and stays
        # debuggable (DB lock, I/O error, import error — all otherwise invisible).
        logger.exception("Failed to create titled session %r", title)
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _resolve_continue_arg(args, *, use_tui: bool) -> None:
    """Resolve ``-c/--continue`` into ``args.resume``.

    Handles both forms:
    - ``-c <name>``: resolve by title/ID. On miss, fail loudly on **stderr**
      (exit 1) so programmatic callers see the error even under quiet mode
      (#86794); with ``--create-if-missing``, create a fresh titled session
      and resume into it instead.
    - bare ``-c``: continue this terminal's breadcrumb session if valid,
      else the most recent session (workspace-scoped MRU, then global
      fallback).
    """
    continue_val = getattr(args, "continue_last", None)
    if continue_val and not getattr(args, "resume", None):
        if isinstance(continue_val, str):
            # -c "session name" — resolve by title or ID
            resolved = _resolve_session_by_name_or_id(continue_val)
            if resolved:
                args.resume = resolved
            elif getattr(args, "create_if_missing", False):
                # --create-if-missing: no session matches the title — create a
                # new session with that title and proceed. This is the
                # programmatic-caller primitive ("send to this named thread,
                # making it if needed"); without it a background/quiet send to
                # a not-yet-existing named session silently no-ops (#86794).
                new_sid = _create_titled_session(continue_val)
                if new_sid:
                    args.resume = new_sid
                else:
                    print(
                        f'Беседа «{continue_val}» не найдена, создать новую с этим названием не удалось.',
                        file=sys.stderr,
                    )
                    sys.exit(1)
            else:
                print(f'Беседа «{continue_val}» не найдена.', file=sys.stderr)
                print(
                    'Посмотрите korra sessions list или добавьте --create-if-missing, чтобы создать беседу с этим названием.',
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            # -c with no argument — prefer this terminal's own breadcrumb
            # (written at session start / rotation) so side-by-side terminals
            # each continue their own conversation. Falls back to the
            # most-recent session when there is no valid breadcrumb, or when
            # session.terminal_continue is false in config.yaml.
            if getattr(args, "create_if_missing", False):
                # --create-if-missing only makes sense with a named session;
                # with a bare -c there is nothing to create, so surface the
                # no-op to programmatic callers instead of silently ignoring it.
                print(
                    'Для --create-if-missing нужно название беседы: -c <name> --create-if-missing',
                    file=sys.stderr,
                )
            try:
                from korra_cli.terminal_breadcrumbs import resolve_breadcrumb_session

                _crumb_id = resolve_breadcrumb_session()
            except Exception:
                _crumb_id = None
            if _crumb_id:
                args.resume = _crumb_id
            else:
                # No valid breadcrumb — continue the most recent session
                source = "tui" if use_tui else "cli"
                last_id = _resolve_last_session(source=source)
                if not last_id and source == "tui":
                    last_id = _resolve_last_session(source="cli")
                if last_id:
                    args.resume = last_id
                else:
                    kind = "TUI" if use_tui else "CLI"
                    print(f'Предыдущая беседа {kind} для продолжения не найдена.')
                    sys.exit(1)


def _read_tui_active_session_file(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        sid = str(data.get("session_id") or "").strip()
        return sid or None
    except Exception:
        return None


def _print_tui_exit_summary(
    session_id: Optional[str], active_session_file: Optional[str] = None
) -> None:
    """Print a shell-visible epilogue after TUI exits."""
    target = (
        _read_tui_active_session_file(active_session_file)
        or session_id
        or _resolve_last_session(source="tui")
    )
    if not target:
        return

    db = None
    try:
        from korra_state import SessionDB

        db = SessionDB()
        session = db.get_session(target)
        if not session:
            return

        title = db.get_session_title(target)
        message_count = int(session.get("message_count") or 0)
        if message_count == 0:
            return  # No real conversation — don't show resume info
        input_tokens = int(session.get("input_tokens") or 0)
        output_tokens = int(session.get("output_tokens") or 0)
        cache_read_tokens = int(session.get("cache_read_tokens") or 0)
        cache_write_tokens = int(session.get("cache_write_tokens") or 0)
        reasoning_tokens = int(session.get("reasoning_tokens") or 0)
        total_tokens = (
            input_tokens
            + output_tokens
            + cache_read_tokens
            + cache_write_tokens
            + reasoning_tokens
        )
    except Exception:
        return
    finally:
        if db is not None:
            db.close()

    print()
    print('Продолжить эту беседу:')
    print(f'  korra --tui --resume {target}')
    if title:
        print(f'  korra --tui -c "{title}"')
    print()
    print(f'Беседа:         {target}')
    if title:
        print(f'Название:       {title}')
    print(f'Сообщений:      {message_count}')
    print(
        f'Токенов:        {total_tokens} (вход: {input_tokens}, выход: {output_tokens}, кеш: {cache_read_tokens + cache_write_tokens}, рассуждения: {reasoning_tokens})'
    )


_NPM_LOCK_RUNTIME_KEYS = frozenset(
    {
        "ideallyInert",
        "peer",
        # npm writes these boolean annotation fields non-deterministically
        # between the declarative package-lock.json and the hidden actualized
        # .package-lock.json.  The intersection comparison (see
        # _tui_need_npm_install) already handles the "field present in root
        # but absent in hidden" case for structured fields like version,
        # dependencies, license, etc.  These boolean flags need explicit
        # exclusion because when present in *both* lockfiles they may still
        # differ (e.g. dev: true → stripped in hidden).
        "dev",
        "extraneous",
        "hasInstallScript",
        "optional",
    }
)
"""Lockfile fields npm writes non-deterministically at install time.

``ideallyInert`` is npm's runtime annotation for packages it skipped installing
(per-platform opt-outs).  ``peer`` is dropped from the hidden ``.package-lock.json``
on dev-dependencies that are *also* declared as peers — the canonical
``package-lock.json`` records the dual role, but npm 9's actualized tree strips
it.  Neither key represents a real skew between what was declared and what was
installed, so we exclude them from the comparison in :func:`_tui_need_npm_install`
to avoid false-positive reinstalls on every launch.

``dev``, ``optional``, ``extraneous``, and ``hasInstallScript`` are boolean
annotations that npm populates differently in the hidden lock (npm >= 10/11
writes ``extraneous`` into the hidden lock only, and ``dev: true`` from the
root lock may be absent or ``false`` in the hidden actualized tree).
They never indicate a changed dependency — the authoritative check is the
``resolved``/``integrity`` pair, which the intersection comparison always
catches.
"""


def _workspace_root(dir: Path) -> Path:
    """Return the npm workspace root for *dir*.

    In a workspace checkout the single ``package-lock.json`` and hoisted
    ``node_modules/`` live at the workspace root (the parent of the
    sub-package directory).  Heuristic: if *dir* has a ``package.json``
    but **no** ``package-lock.json``, and its **parent** has a
    ``package-lock.json``, the parent is the workspace root.
    Otherwise *dir* itself is the root (standalone project or
    prebuilt-bundle layout).

    Used by ``_tui_need_npm_install``, ``_make_tui_argv``, and
    ``_build_web_ui`` so that lockfile/node_modules resolution and
    ``npm install`` cwd stay consistent — a single helper prevents
    the checks from diverging if someone accidentally creates a
    sub-package lockfile (e.g. running ``npm install`` in the wrong
    directory).
    """
    if (
        (dir / "package.json").is_file()
        and not (dir / "package-lock.json").is_file()
        and (dir.parent / "package-lock.json").is_file()
    ):
        return dir.parent
    return dir


def _termux_workspace_install_context(
    dir: Path, *, include_child_workspaces: bool = False
) -> tuple[Path, tuple[str, ...]]:
    """Return Termux-only ``(cwd, npm_args)`` for installing deps for *dir* only."""
    ws_root = _workspace_root(dir)
    if ws_root == dir:
        return dir, ()

    try:
        workspace = dir.relative_to(ws_root).as_posix()
    except ValueError:
        return ws_root, ()

    workspace_args: list[str] = ["--workspace", workspace]
    if include_child_workspaces:
        packages_dir = dir / "packages"
        if packages_dir.is_dir():
            for child in sorted(packages_dir.iterdir()):
                if child.is_dir() and (child / "package.json").is_file():
                    workspace_args.extend(
                        ["--workspace", child.relative_to(ws_root).as_posix()]
                    )
    workspace_args.append("--include-workspace-root=false")
    return ws_root, tuple(workspace_args)


def _npm_lock_workspace_closure(packages: dict, starts) -> Optional[set]:
    """Package-map keys reachable from the selected workspaces via npm resolution.

    *starts* is the set of workspace keys the launch install explicitly scopes
    to (a single str is accepted for convenience).  ``devDependencies`` are
    followed for **each** of those workspaces, since ``npm install`` installs
    the dev toolchain for every workspace it selects.  Returns ``None`` when
    none of *starts* are present in *packages* so callers fall back to the
    full-lockfile comparison.

    The launch install is scoped with ``npm install --workspace ui-tui`` (see
    ``_make_tui_argv``), so only the ui-tui workspace's dependency closure is
    written to the hidden ``.package-lock.json``.  On Termux it additionally
    selects ui-tui's child ``packages/*`` workspaces, so their devDependencies
    join the closure too.  The shared root ``package-lock.json`` additionally
    lists every *other* workspace's deps (``apps/desktop``, ``web``, …);
    comparing the two in full reports those unrelated packages as "missing" and
    reinstalls on every launch (#66978).

    Keys follow npm's v3 ``packages`` map (``""`` root, ``ui-tui`` /
    ``apps/desktop`` workspace members, ``node_modules/<name>`` hoisted deps,
    ``<dir>/node_modules/<name>`` nested deps).  Dependency names resolve to a
    key by walking up ``node_modules`` ancestors, mirroring node resolution, and
    workspace symlinks (``link: true``) are followed to their real entry so a
    linked workspace's own deps join the closure.
    """
    start_set = {starts} if isinstance(starts, str) else {s for s in starts if s}
    present = [s for s in start_set if s in packages]
    if not present:
        return None

    def resolve(from_key: str, dep: str) -> Optional[str]:
        base = from_key
        while True:
            prefix = f"{base}/" if base else ""
            candidate = f"{prefix}node_modules/{dep}"
            if candidate in packages:
                return candidate
            if not base:
                return None
            base = base.rsplit("/", 1)[0] if "/" in base else ""

    seen: set = set()
    stack = list(present)
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        entry = packages.get(key)
        if not isinstance(entry, dict):
            continue
        # Workspace symlink (e.g. node_modules/@hermes/ink → ui-tui/packages/…):
        # follow to the real package entry so its dependencies join the closure.
        resolved = entry.get("resolved")
        if entry.get("link") and isinstance(resolved, str) and resolved in packages:
            stack.append(resolved)
        # devDependencies are installed for each explicitly-selected workspace
        # (its build toolchain), but not for transitive deps.
        fields = ["dependencies", "optionalDependencies", "peerDependencies"]
        if key in start_set:
            fields.append("devDependencies")
        for field in fields:
            deps = entry.get(field)
            if not isinstance(deps, dict):
                continue
            for dep in deps:
                target = resolve(key, dep)
                if target is not None:
                    stack.append(target)
    return seen


def _tui_selected_workspace_keys(tui_dir: Path, ws_root: Path) -> set:
    """Lock-map keys for the workspaces the launch install scopes to.

    Mirrors ``_make_tui_argv``: always the ui-tui workspace, plus its child
    ``packages/*`` workspaces on Termux (where ``include_child_workspaces=True``
    in ``_termux_workspace_install_context``).  ``npm install`` installs the
    devDependencies of every workspace it selects, so the freshness closure must
    treat each as a dev-included root — otherwise a devDependency unique to a
    selected child is dropped from the closure and a genuine missing package
    slips past the check.  Returns an empty set when ui-tui can't be located
    under *ws_root*, so the caller falls back to the full comparison.
    """
    try:
        primary = tui_dir.relative_to(ws_root).as_posix()
    except ValueError:
        return set()
    keys = {primary}
    if _is_termux_startup_environment():
        packages_dir = tui_dir / "packages"
        if packages_dir.is_dir():
            for child in sorted(packages_dir.iterdir()):
                if child.is_dir() and (child / "package.json").is_file():
                    try:
                        keys.add(child.relative_to(ws_root).as_posix())
                    except ValueError:
                        continue
    return keys


def _tui_need_npm_install(root: Path) -> bool:
    """True when @hermes/ink is missing or node_modules is behind package-lock.json.

    Prebuilt bundle mode: when ``dist/entry.js`` exists and there is no
    ``package-lock.json`` (nix install layout only ships ``dist/`` +
    ``package.json``), skip reinstall entirely — the bundle is self-contained
    and there is nothing to install.

    With npm workspaces the single ``package-lock.json`` and the hoisted
    ``node_modules/`` live at the workspace root (the parent of the
    ``ui-tui/`` directory).  The lockfile / ink / marker checks use that
    workspace root; only the prebuilt-bundle sentinel stays relative to
    *root* (``ui-tui/dist/entry.js``).

    Compares ``package-lock.json`` against ``node_modules/.package-lock.json``
    (npm's hidden lockfile) by **content**, not mtime: git checkouts and npm
    rewrites can bump the root lockfile's timestamp even when installed deps
    already match, which used to trigger a spurious "Installing TUI
    dependencies" on every launch.

    For each entry in the root lock's ``packages`` map:
      - missing from hidden lock → reinstall (unless the entry is marked
        ``optional`` or ``peer``, which npm may intentionally skip per platform)
      - present in both → compare only the **intersection** of fields (after
        stripping ``_NPM_LOCK_RUNTIME_KEYS``).  npm's hidden lock
        intentionally omits many metadata fields (version, license, engines,
        dependencies, funding, etc.) — those one-side-only fields are normal
        npm artefacts, not real skew.  A real version/dependency change will
        change ``resolved``/``integrity``, which are present in both locks
        and will be caught by the intersection comparison.

    Extra entries that exist only in the hidden lock are ignored — stale
    transitives left over from a removed dependency don't break runtime and
    we'd rather not force a reinstall for them. Falls back to mtime
    comparison if either lockfile is unparseable.
    """
    # Prebuilt self-contained bundle (nix / packaged release): no lockfile
    # shipped, dist/entry.js is the single runtime artefact.
    entry = root / "dist" / "entry.js"
    # With npm workspaces the lockfile lives at the workspace root.
    ws_root = _workspace_root(root)
    lock = ws_root / "package-lock.json"
    if entry.is_file() and not lock.is_file():
        return False

    ink = ws_root / "node_modules" / "@hermes" / "ink" / "package.json"
    if not ink.is_file():
        return True
    if not lock.is_file():
        return False
    marker = ws_root / "node_modules" / ".package-lock.json"
    if not marker.is_file():
        return True

    # Compare lockfile contents, not mtimes: git checkouts and npm rewrites
    # can bump the root lockfile timestamp even when installed deps already
    # match. Fall back to mtime when either file is unparseable.
    try:
        wanted = json.loads(lock.read_text(encoding="utf-8")).get("packages") or {}
        installed = json.loads(marker.read_text(encoding="utf-8")).get("packages") or {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return lock.stat().st_mtime > marker.stat().st_mtime

    def entries_differ(pkg: dict, installed_pkg: dict) -> bool:
        # Only compare keys present in *both* lockfiles with non-null values.
        # npm's hidden .package-lock.json intentionally omits many metadata
        # fields the root lock records (version, dependencies, license,
        # engines, bin, ...), and npm >= 10/11 writes a further *reduced*
        # hidden lockfile that stores some of them as null.  Missing- or
        # null-on-one-side is a normal npm artefact, not a real skew.  The
        # authoritative fields "resolved" and "integrity" are present in both
        # locks for installed packages, so a genuinely stale install (root
        # lockfile bumped while node_modules is behind) still differs on them.
        a = {k: v for k, v in pkg.items() if k not in _NPM_LOCK_RUNTIME_KEYS}
        b = {
            k: v
            for k, v in installed_pkg.items()
            if k not in _NPM_LOCK_RUNTIME_KEYS
        }
        for k in a.keys() & b.keys():
            if a[k] is None or b[k] is None:
                continue
            if a[k] != b[k]:
                return True
        return False

    # In a shared workspace checkout the launch install is scoped to the ui-tui
    # workspace (plus its child packages/* workspaces on Termux), so only that
    # dependency closure lands in the hidden lock.  Limit the comparison to the
    # same selected-workspace closure so unrelated workspace deps (apps/desktop,
    # web, …) don't force a reinstall every launch (#66978).  Standalone /
    # own-lockfile layouts (ws_root == root) do a full install, so keep the full
    # comparison; a missing/unlocatable workspace falls back to it too.
    closure: Optional[set] = None
    if ws_root != root:
        selected = _tui_selected_workspace_keys(root, ws_root)
        if selected:
            closure = _npm_lock_workspace_closure(wanted, selected)

    for name, pkg in wanted.items():
        if not name:
            continue

        if closure is not None and name not in closure:
            continue

        if not isinstance(pkg, dict):
            continue

        if name not in installed:
            # Workspace link entries (`"link": true`, paths outside
            # node_modules/ like `apps/desktop`, `node_modules/web`) are never
            # materialized by a partial `npm install --workspace ui-tui` —
            # they're deliberately skipped (see #38772) and would otherwise
            # force a reinstall on every launch.
            if pkg.get("optional") or pkg.get("peer") or pkg.get("link"):
                continue
            if not name.startswith("node_modules/"):
                continue
            return True

        if isinstance(installed[name], dict) and entries_differ(
            pkg, installed[name]
        ):
            return True

    return False


_TUI_BUILD_INPUT_DIRS = (
    "src",
    "packages/hermes-ink/src",
)

_TUI_BUILD_INPUT_FILES = (
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "tsconfig.build.json",
    "babel.compiler.config.cjs",
    "scripts/build.mjs",
    "packages/hermes-ink/package.json",
    "packages/hermes-ink/index.js",
    "packages/hermes-ink/text-input.js",
)

_TUI_BUILD_INPUT_SUFFIXES = frozenset(
    {".cjs", ".js", ".jsx", ".json", ".mjs", ".ts", ".tsx"}
)


def _iter_tui_build_inputs(root: Path):
    """Yield source/config files that affect ``ui-tui/dist/entry.js``."""
    for rel in _TUI_BUILD_INPUT_FILES:
        path = root / rel
        if path.is_file():
            yield path

    for rel in _TUI_BUILD_INPUT_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in _TUI_BUILD_INPUT_SUFFIXES:
                yield path


def _tui_need_rebuild(root: Path) -> bool:
    """True when ``dist/entry.js`` is missing or older than TUI inputs.

    The TUI bundle is self-contained. Rebuilding it on every launch adds a
    visible cold-start tax on slow Termux CPUs, while a simple mtime freshness
    check still rebuilds immediately after source updates, dependency updates,
    or local edits. Set ``HERMES_TUI_FORCE_BUILD=1`` to force the old behaviour.
    """
    force = (korra_env("KORRA_TUI_FORCE_BUILD") or "").strip().lower()
    if force in {"1", "true", "yes", "on"}:
        return True

    entry = root / "dist" / "entry.js"
    try:
        output_mtime = entry.stat().st_mtime
    except OSError:
        return True

    for path in _iter_tui_build_inputs(root):
        try:
            if path.stat().st_mtime > output_mtime:
                return True
        except OSError:
            return True
    return False


def _ensure_tui_node() -> None:
    """Make sure `node` + `npm` are on PATH for the TUI.

    If either is missing and scripts/lib/node-bootstrap.sh is available, source
    it and call `ensure_node` (fnm/nvm/proto/brew/bundled cascade). After
    install, capture the resolved node binary path from the bash subprocess
    and prepend its directory to os.environ["PATH"] so shutil.which finds the
    new binaries in this Python process — regardless of which version manager
    was used (nvm, fnm, proto, brew, or the bundled fallback).

    Idempotent no-op when node+npm are already discoverable. Set
    ``HERMES_SKIP_NODE_BOOTSTRAP=1`` to disable auto-install.
    """
    if shutil.which("node") and shutil.which("npm"):
        return
    if korra_env("KORRA_SKIP_NODE_BOOTSTRAP"):
        return

    helper = PROJECT_ROOT / "scripts" / "lib" / "node-bootstrap.sh"
    if not helper.is_file():
        return

    from korra_constants import get_hermes_home

    hermes_home = str(get_hermes_home())
    try:
        # Helper writes logs to stderr; we ask bash to print `command -v node`
        # on stdout once ensure_node succeeds. Subshell PATH edits don't leak
        # back into Python, so the stdout capture is the bridge.
        result = subprocess.run(
            [
                "bash",
                "-c",
                f'source "{helper}" >&2 && ensure_node >&2 && command -v node',
            ],
            env={**os.environ, **korra_env_expand({"KORRA_HOME": hermes_home})},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return

    parts = os.environ.get("PATH", "").split(os.pathsep)
    extras: list[Path] = []

    resolved = (result.stdout or "").strip()
    if resolved:
        extras.append(Path(resolved).resolve().parent)

    extras.extend([Path(hermes_home) / "node" / "bin", Path.home() / ".local" / "bin"])

    for extra in extras:
        s = str(extra)
        if extra.is_dir() and s not in parts:
            parts.insert(0, s)
    os.environ["PATH"] = os.pathsep.join(parts)


def _find_bundled_tui(hermes_cli_dir: Path | None = None) -> Path | None:
    """Find a pre-built TUI entry.js bundled in the wheel."""
    if hermes_cli_dir is None:
        hermes_cli_dir = Path(__file__).parent
    bundled = hermes_cli_dir / "tui_dist" / "entry.js"
    return bundled if bundled.is_file() else None


def _restore_tui_workspace(tui_dir: Path) -> bool:
    """Try to restore a missing ``ui-tui/`` from git, returning True on success.

    On Windows an antivirus / NTFS filter driver can leave tracked ``ui-tui/``
    files deleted in the working tree after ``hermes update`` (HEAD stays
    intact; the files just vanish — see issue #49145). Those files are tracked,
    so ``git restore`` puts them back deterministically. Best-effort: returns
    False (rather than raising) when git is unavailable, this isn't a checkout,
    or the restore leaves the directory still missing — the caller then prints
    the manual-recovery message.
    """
    git = shutil.which("git")
    if not git or not (tui_dir.parent / ".git").exists():
        return False
    try:
        subprocess.run(
            [git, "restore", "--", tui_dir.name],
            cwd=str(tui_dir.parent),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=False,
        )
    except OSError:
        return False
    return tui_dir.is_dir()


def _ensure_tui_workspace(tui_dir: Path) -> None:
    """Ensure ``ui-tui/`` exists before any npm/node subprocess uses it as cwd.

    Without this, a missing workspace falls through to ``subprocess.run(...,
    cwd=<missing ui-tui>)``, which crashes with ``NotADirectoryError``
    (``WinError 267`` on Windows) instead of a usable message (#49145). We
    first try to self-heal via ``git restore``; only if that can't recover the
    directory do we abort with concrete manual-recovery steps.
    """
    if tui_dir.is_dir():
        return

    if _restore_tui_workspace(tui_dir):
        if not korra_env("KORRA_QUIET"):
            print(f'Отсутствовавшие файлы терминального интерфейса восстановлены: {tui_dir}')
        return

    print(
        f'Ошибка: в этой копии Корры отсутствуют файлы терминального интерфейса. Ожидаемая папка: {tui_dir}. Возможно, korra update оставил удалённые файлы ui-tui. Восстановление: из папки Корры выполните git restore -- ui-tui, затем npm install --silent --no-fund --no-audit --progress=false и повторите korra --tui. Если установка всё ещё повреждена: korra update --force.',
        file=sys.stderr,
    )
    sys.exit(1)


def _npm_lifecycle_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Build a clean environment for the pinned UI toolchain lifecycle."""
    run_env = {**os.environ, **(env or {}), "CI": "1"}
    # esbuild treats this as an executable override. If a shell points it at a
    # different release, the pinned package's postinstall rejects that binary.
    run_env.pop("ESBUILD_BINARY_PATH", None)
    return run_env


def _make_tui_argv(tui_dir: Path, tui_dev: bool) -> tuple[list[str], Path]:
    """TUI: --dev → tsx src; else node dist (HERMES_TUI_DIR prebuilt or esbuild)."""
    _ensure_tui_node()

    def _node_bin(bin: str) -> str:
        if bin == "node":
            env_node = korra_env("KORRA_NODE")
            if env_node and os.path.isfile(env_node) and os.access(env_node, os.X_OK):
                return env_node
        # find_node_executable() prefers the managed $HERMES_HOME/node tree,
        # which is not on PATH — a bare which() would declare "node not found"
        # and exit on an install whose only Node is the one Hermes installed,
        # and would pick a system Node over the managed one when both exist.
        from korra_constants import find_node_executable

        path = find_node_executable(bin)
        if not path and bin == "node":
            try:
                from korra_cli.dep_ensure import ensure_dependency
                if ensure_dependency("node"):
                    path = find_node_executable("node")
            except Exception:
                pass
        if not path:
            print(f'{bin} не найден. Для терминального интерфейса установите Node.js.')
            sys.exit(1)
        return path

    # Footgun: --dev against a prebuilt bundle that has no source/node_modules.
    ext_dir = korra_env("KORRA_TUI_DIR")
    if tui_dev and ext_dir:
        print(
            f'Ошибка: --dev несовместим с HERMES_TUI_DIR={ext_dir}. Готовая сборка не содержит исходников для обновления на ходу. Снимите HERMES_TUI_DIR командой unset HERMES_TUI_DIR, чтобы использовать --dev из репозитория.',
            file=sys.stderr,
        )
        sys.exit(1)

    # 1. Prebuilt bundle (nix / packaged release / Docker image): just run it.
    #
    # This must run BEFORE _ensure_tui_workspace() below. A prebuilt install
    # (Docker image, Nix build, or prior `npm run build`) ships
    # korra_cli/tui_dist/entry.js but never ships ui-tui/ at all (that
    # directory only exists in a git checkout) — so requiring the workspace
    # to exist first made every prebuilt dashboard Chat tab connection
    # hard-exit before it ever got a chance to try the bundled entry.js it
    # already has. See #56665.
    if not tui_dev:
        if ext_dir:
            p = Path(ext_dir)
            if (p / "dist" / "entry.js").is_file():
                node = _node_bin("node")
                return [node, "--expose-gc", str(p / "dist" / "entry.js")], p

        # 1b. Bundled prebuilt TUI (Docker image, Nix build, or prior npm build)
        bundled = _find_bundled_tui()
        if bundled is not None:
            node = _node_bin("node")
            return [node, "--expose-gc", str(bundled)], bundled.parent

    # No prebuilt bundle available (or --dev, which never uses one) — we're
    # about to npm install/build from source, so the workspace must exist.
    if not ext_dir:
        _ensure_tui_workspace(tui_dir)

    # 2. Normal flow: npm install if needed, always esbuild, then node dist/entry.js.
    #    --dev flow: npm install if needed, then tsx src/entry.tsx.
    #    Existing desktop behaviour runs npm from the workspace root.  Termux
    #    scopes the install to ui-tui so launch does not pull desktop/web
    #    dependencies into the hot path.
    did_install = False
    termux_startup = _is_termux_startup_environment()
    termux_need_rebuild = False
    if termux_startup and not tui_dev:
        termux_need_rebuild = _tui_need_rebuild(tui_dir)

    skip_install_for_fresh_termux_bundle = (
        termux_startup and not tui_dev and not termux_need_rebuild
    )
    if (
        not skip_install_for_fresh_termux_bundle
        and _tui_need_npm_install(tui_dir)
    ):
        npm = _node_bin("npm")
        if not korra_env("KORRA_QUIET"):
            print('Устанавливаем зависимости терминального интерфейса…')
        npm_cwd = _workspace_root(tui_dir)
        # --workspace ui-tui avoids resolving apps/desktop (Electron + node-pty).
        # See #38772.
        # When ui-tui/ has its own package-lock.json (e.g. curl install),
        # _workspace_root() returns tui_dir itself.  Passing --workspace in
        # that case fails because npm cannot find a workspace named "ui-tui"
        # inside ui-tui/.  See #42973.
        npm_workspace_args: tuple[str, ...] = () if npm_cwd == tui_dir else ("--workspace", "ui-tui")
        if termux_startup:
            npm_cwd, npm_workspace_args = _termux_workspace_install_context(
                tui_dir,
                include_child_workspaces=True,
            )
        npm_install_cmd = [
            npm,
            "install",
            *npm_workspace_args,
            # --include=dev: ui-tui's build toolchain (esbuild, typescript)
            # lives in devDependencies. An inherited NODE_ENV=production
            # (e.g. from a container shell or a parent TUI launch) or an
            # npm `omit=dev` config would silently skip them and the TUI
            # build would fail. See _run_npm_install_deterministic.
            "--include=dev",
            "--silent",
            "--no-fund",
            "--no-audit",
            "--progress=false",
        ]

        def _run_tui_install() -> subprocess.CompletedProcess:
            from korra_constants import with_hermes_node_path

            # Managed tree first on PATH: if the EBADENGINE repair below
            # provisioned a managed Node, npm's shebang/lifecycle scripts must
            # resolve that node, not the mismatched system one.
            return subprocess.run(
                npm_install_cmd,
                cwd=str(npm_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_npm_lifecycle_env(with_hermes_node_path()),
            )

        result = _run_tui_install()
        if result.returncode != 0:
            # An npm outside the root package.json's `engines.npm` range fails
            # here before doing any work; repair once (upgrade a Hermes-managed
            # npm in place, or provision a managed runtime when the npm belongs
            # to the user) and retry rather than dumping EBADENGINE at the user.
            from korra_cli.npm_engine import maybe_repair_npm_engine

            combined_output = f"{result.stdout or ''}\n{result.stderr or ''}"
            repaired_npm = maybe_repair_npm_engine(npm, combined_output)
            if repaired_npm:
                npm = repaired_npm
                npm_install_cmd[0] = repaired_npm
                result = _run_tui_install()
        if result.returncode != 0:
            combined = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print('Не удалось выполнить npm install.')
            if preview:
                print(preview)
            sys.exit(1)
        did_install = True

    if tui_dev:
        # Keep the local @hermes/ink package exports in sync with source.
        # --dev runs src/entry.tsx directly, but @hermes/ink resolves through
        # packages/hermes-ink/dist/entry-exports.js. If that dist bundle is
        # stale after a pull, newer hooks/components can exist in src while
        # being missing at runtime (e.g. useCursorAdvance). Prebuild it here.
        npm = _node_bin("npm")
        ink_dir = tui_dir / "packages" / "hermes-ink"
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(ink_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_npm_lifecycle_env(),
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print('Не удалось подготовить сборку терминального интерфейса для разработки.')
            if preview:
                print(preview)
            sys.exit(1)

        tsx = tui_dir / "node_modules" / ".bin" / "tsx"
        if tsx.exists():
            return [str(tsx), "src/entry.tsx"], tui_dir
        return [npm, "start"], tui_dir

    # Desktop/dev launches retain the historical "always rebuild" behaviour.
    # Termux cold starts use the freshness check because esbuild startup is
    # expensive on old mobile CPUs.
    should_build = True
    if termux_startup:
        should_build = did_install or termux_need_rebuild

    if should_build:
        npm = _node_bin("npm")
        result = subprocess.run(
            [npm, "run", "build"],
            cwd=str(tui_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_npm_lifecycle_env(),
        )
        if result.returncode != 0:
            combined = f"{result.stdout or ''}{result.stderr or ''}".strip()
            preview = "\n".join(combined.splitlines()[-30:])
            print('Не удалось собрать терминальный интерфейс.')
            if preview:
                print(preview)
            sys.exit(1)

    node = _node_bin("node")
    return [node, "--expose-gc", str(tui_dir / "dist" / "entry.js")], tui_dir


def _normalize_tui_toolsets(toolsets: object) -> list[str]:
    """Normalize argparse/Fire-style toolset input for the TUI subprocess."""
    try:
        from korra_cli.oneshot import _normalize_toolsets

        return _normalize_toolsets(toolsets) or []
    except (AttributeError, ImportError):
        if not toolsets:
            return []

        raw_items = [toolsets] if isinstance(toolsets, str) else toolsets
        if not isinstance(raw_items, (list, tuple)):
            raw_items = [raw_items]

        normalized: list[str] = []
        for item in raw_items:
            if isinstance(item, str):
                normalized.extend(part.strip() for part in item.split(","))
            else:
                normalized.append(str(item).strip())

        return [item for item in normalized if item]


def _read_cgroup_memory_limit() -> Optional[int]:
    """Return the container memory limit in bytes, or None if unconstrained.

    Node's V8 heap is NOT cgroup-aware: with a flat ``--max-old-space-size=8192``
    it happily grows the heap toward 8GB regardless of the container's real
    memory limit.  In a Docker/k8s container capped below ~9-10GB, the cgroup
    OOM-killer SIGKILLs Node before V8's own heap monitor ever fires — which
    runs no JS handler, writes no ``[tui-parent]`` breadcrumb, and the user
    sees only a bare gateway ``stdin EOF``.  Reading the real cgroup limit lets
    us size the heap cap below it so V8 GCs/exits gracefully instead of being
    reaped silently.

    Checks cgroup v2 (``/sys/fs/cgroup/memory.max``) then v1
    (``/sys/fs/cgroup/memory/memory.limit_in_bytes``).  A literal ``max`` (v2)
    or the v1 "unlimited" sentinel (a huge near-INT64 value) means no limit.
    """
    candidates = (
        "/sys/fs/cgroup/memory.max",  # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
    )
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except (OSError, ValueError):
            continue
        if raw == "max":
            return None
        if not raw:
            # Blank/empty file: no usable value here. Fall through to the next
            # candidate (don't mistake an empty v2 file for "unlimited").
            continue
        try:
            limit = int(raw)
        except ValueError:
            continue
        if limit <= 0:
            continue
        # cgroup v1 reports "unlimited" as a huge value (often
        # 0x7FFFFFFFFFFFF000 ≈ 9.2 EB, sometimes PAGE_COUNTER_MAX). Anything
        # at/above ~1 PB is effectively unconstrained — treat as no limit.
        if limit >= (1 << 50):
            return None
        return limit
    return None


def _resolve_tui_heap_mb(default_mb: int = 8192) -> int:
    """Pick a V8 ``--max-old-space-size`` (MB) that fits the container.

    Returns ``default_mb`` (8192) when unconstrained or when the box is large
    enough that 8GB fits.  In a memory-limited container, returns ~75% of the
    cgroup limit so the heap + non-heap RSS stays under the cgroup ceiling,
    clamped to a sane floor (1536MB — below this V8 GC-thrashes and the TUI
    is barely usable).  Never exceeds ``default_mb``.
    """
    limit = _read_cgroup_memory_limit()
    if not limit:
        return default_mb
    limit_mb = limit // (1024 * 1024)
    # Leave headroom for non-heap RSS (Node internals, buffers, the Python
    # gateway child shares the same cgroup): cap the heap at 75% of the limit.
    sized = int(limit_mb * 0.75)
    if sized >= default_mb:
        return default_mb
    # Floor so a tiny limit doesn't drive V8 into constant GC. If the container
    # is smaller than the floor, honor the limit-derived value anyway (better a
    # graceful V8 exit than a silent cgroup kill).
    return max(1536, sized) if limit_mb > 2048 else sized


def _safe_tui_cwd(env: Optional[dict] = None) -> str:
    """Return a stable cwd value for the Node TUI child environment."""
    try:
        return os.getcwd()
    except FileNotFoundError:
        candidate = ((env or {}).get("PWD") or os.environ.get("PWD") or "").strip()
        if candidate and Path(candidate).is_dir():
            return candidate
        return str(PROJECT_ROOT)


def _apply_tui_python_env(env: dict) -> None:
    """Seed/repair Python-related env vars shared by CLI and dashboard TUI launches."""
    src_root = str(korra_env("KORRA_PYTHON_SRC_ROOT", env=env) or "").strip()
    if not src_root or not Path(src_root).is_dir():
        korra_env_set(env, "KORRA_PYTHON_SRC_ROOT", str(PROJECT_ROOT))

    cwd = str(korra_env("KORRA_CWD", env=env) or "").strip()
    if not cwd or not Path(cwd).is_dir():
        korra_env_set(env, "KORRA_CWD", _safe_tui_cwd(env))

    python = str(korra_env("KORRA_PYTHON", env=env) or "").strip()
    if os.path.dirname(python):
        python_path = Path(python)
        if not python_path.is_absolute():
            python_path = Path(korra_env("KORRA_CWD", "", env=env)) / python_path
        python_is_executable = python_path.is_file() and os.access(python_path, os.X_OK)
    else:
        python_is_executable = bool(shutil.which(python, path=env.get("PATH")))
    if not python_is_executable:
        korra_env_set(env, "KORRA_PYTHON", sys.executable)


def _launch_tui(
    resume_session_id: Optional[str] = None,
    tui_dev: bool = False,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: object = None,
    skills: object = None,
    verbose: Optional[bool] = None,
    quiet: bool = False,
    query: Optional[str] = None,
    image: Optional[str] = None,
    worktree: bool = False,
    checkpoints: bool = False,
    pass_session_id: bool = False,
    max_turns: Optional[int] = None,
    accept_hooks: bool = False,
):
    """Replace current process with the TUI."""
    tui_dir = PROJECT_ROOT / "ui-tui"

    import tempfile

    # TUI child is a hermes process: propagate the profile-home contract via
    # the single factory; keep secrets (the TUI/agent needs provider creds).
    from tools.environments.local import build_subprocess_env
    env = build_subprocess_env(scrub_secrets=False, inherit_profile_home=True)
    try:
        from korra_cli.config import apply_terminal_config_to_env
        apply_terminal_config_to_env(env=env)
    except Exception:
        logger.debug("Failed to apply terminal config bridge for TUI launch", exc_info=True)
    active_session_fd, active_session_file = tempfile.mkstemp(
        prefix="hermes-tui-active-session-", suffix=".json"
    )
    os.close(active_session_fd)
    korra_env_set(env, "KORRA_TUI_ACTIVE_SESSION_FILE", active_session_file)
    env.setdefault("NODE_ENV", "development" if tui_dev else "production")

    wt_info = None
    if worktree:
        try:
            from cli import (
                _cleanup_worktree,
                _git_repo_root,
                _maintain_pack_health,
                _prune_stale_worktrees,
                _setup_worktree,
            )

            repo = _git_repo_root()
            if repo:
                _prune_stale_worktrees(repo)
                # Same maintenance pass as the CLI path: repack on pack
                # sprawl so `worktree add` never crawls on a multi-agent box
                # (cli._maintain_pack_health is a cheap no-op below the
                # threshold). Runs on a thread — the TUI path calls the
                # pruner synchronously, and a repack must not block launch.
                import threading as _threading

                _threading.Thread(
                    target=_maintain_pack_health,
                    args=(repo,),
                    name="pack-maintenance",
                    daemon=True,
                ).start()
            wt_info = _setup_worktree()
        except Exception as exc:
            print(f'✗ Не удалось создать рабочую копию Git для терминального интерфейса: {exc}', file=sys.stderr)
            wt_info = None
        if not wt_info:
            sys.exit(1)
        korra_env_set(env, "KORRA_CWD", wt_info["path"])
        env["TERMINAL_CWD"] = wt_info["path"]

    _apply_tui_python_env(env)

    if model:
        korra_env_set(env, "KORRA_MODEL", model)
        korra_env_set(env, "KORRA_INFERENCE_MODEL", model)
    if provider:
        korra_env_set(env, "KORRA_TUI_PROVIDER", provider)
        korra_env_set(env, "KORRA_INFERENCE_PROVIDER", provider)
    tui_toolsets = _normalize_tui_toolsets(toolsets)
    if tui_toolsets:
        korra_env_set(env, "KORRA_TUI_TOOLSETS", ",".join(tui_toolsets))
    if skills:
        if isinstance(skills, (list, tuple)):
            flattened = []
            for item in skills:
                flattened.extend(
                    part.strip() for part in str(item).split(",") if part.strip()
                )
            if flattened:
                korra_env_set(env, "KORRA_TUI_SKILLS", ",".join(flattened))
        else:
            value = str(skills).strip()
            if value:
                korra_env_set(env, "KORRA_TUI_SKILLS", value)
    if query:
        korra_env_set(env, "KORRA_TUI_QUERY", query)
    if image:
        korra_env_set(env, "KORRA_TUI_IMAGE", image)
    if checkpoints:
        korra_env_set(env, "KORRA_TUI_CHECKPOINTS", "1")
    if pass_session_id:
        korra_env_set(env, "KORRA_TUI_PASS_SESSION_ID", "1")
    if max_turns is not None:
        korra_env_set(env, "KORRA_TUI_MAX_TURNS", str(max_turns))
    if verbose:
        korra_env_set(env, "KORRA_TUI_TOOL_PROGRESS", "verbose")
    elif quiet:
        korra_env_set(env, "KORRA_TUI_TOOL_PROGRESS", "off")
    if accept_hooks:
        korra_env_set(env, "KORRA_ACCEPT_HOOKS", "1")
    # Guarantee a generous V8 heap for the TUI. Default node cap is ~1.5–4GB
    # depending on version and can fatal-OOM on long sessions with large
    # transcripts / reasoning blobs. We target 8GB on an unconstrained host,
    # but V8 is NOT cgroup-aware: in a memory-limited Docker/k8s container a
    # flat 8GB heap grows past the container limit and the cgroup OOM-killer
    # SIGKILLs Node — running no JS handler, writing no breadcrumb, leaving the
    # user with only a bare gateway `stdin EOF`. _resolve_tui_heap_mb() reads
    # the real cgroup limit and sizes the cap below it so V8 GCs/exits
    # gracefully (and the memory monitor's onCritical breadcrumb can fire)
    # instead of being reaped silently. Token-level merge: respect any
    # user-supplied --max-old-space-size (they may have set it higher).
    # --expose-gc is *not* added here: Node rejects it in NODE_OPTIONS
    # ("--expose-gc is not allowed in NODE_OPTIONS") and refuses to start.
    # It is passed as a direct argv flag in _make_tui_argv() instead.
    _tokens = env.get("NODE_OPTIONS", "").split()
    if not any(t.startswith("--max-old-space-size=") for t in _tokens):
        _tokens.append(f"--max-old-space-size={_resolve_tui_heap_mb()}")
    env["NODE_OPTIONS"] = " ".join(_tokens)
    # HERMES_TUI_RESUME is an internal hand-off from the Python wrapper to the
    # Ink app.  Because we start from a full os.environ snapshot (via
    # build_subprocess_env), an exported/stale value
    # in the user's shell would otherwise make a plain `hermes --tui` try to
    # resume a non-existent session and leave the UI at "error: session not
    # found" with no live session.  Only forward a resume id that argparse
    # resolved for this invocation; direct `node ui-tui/dist/entry.js` users can
    # still set HERMES_TUI_RESUME themselves.
    korra_env_pop(env, "KORRA_TUI_RESUME")
    if resume_session_id:
        korra_env_set(env, "KORRA_TUI_RESUME", resume_session_id)

    argv, cwd = _make_tui_argv(tui_dir, tui_dev)
    code: Optional[int] = None
    try:
        try:
            code = subprocess.call(argv, cwd=str(cwd), env=env)
        except KeyboardInterrupt:
            code = 130

        if code in {0, 130}:
            _print_tui_exit_summary(resume_session_id, active_session_file)
    finally:
        try:
            os.unlink(active_session_file)
        except OSError:
            pass
        if wt_info:
            try:
                _cleanup_worktree(wt_info)
            except Exception:
                pass

    # Exit code 42 = TUI requested an update. Relaunch as `hermes update` so
    # the user sees update output directly and gets the new version.
    # preserve_inherited=False ensures --tui and other flags are NOT carried
    # into the update subcommand.
    if code == 42:
        from korra_cli.relaunch import relaunch

        print()
        print('⚕ Запускаем обновление…')
        print()
        relaunch(["update"], preserve_inherited=False)

    sys.exit(code)


def _pin_kanban_board_env() -> None:
    """Pin the active kanban board into ``HERMES_KANBAN_BOARD`` for the chat session.

    Without this, in-process tools (``kanban_*``) and shelled-out CLI calls
    (``hermes kanban …``) resolve the board on different paths: the env-pin if
    set, otherwise the global ``<root>/kanban/current`` file. A concurrent
    ``hermes kanban boards switch`` from another session can flip the file
    mid-turn, so the same chat sees its tool calls hit board A while its shell
    calls hit board B (#20074). Pinning at chat boot mirrors what the
    dispatcher already does for spawned workers.
    """
    if korra_env("KORRA_KANBAN_BOARD"):
        return
    try:
        from korra_cli.kanban_db import get_current_board

        korra_env_set(os.environ, "KORRA_KANBAN_BOARD", get_current_board())
    except Exception:
        pass


def _sync_bundled_skills_quietly() -> None:
    """Seed ``~/.hermes/skills/`` with the bundled skill library on first launch.

    Called from any CLI entrypoint that the user might use as their first
    interaction with Hermes — chat, dashboard (the desktop GUI's backend),
    and gateway. The skills_sync module is manifest-based and idempotent:
    skipped skills cost ~milliseconds, so calling this repeatedly is fine.

    Failures are swallowed because skills are an enhancement, not a hard
    dependency. Hermes still functions without them; the user just sees an
    empty skills library.
    """
    try:
        from tools.skills_sync import sync_skills

        sync_skills(quiet=True)
    except Exception:
        pass


def _resolve_use_tui(args) -> bool:
    """Decide whether to launch the TUI for a chat/bare invocation.

    Precedence (highest first):
      1. ``--cli`` flag         → always classic REPL
      2. ``--tui`` flag         → always TUI (explicit ask)
      3. no TTY                 → always classic (ambient prefs don't apply)
      4. ``HERMES_TUI=1`` env   → TUI
      5. ``display.interface`` config value ("cli" | "tui")
      6. default → classic REPL

    Explicit flags always win over config so muscle memory and scripts keep
    working regardless of the configured default.

    The TTY gate (3) is load-bearing: ambient TUI preferences (env var or
    config default) must never hijack a NON-interactive invocation. Kanban
    workers, cron jobs, and pipelines run ``hermes … chat -q`` with stdout
    on a pipe; booting the Ink TUI there hits its no-TTY bail-out, which
    prints a resume hint and exits 0 — a kanban worker then dies with
    "exited cleanly without calling kanban_complete — protocol violation"
    on every attempt (found dogfooding the desktop kanban board). A user
    who *explicitly* passes ``--tui`` still gets the informative bail-out.
    """
    if getattr(args, "cli", False):
        return False
    if getattr(args, "tui", False):
        return True
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:
        return False
    if korra_env("KORRA_TUI") == "1":
        return True
    try:
        from korra_cli.config import load_config

        iface = (load_config().get("display", {}) or {}).get("interface", "cli")
        return isinstance(iface, str) and iface.strip().lower() == "tui"
    except Exception:
        return False


def cmd_chat(args):
    """Run interactive chat CLI."""
    use_tui = _resolve_use_tui(args)

    _apply_safe_mode(args)

    # --in DIR: run in DIR. Must happen before any session resolution so the
    # workspace-scoped "latest"/-c lookups key off DIR, and it pins the
    # session there — an explicit --in wins over a resumed session's
    # recorded cwd (so the restore step below is skipped).
    in_dir = getattr(args, "in_dir", None)
    if in_dir:
        # Git Bash / MSYS hands the CLI POSIX-style paths (`--in ~` expands to
        # `/c/Users/x` before Python ever sees it; MSYS2's path conversion is
        # disabled for native executables). Translate the MSYS/Cygwin/WSL
        # drive-root spellings to native Windows form first — no-op elsewhere.
        from tools.environments.local import _msys_to_windows_path

        _target_dir = os.path.abspath(
            os.path.expanduser(_msys_to_windows_path(in_dir))
        )
        if not os.path.isdir(_target_dir):
            print(f'Ошибка: папка --in не найдена: {in_dir}')
            sys.exit(1)
        try:
            os.chdir(_target_dir)
        except OSError as e:
            print(f'Ошибка: не удалось перейти в папку --in {in_dir}: {e}')
            sys.exit(1)
        args.no_restore_cwd = True

    # --resume latest: keyword for "most recent session" — same resolution
    # as `-c` with no name (workspace-scoped MRU, then global fallback).
    # The keyword wins over a session literally titled "latest"; that
    # session stays reachable via its ID or `-c latest` (title match).
    _resume_raw = getattr(args, "resume", None)
    if isinstance(_resume_raw, str) and _resume_raw.strip().lower() == "latest":
        _source = "tui" if use_tui else "cli"
        _last_id = _resolve_last_session(source=_source)
        if not _last_id and _source == "tui":
            _last_id = _resolve_last_session(source="cli")
        if _last_id:
            args.resume = _last_id
        else:
            kind = "TUI" if use_tui else "CLI"
            print(f'Предыдущая беседа {kind} не найдена.')
            print('Посмотреть доступные беседы: korra sessions list.')
            sys.exit(1)

    # Resolve --continue into --resume with the latest session or by name
    _resolve_continue_arg(args, use_tui=use_tui)

    # --resume @claude / --resume @codex: import a foreign session (Claude
    # Code / Codex CLI) and resume the newly created Hermes session.
    _resume_foreign = getattr(args, "resume", None)
    if isinstance(_resume_foreign, str) and _resume_foreign.strip().lower() in (
        "@claude",
        "@codex",
    ):
        from korra_cli.foreign_sessions import (
            import_foreign_session,
            pick_foreign_session,
        )

        _foreign_source = _resume_foreign.strip().lower().lstrip("@")
        _picked = pick_foreign_session(_foreign_source)
        if _picked is None:
            sys.exit(1)
        try:
            _imported_id = import_foreign_session(_picked.source, _picked.path)
        except ValueError as e:
            print(f'Ошибка: {e}')
            sys.exit(1)
        print(f'✓ Беседа импортирована как {_imported_id}. Продолжаем её.')
        print(f'  Позже можно открыть так: korra --resume {_imported_id}')
        args.resume = _imported_id

    # Resolve --resume by title if it's not a direct session ID
    resume_val = getattr(args, "resume", None)
    if resume_val:
        resolved = _resolve_session_by_name_or_id(resume_val)
        if resolved:
            args.resume = resolved
        # If resolution fails, keep the original value — _init_agent will
        # report "Session not found" with the original input

    # Session<->workspace binding: cd back into a resumed session's recorded cwd
    # so it resumes in the repo it belonged to. Opt out with --no-restore-cwd;
    # skipped under --worktree (that path owns its own dir). Best-effort — a
    # missing dir warns and stays put rather than failing the resume.
    if (
        getattr(args, "resume", None)
        and not getattr(args, "no_restore_cwd", False)
        and not getattr(args, "worktree", False)
    ):
        _resume_db = None
        try:
            from korra_state import SessionDB

            _resume_db = SessionDB()
            _saved_cwd = ((_resume_db.get_session(args.resume) or {}).get("cwd") or "").strip()
            if _saved_cwd and not os.path.isdir(_saved_cwd):
                print(f'⚠ Сохранённая папка беседы удалена: {_saved_cwd}. Остаёмся в {os.getcwd()}.')
            elif _saved_cwd and os.path.realpath(_saved_cwd) != os.path.realpath(os.getcwd()):
                os.chdir(_saved_cwd)
                print(f'↪ Рабочая папка восстановлена: {_saved_cwd}')
        except Exception:
            pass  # never let cwd-restore break a resume
        finally:
            if _resume_db is not None:
                try:
                    _resume_db.close()
                except Exception:
                    pass

    # xAI retirement warning — one-shot, non-blocking, never fails startup
    try:
        from korra_cli.xai_retirement import (
            MIGRATION_GUIDE_URL,
            RETIREMENT_DATE,
            find_retired_xai_refs,
            format_issue,
        )
        from korra_cli.config import load_config as _load_config_for_xai_check

        _retired_xai_refs = find_retired_xai_refs(_load_config_for_xai_check())
        if _retired_xai_refs:
            sys.stderr.write(
                f'\x1b[33m⚠ xAI снимает с поддержки модели из ваших настроек ({len(_retired_xai_refs)}) с {RETIREMENT_DATE}:\x1b[0m'
            )
            for _ref in _retired_xai_refs:
                sys.stderr.write(f"  \033[33m⚠\033[0m {format_issue(_ref)}\n")
            sys.stderr.write(f'  \x1b[2mИнструкция по переходу: {MIGRATION_GUIDE_URL}\x1b[0m')
            sys.stderr.write('  Подробнее: korra doctor.')
    except Exception:
        pass

    # First-run guard: check if any provider is configured before launching
    if not _has_any_provider_configured():
        print()
        print(
            'Корра ещё не настроена: ключи API и провайдеры не найдены.'
        )
        print()
        print('  Выполните: korra setup')
        print()

        from korra_cli.setup import (
            is_interactive_stdin,
            print_noninteractive_setup_guidance,
        )

        if not is_interactive_stdin():
            print_noninteractive_setup_guidance(
                'Интерактивный терминал для первой настройки не найден.'
            )
            sys.exit(1)

        try:
            reply = input('Открыть настройку сейчас? [Y/n] ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = "n"
        if reply in {"", "y", "yes"}:
            cmd_setup(args)
            return
        print()
        print('Вы можете настроить Корру в любое время командой korra setup.')
        sys.exit(1)

    # Start update check in background (runs while other init happens).
    # On Termux this imports rich/prompt_toolkit in the foreground and then
    # competes for CPU on single-core devices, so keep it opt-in there.
    if _termux_should_prefetch_update_check():
        try:
            from korra_cli.banner import prefetch_banner_data, prefetch_update_check

            prefetch_update_check()
            # Warm git banner state + skills index off-thread too — their
            # subprocess/file-I/O waits overlap the CPU-bound cli import.
            prefetch_banner_data()
        except Exception:
            pass

    # Sync bundled skills on every CLI launch. Normally runs in a background
    # daemon thread: the sync is idempotent, hash-gated (unchanged skills are
    # skipped), and nothing on the banner path depends on it, yet the scan
    # alone costs ~120-170ms of rglob/hashing on the startup path. Skill
    # loading happens at agent init (first message), by which point the
    # sync has long finished.
    #
    # FIRST RUN is the exception: with an empty ~/.hermes/skills the banner
    # prefetch races the background sync, caches an empty skills index, and
    # the very first launch greets the user with "No skills installed ·
    # 0 skills" while 69 bundled skills land milliseconds later (full-surface
    # CLI QA sweep, Aug 2026). Run the sync in the foreground exactly once —
    # only when the skills dir has no SKILL.md yet — so the first impression
    # matches reality; every later launch keeps the background path.
    def _skills_dir_is_unseeded() -> bool:
        try:
            from korra_cli.config import get_hermes_home
            skills_dir = Path(get_hermes_home()) / "skills"
            if not skills_dir.is_dir():
                return True
            return next(skills_dir.rglob("SKILL.md"), None) is None
        except Exception:
            return False

    def _skills_sync_bg() -> None:
        try:
            _sync_bundled_skills_for_startup()
        except Exception:
            pass

    if _skills_dir_is_unseeded():
        _skills_sync_bg()
        # The banner prefetch thread (started above) may have scanned the
        # still-empty dir and cached an empty skills index — drop it so the
        # banner recomputes against the freshly seeded tree.
        try:
            import korra_cli.banner as _banner_mod
            _banner_mod._available_skills_cache = None
        except Exception:
            pass
    else:
        threading.Thread(
            target=_skills_sync_bg, name="bundled-skills-sync", daemon=True
        ).start()

    # --yolo: bypass all dangerous command approvals.
    # Also set in main() before _prepare_agent_startup() — that is the
    # authoritative site because it runs before tool imports freeze
    # _YOLO_MODE_FROZEN.  This redundant set is a safety net for callers
    # that invoke cmd_chat directly (e.g. subcommand dispatch).
    if getattr(args, "yolo", False):
        korra_env_set(os.environ, "KORRA_YOLO_MODE", "1")

    # --ignore-user-config: make load_cli_config() / load_config() skip the
    # user's ~/.hermes/config.yaml and return built-in defaults. Set BEFORE
    # importing cli (which runs `CLI_CONFIG = load_cli_config()` at module
    # import time). Credentials in .env are still loaded — this flag only
    # ignores behavioral/config settings.
    if getattr(args, "ignore_user_config", False):
        korra_env_set(os.environ, "KORRA_IGNORE_USER_CONFIG", "1")

    # --ignore-rules: skip auto-injection of AGENTS.md/SOUL.md/.cursorrules
    # (rules), memory entries, and any preloaded skills coming from user config.
    # Maps to AIAgent(skip_context_files=True, skip_memory=True).
    if getattr(args, "ignore_rules", False):
        korra_env_set(os.environ, "KORRA_IGNORE_RULES", "1")

    # --source: tag session source for filtering (e.g. 'tool' for third-party integrations)
    if getattr(args, "source", None):
        korra_env_set(os.environ, "KORRA_SESSION_SOURCE", args.source)

    _pin_kanban_board_env()
    _confirm_startup_expensive_model_override(args)

    if use_tui:
        _launch_tui(
            getattr(args, "resume", None),
            tui_dev=getattr(args, "tui_dev", False),
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
            skills=getattr(args, "skills", None),
            verbose=getattr(args, "verbose", None),
            quiet=getattr(args, "quiet", False),
            query=getattr(args, "query", None),
            image=getattr(args, "image", None),
            worktree=getattr(args, "worktree", False),
            checkpoints=getattr(args, "checkpoints", False),
            pass_session_id=getattr(args, "pass_session_id", False),
            max_turns=getattr(args, "max_turns", None),
            accept_hooks=getattr(args, "accept_hooks", False),
        )

    # --query-file: read the single query from a file (or stdin via '-') so
    # callers never have to shell-quote message bodies. This is the transport
    # the Bot Mode DM protocol uses — interpolating arbitrary text into a
    # double-quoted shell argument truncates on quotes and executes $(...)
    # (see tools/bot_mode_probe.py).
    _qfile = getattr(args, "query_file", None)
    if _qfile:
        if args.query:
            # argparse's mutually-exclusive group catches the normal CLI path;
            # this guards programmatic callers that fill the namespace directly.
            print('Ошибка: -q/--query и --query-file нельзя использовать вместе', file=sys.stderr)
            sys.exit(2)
        try:
            if _qfile == "-":
                args.query = sys.stdin.read()
            else:
                with open(_qfile, "r", encoding="utf-8", errors="replace") as _fh:
                    args.query = _fh.read()
        except OSError as _e:
            print(f'Ошибка чтения --query-file {_qfile}: {_e}', file=sys.stderr)
            sys.exit(2)
        if not (args.query or "").strip():
            print(f'Ошибка: файл --query-file {_qfile} пуст', file=sys.stderr)
            sys.exit(2)

    # Build kwargs from args
    kwargs = {
        "model": args.model,
        "provider": getattr(args, "provider", None),
        "reasoning": getattr(args, "reasoning", None),
        "toolsets": args.toolsets,
        "skills": getattr(args, "skills", None),
        "verbose": getattr(args, "verbose", None),
        "quiet": getattr(args, "quiet", False),
        "query": args.query,
        "oneshot": bool(getattr(args, "oneshot_exit", False)),
        "image": getattr(args, "image", None),
        "resume": getattr(args, "resume", None),
        "worktree": getattr(args, "worktree", False),
        "checkpoints": getattr(args, "checkpoints", False),
        "pass_session_id": getattr(args, "pass_session_id", False),
        "max_turns": getattr(args, "max_turns", None),
        "run_budget": getattr(args, "run_budget", None),
        "ignore_rules": getattr(args, "ignore_rules", False) or getattr(args, "safe_mode", False),
        "ignore_user_config": getattr(args, "ignore_user_config", False) or getattr(args, "safe_mode", False),
        "compact": getattr(args, "compact", False),
    }
    # Filter out None values
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    try:
        from cli import main as cli_main

        cli_main(**kwargs)
    except ValueError as e:
        print(f'Ошибка: {e}')
        sys.exit(1)
    except ImportError as e:
        # Mixed-version installs (new cli.py, older korra_cli.config) crash
        # here — e.g. missing resolve_turn_limit / split_model_config_default
        # (#96900). The agent-setup mixin prints this hint too late: HermesCLI
        # construction already failed. Fast-chat launch also goes through
        # cmd_chat, so this one catch covers `hermes` / `hermes chat`.
        from korra_constants import emit_partial_update_hint

        if emit_partial_update_hint(e):
            sys.exit(1)
        raise


def cmd_gateway(args):
    """Gateway management commands."""
    _sync_bundled_skills_quietly()

    from korra_cli.gateway import gateway_command

    gateway_command(args)


def cmd_proxy(args):
    """Local OpenAI-compatible proxy to OAuth providers."""
    # Lazy import — pulls in aiohttp, which is gated behind an extras install
    # for users who don't run the proxy or the messaging gateway.
    from korra_cli.proxy.cli import cmd_proxy as _cmd_proxy

    rc = _cmd_proxy(args)
    if isinstance(rc, int) and rc != 0:
        raise SystemExit(rc)


def cmd_whatsapp(args):
    """Set up WhatsApp: choose mode, configure, install bridge, pair via QR."""
    _require_tty("whatsapp")
    from korra_cli.config import get_env_value, save_env_value
    from korra_constants import find_node_executable, with_hermes_node_path

    print()
    print('⚕ Настройка WhatsApp')
    print("=" * 50)

    # ── Step 1: Choose mode ──────────────────────────────────────────────
    current_mode = get_env_value("WHATSAPP_MODE") or ""
    if not current_mode:
        print()
        print('Как вы будете использовать WhatsApp с Коррой?')
        print()
        print('  1. Отдельный номер бота — рекомендуется')
        print('     Пользователи пишут прямо на номер бота.')
        print(
            '     Нужен второй номер с WhatsApp на устройстве.'
        )
        print()
        print('  2. Личный номер — чат с собой')
        print('     Для общения с агентом вы пишете себе.')
        print('     Настроить быстрее, но пользоваться менее удобно.')
        print()
        try:
            choice = input('  Выберите [1/2]: ').strip()
        except (EOFError, KeyboardInterrupt):
            print('Настройка отменена.')
            return

        if choice == "1":
            save_env_value("WHATSAPP_MODE", "bot")
            wa_mode = "bot"
            print('  ✓ Режим: отдельный номер бота')
            print()
            print("  ┌─────────────────────────────────────────────────┐")
            print('  │  Как получить второй номер для бота:           │')
            print("  │                                                 │")
            print('  │  Установите бесплатный WhatsApp Business       │')
            print('  │  на телефон со вторым номером:                 │')
            print('  │    • Dual-SIM: используйте вторую SIM-карту     │')
            print('  │    • Google Voice: номер США (voice.google)     │')
            print('  │    • Предоплатная SIM-карта: проверка один раз  │')
            print("  │                                                 │")
            print('  │  WhatsApp Business работает рядом с личным     │')
            print('  │  WhatsApp. Второй телефон не нужен.             │')
            print("  └─────────────────────────────────────────────────┘")
        else:
            save_env_value("WHATSAPP_MODE", "self-chat")
            wa_mode = "self-chat"
            print('  ✓ Режим: личный номер, чат с собой')
    else:
        wa_mode = current_mode
        mode_label = (
            'отдельный номер бота' if wa_mode == "bot" else 'личный номер, чат с собой'
        )
        print(f'✓ Режим: {mode_label}')

    # ── Step 2: Mode is selected, will enable WhatsApp only after pairing ──
    # We intentionally don't write WHATSAPP_ENABLED=true here.  If the user
    # aborts the wizard later (Ctrl+C, failed npm install, missed QR scan),
    # we'd otherwise leave .env claiming WhatsApp is ready when the bridge
    # has no creds.json.  Every subsequent `hermes gateway` then paid a 30s
    # bridge-bootstrap timeout and queued WhatsApp for indefinite retries.
    # Now: aborted setup leaves WHATSAPP_ENABLED unset → gateway skips it.
    # Re-runs that already have WHATSAPP_ENABLED=true (from a prior
    # successful pairing) stay enabled — we just don't write it pre-emptively.
    print()
    if (get_env_value("WHATSAPP_ENABLED") or "").lower() == "true":
        print('✓ WhatsApp уже включён')

    # ── Step 3: Allowed users ────────────────────────────────────────────
    current_users = get_env_value("WHATSAPP_ALLOWED_USERS") or ""
    if current_users:
        print(f'✓ Пользователи с доступом: {current_users}')
        try:
            response = input('  Изменить список пользователей с доступом? [y/N] ').strip()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response.lower() in {"y", "yes"}:
            if wa_mode == "bot":
                phone = line_input(
                    '  Номера телефонов с доступом к боту, через запятую: '
                ).strip()
            else:
                phone = line_input('  Ваш номер телефона, например 15551234567: ').strip()
            if phone:
                save_env_value("WHATSAPP_ALLOWED_USERS", phone.replace(" ", ""))
                print(f'  ✓ Обновлено: {phone}')
    else:
        print()
        if wa_mode == "bot":
            print('  Кому разрешить писать боту?')
            phone = line_input(
                '  Номера телефонов через запятую или * для доступа всем: '
            ).strip()
        else:
            phone = line_input('  Ваш номер телефона, например 15551234567: ').strip()
        if phone:
            save_env_value("WHATSAPP_ALLOWED_USERS", phone.replace(" ", ""))
            print(f'  ✓ Доступ разрешён: {phone}')
        else:
            print('  ⚠ Список доступа пуст: агент ответит на все входящие сообщения')

    # ── Step 4: Install bridge dependencies ──────────────────────────────
    from gateway.platforms.whatsapp_common import resolve_whatsapp_bridge_dir
    bridge_dir = resolve_whatsapp_bridge_dir()
    bridge_script = bridge_dir / "bridge.js"

    if not bridge_script.exists():
        print(f'✗ Скрипт моста не найден: {bridge_script}')
        return

    if not (bridge_dir / "node_modules").exists():
        print(
            '→ Устанавливаем зависимости моста WhatsApp. Это может занять несколько минут…'
        )
        npm = find_node_executable("npm")
        if not npm:
            print('  ✗ npm не найден в PATH. Сначала установите Node.js.')
            return
        try:
            result = subprocess.run(
                [npm, "install", "--no-fund", "--no-audit", "--progress=false"],
                cwd=str(bridge_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=with_hermes_node_path(),
            )
        except KeyboardInterrupt:
            print('  ✗ Установка отменена')
            return
        if result.returncode != 0:
            err = (result.stderr or "").strip()
            preview = "\n".join(err.splitlines()[-30:]) if err else '(нет вывода)'
            print('  ✗ Не удалось выполнить npm install:')
            print(preview)
            return
        print('  ✓ Зависимости установлены')
    else:
        print('✓ Зависимости моста уже установлены')

    # ── Step 5: Check for existing session ───────────────────────────────
    session_dir = get_hermes_home() / "whatsapp" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    if (session_dir / "creds.json").exists():
        print('✓ Найдено существующее подключение WhatsApp')
        try:
            response = input(
                '  Подключить заново? Текущее подключение будет удалено. [y/N] '
            ).strip()
        except (EOFError, KeyboardInterrupt):
            response = "n"
        if response.lower() in {"y", "yes"}:
            shutil.rmtree(session_dir, ignore_errors=True)
            session_dir.mkdir(parents=True, exist_ok=True)
            print('  ✓ Подключение сброшено')
        else:
            # Existing pairing — ensure WHATSAPP_ENABLED reflects that.
            # (Older installs may have lost the env var; covers re-runs
            # where the user picked "no, keep my session" but the var
            # was never set or got removed.)
            if (get_env_value("WHATSAPP_ENABLED") or "").lower() != "true":
                save_env_value("WHATSAPP_ENABLED", "true")
            print('✓ WhatsApp настроен и подключён!')
            print('  Запустить шлюз: korra gateway')
            return

    # ── Step 6: QR code pairing ──────────────────────────────────────────
    print()
    print("─" * 50)
    if wa_mode == "bot":
        print('📱 Откройте WhatsApp или WhatsApp Business')
        print('   на телефоне с номером бота и отсканируйте код:')
    else:
        print('📱 Откройте WhatsApp на телефоне и отсканируйте код:')
    print()
    print('   Настройки → Связанные устройства → Привязка устройства')
    print("─" * 50)
    print()

    try:
        subprocess.run(
            [
                find_node_executable("node") or "node",
                str(bridge_script),
                "--pair-only",
                "--session",
                str(session_dir),
            ],
            cwd=str(bridge_dir),
            env=with_hermes_node_path(),
        )
    except KeyboardInterrupt:
        pass

    # ── Step 7: Post-pairing ─────────────────────────────────────────────
    print()
    if (session_dir / "creds.json").exists():
        # Only enable WhatsApp now that pairing actually succeeded.  If the
        # user Ctrl+C'd at any earlier step, WHATSAPP_ENABLED stays unset
        # and `hermes gateway` skips it cleanly instead of paying a 30s
        # bridge timeout + queueing the platform for indefinite retries.
        save_env_value("WHATSAPP_ENABLED", "true")
        print('✓ WhatsApp подключён!')
        print()
        if wa_mode == "bot":
            print('  Дальше:')
            print('    1. Запустите шлюз: korra gateway')
            print('    2. Напишите на номер бота в WhatsApp')
            print('    3. Агент ответит автоматически')
            print()
            print('  Ответы агента начинаются с «⚕ Korra»')
        else:
            print('  Дальше:')
            print('    1. Запустите шлюз: korra gateway')
            print('    2. Откройте WhatsApp → Сообщение себе')
            print('    3. Напишите сообщение — агент ответит')
            print()
            print('  Ответы агента начинаются с «⚕ Korra»')
            print('  Так вы отличите их от своих сообщений.')
        print()
        print('  Или установите службу: korra gateway install')
    else:
        print('⚠ Подключение могло не завершиться. Повторите: korra whatsapp.')


def cmd_whatsapp_cloud(args):
    """Set up WhatsApp Business Cloud API (official Meta integration).

    Walks the user through the Meta-side credentials (Phone Number ID,
    Access Token, App Secret, optional App/WABA IDs) plus webhook
    configuration. Includes field-shape validators that catch the most
    common setup mistakes (e.g. pasting a phone number into the Phone
    Number ID field).

    Distinct from ``hermes whatsapp`` (the Baileys bridge wizard) — the
    two adapters are complementary, not alternatives. See
    ``korra_cli/setup_whatsapp_cloud.py``.
    """
    _require_tty("whatsapp-cloud")
    from korra_cli.setup_whatsapp_cloud import run_whatsapp_cloud_setup

    return run_whatsapp_cloud_setup()


def cmd_setup(args):
    """Interactive setup wizard."""
    from korra_cli.setup import run_setup_wizard

    run_setup_wizard(args)


def cmd_model(args):
    """Select default model — starts with provider selection, then model picker."""
    _require_tty("model")
    if getattr(args, "refresh", False):
        try:
            from korra_cli.models import clear_provider_models_cache
            clear_provider_models_cache()
            print('  Кеш выбора моделей очищен.')
        except Exception:
            pass
    from korra_cli.setup import run_setup_action_with_navigation

    run_setup_action_with_navigation(
        "Model & Provider",
        lambda: select_provider_and_model(args=args),
        cancelled_message='Без изменений.',
    )


def _is_profile_api_key_provider(provider_id: str) -> bool:
    """Return True when provider_id maps to a profile with auth_type='api_key'.

    Used as a catch-all in select_provider_and_model() so that new providers
    declared in plugins/model-providers/<name>/ automatically dispatch to _model_flow_api_key_provider
    without requiring an explicit elif branch here.
    """
    try:
        from providers import get_provider_profile
        _p = get_provider_profile(provider_id)
        return _p is not None and _p.auth_type == "api_key"
    except Exception:
        return False


def select_provider_and_model(args=None):
    """Core provider selection + model picking logic.

    Shared by ``cmd_model`` (``hermes model``) and the setup wizard
    (``setup_model_provider`` in setup.py).  Handles the full flow:
    provider picker, credential prompting, model selection, and config
    persistence.
    """
    from korra_cli.auth import (
        resolve_provider,
        AuthError,
        format_auth_error,
    )
    from korra_cli.config import (
        get_compatible_custom_providers,
        load_config,
        get_env_value,
    )
    from korra_cli.providers import (
        custom_provider_aliases,
        custom_provider_slug,
        resolve_provider_full,
    )

    config = load_config()
    current_model = config.get("model")
    if isinstance(current_model, dict):
        current_model = current_model.get("default", "")
    current_model = current_model or '(не задано)'

    # Read effective provider the same way the CLI does at startup:
    # config.yaml model.provider > env var > auto-detect
    config_provider = None
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        config_provider = model_cfg.get("provider")

    effective_provider = (
        config_provider or korra_env("KORRA_INFERENCE_PROVIDER") or "auto"
    )
    compatible_custom_providers = get_compatible_custom_providers(config)
    def _named_custom_provider_map(cfg) -> dict[str, dict[str, str]]:
        from korra_cli.config import read_raw_config

        # Build lookups of raw (un-expanded) templates keyed by a
        # stable identity. We intentionally bypass
        # ``get_compatible_custom_providers(read_raw_config())`` here because
        # its ``_normalize_custom_provider_entry`` step calls ``urlparse()``
        # on ``base_url`` and drops any entry whose ``base_url`` is itself an
        # env-ref template (e.g. ``${NEURALWATT_API_BASE}``). Dropping those
        # entries is exactly how env-ref preservation fails for the user
        # config that motivated this fix.
        raw_api_key_refs: dict[tuple, str] = {}
        raw_base_url_refs: dict[tuple, str] = {}
        raw_cfg = read_raw_config()

        def _record_raw(
            name: str,
            provider_key: str,
            model: str,
            api_key: str,
            base_url: str,
        ) -> None:
            template = str(api_key or "").strip()
            base_template = str(base_url or "").strip()
            name = str(name or "").strip()
            provider_key = str(provider_key or "").strip()
            model = str(model or "").strip()
            # Index by every plausible identity the loaded (expanded) config
            # might present: (name), (name, model), (provider_key), and
            # (provider_key, model). Case-insensitive on name/provider_key so
            # the loaded entry matches regardless of display casing.
            identities = []
            if name:
                identities.extend(((name.lower(),), (name.lower(), model)))
            if provider_key:
                identities.extend(
                    ((provider_key.lower(),), (provider_key.lower(), model))
                )
            if "${" in template:
                for identity in identities:
                    raw_api_key_refs.setdefault(identity, template)
            if "${" in base_template:
                for identity in identities:
                    raw_base_url_refs.setdefault(identity, base_template)

        raw_list = raw_cfg.get("custom_providers")
        if isinstance(raw_list, list):
            for raw_entry in raw_list:
                if not isinstance(raw_entry, dict):
                    continue
                _record_raw(
                    raw_entry.get("name", ""),
                    "",
                    raw_entry.get("model", "") or raw_entry.get("default_model", ""),
                    raw_entry.get("api_key", ""),
                    raw_entry.get("base_url", "")
                    or raw_entry.get("url", "")
                    or raw_entry.get("api", ""),
                )
        raw_providers = raw_cfg.get("providers")
        if isinstance(raw_providers, dict):
            for raw_key, raw_entry in raw_providers.items():
                if not isinstance(raw_entry, dict):
                    continue
                _record_raw(
                    raw_entry.get("name", "") or raw_key,
                    raw_key,
                    raw_entry.get("model", "") or raw_entry.get("default_model", ""),
                    raw_entry.get("api_key", ""),
                    raw_entry.get("base_url", "")
                    or raw_entry.get("url", "")
                    or raw_entry.get("api", ""),
                )

        def _lookup_ref(
            refs: dict[tuple, str],
            name: str,
            provider_key: str,
            model: str,
        ) -> str:
            name_lc = str(name or "").strip().lower()
            pkey_lc = str(provider_key or "").strip().lower()
            model = str(model or "").strip()
            for identity in (
                (pkey_lc, model),
                (pkey_lc,),
                (name_lc, model),
                (name_lc,),
            ):
                if identity[0] and identity in refs:
                    return refs[identity]
            return ""

        custom_provider_map = {}
        for entry in get_compatible_custom_providers(cfg):
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").strip()
            base_url = (entry.get("base_url") or "").strip()
            if not name or not base_url:
                continue
            provider_key = (entry.get("provider_key") or "").strip()
            key = custom_provider_slug(name, provider_key)
            custom_provider_map[key] = {
                "name": name,
                "base_url": base_url,
                "api_key": entry.get("api_key", ""),
                "key_env": entry.get("key_env") or entry.get("api_key_env", ""),
                "model": entry.get("model", ""),
                "models": entry.get("models", {}),
                "models_discovered": entry.get("models_discovered", False),
                "extra_headers": entry.get("extra_headers", {}),
                "discover_models": entry.get("discover_models", True),
                "api_mode": entry.get("api_mode", ""),
                "provider_key": provider_key,
                "api_key_ref": _lookup_ref(
                    raw_api_key_refs, name, provider_key, entry.get("model", "")
                ),
                "base_url_ref": _lookup_ref(
                    raw_base_url_refs, name, provider_key, entry.get("model", "")
                ),
            }
        return custom_provider_map

    def _norm_base_url(url: str) -> str:
        return str(url or "").strip().rstrip("/").lower()

    # Add user-defined custom providers from config.yaml
    _custom_provider_map = _named_custom_provider_map(
        config
    )  # key → {name, base_url, api_key}

    def _canonical_named_custom_key(provider_id: str) -> str:
        requested = str(provider_id or "").strip().lower()
        for key, provider_info in _custom_provider_map.items():
            if requested in custom_provider_aliases(
                provider_info.get("name", ""),
                provider_info.get("provider_key", ""),
            ):
                return key
        return provider_id

    def _active_custom_key_from_base_url() -> str:
        if effective_provider != "custom" or not isinstance(model_cfg, dict):
            return ""
        current_base = _norm_base_url(model_cfg.get("base_url", ""))
        if not current_base:
            return ""
        for key, provider_info in _custom_provider_map.items():
            if _norm_base_url(provider_info.get("base_url", "")) == current_base:
                return key
        return ""

    active = _active_custom_key_from_base_url()
    if active is None:
        active = ""
    if not active and effective_provider != "auto":
        active_def = resolve_provider_full(
            effective_provider,
            config.get("providers"),
            compatible_custom_providers,
        )
        if active_def is not None:
            active = active_def.id
            if active_def.source == "user-config":
                active = _canonical_named_custom_key(active)
        else:
            warning = (
                f'Неизвестный провайдер «{effective_provider}». Посмотрите доступных через korra model или проверьте настройки через korra doctor.'
            )
            print(f'Внимание: {warning} Используется автоматический выбор провайдера.')
    if not active:
        try:
            active = resolve_provider("auto")
        except AuthError as exc:
            if effective_provider == "auto":
                warning = format_auth_error(exc)
                print(f'Внимание: {warning} Используется автоматический выбор провайдера.')
            active = None  # no provider yet; default to first in list

    # Detect custom endpoint
    if active == "openrouter" and get_env_value("OPENAI_BASE_URL"):
        active = "custom"

    from korra_cli.models import (
        CANONICAL_PROVIDERS,
        _PROVIDER_LABELS,
        _PROVIDER_ALIASES,
        group_providers,
        provider_group_for_slug,
    )

    provider_labels = dict(_PROVIDER_LABELS)  # derive from canonical list
    if active and active in _custom_provider_map:
        active_label = _custom_provider_map[active]["name"]
    else:
        active_label = provider_labels.get(active, active) if active else "none"

    print()
    print(f'  Текущая модель:   {current_model}')
    print(f'  Провайдер:        {active_label}')
    print()

    # Step 1: Provider selection.
    #
    # Canonical providers are folded into top-level groups (display only — see
    # PROVIDER_GROUPS in korra_cli/models.py). A multi-member group shows one
    # row ("Kimi / Moonshot ▸"); picking it opens a member sub-picker that
    # resolves back to a concrete slug, so the dispatch chain below is
    # unchanged. Custom providers and the trailing actions stay flat.
    canonical_descs = {p.slug: p.tui_desc for p in CANONICAL_PROVIDERS}
    # Honor ``model_catalog.excluded_providers`` so the CLI ``hermes model``
    # picker hides the same providers the gateway/TUI pickers do. A canonical
    # provider is hidden if its slug OR any of its aliases appears in the
    # exclusion list (case-insensitive), matching list_authenticated_providers'
    # matching against hermes_id / alias / canonical slug.
    _cli_excluded = {
        str(p).strip().lower()
        for p in (config.get("model_catalog", {}) or {}).get("excluded_providers") or []
        if p
    }
    if _cli_excluded:
        _alias_to_canon = _PROVIDER_ALIASES
        _names_for: dict[str, set[str]] = {}
        for _p in CANONICAL_PROVIDERS:
            _names_for[_p.slug] = {_p.slug.lower()}
        for _alias, _canon in _alias_to_canon.items():
            _names_for.setdefault(_canon, {_canon.lower()}).add(_alias.lower())
        _visible_slugs = [
            p.slug for p in CANONICAL_PROVIDERS
            if not _names_for.get(p.slug, {p.slug.lower()}) & _cli_excluded
        ]
    else:
        _visible_slugs = [p.slug for p in CANONICAL_PROVIDERS]
    grouped_rows = group_providers(_visible_slugs)

    # The group/slug that should be pre-selected: the active provider's group
    # if it's grouped, otherwise the active slug itself.
    active_group = provider_group_for_slug(active) if active else ""

    # ordered entries: (key, label, members)
    #   members == [] → leaf row, key is a provider slug / action
    #   members != [] → group row, key is "group:<gid>"
    ordered: list[tuple[str, str, list[str]]] = []
    default_idx = 0
    for row in grouped_rows:
        if row["kind"] == "group":
            gid = row["group_id"]
            group_desc = row.get("description", "")
            label = f"{row['label']} ▸ ({group_desc})" if group_desc else f"{row['label']} ▸"
            key = f"group:{gid}"
            is_active = bool(active_group) and gid == active_group
            members = row["members"]
        else:
            slug = row["slug"]
            label = canonical_descs.get(slug, provider_labels.get(slug, slug))
            key = slug
            is_active = bool(active) and slug == active
            members = []
        if is_active:
            ordered.append((key, f'{label}  ← выбран сейчас', members))
            default_idx = len(ordered) - 1
        else:
            ordered.append((key, label, members))

    for key, provider_info in _custom_provider_map.items():
        name = provider_info["name"]
        base_url = provider_info["base_url"]
        short_url = base_url.replace("https://", "").replace("http://", "").rstrip("/")
        saved_model = provider_info.get("model", "")
        model_hint = f" — {saved_model}" if saved_model else ""
        label = f"{name} ({short_url}){model_hint}"
        if active and key == active:
            ordered.append((key, f'{label}  ← выбран сейчас', []))
            default_idx = len(ordered) - 1
        else:
            ordered.append((key, label, []))

    ordered.append(("custom", 'Свой сервер: ввести адрес вручную', []))
    _has_saved_custom_list = isinstance(config.get("custom_providers"), list) and bool(
        config.get("custom_providers")
    )
    if _has_saved_custom_list:
        ordered.append(("remove-custom", 'Удалить сохранённого провайдера', []))
    ordered.append(("aux-config", 'Настроить вспомогательные модели…', []))
    ordered.append(("cancel", 'Оставить без изменений', []))

    provider_idx = _prompt_provider_choice(
        [label for _, label, _ in ordered],
        default=default_idx,
    )
    if provider_idx is None or ordered[provider_idx][0] == "cancel":
        print('Без изменений.')
        return

    selected_key = ordered[provider_idx][0]
    selected_members = ordered[provider_idx][2]

    # Group row → drill into a member sub-picker. Default to the active member
    # if the active provider lives in this group. The descriptive text lives on
    # the group row itself, so member rows show only their short label here.
    if selected_members:
        member_default = 0
        if active in selected_members:
            member_default = selected_members.index(active)
        member_labels = [
            provider_labels.get(m, m) for m in selected_members
        ]
        group_label = ordered[provider_idx][1].split(" ▸", 1)[0]
        member_idx = _prompt_provider_choice(
            member_labels,
            default=member_default,
            title=f'Выберите провайдера {group_label}:',
        )
        if member_idx is None:
            print('Без изменений.')
            return
        selected_provider = selected_members[member_idx]
    else:
        selected_provider = selected_key

    if selected_provider == "aux-config":
        _aux_config_menu()
        return

    # Step 2: Provider-specific setup + model selection
    if selected_provider == "openrouter":
        _model_flow_openrouter(config, current_model)
    elif selected_provider == "moa":
        _model_flow_moa(config, current_model)
    elif selected_provider == "ai-gateway":
        _model_flow_ai_gateway(config, current_model)
    elif selected_provider == "nous":
        _model_flow_nous(config, current_model, args=args)
    elif selected_provider == "openai-codex":
        _model_flow_openai_codex(config, current_model)
    elif selected_provider == "xai-oauth":
        _model_flow_xai_oauth(config, current_model, args=args)
    elif selected_provider == "qwen-oauth":
        _model_flow_qwen_oauth(config, current_model)
    elif selected_provider == "minimax-oauth":
        _model_flow_minimax_oauth(config, current_model, args=args)
    elif selected_provider == "copilot-acp":
        _model_flow_copilot_acp(config, current_model)
    elif selected_provider == "copilot":
        _model_flow_copilot(config, current_model)
    elif selected_provider == "custom":
        _model_flow_custom(config)
    elif (
        selected_provider.startswith("custom:")
        or selected_provider in _custom_provider_map
    ):
        provider_info = _named_custom_provider_map(load_config()).get(selected_provider)
        if provider_info is None:
            print(
                'Выбранный сохранённый провайдер больше недоступен. Возможно, он удалён из config.yaml. Настройки не изменены.'
            )
            return
        _model_flow_named_custom(config, provider_info)
    elif selected_provider == "remove-custom":
        _remove_custom_provider(config)
    elif selected_provider == "anthropic":
        _model_flow_anthropic(config, current_model)
    elif selected_provider == "kimi-coding":
        _model_flow_kimi(config, current_model)
    elif selected_provider == "stepfun":
        _model_flow_stepfun(config, current_model)
    elif selected_provider == "bedrock":
        _model_flow_bedrock(config, current_model)
    elif selected_provider == "vertex":
        _model_flow_vertex(config, current_model)
    elif selected_provider == "azure-foundry":
        _model_flow_azure_foundry(config, current_model)
    elif selected_provider in {
        "openai-api",
        "gemini",
        "deepseek",
        "xai",
        "zai",
        "kimi-coding-cn",
        "minimax",
        "minimax-cn",
        "kilocode",
        "opencode-zen",
        "opencode-go",
        "opencode-free",
        "alibaba",
        "huggingface",
        "xiaomi",
        "arcee",
        "gmi",
        "nvidia",
        "ollama-cloud",
        "tencent-tokenhub",
        "tencent-tokenplan",
        "lmstudio",
    } or _is_profile_api_key_provider(selected_provider):
        _model_flow_api_key_provider(config, selected_provider, current_model)

    # ── Post-switch cleanup: clear stale OPENAI_BASE_URL ──────────────
    # When the user switches to a named provider (anything except "custom"),
    # a leftover OPENAI_BASE_URL in ~/.hermes/.env can poison auxiliary
    # clients that use provider:auto. Clear it proactively.  (#5161)
    if selected_provider not in {
        "custom",
        "cancel",
        "remove-custom",
    } and not selected_provider.startswith("custom:"):
        _clear_stale_openai_base_url()


def _clear_stale_openai_base_url():
    """Remove OPENAI_BASE_URL from ~/.hermes/.env if the active provider is not 'custom'.

    After a provider switch, a leftover OPENAI_BASE_URL causes auxiliary
    clients (compression, vision, delegation) with provider:auto to route
    requests to the old custom endpoint instead of the newly selected
    provider.  See issue #5161.
    """
    from korra_cli.config import get_env_value, save_env_value, load_config

    cfg = load_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        provider = (model_cfg.get("provider") or "").strip().lower()
    else:
        provider = ""

    if provider == "custom" or not provider:
        return  # custom provider legitimately uses OPENAI_BASE_URL

    stale_url = get_env_value("OPENAI_BASE_URL")
    if stale_url:
        save_env_value("OPENAI_BASE_URL", "")
        print(
            f'Устаревший OPENAI_BASE_URL удалён из .env; прежнее значение: {stale_url[:40]}…'
            if len(stale_url) > 40
            else f'Устаревший OPENAI_BASE_URL удалён из .env; прежнее значение: {stale_url}'
        )


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliary model configuration
#
# Hermes uses lightweight "auxiliary" models for side tasks (vision analysis,
# context compression, web extraction, session search, etc.). Each task has
# its own provider+model pair in config.yaml under `auxiliary.<task>`.
#
# The UI lives behind "Configure auxiliary models..." at the bottom of the
# `hermes model` provider picker. It does NOT re-run credential setup — it
# only routes already-authenticated providers to specific aux tasks. Users
# configure new providers through the normal `hermes model` flow first.
# ─────────────────────────────────────────────────────────────────────────────

# (task_key, display_name, short_description)
_AUX_TASKS: list[tuple[str, str, str]] = [
    ("vision", 'Изображения', 'анализ изображений и снимков экрана'),
    ("compression", 'Сжатие', 'краткое изложение контекста'),
    ("approval", 'Подтверждения', 'умное подтверждение команд'),
    ("mcp", "MCP", 'рассуждения инструментов MCP'),
    ("title_generation", 'Названия бесед', 'создание названий бесед'),
    ("review", 'Проверка', 'независимый агент проверки /review'),
    ("memory_query_rewrite", 'Поиск в памяти', 'уточнение поисковых запросов к памяти'),
    ("tts_audio_tags", 'Голосовые пометки', 'пометки для синтеза речи Gemini'),
    ("skills_hub", 'Каталог навыков', 'поиск и установка навыков'),
    ("triage_specifier", 'Уточнение задач', 'подготовка заданий на доске'),
    ("kanban_decomposer", 'Разделение задач', 'разделение на подзадачи'),
    ("profile_describer", 'Описания профилей', 'автоматическое описание профилей'),
    ("curator", 'Обслуживание навыков', 'проверка использования навыков'),
]

# Special non-auxiliary task surfaced in the same picker: subagent delegation.
# Routing lives under top-level `delegation.*` in config.yaml (NOT
# `auxiliary.delegation`) because delegate_task spawns full child agents via
# tools/delegate_tool.py::_resolve_delegation_credentials(), which reads the
# delegation section directly. "auto" here means "inherit the parent agent's
# provider/model/credentials" and is stored as empty strings — never persist
# the literal "auto", or it would be resolved as a provider name.
_DELEGATION_TASK_KEY = "delegation"
_DELEGATION_TASK_NAME = 'Делегирование'
_DELEGATION_TASK_DESC = 'модель подчинённых агентов (delegate_task)'


def _all_aux_tasks() -> list[tuple[str, str, str]]:
    """Return built-in + plugin-registered auxiliary tasks for picker/menu use.

    Built-in tasks come first (preserving order), followed by plugin tasks
    sorted by key. Used by ``_aux_config_menu``, ``_reset_aux_to_auto``, and
    display-name lookups so plugin-registered tasks (registered via
    :meth:`korra_cli.plugins.PluginContext.register_auxiliary_task`) appear
    in the same surfaces as built-in ones without core knowing about them.
    """
    tasks = list(_AUX_TASKS)
    try:
        from korra_cli.plugins import get_plugin_auxiliary_tasks
        for entry in get_plugin_auxiliary_tasks():
            tasks.append((entry["key"], entry["display_name"], entry["description"]))
    except Exception:
        # Plugin discovery failure must not break the aux config UI.
        # Built-in tasks remain available.
        pass
    return tasks


def _format_aux_current(task_cfg: dict) -> str:
    """Render the current aux config for display in the task menu."""
    if not isinstance(task_cfg, dict):
        return "auto"
    base_url = str(task_cfg.get("base_url") or "").strip()
    provider = str(task_cfg.get("provider") or "auto").strip() or "auto"
    model = str(task_cfg.get("model") or "").strip()
    if base_url:
        short = base_url.replace("https://", "").replace("http://", "").rstrip("/")
        return f'свой сервер ({short})' + (f" · {model}" if model else "")
    if provider == "auto":
        return "auto" + (f" · {model}" if model else "")
    if model:
        return f"{provider} · {model}"
    return provider


def _delegation_cfg_as_task(cfg: dict) -> dict:
    """Project the top-level ``delegation`` section into aux-task shape.

    Returns a dict with provider/model/base_url/api_key keys so the shared
    rendering (``_format_aux_current``) and picker code can treat delegation
    like any other task. Empty provider means "inherit parent" which renders
    as "auto".
    """
    d = cfg.get("delegation")
    if not isinstance(d, dict):
        d = {}
    return {
        "provider": str(d.get("provider") or "").strip(),
        "model": str(d.get("model") or "").strip(),
        "base_url": str(d.get("base_url") or "").strip(),
        "api_key": str(d.get("api_key") or "").strip(),
    }


def _aux_task_display_name(task: str) -> str:
    """Display name for a task key, covering the special delegation entry."""
    if task == _DELEGATION_TASK_KEY:
        return _DELEGATION_TASK_NAME
    return next((name for key, name, _ in _all_aux_tasks() if key == task), task)


def _save_aux_choice(
    task: str,
    *,
    provider: str,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
) -> None:
    """Persist an auxiliary task's provider/model to config.yaml.

    Only writes the four routing fields — timeout, download_timeout, and any
    other task-specific settings are preserved untouched. The main model
    config (``model.default``/``model.provider``) is never modified.

    The special ``delegation`` task writes to the top-level ``delegation``
    section (consumed by ``tools/delegate_tool.py``), not ``auxiliary.*``.
    There, "auto" (inherit the parent agent) is stored as an empty provider —
    the literal string "auto" would be resolved as a provider name.
    """
    from korra_cli.config import load_config, save_config

    cfg = load_config()

    if task == _DELEGATION_TASK_KEY:
        entry = cfg.setdefault("delegation", {})
        if not isinstance(entry, dict):
            entry = {}
            cfg["delegation"] = entry
        entry["provider"] = "" if provider == "auto" else provider
        entry["model"] = model or ""
        entry["base_url"] = base_url or ""
        entry["api_key"] = api_key or ""
        save_config(cfg)
        return

    aux = cfg.setdefault("auxiliary", {})
    if not isinstance(aux, dict):
        aux = {}
        cfg["auxiliary"] = aux
    entry = aux.setdefault(task, {})
    if not isinstance(entry, dict):
        entry = {}
        aux[task] = entry
    entry["provider"] = provider
    entry["model"] = model or ""
    entry["base_url"] = base_url or ""
    entry["api_key"] = api_key or ""
    save_config(cfg)


def _reset_aux_to_auto() -> int:
    """Reset every known aux task back to auto/empty. Returns number reset.

    Includes plugin-registered tasks (via ``_all_aux_tasks``) so a plugin
    that contributed an auxiliary task gets reset alongside built-ins.
    """
    from korra_cli.config import load_config, save_config

    cfg = load_config()
    aux = cfg.setdefault("auxiliary", {})
    if not isinstance(aux, dict):
        aux = {}
        cfg["auxiliary"] = aux
    count = 0
    for task, _name, _desc in _all_aux_tasks():
        entry = aux.setdefault(task, {})
        if not isinstance(entry, dict):
            entry = {}
            aux[task] = entry
        changed = False
        if entry.get("provider") not in {None, "", "auto"}:
            entry["provider"] = "auto"
            changed = True
        for field in ("model", "base_url", "api_key"):
            if entry.get(field):
                entry[field] = ""
                changed = True
        # Preserve timeout/download_timeout — those are user-tuned, not routing
        if changed:
            count += 1
    # Delegation (top-level section) — clear only the routing fields; other
    # delegation settings (max_concurrent_children, max_spawn_depth, etc.)
    # are not routing and must be preserved.
    dele = cfg.get("delegation")
    if isinstance(dele, dict):
        changed = False
        for field in ("provider", "model", "base_url", "api_key"):
            if dele.get(field):
                dele[field] = ""
                changed = True
        if changed:
            count += 1
    save_config(cfg)
    return count


def _aux_config_menu() -> None:
    """Top-level auxiliary-model picker — choose a task to configure.

    Loops until the user picks "Back" so multiple tasks can be configured
    without returning to the main provider menu.
    """
    from korra_cli.config import load_config

    while True:
        cfg = load_config()
        aux = cfg.get("auxiliary", {}) if isinstance(cfg.get("auxiliary"), dict) else {}

        print()
        print('  Вспомогательные модели для отдельных задач')
        print()
        print('  Анализ изображений, сжатие, чтение страниц и другие задачи')
        print('  по умолчанию используют основную модель. auto означает')
        print('  «использовать основную модель». Если она недоступна,')
        print('  Корра пробует облегчённого провайдера OpenRouter или Nous.')
        print('  Ниже можно закрепить провайдера и модель за конкретной задачей.')
        print()

        # Build the task menu with current settings inline
        all_tasks = _all_aux_tasks()
        menu_tasks = all_tasks + [
            (_DELEGATION_TASK_KEY, _DELEGATION_TASK_NAME, _DELEGATION_TASK_DESC)
        ]
        name_col = max(len(name) for _, name, _ in menu_tasks) + 2
        desc_col = max(len(desc) for _, _, desc in menu_tasks) + 4
        entries: list[tuple[str, str]] = []
        for task_key, name, desc in menu_tasks:
            if task_key == _DELEGATION_TASK_KEY:
                task_cfg = _delegation_cfg_as_task(cfg)
            else:
                task_cfg = (
                    aux.get(task_key, {}) if isinstance(aux.get(task_key), dict) else {}
                )
            current = _format_aux_current(task_cfg)
            label = (
                f"{name.ljust(name_col)}{('(' + desc + ')').ljust(desc_col)}{current}"
            )
            entries.append((task_key, label))
        entries.append(("__reset__", 'Вернуть автоматический выбор для всех'))
        entries.append(("__back__", 'Назад'))

        idx = _prompt_provider_choice(
            [label for _, label in entries],
            default=0,
        )
        if idx is None:
            return
        key = entries[idx][0]
        if key == "__back__":
            return
        if key == "__reset__":
            n = _reset_aux_to_auto()
            if n:
                print(f'Автоматический выбор восстановлен для вспомогательных задач: {n}.')
            else:
                print('Все вспомогательные задачи уже настроены на автоматический выбор.')
            print()
            continue
        # Otherwise configure the specific task
        _aux_select_for_task(key)


def _aux_select_for_task(task: str) -> None:
    """Pick a provider + model for a single auxiliary task and persist it.

    Provider rows come from ``build_aux_picker_rows()`` — the shared aux-picker
    substrate — so this surface shows exactly what every other aux picker
    shows: authenticated built-ins, the user's own ``providers:`` /
    ``custom_providers:`` endpoints, and providers whose credential pool is
    temporarily exhausted. Only already-configured providers appear; users set
    up new ones through the normal ``hermes model`` flow, then route aux tasks
    to them here.
    """
    from korra_cli.config import load_config
    from korra_cli.inventory import build_aux_picker_rows, format_aux_picker_entries

    cfg = load_config()
    if task == _DELEGATION_TASK_KEY:
        task_cfg = _delegation_cfg_as_task(cfg)
    else:
        aux = cfg.get("auxiliary", {}) if isinstance(cfg.get("auxiliary"), dict) else {}
        task_cfg = aux.get(task, {}) if isinstance(aux.get(task), dict) else {}
    current_provider = str(task_cfg.get("provider") or "auto").strip() or "auto"
    current_model = str(task_cfg.get("model") or "").strip()
    current_base_url = str(task_cfg.get("base_url") or "").strip()

    display_name = _aux_task_display_name(task)

    # Gather authenticated providers (has credentials + curated model list)
    try:
        providers = build_aux_picker_rows(
            current_provider=current_provider,
            current_model=current_model,
            current_base_url=current_base_url,
        )
    except Exception as exc:
        print(f'Не удалось определить провайдеров с выполненным входом: {exc}')
        providers = []

    entries: list[tuple[str, str, list[str]]] = []  # (slug, label, models)
    # "auto" always first
    auto_marker = (
        "  ← current" if current_provider == "auto" and not current_base_url else ""
    )
    auto_label = (
        'авто: модель основного агента'
        if task == _DELEGATION_TASK_KEY
        else "auto (recommended)"
    )
    entries.append(("__auto__", f"{auto_label}{auto_marker}", []))

    entries.extend(
        format_aux_picker_entries(
            providers,
            current_provider=current_provider,
            current_base_url=current_base_url,
        )
    )

    # Custom endpoint (raw base_url)
    custom_marker = "  ← current" if current_base_url else ""
    entries.append(("__custom__", f'Свой сервер по прямому адресу{custom_marker}', []))
    entries.append(("__back__", 'Назад', []))

    print()
    print(f'  Настройка {display_name}; сейчас: {_format_aux_current(task_cfg)}')
    print()

    idx = _prompt_provider_choice([label for _, label, _ in entries], default=0)
    if idx is None:
        return
    slug, _label, models = entries[idx]

    if slug == "__back__":
        return

    if slug == "__auto__":
        _save_aux_choice(task, provider="auto", model="", base_url="", api_key="")
        print(f'{display_name}: восстановлен автоматический выбор.')
        return

    if slug == "__custom__":
        _aux_flow_custom_endpoint(task, task_cfg)
        return

    # Regular provider — pick a model from its curated list
    _aux_flow_provider_model(task, slug, models, current_model)


def _aux_flow_provider_model(
    task: str,
    provider_slug: str,
    curated_models: list,
    current_model: str = "",
) -> None:
    """Prompt for a model under an already-authenticated provider, save to aux."""
    from korra_cli.auth import _prompt_model_selection
    from korra_cli.models import get_pricing_for_provider

    display_name = _aux_task_display_name(task)

    # Fetch live pricing for this provider (non-blocking)
    pricing: dict = {}
    try:
        pricing = get_pricing_for_provider(provider_slug) or {}
    except Exception:
        pricing = {}

    model_list = list(curated_models)

    # Let the user pick a model. _prompt_model_selection supports "Enter custom
    # model name" and cancel.  When there's no curated list (rare), fall back
    # to a raw input prompt.
    if not model_list:
        print(f'Для {provider_slug} нет готового списка моделей.')
        print('Введите имя модели вручную; пустая строка — выбор провайдера по умолчанию:')
        try:
            val = line_input('Модель: ').strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        selected = val or ""
    else:
        selected = _prompt_model_selection(
            model_list,
            current_model=current_model,
            pricing=pricing,
            confirm_provider=provider_slug,
        )
        if selected is None:
            print('Без изменений.')
            return

    _save_aux_choice(
        task, provider=provider_slug, model=selected or "", base_url="", api_key=""
    )
    if selected:
        print(f"{display_name}: {provider_slug} · {selected}")
    else:
        print(f'{display_name}: {provider_slug}; модель провайдера по умолчанию')


def _aux_flow_custom_endpoint(task: str, task_cfg: dict) -> None:
    """Prompt for a direct OpenAI-compatible base_url + optional api_key/model."""
    from korra_cli.secret_prompt import masked_secret_prompt

    display_name = _aux_task_display_name(task)
    current_base_url = str(task_cfg.get("base_url") or "").strip()
    current_model = str(task_cfg.get("model") or "").strip()

    print()
    print(f'  Свой адрес сервера для {display_name}')
    print('  Укажите адрес API, совместимого с OpenAI, например http://localhost:11434/v1')
    print()
    try:
        url_prompt = (
            f'Адрес API [{current_base_url}]: ' if current_base_url else 'Адрес API: '
        )
        url = line_input(url_prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    url = url or current_base_url
    if not url:
        print('Адрес не указан. Без изменений.')
        return
    try:
        model_prompt = (
            f'Имя модели (необязательно) [{current_model}]: '
            if current_model
            else 'Имя модели (необязательно): '
        )
        model = line_input(model_prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return
    model = model or current_model
    try:
        api_key = masked_secret_prompt(
            'Ключ API (необязательно; пусто — использовать OPENAI_API_KEY): '
        ).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    _save_aux_choice(
        task,
        provider="custom",
        model=model,
        base_url=url,
        api_key=api_key,
    )
    short_url = url.replace("https://", "").replace("http://", "").rstrip("/")
    print(f'{display_name}: свой сервер ({short_url})' + (f" · {model}" if model else ""))


def _prompt_provider_choice(choices, *, default=0, title='Выберите провайдера:'):
    """Show provider selection menu with curses arrow-key navigation.

    Falls back to a numbered list when curses is unavailable (e.g. piped
    stdin, non-TTY environments).  Returns the selected index, or None
    if the user cancels.
    """
    try:
        from korra_cli.setup import _curses_prompt_choice

        idx = _curses_prompt_choice(title, choices, default)
        if idx >= 0:
            print()
            return idx
    except Exception:
        pass

    # Fallback: numbered list
    print(title)
    for i, c in enumerate(choices, 1):
        marker = "→" if i - 1 == default else " "
        print(f"  {marker} {i}. {c}")
    print()
    while True:
        try:
            val = input(f'Выберите [1–{len(choices)}] ({default + 1}): ').strip()
            if not val:
                return default
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return idx
            print(f'Введите число от 1 до {len(choices)}')
        except ValueError:
            print('Введите число')
        except (KeyboardInterrupt, EOFError):
            print()
            return None










_DEFAULT_QWEN_PORTAL_MODELS = [
    "qwen3-coder-plus",
    "qwen3-coder",
]


def _prompt_custom_api_mode_selection(base_url: str, current_api_mode: str = "") -> Optional[str]:
    """Prompt for a custom provider API mode.

    Returns an explicit mode string, or None to keep auto-detect behavior.
    """
    from korra_cli.runtime_provider import _detect_api_mode_for_url

    detected_mode = _detect_api_mode_for_url(base_url)
    normalized_current = str(current_api_mode or "").strip().lower()
    default_mode = normalized_current or detected_mode or ""

    mode_options = [
        (
            "",
            'Определить автоматически',
            'Определить по адресу; подходит для обычных серверов с API OpenAI.',
        ),
        (
            "chat_completions",
            "Chat Completions",
            'Использовать /chat/completions для серверов с API OpenAI.',
        ),
        (
            "codex_responses",
            "Responses / Codex",
            'Использовать /responses для серверов с инструментами Codex.',
        ),
        (
            "anthropic_messages",
            "Anthropic Messages",
            'Использовать /v1/messages для серверов с API Anthropic.',
        ),
    ]

    print()
    print('Выберите режим совместимости API:')
    for idx, (value, label, description) in enumerate(mode_options, 1):
        markers = []
        if value == detected_mode:
            markers.append('определён автоматически')
        if value == default_mode:
            markers.append('текущий')
        suffix = f" [{' / '.join(markers)}]" if markers else ""
        print(f"  {idx}. {label}{suffix}")
        print(f"     {description}")

    try:
        raw = input(
            'Выберите [1–4]; Enter — сохранить текущий или определённый режим: '
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print('Отменено.')
        raise

    if not raw:
        return default_mode or None

    if raw in {"1", "auto", "detect", "auto-detect"}:
        return None
    if raw in {"2", "chat", "chat_completions", "completions"}:
        return "chat_completions"
    if raw in {"3", "responses", "codex", "codex_responses"}:
        return "codex_responses"
    if raw in {"4", "anthropic", "anthropic_messages", "messages"}:
        return "anthropic_messages"

    print(f'Неверный режим API: {raw}. Используется автоопределение.')
    return None


def _auto_provider_name(base_url: str) -> str:
    """Generate a display name from a custom endpoint URL.

    Returns a human-friendly label like "Local (localhost:11434)" or
    "RunPod (xyz.runpod.io)".  Used as the default when prompting the
    user for a display name during custom endpoint setup.
    """
    import re

    clean = base_url.replace("https://", "").replace("http://", "").rstrip("/")
    clean = re.sub(r"/v1/?$", "", clean)
    name = clean.split("/")[0]
    if "localhost" in name or "127.0.0.1" in name:
        name = f'Локальный сервер ({name})'
    elif "runpod" in name.lower():
        name = f"RunPod ({name})"
    else:
        name = name.capitalize()
    return name


def _custom_provider_api_key_config_value(provider_info, resolved_api_key=""):
    """Return the value that should be persisted for a custom provider key."""
    api_key_ref = str(provider_info.get("api_key_ref", "") or "").strip()
    if api_key_ref:
        return api_key_ref

    key_env = str(provider_info.get("key_env", "") or "").strip()
    if key_env and not str(provider_info.get("api_key", "") or "").strip():
        return f"${{{key_env}}}"

    return str(resolved_api_key or "").strip()


def _custom_provider_base_url_config_value(provider_info, resolved_base_url=""):
    """Return the value that should be persisted for a custom provider URL."""
    base_url_ref = str(provider_info.get("base_url_ref", "") or "").strip()
    if base_url_ref:
        return base_url_ref
    return str(resolved_base_url or "").strip()


def _save_custom_provider(
    base_url, api_key="", model="", context_length=None, name=None, api_mode=None,
    key_env=""
):
    """Save a custom endpoint to custom_providers in config.yaml.

    Deduplicates by base_url — if the URL already exists, updates the
    model name, context_length, and api_mode but doesn't add a duplicate entry.
    Uses *name* when provided, otherwise auto-generates from the URL.

    When *key_env* is set the caller has already written the key to ``.env``,
    so the entry references it instead of inlining the secret (#69449).
    """
    from korra_cli.config import load_config, save_config

    cfg = load_config()
    providers = cfg.get("custom_providers") or []
    if not isinstance(providers, list):
        providers = []

    # Check if this URL is already saved — update model/context_length if so
    for entry in providers:
        if isinstance(entry, dict) and entry.get("base_url", "").rstrip(
            "/"
        ) == base_url.rstrip("/"):
            changed = False
            if model and entry.get("model") != model:
                entry["model"] = model
                changed = True
            if model and context_length:
                models_cfg = entry.get("models", {})
                if not isinstance(models_cfg, dict):
                    models_cfg = {}
                models_cfg[model] = {"context_length": context_length}
                entry["models"] = models_cfg
                changed = True
            if api_mode:
                if entry.get("api_mode") != api_mode:
                    entry["api_mode"] = api_mode
                    changed = True
            elif "api_mode" in entry:
                entry.pop("api_mode", None)
                changed = True
            if key_env and (entry.get("key_env") != key_env or entry.get("api_key")):
                entry["key_env"] = key_env
                entry.pop("api_key", None)
                changed = True
            if changed:
                cfg["custom_providers"] = providers
                save_config(cfg)
            return  # already saved, updated if needed

    # Use provided name or auto-generate from URL
    if not name:
        name = _auto_provider_name(base_url)

    entry = {"name": name, "base_url": base_url}
    if key_env:
        entry["key_env"] = key_env
    elif api_key:
        entry["api_key"] = api_key
    if model:
        entry["model"] = model
    if api_mode:
        entry["api_mode"] = api_mode
    if model and context_length:
        entry["models"] = {model: {"context_length": context_length}}

    providers.append(entry)
    cfg["custom_providers"] = providers
    save_config(cfg)
    print(f'  💾 Сохранено в своих провайдерах как «{name}»; можно изменить в config.yaml')




def _remove_custom_provider(config):
    """Let the user remove a saved custom provider from config.yaml."""
    from korra_cli.config import load_config, save_config

    cfg = load_config()
    providers = cfg.get("custom_providers") or []
    if not isinstance(providers, list) or not providers:
        print('Свои провайдеры не настроены.')
        return

    print('Удаление своего провайдера:')

    choices = []
    for entry in providers:
        if isinstance(entry, dict):
            name = entry.get("name", "unnamed")
            url = entry.get("base_url", "")
            short_url = url.replace("https://", "").replace("http://", "").rstrip("/")
            choices.append(f"{name} ({short_url})")
        else:
            choices.append(str(entry))
    choices.append("Cancel")

    try:
        from korra_cli.curses_ui import curses_radiolist

        idx = curses_radiolist(
            'Выберите провайдера для удаления:',
            list(choices),
            selected=0,
            cancel_returns=-1,
        )
        print()
        if idx < 0:
            idx = None
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        print()
        try:
            val = input(f'Выберите [1–{len(choices)}]: ').strip()
            idx = int(val) - 1 if val else None
        except (ValueError, KeyboardInterrupt, EOFError):
            idx = None

    if idx is None or idx >= len(providers):
        print('Без изменений.')
        return

    removed = providers.pop(idx)
    cfg["custom_providers"] = providers
    save_config(cfg)
    removed_name = (
        removed.get("name", "unnamed") if isinstance(removed, dict) else str(removed)
    )
    print(f'✅ Провайдер «{removed_name}» удалён из ваших настроек.')




# Lazy-export the model catalog at module level. Tests and a handful of
# downstream call sites read `korra_cli.main._PROVIDER_MODELS` directly,
# so the symbol needs to be reachable as a module attribute. But importing
# the catalog eagerly costs ~55ms on every `hermes` invocation — including
# fast paths like `hermes --version` and slash-command dispatch that never
# touch the catalog. PEP 562 module-level __getattr__ defers the import
# until first attribute access, so the cost is only paid by callers that
# actually look up the catalog. Termux already defers via the same
# mechanism (its model-selection handlers do their own function-local
# imports), so the explicit termux branch from before is no longer needed.
_LAZY_MODEL_EXPORTS = ("_PROVIDER_MODELS",)


# The main.py decomposition moved the sessions/update/dashboard command
# implementations into their own modules, but main.py still re-exports their
# surface so argparse wiring and test monkeypatches on korra_cli.main.<name>
# keep resolving unchanged. Importing those modules eagerly costs ~50ms on
# every `hermes` invocation, including fast paths like `hermes --version`
# that never run a subcommand. Resolve the re-exports through the module
# __getattr__ below instead, so each module is only imported when one of its
# names is actually touched. Monkeypatching keeps working: patch.object sets
# a real module attribute, which shadows __getattr__.
_LAZY_COMMAND_EXPORTS = {
    "korra_cli.sessions_cmd": (
        "cmd_sessions",
    ),
    "korra_cli.dashboard_procs": (
        "_detect_concurrent_hermes_instances",
        "_filter_dashboard_respawn_candidates",
        "_kill_stale_dashboard_processes",
        "_scan_dashboard_processes",
    ),
    "korra_cli.update_cmd": (
        "_abort_dependency_sync_if_self_locked",
        "_add_upstream_remote",
        "_apply_pending_fleet_restart_catchup",
        "_atomic_replace_dir",
        "_capture_active_lazy_features",
        "_capture_active_tool_dependencies",
        "_capture_head_sha",
        "_classify_concurrent_instance",
        "_clear_fleet_restart_pending_marker",
        "_assess_parked_branch_switch",
        "_branch_head_label",
        "_branch_head_suffix",
        "_cmd_update_check",
        "_cmd_update_impl",
        "_cold_start_windows_gateway_after_update",
        "_count_commits_between",
        "_dependency_sync_would_rewrite",
        "_detect_self_loaded_native_modules",
        "_detect_venv_python_processes",
        "_desktop_owns_gateway_lifecycle",
        "_defer_update_for_self_lock",
        "_discard_lockfile_churn",
        "_discard_stashed_changes",
        "_park_stashed_changes",
        "_ensure_acp_launcher",
        "_ensure_fhs_path_guard",
        "_ensure_uv_for_termux",
        "_finish_dashboard_update_cleanup",
        "_fleet_probe_expected_runtimes",
        "_fleet_restart_pending_marker_path",
        "_filter_non_gateway_concurrent_instances",
        "_for_each_systemd_gateway_unit",
        "_format_concurrent_instances_message",
        "_format_time_ago",
        "_gateway_service_matches_profile",
        "_gateway_recovery_partition",
        "_gateway_restart_recovery_profiles",
        "_handoff_reapable_backend_pids",
        "_ledger_reapable_backend_pids",
        "_purge_stale_hermes_modules",
        "_format_venv_python_holders_message",
        "_gateway_prompt",
        "_get_origin_url",
        "_has_upstream_remote",
        "_install_psutil_android_compat",
        "_invalidate_update_cache",
        "_is_android_python",
        "_is_fork",
        "_leftover_pausable_gateway_pids",
        "_ledger_manual_serve_holders",
        "_relaunch_stopped_serves",
        "_serve_relaunch_commands",
        "_log_only_write",
        "_mark_skip_upstream_prompt",
        "_npm_bin_exists",
        "_npm_lockfile_changed",
        "_npm_manifest_paths",
        "_npm_manifests_digest",
        "_orphaned_desktop_backend_pids",
        "_pending_fleet_restart_needed",
        "_pause_windows_gateways_for_update",
        "_print_curator_first_run_notice",
        "_print_curator_recent_run_notice",
        "_print_fts_optimize_available_notice",
        "_print_parked_branch_kept_notice",
        "_print_parked_branch_skip_warning",
        "_print_stash_cleanup_guidance",
        "_print_update_completion",
        "_record_npm_lockfile_hash",
        "_refresh_active_lazy_features",
        "_refresh_active_memory_provider_dependencies",
        "_refresh_bootstrap_cache_scripts",
        "_refresh_windows_gateway_launchers",
        "_recover_gateway_restart_after_abort",
        "_reload_updated_runtime_modules",
        "_resolve_pre_update_backup_mode",
        "_resolve_stash_selector",
        "_restart_phase_failure_is_incomplete",
        "_restore_active_tool_dependencies",
        "_restore_stashed_changes",
        "_resume_windows_gateways_after_update",
        "_run_pending_fleet_restart",
        "_run_logged_subprocess",
        "_run_pre_update_backup",
        "_service_unit_supports_graceful_sigusr1_restart",
        "_should_skip_upstream_prompt",
        "_stash_apply_failed_only_on_existing_untracked",
        "_stash_local_changes_if_needed",
        "_stop_process_trees",
        "_surviving_gateway_pids_after_failed_restart",
        "_sync_fork_with_upstream",
        "_sync_with_upstream_if_needed",
        "_update_node_dependencies",
        "_update_via_zip",
        "_upgrade_pip_before_lazy_refresh",
        "_validate_critical_files_syntax",
        "_validate_critical_modules_import",
        "_venv_core_imports_healthy",
        "_venv_launcher_ancestors",
        "_wait_for_windows_update_gateway_exit",
        "_warn_gateway_restart_phase_aborted",
        "_warn_incomplete_gateway_fleet_restart",
        "_warn_pending_fleet_restart_on_startup",
        "_web_build_toolchain_ready",
        "_web_toolchain_roots",
        "_write_fleet_restart_pending_marker",
        "_write_lazy_refresh_incomplete_marker",
        "_write_marker_file",
        "_write_update_incomplete_marker",
        "_write_update_planned_stop_marker",
        "_UPDATE_RUNTIME_RELOAD_MODULES",
        "_UPDATE_CRITICAL_FILES",
        "_UPDATE_CRITICAL_MODULES",
        "OFFICIAL_REPO_URLS",
        "OFFICIAL_REPO_URL",
        "SKIP_UPSTREAM_PROMPT_FILE",
        "_PRE_UPDATE_SNAPSHOT_KEEP",
        "_PRE_UPDATE_SNAPSHOT_MAX_FILE_SIZE",
    ),
}

_LAZY_COMMAND_ATTR_TO_MODULE = {
    attr: module for module, attrs in _LAZY_COMMAND_EXPORTS.items() for attr in attrs
}

# Back-compat alias: some tests and external callers import the old warn-only
# name. The kill behaviour replaced it; resolve to the new name lazily.
_LAZY_COMMAND_ALIASES = {
    "_warn_stale_dashboard_processes": (
        "korra_cli.dashboard_procs",
        "_kill_stale_dashboard_processes",
    ),
}


def _self():
    """This module, for attribute access at call time.

    Bare-name global lookups inside this module do not go through the PEP 562
    __getattr__ below, so internal callers of the lazily re-exported names use
    _self().<name> instead. That resolves the lazy re-export on first use and
    keeps monkeypatches on korra_cli.main.<name> working, exactly like a
    globals lookup did. ``sys`` is imported locally because some tests patch
    this module's ``sys`` attribute.
    """
    import sys as _sys

    return _sys.modules[__name__]


def __getattr__(name):
    """Defer the model-catalog and command-module imports until first read."""
    if name in _LAZY_MODEL_EXPORTS:
        from korra_cli.models import _PROVIDER_MODELS
        # Cache on the module so subsequent accesses skip the import machinery.
        globals()[name] = _PROVIDER_MODELS
        return _PROVIDER_MODELS
    module = _LAZY_COMMAND_ATTR_TO_MODULE.get(name)
    if module is not None:
        import importlib

        value = getattr(importlib.import_module(module), name)
        globals()[name] = value
        return value
    alias = _LAZY_COMMAND_ALIASES.get(name)
    if alias is not None:
        import importlib

        module_name, attr = alias
        value = getattr(importlib.import_module(module_name), attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _current_reasoning_effort(config) -> str:
    agent_cfg = config.get("agent")
    if isinstance(agent_cfg, dict):
        return str(agent_cfg.get("reasoning_effort") or "").strip().lower()
    return ""


def _set_reasoning_effort(config, effort: str) -> None:
    agent_cfg = config.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config["agent"] = agent_cfg
    agent_cfg["reasoning_effort"] = effort


def _prompt_reasoning_effort_selection(efforts, current_effort=""):
    """Prompt for a reasoning effort. Returns effort, 'none', or None to keep current."""
    deduped = list(
        dict.fromkeys(
            str(effort).strip().lower() for effort in efforts if str(effort).strip()
        )
    )
    canonical_order = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
    ordered = [effort for effort in canonical_order if effort in deduped]
    ordered.extend(effort for effort in deduped if effort not in canonical_order)
    if not ordered:
        return None

    def _label(effort):
        if effort == current_effort:
            return f'{effort}  ← используется сейчас'
        return effort

    disable_label = 'Отключить рассуждения'
    skip_label = 'Пропустить и сохранить текущее'

    if current_effort == "none":
        default_idx = len(ordered)
    elif current_effort in ordered:
        default_idx = ordered.index(current_effort)
    elif "medium" in ordered:
        default_idx = ordered.index("medium")
    else:
        default_idx = 0

    try:
        from korra_cli.curses_ui import curses_radiolist

        choices = [_label(effort) for effort in ordered]
        choices.append(disable_label)
        choices.append(skip_label)
        idx = curses_radiolist(
            'Выберите глубину рассуждений:',
            choices,
            selected=default_idx,
            cancel_returns=-1,
        )
        if idx < 0:
            return None
        print()
        if idx < len(ordered):
            return ordered[idx]
        if idx == len(ordered):
            return "none"
        return None
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        pass

    print('Выберите глубину рассуждений:')
    for i, effort in enumerate(ordered, 1):
        print(f"  {i}. {_label(effort)}")
    n = len(ordered)
    print(f"  {n + 1}. {disable_label}")
    print(f"  {n + 2}. {skip_label}")
    print()

    while True:
        try:
            choice = input(f'Выберите [1–{n + 2}]; по умолчанию сохранить текущее: ').strip()
            if not choice:
                return None
            idx = int(choice)
            if 1 <= idx <= n:
                return ordered[idx - 1]
            if idx == n + 1:
                return "none"
            if idx == n + 2:
                return None
            print(f'Введите число от 1 до {n + 2}')
        except ValueError:
            print('Введите число')
        except (KeyboardInterrupt, EOFError):
            return None






def _prompt_api_key(
    pconfig,
    existing_key: str,
    provider_id: str = "",
    existing_source: str = "",
) -> tuple:
    """Shared API-key entry point for ``hermes setup`` / ``hermes model``.

    Handles both first-time entry and the already-configured case.  When a key
    is already present, offers [K]eep / [R]eplace / [C]lear so the user can
    recover from a malformed paste without editing ``~/.hermes/.env`` by hand.

    Returns ``(resolved_key, abort)``.  ``abort=True`` means the caller should
    ``return`` immediately — the user cancelled entry, declined to replace, or
    cleared the key and is now unconfigured.
    """
    from korra_cli.auth import LMSTUDIO_NOAUTH_PLACEHOLDER
    from korra_cli.config import save_env_value
    from korra_cli.secret_prompt import masked_secret_prompt

    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""

    def _prompt_new_key(*, allow_lmstudio_default: bool) -> str:
        if provider_id == "lmstudio" and allow_lmstudio_default:
            prompt = f'{key_env} (Enter — без авторизации, значение {LMSTUDIO_NOAUTH_PLACEHOLDER!r}): '
        else:
            prompt = f'{key_env} (Enter — отмена): '
        try:
            entered = masked_secret_prompt(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return ""
        if not entered and provider_id == "lmstudio" and allow_lmstudio_default:
            return LMSTUDIO_NOAUTH_PLACEHOLDER
        return entered

    # First-time entry ────────────────────────────────────────────────────
    if not existing_key:
        print(f'Ключ API {pconfig.name} не настроен.')
        if not key_env:
            return "", True
        new_key = _prompt_new_key(allow_lmstudio_default=True)
        if not new_key:
            print('Отменено.')
            return "", True
        save_env_value(key_env, new_key)
        print('Ключ API сохранён.')
        print()
        return new_key, False

    # Already configured — offer K / R / C ────────────────────────────────
    from korra_cli.env_loader import format_secret_source_suffix

    source_suffix = format_secret_source_suffix(key_env) if key_env else ""
    print(f'  Ключ API {pconfig.name}: {existing_key[:8]}… ✓{source_suffix}')
    if not key_env:
        # Nothing we can rewrite; just acknowledge and move on.
        print()
        return existing_key, False
    pool_backed = existing_source.startswith("credential_pool:")
    menu = (
        "  [K]eep / [R]eplace (default K): "
        if pool_backed
        else "  [K]eep / [R]eplace / [C]lear (default K): "
    )
    try:
        choice = input(menu).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        choice = "k"

    if choice.startswith("r"):
        new_key = _prompt_new_key(allow_lmstudio_default=False)
        if not new_key:
            print('  Без изменений.')
            print()
            return existing_key, False
        save_env_value(key_env, new_key)
        print('  Ключ API обновлён.')
        print()
        return new_key, False

    if choice.startswith("c") and not pool_backed:
        save_env_value(key_env, "")
        print(
            f'  Ключ API удалён. Повторно настройте {pconfig.name} командой korra setup.'
        )
        return "", True

    # Keep (default, or any other input)
    print()
    return existing_key, False




def _infer_stepfun_region(base_url: str) -> str:
    """Infer the current StepFun region from the configured endpoint."""
    normalized = (base_url or "").strip().lower()
    if "api.stepfun.com" in normalized:
        return "china"
    return "international"


def _stepfun_base_url_for_region(region: str) -> str:
    from korra_cli.auth import (
        STEPFUN_STEP_PLAN_CN_BASE_URL,
        STEPFUN_STEP_PLAN_INTL_BASE_URL,
    )

    return (
        STEPFUN_STEP_PLAN_CN_BASE_URL
        if region == "china"
        else STEPFUN_STEP_PLAN_INTL_BASE_URL
    )










def _run_anthropic_oauth_flow(save_env_value):
    """Run the Claude OAuth setup-token flow. Returns True if credentials were saved."""
    from agent.anthropic_adapter import (
        run_oauth_setup_token,
        read_claude_code_credentials,
        is_claude_code_token_valid,
    )
    from korra_cli.config import (
        save_anthropic_oauth_token,
        use_anthropic_claude_code_credentials,
    )

    def _activate_claude_code_credentials_if_available() -> bool:
        try:
            creds = read_claude_code_credentials()
        except Exception:
            creds = None
        if creds and (
            is_claude_code_token_valid(creds) or bool(creds.get("refreshToken"))
        ):
            use_anthropic_claude_code_credentials(save_fn=save_env_value)
            print('  ✓ Данные входа Claude Code подключены.')
            from korra_constants import display_hermes_home as _dhh_fn

            print(
                f'    Корра будет использовать хранилище Claude напрямую, без копирования setup-token в {_dhh_fn()}/.env.'
            )
            return True
        return False

    try:
        print()
        print('  Запускаем claude setup-token. Следуйте подсказкам ниже.')
        print('  Откроется браузер для разрешения доступа.')
        print()
        token = run_oauth_setup_token()
        if token:
            if _activate_claude_code_credentials_if_available():
                return True
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print('  ✓ Данные входа OAuth сохранены.')
            return True

        # Subprocess completed but no token auto-detected — ask user to paste
        print()
        print('  Если токен setup-token показан выше, вставьте его сюда:')
        print()
        from korra_cli.secret_prompt import masked_secret_prompt

        try:
            manual_token = masked_secret_prompt(
                '  Вставьте setup-token; Enter — отмена: '
            ).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if manual_token:
            save_anthropic_oauth_token(manual_token, save_fn=save_env_value)
            print('  ✓ Setup-token сохранён.')
            return True

        print('  ⚠ Не удалось найти сохранённые данные входа.')
        return False

    except FileNotFoundError:
        # Claude CLI not installed — guide user through manual setup
        print()
        print('  Для входа через OAuth нужна команда claude.')
        print()
        print('  Установка и вход:')
        print()
        print('    1. Установите Claude Code: npm install -g @anthropic-ai/claude-code')
        print('    2. Выполните: claude setup-token')
        print('    3. Подтвердите доступ в браузере')
        print('    4. Повторите: korra model')
        print()
        print('  Или вставьте готовый setup-token сейчас: sk-ant-oat-...')
        print()
        from korra_cli.secret_prompt import masked_secret_prompt

        try:
            token = masked_secret_prompt('  Setup-token; Enter — отмена: ').strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if token:
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print('  ✓ Setup-token сохранён.')
            return True
        print('  Отменено. Установите Claude Code и повторите попытку.')
        return False




def cmd_login(args):
    """Authenticate Hermes CLI with a provider."""
    from korra_cli.auth import login_command

    login_command(args)


def cmd_logout(args):
    """Clear provider authentication."""
    from korra_cli.auth import logout_command

    logout_command(args)


def cmd_auth(args):
    """Manage pooled credentials."""
    from korra_cli.auth_commands import auth_command

    auth_command(args)


def cmd_status(args):
    """Show status of all components."""
    from korra_cli.status import show_status

    show_status(args)


def cmd_cron(args):
    """Cron job management."""
    from korra_cli.cron import cron_command

    cron_command(args)


def cmd_sync(args):
    """Skill Sync — personal sync across devices, plus sharing with your org."""
    import json as _json

    sub = getattr(args, "sync_command", None)

    if sub in {None, ""}:
        print(
            'Использование: korra sync <status|pull|push|now|enable|disable|device|propose>. Между вашими устройствами: status — состояние; pull — получить навыки; push — отправить выбранные навыки; now — получить и отправить; enable <skill> — включить синхронизацию; disable <skill> — исключить; device [--name N] — имя устройства. Для команды: propose <skill> — предложить навык организации.',
            file=sys.stderr,
        )
        return 1

    if sub == "device":
        from tools import skills_sync_client as ssc

        name = getattr(args, "device_name", None)
        if name is not None:
            try:
                stored = ssc.set_device_name(name)
            except ValueError as e:
                print(f'Ошибка: {e}', file=sys.stderr)
                return 1
            print(f'Имя устройства изменено на «{stored}».')
            print(
                'Новые коммиты этого устройства будут использовать это имя; в старых оно сохранится.',
                file=sys.stderr,
            )
            return 0
        # No --name: print the current (creating a default on first use).
        print(ssc.stable_device_id())
        return 0

    if sub == "propose":
        from tools import skills_sync_client as ssc

        name = args.name
        try:
            result = ssc.propose_skill(name, message=args.message)
        except ssc.SyncInertError as e:
            print(f'Не удалось поделиться навыком: {e}', file=sys.stderr)
            return 1
        except ssc.SyncError as e:
            print(f'Не удалось поделиться «{name}»: {e}', file=sys.stderr)
            return 1
        if result.get("proposal_pending"):
            print(
                f"Навык «{name}» предложен организации. Нужна проверка администратора (предложение №{result.get('proposal_id')}); до одобрения команда его не получит."
            )
        else:
            print(f'Навык «{name}» добавлен в общие навыки организации.')
        return 0

    if sub in {"enable", "disable"}:
        from tools.skill_usage import set_sync, is_curation_eligible

        skill = args.skill
        if not is_curation_eligible(skill):
            print(
                f'Навык «{skill}» не подходит для синхронизации: встроенный, из каталога, внешний или не найден. Синхронизируются только навыки, созданные агентом или вами в папке навыков профиля.',
                file=sys.stderr,
            )
            return 1
        set_sync(skill, sub == "enable")
        print(f"Синхронизация {('включена' if sub == 'enable' else 'отключена')} для «{skill}».")
        return 0

    from tools import skills_sync_client as ssc

    if sub == "status":
        status = ssc.sync_status()
        print(_json.dumps(status, indent=2, ensure_ascii=False))
        if status.get("org_available"):
            n = len(status.get("org_skills") or [])
            modified = status.get("org_skills_modified") or []
            print(
                f"Общие навыки: {n}; ваша роль в организации — {status.get('org_role')}. Загружаются рядом с личными, с указанием источника, и доступны для редактирования.",
                file=sys.stderr,
            )
            if modified:
                print(
                    f"  Общих навыков с неопубликованными локальными правками: {len(modified)}: {', '.join(modified)}. Поделиться: korra sync propose <skill>. Обновления организации не заменят ваши правки.",
                    file=sys.stderr,
                )
        elif status.get("logged_in"):
            print(
                'Общие навыки недоступны: учётная запись не входит в организацию.',
                file=sys.stderr,
            )
        if not status.get("logged_in"):
            print('Вход Nous не выполнен; синхронизация не работает.', file=sys.stderr)
        elif not status.get("nous_admin"):
            print(
                'Синхронизация пока не включена для вашей учётной записи.',
                file=sys.stderr,
            )
        elif not status.get("feature_enabled"):
            print(
                'Синхронизация отключена для этой установки. Включите sync.enabled: true в config.yaml.',
                file=sys.stderr,
            )
        elif not status.get("base_url"):
            print(
                'Адрес синхронизации не настроен. Задайте sync.base_url в config.yaml.',
                file=sys.stderr,
            )
        return 0

    # pull / push / now — enforce the gate up front with a clear message.
    try:
        identity = ssc.resolve_identity()
    except ssc.SyncInertError as e:
        print(f'Синхронизация не работает: {e}', file=sys.stderr)
        return 1
    if not identity.get("nous_admin"):
        print(
            'Синхронизация пока недоступна для вашей учётной записи.',
            file=sys.stderr,
        )
        return 1
    if not ssc.resolve_sync_base_url():
        print(
            'Синхронизация не работает: адрес sync.base_url в config.yaml не настроен.',
            file=sys.stderr,
        )
        return 1

    try:
        if sub == "pull":
            result = ssc.pull_skills(identity=identity)
            # Refresh the org mirror too when this account belongs to an
            # organisation (no-op otherwise), so one pull covers both.
            org_result = ssc.maybe_pull_org_skills()
            if org_result:
                n = len(org_result.get("updated") or [])
                print(
                    f'Организация: обновлено общих навыков — {n}.',
                    file=sys.stderr,
                )
                clashes = org_result.get("conflicted") or []
                if clashes:
                    print(
                        f"Организация: у {len(clashes)} навыков есть одновременно ваши правки и обновления команды, поэтому они сохранены без изменений: {', '.join(clashes)}. Проверьте локальную версию и предложите её команде либо удалите локальную копию и получите версию организации заново.",
                        file=sys.stderr,
                    )
        elif sub == "push":
            result = ssc.push_skills(identity=identity, message='korra sync push')
        elif sub == "now":
            pull_res = ssc.pull_skills(identity=identity)
            push_res = ssc.push_skills(identity=identity, message='korra sync now')
            result = {"pull": pull_res, "push": push_res}
        else:
            print(f'Неизвестная подкоманда sync: {sub}', file=sys.stderr)
            return 1
    except ssc.SyncError as e:
        print(f'Ошибка синхронизации: {e}', file=sys.stderr)
        return 1

    print(_json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_webhook(args):
    """Webhook subscription management."""
    from korra_cli.webhook import webhook_command

    webhook_command(args)


def cmd_slack(args):
    """Slack integration helpers.

    Dispatches ``hermes slack <subcommand>``. Currently supports:
      manifest — print or write a Slack app manifest with every gateway
                 command registered as a first-class slash.
    """
    sub = getattr(args, "slack_command", None)
    if sub in {None, ""}:
        # No subcommand — print usage hint.
        print(
            'Использование: korra slack <subcommand>. manifest — создать манифест Slack с командами шлюза. Подробнее: korra slack manifest -h.',
            file=sys.stderr,
        )
        return 1

    if sub == "manifest":
        from korra_cli.slack_cli import slack_manifest_command

        status = slack_manifest_command(args)
        if status:
            raise SystemExit(status)
        return status

    print(f'Неизвестная подкоманда slack: {sub}', file=sys.stderr)
    return 1


def cmd_kanban(args):
    """Multi-profile collaboration board."""
    from korra_cli.kanban import kanban_command

    return kanban_command(args)


def cmd_project(args):
    """Manage projects (named, multi-folder workspaces)."""
    from korra_cli.projects_cmd import projects_command

    return projects_command(args)


def cmd_hooks(args):
    """Shell-hook inspection and management."""
    from korra_cli.hooks import hooks_command

    hooks_command(args)


def cmd_doctor(args):
    """Check configuration and dependencies."""
    from korra_cli.doctor import run_doctor

    run_doctor(args)


def cmd_verify(args):
    """Detect a project's run recipe and smoke-test it."""
    from korra_cli.verify_cmd import run_verify_command

    sys.exit(run_verify_command(args))


def cmd_security(args):
    """Dispatch `hermes security <subcmd>`."""
    sub = getattr(args, "security_command", None)
    if sub in ("audit", None):
        from korra_cli.security_audit import cmd_security_audit

        # Default subcommand is `audit` when no subcmd is given.
        code = cmd_security_audit(args)
        sys.exit(int(code or 0))
    print(f'Неизвестная подкоманда security: {sub}', file=sys.stderr)
    sys.exit(2)


def cmd_approvals(args):
    """Dispatch `hermes approvals <subcmd>`."""
    from korra_cli.approvals_suggest import approvals_command

    status = approvals_command(args)
    if status:
        sys.exit(status)
    return status


def cmd_dump(args):
    """Dump setup summary for support/debugging."""
    from korra_cli.dump import run_dump

    run_dump(args)


def cmd_debug(args):
    """Debug tools (share report, etc.)."""
    from korra_cli.debug import run_debug

    run_debug(args)


def cmd_config(args):
    """Configuration management."""
    from korra_cli.config import config_command

    try:
        config_command(args)
    except RuntimeError as exc:
        # Safety net for the fail-closed config write guard (unparseable /
        # non-mapping / unreadable config.yaml raises RuntimeError from
        # require_readable_config_before_write). set/unset already surface
        # this per-branch; this covers migrate and future write subcommands
        # so no path ends in a raw traceback.
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_skin(args):
    """Skin management (list / use / set)."""
    from korra_cli.skin_cmd import skin_command

    skin_command(args)


def cmd_backup(args):
    """Back up Hermes home directory to a zip file."""
    if getattr(args, "quick", False):
        from korra_cli.backup import run_quick_backup

        run_quick_backup(args)
    else:
        from korra_cli.backup import run_backup

        run_backup(args)


def cmd_import(args):
    """Restore a Hermes backup from a zip file."""
    from korra_cli.backup import run_import

    run_import(args)


def _print_version_info(*, check_updates: bool = True) -> None:
    # Single source of truth for version output — shared with the
    # `hermes --version` pre-import fast path (the `version` subcommand
    # was consolidated into `--version`).
    _startup_fast.print_fast_version_info(check_updates=check_updates)


def cmd_version(args):
    """Show version (--version/-V flag)."""
    _print_version_info(check_updates=True)


def cmd_uninstall(args):
    """Uninstall Hermes Agent (or just the Chat GUI with --gui)."""
    # Machine-readable install snapshot for the desktop app's uninstall UI.
    # Must run before any TTY gate — it's called from a non-interactive child.
    if getattr(args, "gui_summary", False):
        from korra_cli.gui_uninstall import gui_install_summary

        print(json.dumps(gui_install_summary()))
        return

    # GUI-only uninstall. The desktop app shells out to this non-interactively
    # with --yes, so only gate on a TTY when we actually need to prompt.
    if getattr(args, "gui", False):
        if not getattr(args, "yes", False):
            _require_tty("uninstall --gui")
        from korra_cli.uninstall import run_gui_uninstall

        run_gui_uninstall(args)
        return

    # Full/keep-data uninstall. ``--yes`` runs non-interactively (the desktop
    # app's lite/full modes drive this from a detached cleanup script), so only
    # gate on a TTY when we actually need to prompt for the option + confirm.
    if not getattr(args, "yes", False):
        _require_tty("uninstall")
    from korra_cli.uninstall import run_uninstall

    run_uninstall(args)


def _clear_bytecode_cache(root: Path) -> int:
    """Remove all __pycache__ directories under *root*.

    Stale .pyc files can cause ImportError after code updates when Python
    loads a cached bytecode file that references names that no longer exist
    (or don't yet exist) in the updated source.  Clearing them forces Python
    to recompile from the .py source on next import.

    Returns the number of directories removed.
    """
    removed = 0
    for dirpath, dirnames, _ in os.walk(root):
        # Skip venv / node_modules / .git entirely
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {"venv", ".venv", "node_modules", ".git", ".worktrees"}
        ]
        if os.path.basename(dirpath) == "__pycache__":
            try:
                shutil.rmtree(dirpath)
                removed += 1
            except OSError:
                pass
            dirnames.clear()  # nothing left to recurse into
    return removed


# Update pipeline lives in korra_cli/update_cmd.py (main.py decomposition,
# mechanical move). Its names are re-exported lazily through the module-level
# __getattr__ above (see _LAZY_COMMAND_EXPORTS) so argparse wiring and test
# monkeypatches on korra_cli.main.<name> keep resolving unchanged without
# paying the update_cmd import cost on every CLI invocation.

# Stamp file recording the checkout fingerprint the bytecode cache was last
# validated against. Lives next to the checkout (NOT in HERMES_HOME) because
# __pycache__ is per-checkout state shared by every profile.
_BYTECODE_FINGERPRINT_FILE = ".bytecode-fingerprint"


def _record_bytecode_fingerprint() -> None:
    """Persist the current checkout fingerprint after a bytecode sweep.

    Never raises. A failed write just means the next launch re-sweeps —
    safe, merely redundant.
    """
    try:
        fingerprint = _read_git_revision_fingerprint(PROJECT_ROOT)
        if not fingerprint:
            return
        stamp_path = PROJECT_ROOT / _BYTECODE_FINGERPRINT_FILE
        tmp_path = stamp_path.with_name(stamp_path.name + ".tmp")
        tmp_path.write_text(fingerprint, encoding="utf-8")
        tmp_path.replace(stamp_path)
    except OSError as exc:
        logger.debug("Could not record bytecode fingerprint: %s", exc)


def _sweep_stale_bytecode_if_checkout_changed() -> None:
    """Clear ``__pycache__`` at launch when the checkout changed underneath us.

    The stale-bytecode bug class (issues #6207, #60242; Dhruv's WhatsApp
    ``cannot import name 'parse_model_flags_detailed'`` report) has one
    shared shape: the checkout's ``.py`` files change (git pull inside
    ``hermes update``, a manual ``git pull``, a ZIP update, a file-sync
    restore) while ``__pycache__`` retains bytecode from the previous
    revision, and a later process trusts the stale ``.pyc`` instead of the
    fresh source.

    Update-time clears alone can never close this class: ``hermes update``
    always executes the PRE-pull updater code, so any hardening added to it
    only takes effect one update late, and manual ``git pull`` never runs
    the updater at all. This launch-time guard closes the loop: every
    ``hermes`` entry point compares the checkout fingerprint (cheap file
    reads, no git subprocess) against the last-validated stamp and sweeps
    the bytecode cache once when they diverge.

    Never raises — a failure here must not block launch.
    """
    try:
        fingerprint = _read_git_revision_fingerprint(PROJECT_ROOT)
        if not fingerprint:
            return  # non-git install — the ZIP update path clears explicitly
        stamp_path = PROJECT_ROOT / _BYTECODE_FINGERPRINT_FILE
        try:
            recorded = stamp_path.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded == fingerprint:
            return
        removed = _clear_bytecode_cache(PROJECT_ROOT)
        if removed:
            logger.info(
                "Checkout changed since last launch (%s -> %s): cleared %d stale __pycache__ director%s",
                recorded or "unknown",
                fingerprint,
                removed,
                "y" if removed == 1 else "ies",
            )
        _record_bytecode_fingerprint()
    except Exception as exc:
        logger.debug("Stale-bytecode launch sweep failed: %s", exc)


def _web_ui_build_needed(web_dir: Path) -> bool:
    """Return True if the web UI dist is missing or its source content changed.

    Uses a SHA-256 content hash of the web source tree (the same approach
    ``_desktop_build_needed()`` already uses for the Electron build), NOT
    mtime comparison. ``git checkout`` / ``git pull`` / ``hermes update``
    rewrite source mtimes without changing content, which made the old
    mtime check unreliable in both directions: it could skip a rebuild when
    source had genuinely changed (serving a stale dashboard) and force a
    rebuild when nothing had. A content hash is stable across mtime churn.

    The dashboard source lives under ``web/`` but Vite outputs to
    ``korra_cli/web_dist/`` (per vite.config.ts outDir), NOT ``web/dist/``,
    so the dist directory is never part of the hashed source tree.
    """
    project_root = web_dir.parent.parent if web_dir.parent.name == "apps" else web_dir.parent
    dist_dir = project_root / "korra_cli" / "web_dist"
    sentinel = dist_dir / ".vite" / "manifest.json"
    if not sentinel.exists():
        sentinel = dist_dir / "index.html"
    if not sentinel.exists():
        return True
    stamp_file = _web_ui_stamp_path()
    if not stamp_file.is_file():
        return True
    try:
        stamp_data = json.loads(stamp_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(stamp_data, dict):
        return True
    saved_hash = stamp_data.get("contentHash")
    if not saved_hash:
        return True
    return _compute_web_ui_content_hash(project_root, web_dir) != saved_hash


def _compute_web_ui_content_hash(project_root: Path, web_dir: Path) -> str:
    """Return a SHA-256 hex digest of the web UI source tree.

    Covers ``web_dir`` (the dashboard frontend source) plus the root
    ``package.json`` / ``package-lock.json`` (workspace config that
    determines dependency resolution). Mirrors
    ``_compute_desktop_content_hash()``: ignored paths (``node_modules/``,
    ``dist/``, ``*.pyc``, ...) are skipped via the repo-root ``.gitignore``
    so build output never feeds back into its own staleness check.
    """
    h = hashlib.sha256()

    def _hash_file(path: Path) -> None:
        rel = str(path.relative_to(project_root))
        h.update(rel.encode())
        h.update(b"\0")
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except OSError:
            pass
        h.update(b"\0")

    from pathspec import PathSpec

    gitignore = project_root / ".gitignore"
    lines: list[str] = []
    if gitignore.is_file():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    spec = PathSpec.from_lines("gitignore", lines)

    # Root workspace config (single package-lock.json covers all workspaces).
    for name in ("package.json", "package-lock.json"):
        p = project_root / name
        if p.is_file():
            rel = str(p.relative_to(project_root))
            if not spec.match_file(rel):
                _hash_file(p)

    # Walk the web source tree, pruning ignored directories in-place so we
    # never descend into node_modules/ or a stray dist/. Sort filenames for
    # a deterministic, order-independent digest.
    for dirpath, dirnames, filenames in os.walk(web_dir, topdown=True):
        dirnames[:] = [
            d for d in dirnames
            if not spec.match_file(str((Path(dirpath) / d).relative_to(project_root)))
        ]
        for fn in sorted(filenames):
            fp = Path(dirpath) / fn
            rel = str(fp.relative_to(project_root))
            if not spec.match_file(rel):
                _hash_file(fp)

    return h.hexdigest()


def _web_ui_stamp_path() -> Path:
    """Return the path to the web UI build stamp file under $HERMES_HOME."""
    from korra_constants import get_hermes_home
    return get_hermes_home() / "web-ui-build-stamp.json"


def _write_web_ui_build_stamp(project_root: Path, web_dir: Path) -> None:
    """Write the web UI build stamp after a successful build."""
    stamp_file = _web_ui_stamp_path()
    try:
        stamp_file.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        stamp_data = {
            "contentHash": _compute_web_ui_content_hash(project_root, web_dir),
            "builtAt": datetime.now(timezone.utc).isoformat(),
        }
        stamp_file.write_text(json.dumps(stamp_data, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        # Never let stamp-writing block or fail a build.
        logger.debug("Failed to write web UI build stamp: %s", exc)


def _run_with_idle_timeout(
    cmd: list[str],
    cwd: Path,
    *,
    idle_timeout_seconds: int = 180,
    indent: str = "    ",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess that streams output, with an idle-output timeout.

    Issue #33788: ``npm run build`` (Vite) was invoked with
    ``capture_output=True`` and no timeout. On low-memory hosts (notably
    WSL2 with the default 4 GB cap) the build can stall or sit silent for
    minutes; users see a frozen terminal, assume the update is hung, and
    reboot — leaving the editable install in a half-state with the
    ``hermes`` launcher present but ``korra_cli`` not importable.

    This helper fixes both halves: stdout is streamed (so the user sees
    progress), and if no bytes have appeared on stdout/stderr for
    ``idle_timeout_seconds``, the process is terminated and the call
    returns with a non-zero ``returncode``. The caller's existing
    stale-dist fallback (#23817) takes over from there.

    Returns a ``CompletedProcess`` with merged stdout (text), empty
    stderr, and an integer returncode. Never raises on idle timeout —
    propagation of failure is via the returncode.
    """
    merged_chunks: list[str] = []
    last_output_ts = _time.monotonic()
    lock = threading.Lock()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        # E.g. npm not on PATH between the which() check and now.
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(exc))

    def _reader() -> None:
        nonlocal last_output_ts
        assert proc.stdout is not None
        for line in proc.stdout:
            try:
                print(f"{indent}{line.rstrip()}", flush=True)
            except UnicodeEncodeError:
                # Windows cp1252 fallback — same pattern as _say().
                enc = getattr(sys.stdout, "encoding", None) or "ascii"
                safe = line.rstrip().encode(enc, errors="replace").decode(enc, errors="replace")
                print(f"{indent}{safe}", flush=True)
            with lock:
                merged_chunks.append(line)
                last_output_ts = _time.monotonic()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    idle_killed = False
    while True:
        try:
            rc = proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            with lock:
                idle = _time.monotonic() - last_output_ts
            if idle > idle_timeout_seconds:
                idle_killed = True
                proc.terminate()
                try:
                    rc = proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
                break

    # Drain reader so we don't leak the stdout file descriptor.
    reader_thread.join(timeout=2)

    combined = "".join(merged_chunks)
    if idle_killed:
        msg = (
            f'  ⚠ Сборка не выдавала сообщений {idle_timeout_seconds} с и остановлена. Возможные причины: мало памяти в WSL или контейнере, зависший Node либо проверка файлов антивирусом.'
        )
        combined += msg
        # Force a non-zero rc even if terminate() raced with a clean exit.
        if rc == 0:
            rc = 124  # GNU `timeout` convention
    return subprocess.CompletedProcess(cmd, rc, stdout=combined, stderr="")


def _nixos_build_env() -> dict[str, str] | None:
    """Return extra env vars for native module builds on NixOS.

    On NixOS, python3 is typically not on the system PATH (it lives in
    the Nix store and only enters PATH inside a nix-shell or when
    explicitly installed as a system package).  node-gyp uses Python to
    compile native addons like ``node-pty`` and its ``find-python.js``
    does a bare ``PATH`` lookup — which fails on NixOS.

    Two-tier resolution:
    1. Fast path — the hermes venv's python3 (present in managed installs)
    2. Fallback — resolves the absolute python3 path via ``nix-shell``

    Returns an env dict suitable for ``subprocess.run(env=...)`` or
    ``None`` when we are not on NixOS or python3 is already on PATH.
    """
    import re

    try:
        os_release = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        return None
    if not re.search(r"^ID=nixos$", os_release, re.M):
        return None

    # python3 already on PATH — nothing to do
    if shutil.which("python3"):
        return None

    # Tier 1: fast path — hermes venv python3, no nix-shell overhead
    for venv_name in ("venv", ".venv"):
        venv_python = PROJECT_ROOT / venv_name / "bin" / "python3"
        if venv_python.exists():
            return {**os.environ, "PYTHON": str(venv_python)}

    # Tier 2: nix-shell fallback — resolves the absolute python3 path once.
    # Slower (~2–5 s for the nix-shell eval) but always works, even without
    # a hermes venv (pip / non-managed / bare-git installs).  The resolved
    # path is a self-contained Nix store binary (all deps via RPATH) so it
    # stays valid even after the nix-shell exits.
    try:
        result = subprocess.run(
            ["nix-shell", "-p", "python3", "--run", "which python3"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=15,
        )
        if result.returncode == 0:
            python3_path = result.stdout.strip()
            if python3_path and Path(python3_path).exists():
                return {**os.environ, "PYTHON": python3_path}
    except Exception:
        pass  # nix-shell not available — caller will get None

    return None
def _run_npm_install_deterministic(
    npm: str,
    cwd: Path,
    *,
    extra_args: tuple[str, ...] = (),
    capture_output: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a deterministic npm install that does not mutate ``package-lock.json``.

    Prefers ``npm ci`` (strict, lockfile-preserving) when a lockfile is present;
    falls back to ``npm install`` only if ``npm ci`` fails (e.g. lockfile out of
    sync on a WIP checkout).  Without this, ``npm install`` on npm ≥ 10 silently
    rewrites committed lockfiles (stripping ``"peer": true`` etc.), which leaves
    the working tree dirty and causes the next ``hermes update`` to stash the
    lockfile — repeatedly.

    ``--include=dev`` is forced on every invocation: the callers are frontend
    builds (web UI / TUI / desktop workspaces), and those builds need the dev
    toolchain (``tsc``, ``vite``, ``electron-builder`` — all
    ``devDependencies``).  If the caller's environment has
    ``NODE_ENV=production`` (or npm config ``omit=dev``) — which leaks in from
    a shell profile, a container image, or the bundled TUI launcher that sets
    ``NODE_ENV=production`` on its subprocess env — npm silently omits
    devDependencies (exit 0, no error), so the build toolchain never installs
    and the subsequent build dies with ``tsc: command not found`` (exit 127).
    The flag overrides both the env var and npm config, unlike scrubbing
    ``NODE_ENV`` from the environment which only fixes the env-leak case.

    ``--no-save`` on the ``npm install`` fallback keeps it true to this
    function's contract: never mutate ``package-lock.json``.  Without it, an
    out-of-sync lockfile gets rewritten by the fallback, which drifts the
    committed lockfile and makes every future ``npm ci`` fail — a
    self-reinforcing cycle where web devDeps never install and a stale dist
    is served on every update (PR #65595).
    """
    # unicode-animations' postinstall animates to /dev/tty (bypasses
    # --silent/capture_output). It no-ops when CI is set — same as the TUI
    # install path and nix/lib.nix npm ci hooks.
    run_env = _npm_lifecycle_env(env)

    def _run(cmd: list[str]) -> subprocess.CompletedProcess:
        return _run_npm_watching_for_engine_failure(
            cmd,
            cwd=cwd,
            env=run_env,
            capture_output=capture_output,
        )

    def _attempt(npm_exe: str) -> subprocess.CompletedProcess:
        lockfile = cwd / "package-lock.json"
        if lockfile.exists():
            ci_result = _run([npm_exe, "ci", "--include=dev", *extra_args])
            if ci_result.returncode == 0:
                return ci_result
            # Fall through to `npm install` — lockfile may be out of sync on a
            # WIP fork/branch, or `npm ci` may not be available on very old npm.
        return _run([npm_exe, "install", "--no-save", "--include=dev", *extra_args])

    result = _attempt(npm)
    if result.returncode == 0:
        return result

    # An npm outside the root package.json's `engines.npm` range fails every
    # command here identically (the `npm install` fallback included), so the
    # failure is worth exactly one repair attempt. `maybe_repair_npm_engine`
    # returns the npm to retry with — the same one after an in-place upgrade
    # of a Hermes-managed install, or a freshly provisioned managed npm when
    # the failing npm belongs to the user's own toolchain.
    from korra_cli.npm_engine import maybe_repair_npm_engine

    combined = f"{result.stdout or ''}\n{result.stderr or ''}"
    repaired_npm = maybe_repair_npm_engine(npm, combined)
    if not repaired_npm:
        return result
    # The repaired npm may be a freshly provisioned managed one whose shebang
    # and lifecycle scripts resolve `node` from PATH — put the managed tree
    # first so they find the managed Node, not the mismatched system one.
    from korra_constants import with_hermes_node_path

    run_env["PATH"] = with_hermes_node_path(run_env)["PATH"]
    return _attempt(repaired_npm)


def _run_npm_watching_for_engine_failure(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    capture_output: bool,
) -> subprocess.CompletedProcess:
    """Run *cmd*, always retaining stderr so ``EBADENGINE`` stays detectable.

    ``capture_output=False`` callers stream npm's progress live and would
    otherwise hand back a ``CompletedProcess`` with ``stderr=None``, leaving the
    engine-failure recovery nothing to read. Tee stderr instead: each line is
    forwarded to this process's stderr as it arrives (so live output is
    unchanged) and accumulated for the caller.
    """
    if capture_output:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    captured: list[str] = []
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        if proc.stderr is not None:
            for line in proc.stderr:
                captured.append(line)
                sys.stderr.write(line)
            sys.stderr.flush()
        returncode = proc.wait()
    return subprocess.CompletedProcess(cmd, returncode, None, "".join(captured))


def _missing_web_build_tool(output: str) -> str | None:
    """Return the build tool a failed ``npm run build`` could not resolve.

    Each shell words this differently: ``sh: 1: tsc: not found`` (dash),
    ``vite: command not found`` (bash/zsh), and ``'tsc' is not recognized as
    an internal or external command`` (cmd.exe).
    """
    lowered = output.lower()
    for tool in ("tsc", "vite"):
        if any(
            phrase in lowered
            for phrase in (
                f"{tool}: not found",
                f"{tool}: command not found",
                f"'{tool}' is not recognized",
            )
        ):
            return tool
    return None


def _build_web_ui(web_dir: Path, *, fatal: bool = False) -> bool:
    """Build the web UI frontend if npm is available, serializing across processes.

    Concurrent dashboard boots (e.g. the desktop app's retry loop after a
    readiness timeout) used to each spawn their own ``npm install`` +
    ``vite build`` over the same tree; the parallel builds starved each
    other, none finished, the dist sentinel never advanced, and every new
    boot re-triggered the build. One process builds under an exclusive
    flock; the rest serve the existing dist (stale is acceptable) or, when
    no dist exists yet, block until the builder finishes.

    Staleness is checked once, inside :func:`_do_build_web_ui`, after the
    lock is held — so a process that queued behind the builder skips the
    rebuild, and the (os.walk-based) check runs at most once per boot.
    """
    if not (web_dir / "package.json").exists():
        return True
    try:
        import fcntl
    except ImportError:
        # Windows: no flock — fall through to the unserialized build.
        return _do_build_web_ui(web_dir, fatal=fatal)
    project_root = web_dir.parent.parent if web_dir.parent.name == "apps" else web_dir.parent
    dist_index = project_root / "korra_cli" / "web_dist" / "index.html"
    try:
        lock_file = open(project_root / ".web_ui_build.lock", "a", encoding="utf-8")
    except OSError:
        return _do_build_web_ui(web_dir, fatal=fatal)
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if dist_index.exists():
                # Another process is already building — serve the current
                # dist instead of piling a second build onto the same tree.
                return True
            # No dist at all (first-ever build): wait for the builder.
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return _do_build_web_ui(web_dir, fatal=fatal)
    finally:
        lock_file.close()


def _do_build_web_ui(web_dir: Path, *, fatal: bool = False) -> bool:
    """Build the web UI frontend if npm is available.

    Args:
        web_dir: Path to the dashboard frontend source directory.
        fatal: If True, print error guidance and return False on failure
               instead of a soft warning (used by ``hermes web``).

    Returns True if the build succeeded or was skipped (no package.json).
    """
    if not (web_dir / "package.json").exists():
        return True

    if not _web_ui_build_needed(web_dir):
        return True

    # Console-encoding-safe print: Windows consoles default to cp1252
    # (or similar) and will raise UnicodeEncodeError on arrow / check
    # glyphs unless PYTHONIOENCODING=utf-8 is set. Routing every print
    # in this function through _say() with errors="replace" keeps the
    # build path usable on a stock `py -m korra_cli.main web` invocation.
    def _say(text: str) -> None:
        try:
            print(text)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))

    from korra_constants import with_hermes_node_path

    npm = _resolve_node_runtime_npm()
    if not npm:
        if fatal:
            _say('Веб-панель не собрана, npm недоступен.')
            _say('Установите Node.js, затем выполните cd web && npm install && npm run build.')
        return not fatal
    build_env = _npm_lifecycle_env(with_hermes_node_path())
    _say('→ Собираем веб-панель…')

    def _relay(result: "subprocess.CompletedProcess") -> None:
        """Print captured npm output so users can see *why* a step failed.

        Windows users hitting `rm -rf` / `cp -r` errors (or any other
        sync-assets / Vite failure) would otherwise see only ``Web UI
        build failed`` with no hint of the underlying cause, because
        the npm calls run with ``capture_output=True``.
        """
        for blob in (result.stdout, result.stderr):
            if not blob:
                continue
            text = blob.decode("utf-8", errors="replace").rstrip() if isinstance(blob, bytes) else blob.rstrip()
            if text:
                _say(text)

    npm_cwd = _workspace_root(web_dir)
    # Scope the install to the web workspace only so that the full workspace
    # graph (including apps/desktop with its Electron + node-pty deps) is never
    # resolved here.  Without --workspace the root package.json's apps/* glob
    # would pull in desktop on every web build. See #38772.
    # When web/ has its own package-lock.json, _workspace_root() returns
    # web_dir itself and --workspace would fail.  See #42973.
    #
    # When running from the workspace root, this must name the SAME closure
    # as `hermes update`'s _update_node_dependencies() (ui-tui + web +
    # --include-workspace-root): the helper prefers `npm ci`, which deletes
    # node_modules before reifying the requested tree, so a narrower closure
    # here silently prunes everything the update step just installed (root
    # devDependencies and the ui-tui workspace) while still exiting 0 —
    # and since the manifests digest was already recorded, later no-op
    # updates skip the repair. See #43564/#64354.
    npm_workspace_args: tuple[str, ...]
    if npm_cwd == web_dir:
        npm_workspace_args = ()
    else:
        npm_workspace_args = ("--workspace", "web", "--include-workspace-root")
        # Prebuilt/partial checkouts can lack the ui-tui workspace; naming a
        # missing workspace makes npm fail hard, so only include it when
        # present (same guard as _update_node_dependencies()).
        if (npm_cwd / "ui-tui" / "package.json").exists():
            npm_workspace_args = ("--workspace", "ui-tui", *npm_workspace_args)
    if _is_termux_startup_environment():
        npm_cwd, npm_workspace_args = _termux_workspace_install_context(web_dir)

    def _install_web_deps(*, silent: bool) -> "subprocess.CompletedProcess":
        return _run_npm_install_deterministic(
            npm,
            npm_cwd,
            extra_args=(*npm_workspace_args, "--silent", "--prefer-offline") if silent else (*npm_workspace_args, "--prefer-offline"),
            env=build_env,
        )

    r1 = _install_web_deps(silent=True)
    if r1.returncode != 0:
        _say(
            f"  {('✗' if fatal else '⚠')} Не удалось установить зависимости веб-панели через npm"
            + ("" if fatal else ' (веб-панель Корры будет недоступна)')
        )
        _relay(r1)
        if fatal:
            _say('  Выполните вручную: npm install --workspace web && npm run build -w web')
        return False
    # First attempt — stream output via idle-timeout helper (issue #33788).
    # capture_output=True on a long Vite build looks identical to a hang;
    # users react by rebooting, which leaves the editable install in a
    # half-state. Streaming + idle-kill makes failures observable AND
    # recoverable (the stale-dist fallback below handles the kill path).
    r2 = _run_with_idle_timeout([npm, "run", "build"], cwd=web_dir, env=build_env)
    if r2.returncode != 0:
        # The install above can exit 0 while leaving the tree without a build
        # toolchain — a lockfile-hash skip over a half-installed tree, or an
        # interrupted link step. The generic retry below just reruns the same
        # command, so `tsc: not found` survives it and the stale dist is
        # served forever. Reinstall (non-silent, so the user sees it) first.
        missing_tool = _missing_web_build_tool((r2.stdout or "") + (r2.stderr or ""))
        if missing_tool:
            _say(f'  ⚠ При сборке не найден {missing_tool}. Переустанавливаем зависимости веб-панели…')
            _install_web_deps(silent=False)
            r2 = _run_with_idle_timeout([npm, "run", "build"], cwd=web_dir, env=build_env)
        if r2.returncode != 0:
            # Retry once after a short delay — covers boot-time races on Windows
            # (antivirus scanning Node.js binaries, npm cache not ready, transient
            # I/O when launched via Scheduled Task at logon). See issue #23817.
            _time.sleep(3)
            r2 = _run_with_idle_timeout([npm, "run", "build"], cwd=web_dir, env=build_env)

    if r2.returncode != 0:
        # _run_with_idle_timeout merges stderr into stdout; older callers
        # using subprocess.run kept them split. Pull from whichever has
        # content so the error surfaces regardless of which path produced
        # the CompletedProcess.
        build_output = (r2.stderr or "") + (r2.stdout or "")
        stderr_preview = build_output.strip()
        stderr_tail = "\n  ".join(stderr_preview.splitlines()[-10:]) if stderr_preview else ""
        project_root = web_dir.parent.parent if web_dir.parent.name == "apps" else web_dir.parent
        dist_dir = project_root / "korra_cli" / "web_dist"
        dist_index = dist_dir / "index.html"

        # If a stale dist exists, serve it as a fallback instead of failing.
        # A stale UI is far better than no UI for non-interactive callers
        # (Windows Scheduled Tasks, CI) — issue #23817.
        if dist_index.exists():
            _say('  ⚠ Не удалось собрать веб-панель. Используется предыдущая сборка.')
            if stderr_tail:
                _say(f'  Ошибка сборки: {stderr_tail}')
            return True

        _say(
            f"  {('✗' if fatal else '⚠')} Не удалось собрать веб-панель"
            + ("" if fatal else ' (веб-панель Корры будет недоступна)')
        )
        _relay(r2)
        if fatal:
            _say('  Выполните вручную: npm install --workspace web && npm run build -w web')
        return False
    _say('  ✓ Веб-панель собрана')
    project_root = web_dir.parent.parent if web_dir.parent.name == "apps" else web_dir.parent
    _write_web_ui_build_stamp(project_root, web_dir)
    return True


def _desktop_dist_exists(desktop_dir: Path) -> bool:
    """Return True when a local desktop renderer build is present."""
    return (desktop_dir / "dist" / "index.html").exists()


# ---------------------------------------------------------------------------
# Desktop build stamp — content-hash based skip logic
# ---------------------------------------------------------------------------
# The desktop Electron build is expensive.
# Unlike the web UI (which uses mtime comparison), the desktop uses a
# SHA-256 content hash of the source tree so that:
#   - ``git checkout`` / ``git pull`` that touch mtimes but not content
#     don't trigger a rebuild
#   - ``hermes update`` can unconditionally call ``hermes desktop --build-only``
#     and it will skip if nothing actually changed
#   - ``hermes desktop`` (interactive launch) skips the build when the
#     stamp matches, making repeated launches fast
#
# Stamp file: $HERMES_HOME/desktop-build-stamp.json
# Schema:
#   {
#     "contentHash": "<sha256 hex of source files>",
#     "sourceMode": true | false,
#     "builtAt": "<ISO 8601>"
#   }

def _compute_desktop_content_hash(project_root: Path) -> str:
    """Return a SHA-256 hex digest of all source files that feed the desktop build.

    Covers ``apps/desktop/`` (excluding anything matched by .gitignore)
    plus the root ``package.json`` / ``package-lock.json`` (workspace config
    that determines dependency resolution for the desktop workspace).

    Parses the repo-root ``.gitignore`` via *pathspec* so we automatically
    skip ``node_modules/``, ``dist/``, ``*.pyc``, etc. without maintaining
    a hardcoded skip-list.
    """
    h = hashlib.sha256()

    def _hash_file(path: Path) -> None:
        rel = str(path.relative_to(project_root))
        h.update(rel.encode())
        h.update(b"\0")
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        except (OSError, IOError):
            pass
        h.update(b"\0")


    from pathspec import PathSpec

    gitignore = project_root / ".gitignore"
    lines: list[str] = []
    if gitignore.is_file():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    spec = PathSpec.from_lines("gitignore", lines)

    # Root workspace config
    for name in ("package.json", "package-lock.json"):
        p = project_root / name
        if p.is_file():
            rel = str(p.relative_to(project_root))
            if not spec.match_file(rel):
                _hash_file(p)

    # Walk apps/desktop/ — prune ignored directories in-place
    desktop_dir = project_root / "apps" / "desktop"
    for dirpath, dirnames, filenames in os.walk(desktop_dir, topdown=True):
        # Prune ignored directories so we never descend into them
        dirnames[:] = [
            d for d in dirnames
            if not spec.match_file(str((Path(dirpath) / d).relative_to(project_root)))
        ]

        for fn in sorted(filenames):
            fp = Path(dirpath) / fn
            rel = str(fp.relative_to(project_root))
            if not spec.match_file(rel):
                _hash_file(fp)

    return h.hexdigest()


def _desktop_stamp_path() -> Path:
    """Return the path to the desktop build stamp file under $HERMES_HOME."""
    from korra_constants import get_hermes_home
    return get_hermes_home() / "desktop-build-stamp.json"


def _renderer_bundle_dir(desktop_dir: Path, *, source_mode: bool) -> Optional[Path]:
    """The renderer ``dist`` directory a launch loads, when it is inspectable.

    Source mode builds to ``apps/desktop/dist``. A packaged app ships the same
    bundle twice — inside ``app.asar`` and, because ``asarUnpack`` lists
    ``dist/**``, beside it in ``app.asar.unpacked``. Only the unpacked copy is
    a real directory; that is also the one an interrupted replace tears, so
    checking it catches the failure we care about.
    """
    if source_mode:
        return desktop_dir / "dist"

    executable = _desktop_packaged_executable(desktop_dir)
    if executable is None:
        return None

    # macOS: …/Hermes.app/Contents/MacOS/Hermes → …/Contents/Resources
    resources = (
        executable.parent.parent / "Resources"
        if sys.platform == "darwin"
        else executable.parent / "resources"
    )
    return resources / "app.asar.unpacked" / "dist"


# The module files the renderer fetches before any app code runs: Vite emits
# them as `<script type="module" src>` plus `<link rel="modulepreload" href>`.
_HTML_TAG_WITH_URL = re.compile(r"""<(?:script|link)\b[^>]*\b(?:src|href)=["']([^"']+)["'][^>]*>""", re.IGNORECASE)
_MODULE_TAG = re.compile(r"""\btype=["']module["']|\brel=["']modulepreload["']""", re.IGNORECASE)


def _renderer_bundle_torn(dist_dir: Path) -> bool:
    """True when ``index.html`` names hashed module files that aren't there.

    ``index.html`` and the hashed chunks under ``assets/`` are ONE generation.
    An update that replaces the app while its files are locked (antivirus, a
    still-running instance, an interrupted Windows replace) can leave the two
    behind from different generations. The app then launches and dies on the
    first lazy import with ``Failed to fetch dynamically imported module:
    …/assets/<chunk>-<hash>.js`` — and because the content stamp still matches
    the intact SOURCE tree, ``hermes desktop`` skips the rebuild that would fix
    it, so every relaunch reproduces the crash and reinstalling looks like the
    only way out. Detecting the tear turns it into a normal rebuild.

    Conservative: an unreadable index, or one naming nothing checkable, is NOT
    reported as torn — the missing-bundle guards own those cases.
    """
    try:
        html = (dist_dir / "index.html").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    for match in _HTML_TAG_WITH_URL.finditer(html):
        href = match.group(1)
        # Absolute/CDN URLs aren't part of this bundle's generation.
        if not _MODULE_TAG.search(match.group(0)) or re.match(r"^[a-z]+:|^//", href, re.IGNORECASE):
            continue
        rel = href.split("?", 1)[0].split("#", 1)[0].lstrip("./")
        if rel and not (dist_dir / rel).exists():
            return True

    return False


def _desktop_build_needed(desktop_dir: Path, project_root: Path, *, source_mode: bool) -> bool:
    """Return True when the desktop build output is stale, missing, or torn.

    Compares the current content hash against the saved stamp. Also returns
    True if the expected build artifact doesn't exist (e.g. first run after
    ``hermes update`` that pulled new source but hasn't built yet).
    """
    # If there's no build output at all, we definitely need to build
    if source_mode:
        if not _desktop_dist_exists(desktop_dir):
            return True
    else:
        if _desktop_packaged_executable(desktop_dir) is None:
            return True

    # A torn renderer bundle is stale no matter what the stamp says: the hash
    # describes the SOURCE tree, which is intact, while the built output is the
    # half-replaced one that crashes on its first lazy import.
    dist_dir = _renderer_bundle_dir(desktop_dir, source_mode=source_mode)
    if dist_dir is not None and _renderer_bundle_torn(dist_dir):
        print(f'  ⚠ После прежнего обновления приложение неполное ({dist_dir}); пересобираем')
        return True

    stamp_file = _desktop_stamp_path()
    if not stamp_file.is_file():
        return True

    try:
        stamp_data = json.loads(stamp_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError):
        return True

    # If the mode changed (source vs packaged), force a rebuild
    if stamp_data.get("sourceMode") != source_mode:
        return True

    saved_hash = stamp_data.get("contentHash")
    if not saved_hash:
        return True

    current_hash = _compute_desktop_content_hash(project_root)
    return current_hash != saved_hash


def _write_desktop_build_stamp(project_root: Path, *, source_mode: bool) -> None:
    """Write the desktop build stamp after a successful build."""
    stamp_file = _desktop_stamp_path()
    try:
        stamp_file.parent.mkdir(parents=True, exist_ok=True)
        content_hash = _compute_desktop_content_hash(project_root)
        from datetime import datetime, timezone
        stamp_data = {
            "contentHash": content_hash,
            "sourceMode": source_mode,
            "builtAt": datetime.now(timezone.utc).isoformat(),
        }
        stamp_file.write_text(json.dumps(stamp_data, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        # Never let stamp-writing block or fail a build
        logger.debug("Failed to write desktop build stamp: %s", exc)


def _desktop_packaged_executable(desktop_dir: Path) -> Optional[Path]:
    """Return the current platform's unpacked Electron app executable."""
    release_dir = desktop_dir / "release"
    if sys.platform == "darwin":
        candidates = list(release_dir.glob("mac*/Hermes.app/Contents/MacOS/Hermes"))
    elif sys.platform == "win32":
        candidates = [
            release_dir / "win-unpacked" / "Hermes.exe",
            release_dir / "win-ia32-unpacked" / "Hermes.exe",
            release_dir / "win-arm64-unpacked" / "Hermes.exe",
        ]
    else:
        candidates = [
            release_dir / "linux-unpacked" / "hermes",
            release_dir / "linux-unpacked" / "Hermes",
            release_dir / "linux-arm64-unpacked" / "hermes",
            release_dir / "linux-arm64-unpacked" / "Hermes",
        ]

    existing = [p for p in candidates if p.exists()]
    if not existing:
        return None
    if sys.platform == "win32" and len(existing) > 1:
        # Multiple unpacked trees can coexist (e.g. a stale win-arm64-unpacked
        # left behind by a cross-arch experiment next to the real win-unpacked).
        # Picking purely by mtime can then hand a wrong-architecture Hermes.exe
        # to the launcher, which Windows rejects with "This app can't run on
        # your computer" (#69179). Prefer candidates whose PE machine field
        # matches the host; fall back to mtime when none can be parsed.
        expected = _expected_windows_pe_machines()
        matching = [p for p in existing if _pe_machine_or_none(p) in expected]
        if matching:
            existing = matching
    return max(existing, key=lambda p: p.stat().st_mtime)


# ─── Desktop exe integrity gate (#69179) ────────────────────────────────────
#
# The desktop self-update chain (Desktop → hermes-setup --update →
# `hermes update` → `hermes desktop --build-only` → relaunch) rebuilds
# Hermes.exe on the end user's machine and used to verify only that the file
# EXISTS before declaring success. A corrupt cached Electron zip whose
# extraction produced a truncated electron.exe, an interrupted rcedit resource
# rewrite, a disk-full pack, or a wrong-arch unpacked tree therefore shipped a
# broken binary that Windows refuses to load ("This app can't run on your
# computer" / 此应用无法在你的电脑上运行). These helpers parse the PE header —
# no signature infrastructure required — so a structurally broken or
# wrong-architecture Hermes.exe is caught BEFORE the updater replaces the
# working app, and the previous build can be restored from the .bak tree that
# apps/desktop/scripts/before-pack.mjs now preserves.

_PE_MACHINE_I386 = 0x014C
_PE_MACHINE_AMD64 = 0x8664
_PE_MACHINE_ARM64 = 0xAA64

_PE_MACHINE_NAMES = {
    _PE_MACHINE_I386: "x86 (32-bit)",
    _PE_MACHINE_AMD64: "x64 (AMD64)",
    _PE_MACHINE_ARM64: "ARM64",
}

_PE_MACHINE_TO_NAME = {
    _PE_MACHINE_ARM64: "ARM64",
    _PE_MACHINE_AMD64: "AMD64",
    _PE_MACHINE_I386: "X86",
}

# MACHINE_ATTRIBUTES bits (processthreadsapi.h). UserEnabled means the host
# can run user-mode code of that machine type — natively or under emulation.
_MACHINE_ATTRIBUTE_USER_ENABLED = 0x00000001


def _windows_native_machine_from_iswow64() -> Optional[str]:
    """Ask IsWow64Process2 for the OS-native machine (None if unavailable/fail).

    ctypes defaults ``GetCurrentProcess``'s restype to ``c_int``, so the
    current-process pseudo-handle ``(HANDLE)-1`` is truncated to
    ``0xFFFFFFFF`` and zero-extended into a 64-bit invalid handle. On Win64
    that makes ``IsWow64Process2`` fail with ``ERROR_INVALID_HANDLE`` (6),
    which is exactly the residual Windows-on-ARM failure after #71218: the
    gate fell through to ``PROCESSOR_ARCHITECTURE=AMD64`` (the emulated
    process arch) and rejected a correctly-built ARM64 ``Hermes.exe``.
    Binding ``restype``/``argtypes`` to ``wintypes.HANDLE`` keeps the full
    ``0xFFFFFFFFFFFFFFFF`` pseudo-handle.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.IsWow64Process2.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.USHORT),
        ctypes.POINTER(wintypes.USHORT),
    ]
    kernel32.IsWow64Process2.restype = wintypes.BOOL

    process_machine = wintypes.USHORT(0)
    native_machine = wintypes.USHORT(0)
    if not kernel32.IsWow64Process2(
        kernel32.GetCurrentProcess(),
        ctypes.byref(process_machine),
        ctypes.byref(native_machine),
    ):
        return None
    return _PE_MACHINE_TO_NAME.get(native_machine.value)


def _windows_user_runnable_pe_machines() -> Optional[set]:
    """PE machines this host can run in user mode, via GetMachineTypeAttributes.

    This asks the question the integrity gate actually cares about — "can this
    Windows host load a PE of machine X?" — instead of inferring it from a
    host-architecture name. It is also the only documented API that reports
    AMD64-on-ARM64 emulation support; ``IsWow64GuestMachineSupported`` only
    answers for 32-bit guests.

    Returns None when the API is unavailable (pre-Windows-11 build 22000) or
    reports nothing runnable, so callers fall back to name-based detection.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetMachineTypeAttributes.argtypes = [
        wintypes.USHORT,
        ctypes.POINTER(ctypes.c_int),
    ]
    kernel32.GetMachineTypeAttributes.restype = ctypes.c_long

    runnable = set()
    for machine in (_PE_MACHINE_ARM64, _PE_MACHINE_AMD64, _PE_MACHINE_I386):
        attributes = ctypes.c_int(0)
        # HRESULT: zero is success, any nonzero value is a failure.
        if kernel32.GetMachineTypeAttributes(machine, ctypes.byref(attributes)):
            continue
        if attributes.value & _MACHINE_ATTRIBUTE_USER_ENABLED:
            runnable.add(machine)
    return runnable or None


def _windows_native_machine() -> str:
    """The Windows host OS's NATIVE machine architecture, normalized upper.

    ``platform.machine()`` reports the PROCESS architecture, which lies under
    emulation: the desktop update chain runs an x64 hermes-setup.exe (and thus
    x64 Python) on Windows-on-ARM devices, where ``platform.machine()``
    returns ``AMD64`` even though the OS is ARM64. The #71119 integrity gate
    then rejected the CORRECT ARM64 rebuild as an "architecture mismatch"
    (#69179 follow-up report). Probe order:

    1. ``IsWow64Process2`` with a correctly-typed current-process HANDLE
       (#71218 + HANDLE-truncation fix). This is the only API that tells the
       truth from an x64 process emulated on ARM64.
    2. ``PROCESSOR_ARCHITEW6432`` / ``PROCESSOR_ARCHITECTURE`` — WOW64
       (32-bit) hosts and pre-1511 Windows 10 without the newer API.
    3. ``platform.machine()``.

    Note ``GetNativeSystemInfo`` is deliberately NOT used: Microsoft documents
    that it "also returns emulated processor details when run from an app
    under emulation", so on the very WoA hosts this function exists to serve
    it reports AMD64 — no better than the env-var rung below it.
    """
    if sys.platform == "win32":
        try:
            name = _windows_native_machine_from_iswow64()
        except (OSError, AttributeError, TypeError, ValueError):
            # API missing (pre-1511), DLL load failure in tests, or a
            # mistyped ctypes binding — fall through to the env vars.
            name = None
        if name:
            return name
        env_arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get(
            "PROCESSOR_ARCHITECTURE"
        )
        if env_arch:
            return env_arch.upper()
    import platform as _platform

    return (_platform.machine() or "").upper()


def _expected_windows_pe_machines() -> set:
    """PE machine values the current Windows host can natively load.

    Preferred source is ``GetMachineTypeAttributes``, which answers this
    question directly (including AMD64-on-ARM64 emulation) instead of
    inferring it from an architecture name.

    Fallback is name-based: AMD64 hosts run x64 and (via WOW64) x86. ARM64
    hosts run ARM64 and (Windows 11 emulation) x64. 32-bit x86 hosts run only
    x86. Unknown machines return the permissive full set so the integrity gate
    can never brick launch on exotic hosts. Host detection uses the OS-native
    machine (see ``_windows_native_machine``), not the process architecture.
    """
    if sys.platform == "win32":
        try:
            runnable = _windows_user_runnable_pe_machines()
        except (OSError, AttributeError, TypeError, ValueError):
            runnable = None
        if runnable:
            return runnable
    machine = _windows_native_machine().upper()
    if machine in ("AMD64", "X86_64", "X64"):
        return {_PE_MACHINE_AMD64, _PE_MACHINE_I386}
    if machine in ("ARM64", "AARCH64"):
        return {_PE_MACHINE_ARM64, _PE_MACHINE_AMD64}
    if machine in ("X86", "I386", "I486", "I586", "I686"):
        return {_PE_MACHINE_I386}
    return {_PE_MACHINE_AMD64, _PE_MACHINE_ARM64, _PE_MACHINE_I386}


def _parse_pe_machine(path: Path) -> int:
    """Parse ``path`` as a PE executable and return its COFF machine field.

    Raises ``ValueError`` with a human-readable reason when the file is not a
    structurally complete PE: missing MZ/PE magic (an HTML error page or JSON
    body saved as .exe), header truncation, or raw section data extending past
    the end of the file (the truncated-download / interrupted-extraction
    shape). Purely a header walk — cheap even on a 200 MB Electron exe.
    """
    import struct

    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f'не удалось прочитать: {exc}')
    if file_size < 512:
        raise ValueError(
            f'В файле только {file_size} байт — слишком мало для программы Windows'
        )
    with path.open("rb") as fh:
        head = fh.read(64)
        if len(head) < 64 or head[:2] != b"MZ":
            raise ValueError(
                'Нет заголовка MZ: это не программа Windows. Возможно, файл .exe неполный или содержит текст.'
            )
        e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
        if e_lfanew <= 0 or e_lfanew + 24 > file_size:
            raise ValueError('Повреждён заголовок DOS: указатель PE находится за концом файла')
        fh.seek(e_lfanew)
        pe_head = fh.read(24)
        if len(pe_head) < 24 or pe_head[:4] != b"PE\x00\x00":
            raise ValueError('Нет сигнатуры PE: заголовок программы повреждён')
        machine, n_sections = struct.unpack_from("<HH", pe_head, 4)
        size_of_optional = struct.unpack_from("<H", pe_head, 20)[0]
        fh.seek(e_lfanew + 24 + size_of_optional)
        max_section_end = 0
        for _ in range(n_sections):
            section = fh.read(40)
            if len(section) < 40:
                raise ValueError('Таблица секций PE обрезана')
            size_of_raw, pointer_to_raw = struct.unpack_from("<II", section, 16)
            max_section_end = max(max_section_end, pointer_to_raw + size_of_raw)
        if file_size < max_section_end:
            raise ValueError(
                f'Программа обрезана: файл содержит {file_size} байт, а секции PE требуют {max_section_end}'
            )
    return machine


def _pe_machine_or_none(path: Path) -> Optional[int]:
    try:
        return _parse_pe_machine(path)
    except ValueError:
        return None


def _desktop_exe_integrity_error(path: Path) -> Optional[str]:
    """Return a human-readable reason ``path`` cannot run on this Windows host,
    or ``None`` when the exe parses as a complete PE of a loadable architecture.
    """
    try:
        machine = _parse_pe_machine(path)
    except ValueError as exc:
        return str(exc)
    expected = _expected_windows_pe_machines()
    if machine not in expected:
        got = _PE_MACHINE_NAMES.get(machine, f'неизвестная архитектура 0x{machine:04X}')
        return (
            f'Несовместимая архитектура: программа для {got}, а Windows работает на {_windows_native_machine()}'
        )
    return None


def _desktop_backup_unpacked_dir(packaged_executable: Path) -> Path:
    """The rollback tree before-pack.mjs preserves: ``<unpacked-dir>.bak``."""
    unpacked = packaged_executable.parent
    return unpacked.parent / (unpacked.name + ".bak")


def _rollback_desktop_from_backup(packaged_executable: Path) -> Optional[Path]:
    """Restore the previous unpacked desktop app from its ``.bak`` tree.

    Returns the restored executable path, or ``None`` when no usable backup
    exists (missing, or its exe fails the same integrity probe). The corrupt
    tree is kept alongside as ``<unpacked-dir>.corrupt`` for diagnostics.
    Best-effort: never raises.
    """
    unpacked = packaged_executable.parent
    backup_dir = _desktop_backup_unpacked_dir(packaged_executable)
    backup_exe = backup_dir / packaged_executable.name
    if not backup_exe.exists():
        return None
    if _desktop_exe_integrity_error(backup_exe) is not None:
        return None
    corrupt_dir = unpacked.parent / (unpacked.name + ".corrupt")
    try:
        shutil.rmtree(corrupt_dir, ignore_errors=True)
        try:
            unpacked.rename(corrupt_dir)
        except OSError:
            shutil.rmtree(unpacked, ignore_errors=True)
        backup_dir.rename(unpacked)
    except OSError:
        return None
    restored = unpacked / packaged_executable.name
    return restored if restored.exists() else None


def _ensure_desktop_exe_launchable(
    desktop_dir: Path, packaged_executable: Optional[Path]
) -> tuple:
    """Windows post-build integrity gate for the self-update rebuild (#69179).

    Returns ``(verified_exe_or_None, rolled_back)``:

    - exe passed the probe → ``(exe, False)``
    - exe corrupt/wrong-arch, previous build restored → ``(old_exe, True)``
    - exe corrupt and nothing restorable → ``(None, False)``

    On any integrity failure the corrupt cached Electron zip is purged and the
    desktop build stamp invalidated, so the updater's retry-once rebuild pulls
    a fresh, SHASUM-verified Electron download instead of re-staging the same
    corrupt bytes. No-op off Windows and when there is no executable to check.
    """
    if packaged_executable is None or sys.platform != "win32":
        return packaged_executable, False

    error = _desktop_exe_integrity_error(packaged_executable)
    if error is None:
        return packaged_executable, False

    print(f'✗ Собранное приложение не прошло проверку целостности: {error}')
    print(f'    Путь: {packaged_executable}')

    # Self-heal setup for the retry: drop the (likely corrupt) cached Electron
    # zip and the content stamp so the next rebuild is a genuine re-download +
    # re-stage rather than a replay of the same broken extraction.
    _purge_electron_build_cache(desktop_dir)
    try:
        _desktop_stamp_path().unlink()
    except OSError:
        pass

    restored = _rollback_desktop_from_backup(packaged_executable)
    if restored is not None:
        print('  ↩ Обновление отменено. Рабочая версия приложения восстановлена из резервной копии.')
        print('    Прежняя версия сохранена и работает. Повторите korra desktop')
        print('    или обновление из приложения, чтобы заново загрузить Electron.')
        return restored, True

    print('  ✗ Подходящая резервная копия не найдена.')
    print('    Пересоберите: korra desktop --force-build,')
    print('    либо повторно запустите установщик Корры для восстановления.')
    return None, False


def _electron_download_cache_dirs() -> list[Path]:
    """Return the per-user Electron download cache directories for this OS.

    electron-builder's ``app-builder unpack-electron`` extracts the Electron
    distribution from a zip stored in this cache (NOT from node_modules), so a
    corrupt zip here — not a bad workspace install — is what poisons the build.
    Honors the ``electron_config_cache`` / ``ELECTRON_CACHE`` overrides that
    ``@electron/get`` respects, then falls back to the platform defaults.
    """
    home = Path.home()
    candidates: list[Path] = []
    override = os.environ.get("electron_config_cache") or os.environ.get("ELECTRON_CACHE")
    if override:
        candidates.append(Path(override))
    if sys.platform == "darwin":
        candidates.append(home / "Library" / "Caches" / "electron")
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "electron" / "Cache")
        candidates.append(home / "AppData" / "Local" / "electron" / "Cache")
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            candidates.append(Path(xdg) / "electron")
        candidates.append(home / ".cache" / "electron")

    seen: set[Path] = set()
    out: list[Path] = []
    for c in candidates:
        rc = c.expanduser()
        if rc not in seen:
            seen.add(rc)
            out.append(rc)
    return out


def _purge_electron_build_cache(desktop_dir: Path) -> list[Path]:
    """Clear the cached Electron download + half-written unpacked dir so the
    next ``pack`` re-downloads and re-stages from scratch.

    Root cause of the ``ENOENT … rename '…/linux-unpacked/electron' ->
    '…/linux-unpacked/Hermes'`` desktop build failure: a corrupt zip in the
    per-user Electron download cache (a partial download resumed into the same
    file leaves prepended/concatenated junk, or an interrupted write truncates
    it). electron-builder's ``app-builder unpack-electron`` extracts the
    distribution from that cached zip (NOT from node_modules); a bad zip yields
    a partial tree MISSING the 193 MB ``electron`` binary, so the final rename
    dies. Re-running repeats the same broken extraction forever.

    We deliberately do NOT try to detect corruption ourselves. stdlib
    ``zipfile`` silently tolerates the prepended/concatenated junk that is the
    most common corruption here — it reads from the end-of-central-directory
    backward, so ``testzip()`` returns clean on exactly the zips ``unzip -t``
    and ``@electron/get`` reject. Gating the purge on a self-rolled validator
    would therefore skip the real-world case and never self-heal. Instead, on a
    packaged-build failure we unconditionally remove the version's cached zips
    and the stale unpacked dir, then let the caller retry once: ``@electron/get``
    re-downloads with its own SHASUM verification (the real source of truth),
    and ``before-pack.cjs`` re-wipes the unpacked dir. If the failure was
    unrelated, a clean re-download is harmless and the retry fails the same way.

    Best-effort: never raises. Returns the paths removed so the caller can log
    them and decide whether a retry is worthwhile (empty list ⇒ nothing to
    clear, so no point retrying).
    """
    removed: list[Path] = []

    for cache_dir in _electron_download_cache_dirs():
        if not cache_dir.is_dir():
            continue
        for zip_path in sorted(cache_dir.rglob("electron-*.zip")):
            try:
                zip_path.unlink()
                removed.append(zip_path)
            except OSError:
                # Locked/permission-denied entry is out of our hands; let the
                # build report its own error rather than masking it.
                pass

    # Drop the half-written unpacked dir too: an interrupted prior pack leaves
    # a partial tree that poisons the rename even after the zip is fixed.
    # (before-pack.cjs also handles this, but clearing it here makes the retry
    # robust even if the hook is somehow skipped.)
    release_dir = desktop_dir / "release"
    if release_dir.is_dir():
        for unpacked in release_dir.glob("*-unpacked"):
            try:
                shutil.rmtree(unpacked, ignore_errors=True)
                removed.append(unpacked)
            except OSError:
                pass

    return removed


# Last-resort Electron mirror after GitHub download fails (#47266). Only used
# when the user hasn't pinned ELECTRON_MIRROR.
_ELECTRON_FALLBACK_MIRROR = "https://npmmirror.com/mirrors/electron/"


def _electron_dir(project_root: Path) -> Path:
    """Return the Electron package directory the desktop workspace installs.

    npm may keep workspace-only dev dependencies under
    ``apps/desktop/node_modules`` instead of hoisting them to the repo root.
    Which layout you get depends on the npm version and what else is installed,
    so a build path that assumes one or the other breaks intermittently across
    machines. ``apps/desktop/package.json`` points electron-builder's
    ``electronDist`` at ``node_modules/electron/dist`` relative to the desktop
    project, so prefer the workspace-local package and fall back to the root
    hoist when that's where npm landed it.
    """
    desktop_local = project_root / "apps" / "desktop" / "node_modules" / "electron"
    if desktop_local.exists():
        return desktop_local
    return project_root / "node_modules" / "electron"


def _electron_dist_binary(project_root: Path) -> Path:
    """Return the path to the Electron main binary inside the installed package.

    electron-builder reads the binary from ``build.electronDist`` since #38673,
    so this is the exact file whose absence makes a pack fail with "The
    specified electronDist does not exist". The basename differs per OS (the
    platform Electron is named for the host the build runs on).
    """
    dist = _electron_dir(project_root) / "dist"
    if sys.platform == "darwin":
        return dist / "Electron.app" / "Contents" / "MacOS" / "Electron"
    if sys.platform == "win32":
        return dist / "electron.exe"
    return dist / "electron"


def _electron_dist_ok(project_root: Path) -> bool:
    """True when ``node_modules/electron/dist`` holds a usable Electron binary.

    A directory that exists but is missing the binary (a partial extraction from
    a corrupt cached zip, or an interrupted postinstall) counts as NOT ok, since
    that is exactly the shape that makes electron-builder throw on the pinned
    electronDist.
    """
    try:
        return _electron_dist_binary(project_root).exists()
    except OSError:
        return False


def _electron_pkg_staged_missing_dist(project_root: Path) -> bool:
    """electron staged (package.json + install.js) but dist missing — blocked postinstall."""
    electron_dir = _electron_dir(project_root)
    return (
        (electron_dir / "package.json").is_file()
        and (electron_dir / "install.js").is_file()
        and not _electron_dist_ok(project_root)
    )


def _redownload_electron_dist(
    project_root: Path,
    env: dict,
    *,
    mirror: Optional[str] = None,
) -> bool:
    """Best-effort: run electron's install.js to populate dist/ (optional mirror)."""
    if _electron_dist_ok(project_root):
        return True

    electron_dir = _electron_dir(project_root)
    installer = electron_dir / "install.js"
    if not installer.is_file():
        return False
    from korra_constants import find_node_executable, with_hermes_node_path

    node = find_node_executable("node")
    if not node:
        return False

    dist_dir = electron_dir / "dist"
    shutil.rmtree(dist_dir, ignore_errors=True)
    try:
        (electron_dir / "path.txt").unlink()
    except OSError:
        pass

    dl_env = with_hermes_node_path(env)
    if mirror:
        dl_env["ELECTRON_MIRROR"] = mirror
    try:
        subprocess.run([node, str(installer)], cwd=str(electron_dir), env=dl_env, check=False)
    except OSError:
        return False
    return _electron_dist_ok(project_root)


def _try_redownload_electron_dist(project_root: Path, env: dict) -> bool:
    """Canonical download, then fallback mirror unless the user pinned one."""
    if _redownload_electron_dist(project_root, env):
        return True
    if env.get("ELECTRON_MIRROR"):
        return False
    return _redownload_electron_dist(project_root, env, mirror=_ELECTRON_FALLBACK_MIRROR)


def _stop_desktop_processes_locking_build(desktop_dir: Path) -> list[int]:
    """Terminate any running desktop app executing from this build's ``release``
    dir so a rebuild can replace its (otherwise locked) executable.

    On Windows a running ``Hermes.exe`` keeps an exclusive lock on
    ``release/win-unpacked/Hermes.exe``. electron-builder's pack then can't
    delete the stale binary and dies with ``remove …\\Hermes.exe: Access is
    denied`` / ``ERR_ELECTRON_BUILDER_CANNOT_EXECUTE`` (before-pack hits the same
    EPERM cleaning the dir). The retry path repeats the failure because the lock
    is still held. POSIX lets you unlink a running binary, so this is a no-op
    off-Windows.

    Scope is deliberately narrow: only processes whose executable lives *inside*
    this desktop's ``release`` tree are stopped — a packaged install elsewhere or
    an unrelated "Hermes" process is never touched. Best-effort: never raises.
    Returns the PIDs we asked to stop.
    """
    if sys.platform != "win32":
        return []
    try:
        import psutil
    except Exception:
        return []
    try:
        release_dir = (desktop_dir / "release").resolve()
    except OSError:
        return []
    if not release_dir.is_dir():
        return []

    me = os.getpid()
    victims = []
    try:
        proc_iter = psutil.process_iter(["pid", "exe"])
    except Exception:
        return []
    for proc in proc_iter:
        try:
            info = proc.info
        except Exception:
            continue
        pid = info.get("pid")
        exe = info.get("exe")
        if not exe or pid is None or pid == me:
            continue
        try:
            exe_path = Path(exe).resolve()
        except (OSError, ValueError):
            continue
        if release_dir in exe_path.parents:
            victims.append(proc)

    stopped: list[int] = []
    for proc in victims:
        try:
            proc.terminate()
            stopped.append(int(proc.pid))
        except Exception:
            continue
    if stopped:
        # Wait for the handles (and thus the file locks) to actually release.
        try:
            _, alive = psutil.wait_procs(victims, timeout=5)
            for proc in alive:
                try:
                    proc.kill()
                except Exception:
                    continue
        except Exception:
            pass
    return stopped


def _desktop_macos_bundle_id(bundle: Path) -> Optional[str]:
    """Return a bundle/framework CFBundleIdentifier for local macOS signing."""
    import plistlib

    info = bundle / "Contents" / "Info.plist"
    if not info.exists() and bundle.suffix == ".framework":
        candidates = list(bundle.glob("Versions/*/Resources/Info.plist")) + list(
            bundle.glob("Resources/Info.plist")
        )
        if candidates:
            info = candidates[0]
    if not info.exists():
        return None
    try:
        data = plistlib.loads(info.read_bytes())
    except Exception:
        return None
    ident = data.get("CFBundleIdentifier")
    return str(ident) if ident else None


def _desktop_macos_local_signing_identity() -> Optional[str]:
    """Return the opt-in keychain identity for local macOS desktop signing.

    ``desktop.macos_signing_identity`` in config.yaml names a persistent
    code-signing certificate in the user's login keychain (a self-signed
    "Code Signing" cert made in Keychain Access is enough — no Apple Developer
    account needed). Signing with any identity gives the app a
    certificate-anchored Designated Requirement, which is the strongest way to
    keep macOS TCC grants (Full Disk Access, Accessibility, Automation, Files
    and Folders) stable across local rebuilds. Empty/unset keeps the default
    identifier-pinned ad-hoc signing.
    """
    if sys.platform != "darwin":
        return None
    try:
        from korra_cli.config import load_config

        desktop = load_config().get("desktop", {})
        if not isinstance(desktop, dict):
            return None
        identity = desktop.get("macos_signing_identity")
        if not isinstance(identity, str):
            return None
        return identity.strip() or None
    except Exception as exc:
        print(
            f'  Не удалось загрузить desktop.macos_signing_identity: {exc}. Используется временная подпись.'
        )
        return None


def _desktop_macos_has_valid_real_signature(app: Path) -> bool:
    """True when the bundle carries an intact non-ad-hoc (Team ID) signature.

    Used to make the relaunch fixup a no-op on properly signed/notarized
    builds even when CSC_LINK / APPLE_SIGNING_IDENTITY aren't in the
    environment (e.g. a release DMG install being repaired) — clobbering a
    Developer ID signature with an ad-hoc one would reset TCC grants and can
    break the hardened runtime. A *stale* real signature (in-place rebuild
    tampered with the bundle) fails --verify and returns False so the fixup
    can repair it.
    """
    codesign = shutil.which("codesign")
    if not codesign:
        return False
    try:
        info = subprocess.run(
            [codesign, "-dv", str(app)], check=False, capture_output=True, text=True
        )
        output = f"{info.stdout}\n{info.stderr}"
        if info.returncode != 0 or "TeamIdentifier=" not in output \
                or "TeamIdentifier=not set" in output:
            return False
        verify = subprocess.run(
            [codesign, "--verify", "--deep", "--strict", str(app)],
            check=False, capture_output=True,
        )
        return verify.returncode == 0
    except Exception:
        return False


def _desktop_macos_local_codesign(
    app: Path, *, desktop_dir: Path, identity: str = "-"
) -> bool:
    """Re-sign a local Desktop build so macOS TCC grants survive rebuilds.

    A plain ``codesign --deep --sign -`` leaves the bundle with a cdhash-only
    Designated Requirement and strips electron-builder's entitlements. Every
    rebuild changes the cdhash, so TCC (Full Disk Access, Accessibility,
    Automation, Files and Folders: Desktop/Downloads/Documents, microphone)
    treats the rebuilt app as different code and the user must re-grant
    everything — and the lost entitlements break microphone/JIT under the
    hardened runtime.

    Instead, sign inside-out (standalone Mach-O binaries, then nested
    frameworks/helper apps, then the main bundle), preserving the repo's
    entitlement plists, and pin an explicit identifier-based Designated
    Requirement when signing ad-hoc. With a real ``identity`` the certificate
    anchors the DR, so no explicit requirement is needed. Raises on signing
    failure; returns True after strict verification passes.
    """
    codesign = shutil.which("codesign")
    if not codesign:
        return False

    ent_main = desktop_dir / "electron" / "entitlements.mac.plist"
    ent_inherit = desktop_dir / "electron" / "entitlements.mac.inherit.plist"
    if not (ent_main.exists() and ent_inherit.exists()):
        # Hardened-runtime restrictions are enforced even for ad-hoc
        # signatures. Signing with --options runtime but WITHOUT the allow-jit
        # entitlements would leave Electron/V8 crashing on launch — strictly
        # worse than the legacy plain ad-hoc sign. Bail out so the caller
        # falls back to that legacy path instead.
        raise FileNotFoundError(
            f"Файлы разрешений приложения plist отсутствуют в {desktop_dir / 'electron'}"
        )

    def sign_path(
        path: Path,
        *,
        entitlements: Optional[Path] = None,
        identifier: Optional[str] = None,
        runtime: bool = True,
    ) -> None:
        args = [codesign, "--force", "--sign", identity, "--timestamp=none"]
        if runtime:
            args += ["--options", "runtime"]
        if entitlements is not None and entitlements.exists():
            args += ["--entitlements", str(entitlements)]
        if identifier and identity == "-":
            # Ad-hoc signatures get a cdhash-only DR by default; pin an
            # identifier-based DR so TCC has something stable to persist.
            args += ["--requirements", f'=designated => identifier "{identifier}"']
        args.append(str(path))
        subprocess.run(args, check=True, capture_output=True)

    # 1) Standalone Mach-O files (native modules, dylibs, crashpad handler).
    #    Compare paths relative to the app root — the absolute path always
    #    contains the outer Hermes.app component, so an absolute-parts check
    #    would skip every file.
    contents = app / "Contents"
    standalone: list[Path] = []
    for root, _dirs, files in os.walk(contents):
        root_path = Path(root)
        rel_parts = root_path.relative_to(app).parts
        if any(part.endswith(".app") for part in rel_parts):
            continue  # nested helper apps are signed as bundles below
        for name in files:
            fp = root_path / name
            if name in {"chrome_crashpad_handler", "spawn-helper"} or fp.suffix in {
                ".node",
                ".dylib",
            }:
                standalone.append(fp)
    for fp in sorted(standalone, key=lambda p: len(p.parts), reverse=True):
        sign_path(fp, runtime=False)

    # 2) Nested frameworks and helper apps, deepest first.
    bundles: list[Path] = []
    frameworks_dir = contents / "Frameworks"
    if frameworks_dir.exists():
        for root, _dirs, _files in os.walk(frameworks_dir):
            p = Path(root)
            if p.suffix in {".framework", ".app"}:
                bundles.append(p)
    for bundle in sorted(set(bundles), key=lambda p: len(p.parts), reverse=True):
        ent = ent_inherit if bundle.suffix == ".app" and "Helper" in bundle.name else None
        sign_path(bundle, entitlements=ent, identifier=_desktop_macos_bundle_id(bundle))

    # 3) The main bundle, with the app's own entitlements.
    sign_path(app, entitlements=ent_main, identifier=_desktop_macos_bundle_id(app))
    subprocess.run(
        [codesign, "--verify", "--deep", "--strict", str(app)],
        check=True, capture_output=True,
    )
    return True


def _desktop_macos_relaunchable_fixup(
    desktop_dir: Path,
    *,
    publisher_signing_configured: Optional[bool] = None,
) -> bool:
    """Make a locally-built macOS desktop app survive in-place self-update
    without resetting the user's TCC permission grants.

    An ad-hoc-signed .app has no stable Designated Requirement, so when the
    self-updater rebuilds the bundle in place (new cdhash) Gatekeeper reports
    "Hermes is damaged and can't be opened" — and macOS TCC forgets every
    permission the user granted (Full Disk Access, Desktop/Downloads/Documents,
    Accessibility, Automation, microphone), re-prompting on every launch after
    every update.

    Clear the quarantine xattrs, then re-sign with a stable identity:
    ``desktop.macos_signing_identity`` (a persistent keychain cert — strongest)
    when configured, else ad-hoc with identifier-pinned Designated Requirements,
    preserving the repo's entitlement plists either way. No-op when a real
    publisher identity is configured (CSC_LINK / APPLE_SIGNING_IDENTITY) or the
    bundle already carries an intact Developer ID signature, so a properly
    signed/notarized build is never clobbered. Callers that already made the
    publisher-signing decision may pass it explicitly so a later dotenv load
    can't reverse it. Falls back to the legacy deep ad-hoc sign if the
    entitlement-preserving path fails. Best-effort: never raises. Returns True
    when no work was needed or signing + strict verification succeeded.
    """
    if sys.platform != "darwin":
        return True
    if publisher_signing_configured is None:
        publisher_signing_configured = bool(
            os.environ.get("CSC_LINK") or os.environ.get("APPLE_SIGNING_IDENTITY")
        )
    if publisher_signing_configured:
        return True
    exe = _desktop_packaged_executable(desktop_dir)
    if exe is None:
        return True
    # exe = .../Hermes.app/Contents/MacOS/Hermes  ->  app bundle = .../Hermes.app
    app = exe.parents[2]
    if not str(app).endswith(".app") or not app.is_dir():
        return True
    codesign = shutil.which("codesign")
    if not codesign:
        return False
    if _desktop_macos_has_valid_real_signature(app):
        return True
    subprocess.run(["xattr", "-cr", str(app)], check=False)
    identity = _desktop_macos_local_signing_identity() or "-"
    try:
        if _desktop_macos_local_codesign(app, desktop_dir=desktop_dir, identity=identity):
            label = 'подпись из связки ключей' if identity != "-" else 'постоянная локальная подпись'
            print(f'  → Приложение macOS подписано с {label}; разрешения TCC сохранятся после пересборки')
            return True
    except Exception as exc:
        if identity != "-":
            print(
                f'  Настроенная подпись macOS не сработала: {identity!r}. Используется временная; возможно, разрешения TCC придётся выдать заново.'
            )
        print(f'  Постоянная подпись macOS не удалась: {exc}. Используется прежняя временная подпись.')
    try:
        # Legacy ad-hoc fallback: re-sign, but NEVER delete the safeStorage
        # keychain item. Deleting it would permanently orphan every
        # credential encrypted under it (gateway token, native OAuth access/
        # refresh tokens) — and this path is reached exactly when the
        # entitlement-preserving signer failed, so there is no verified
        # successor identity to hand the key to. The keychain prompt macOS
        # shows instead is recoverable ("Always Allow" updates the item's ACL
        # partition list and preserves the key); deletion is not. The real
        # fix (proof-carrying rotation/migration) belongs in Electron, where
        # safeStorage can read the old key. Tracked as follow-up.
        result = subprocess.run(
            [codesign, "--force", "--deep", "--sign", "-", str(app)],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(
                f'  Повторная временная подпись завершилась с кодом {result.returncode}; запись safeStorage в связке ключей сохранена.'
            )
            return False
        verify = subprocess.run(
            [codesign, "--verify", "--deep", "--strict", str(app)],
            check=False, capture_output=True, text=True,
        )
        if verify.returncode != 0:
            print(
                f'  Временная подпись не прошла строгую проверку; запись safeStorage в связке ключей сохранена.'
            )
            return False
        print('  → Приложение macOS переподписано прежним способом; запись ключей safeStorage сохранена')
        return True
    except Exception as exc:
        print(f'  Исправление перезапуска macOS пропущено: {exc}')
    return False


def _macos_codesigning_identity_valid(security: str, identity: str) -> bool:
    """True when `identity` appears among VALID code-signing identities.

    ``security find-identity -p codesigning`` (without ``-v``) also lists
    certificates macOS will refuse to sign with — e.g. a self-signed cert that
    was imported but never trusted for the codeSign policy. Only the ``-v``
    listing proves codesign can actually use it, so this is both the
    idempotency probe and the success postcondition for
    ``--setup-tcc-identity``. Never raises.
    """
    try:
        result = subprocess.run(
            [security, "find-identity", "-v", "-p", "codesigning"],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        return False

    return f'"{identity}"' in (result.stdout or "")


def _desktop_macos_setup_tcc_identity(identity: str = "Hermes Local Signing") -> bool:
    """Create/import a self-signed code-signing cert and configure Hermes to use it.

    One-shot setup for ``hermes desktop --setup-tcc-identity``. Creates a
    self-signed "Code Signing" certificate in the login keychain (the same
    artifact the docs describe creating manually via Keychain Access), grants
    ``codesign`` access to it, writes ``desktop.macos_signing_identity`` to
    config.yaml, and re-signs the already-packaged app so the next launch uses
    the certificate-anchored identity.

    Why this matters: macOS TCC grants (Full Disk Access, Accessibility,
    Automation, Files and Folders, microphone) persist against the app's
    code-signing identity, not its path. A plain ad-hoc signature gets a
    cdhash-only Designated Requirement, so every rebuild looks like a new app
    and the user must re-grant everything. A certificate-anchored identity is
    stable across rebuilds — the same mechanism yabai/skhd users rely on.

    Idempotent: re-running after an update finds the existing certificate and
    only re-points the config + re-signs. Returns True on success (or when
    already configured), False on failure. Never raises.
    """
    if sys.platform != "darwin":
        print('  --setup-tcc-identity доступен только в macOS; пропускаем')
        return False

    openssl = shutil.which("openssl")
    security = shutil.which("security")
    codesign = shutil.which("codesign")
    if not (openssl and security and codesign):
        print(
            f'  Для --setup-tcc-identity нужны openssl, security и codesign. Найдены: openssl={bool(openssl)}, security={bool(security)}, codesign={bool(codesign)}'
        )
        return False

    keychain = str(Path.home() / "Library" / "Keychains" / "login.keychain-db")
    # A certificate that merely EXISTS in the keychain is not enough — macOS
    # only treats it as a code-signing identity once it is trusted for the
    # codeSign policy. Probe with `-v` (valid identities only) so a previously
    # imported-but-untrusted cert is repaired rather than reported as done.
    already_imported = _macos_codesigning_identity_valid(security, identity)

    if not already_imported:
        # Create a self-signed code-signing cert (valid 10 years) and import it
        # into the login keychain with codesign access so signing works without
        # an interactive unlock prompt.
        tmp_dir = Path(tempfile.mkdtemp(prefix="hermes-tcc-"))
        try:
            key = tmp_dir / "sign.key"
            crt = tmp_dir / "sign.crt"
            p12 = tmp_dir / "sign.p12"
            subprocess.run(
                [
                    openssl, "req", "-x509", "-newkey", "rsa:2048",
                    "-keyout", str(key), "-out", str(crt),
                    "-days", "3650", "-nodes",
                    "-subj", f"/CN={identity}",
                    "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
                    "-addext", "extendedKeyUsage=codeSigning",
                ],
                capture_output=True, check=True,
            )
            # OpenSSL 3 defaults to AES/SHA-2 PKCS#12 encryption that macOS
            # `security import` rejects with "MAC verification failed during
            # PKCS12 import (wrong password?)". The `-legacy` flag restores the
            # RC2/SHA-1 format the importer accepts, but only exists on
            # OpenSSL 3 — so try the plain export first and fall back to
            # `-legacy` when the IMPORT fails with that signature. (Verified
            # E2E on macOS 26.3.1 / OpenSSL 3.6.3 by @ctaylor86 on PR #77189.)
            def _export_p12(extra_args: list) -> None:
                subprocess.run(
                    [
                        openssl, "pkcs12", "-export", *extra_args,
                        "-inkey", str(key), "-in", str(crt),
                        "-out", str(p12), "-passout", "pass:hermeslocal",
                    ],
                    capture_output=True, check=True,
                )

            def _import_p12():
                return subprocess.run(
                    [
                        security, "import", str(p12), "-k", keychain,
                        "-P", "hermeslocal",
                        "-T", codesign, "-T", "/usr/bin/codesign_allocate",
                    ],
                    capture_output=True, text=True, check=False,
                )

            _export_p12([])
            imported = _import_p12()
            if imported.returncode != 0 and "MAC verification failed" in (imported.stderr or ""):
                try:
                    _export_p12(["-legacy"])
                    imported = _import_p12()
                except subprocess.CalledProcessError:
                    # Older OpenSSL without -legacy: keep the original failure.
                    pass
            if imported.returncode != 0:
                print(f'  Не удалось импортировать подпись в связку ключей: {imported.stderr.strip()}')
                return False

            # Importing is still not enough: without explicit trust for the
            # codeSign policy, `security find-identity -v -p codesigning`
            # reports 0 valid identities and codesign refuses the cert. Trust
            # the self-signed root for code signing. This writes to the user's
            # trust settings, so macOS may prompt for the login password ONCE
            # here — that is the one-time setup cost this command exists to
            # front-load.
            trusted = subprocess.run(
                [security, "add-trusted-cert", "-r", "trustRoot", "-p", "codeSign", "-k", keychain, str(crt)],
                capture_output=True, text=True, check=False,
            )
            if trusted.returncode != 0:
                print(
                    f'  Не удалось добавить сертификат подписи в доверенные: {(trusted.stderr or trusted.stdout).strip()}'
                )
                return False
            print(f'  → Создан, импортирован и добавлен в доверенные самоподписанный сертификат: {identity!r}')
        except Exception as exc:
            print(f'  Не удалось создать сертификат: {exc}')
            return False
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        print(f'  → Подпись {identity!r} уже действительна в связке ключей')

    # Postcondition gate: only report success once macOS actually agrees the
    # identity is usable for code signing. Name-in-output checks pass for
    # invalid identities; this is the check that failed silently before.
    if not _macos_codesigning_identity_valid(security, identity):
        print(
            f'  Подпись {identity!r} импортирована, но не подходит для подписи кода. Проверьте security find-identity -v -p codesigning и инструкцию по связке ключей в документации приложения.'
        )
        return False

    # Point Hermes at the identity (config.yaml, not .env — it's not a secret).
    try:
        from korra_cli.config import set_config_value

        set_config_value("desktop.macos_signing_identity", identity)
        print(f'  → Сохранено desktop.macos_signing_identity = {identity!r}')
    except Exception as exc:
        print(f'  Не удалось сохранить desktop.macos_signing_identity: {exc}')
        return False

    # Re-sign the packaged app so the current build already uses the identity.
    desktop_dir = PROJECT_ROOT / "apps" / "desktop"
    if _desktop_packaged_executable(desktop_dir) is not None:
        try:
            if _desktop_macos_relaunchable_fixup(desktop_dir):
                print(
                    '  → Приложение переподписано сертификатом; разрешения TCC сохранятся после пересборки'
                )
        except Exception as exc:
            print(f'  Не удалось переподписать приложение: {exc}')

    print(
        '  macOS запросит разрешения ещё один раз из-за смены подписи. После подтверждения они сохранятся. Если разрешение не применяется, сбросьте его: tccutil reset All com.nousresearch.hermes'
    )
    return True


def _force_adhoc_macos_signing(env: dict, *, source_mode: bool) -> bool:
    """Stop electron-builder grabbing a random keychain identity on self-update.

    The desktop self-updater rebuilds *and re-signs the .app on the end user's
    machine* (``hermes desktop --build-only`` → electron-builder ``--dir``).
    With ``CSC_IDENTITY_AUTO_DISCOVERY`` on (its default), electron-builder
    signs the ``type=distribution``, hardened-runtime bundle with whatever it
    finds in that user's keychain — typically a personal "Apple Development"
    cert. That stalls/fails the sign step (no Developer ID + no provisioning
    profile) or clobbers your real notarized signature with an unusable one, so
    every post-update launch trips Gatekeeper.

    Force ad-hoc signing for the local packaged rebuild instead: deterministic,
    and exactly what ``_desktop_macos_relaunchable_fixup`` already finishes off.
    No-op for source runs, off-macOS, when a real identity is configured
    (``CSC_LINK`` / ``APPLE_SIGNING_IDENTITY``), or when the caller already
    pinned the flag. Mutates ``env``; returns True when it set the flag.
    """
    if sys.platform != "darwin" or source_mode:
        return False
    if env.get("CSC_LINK") or env.get("APPLE_SIGNING_IDENTITY"):
        return False
    if "CSC_IDENTITY_AUTO_DISCOVERY" in env:
        return False
    env["CSC_IDENTITY_AUTO_DISCOVERY"] = "false"
    return True


def _desktop_linux_needs_no_sandbox() -> bool:
    """Return True when Chromium/Electron should bypass the Linux sandbox.

    Ubuntu 23.10+ can enable AppArmor's
    ``apparmor_restrict_unprivileged_userns`` hardening, which breaks
    Chromium/Electron's user-namespace sandbox for normal users unless the app
    ships a working root-owned 4755 ``chrome-sandbox`` helper. In headless or
    non-interactive CLI contexts we may be unable to ``sudo chown/chmod`` that
    helper, so detect the host restriction and fall back to ``--no-sandbox``
    rather than hard-failing the launcher.

    We intentionally do NOT return True for root users here: running Electron as
    root without a sandbox is a qualitatively riskier path than launching as an
    unprivileged desktop user on an AppArmor-restricted host. The root case
    should remain an explicit user choice.
    """
    if os.environ.get("ELECTRON_DISABLE_SANDBOX", 0) == "1":
        return True

    if sys.platform != "linux":
        return False
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return False
    try:
        with open("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", encoding="utf-8") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def _desktop_linux_sandbox_helper_is_regular_file(packaged_executable: Path) -> bool:
    """Return True when ``chrome-sandbox`` exists as a regular file."""
    if sys.platform != "linux":
        return False
    sandbox = packaged_executable.parent / "chrome-sandbox"
    try:
        sandbox_lstat = sandbox.lstat()
    except OSError:
        return False
    return stat.S_ISREG(sandbox_lstat.st_mode)



def _desktop_linux_sandbox_fixup(packaged_executable: Path) -> bool:
    """Configure Electron's Linux SUID sandbox helper when required."""
    if sys.platform != "linux":
        return True

    sandbox = packaged_executable.parent / "chrome-sandbox"
    if not sandbox.exists():
        print(f'✗ В приложении Корры отсутствует компонент изоляции Electron для Linux: {sandbox}')
        return False

    # Reject symlinks — chown/chmod must not follow an attacker-controlled
    # link to an arbitrary path.  Use lstat() so we inspect the link itself
    # rather than the target, and require a regular file.
    try:
        sandbox_lstat = sandbox.lstat()
    except OSError:
        print(f'✗ Не удалось прочитать сведения о компоненте изоляции Electron: {sandbox}')
        return False
    if not stat.S_ISREG(sandbox_lstat.st_mode):
        print(f'✗ Компонент изоляции Electron не является обычным файлом: {sandbox}')
        return False

    if sandbox_lstat.st_uid == 0 and stat.S_IMODE(sandbox_lstat.st_mode) == 0o4755:
        return True

    sudo = shutil.which("sudo")
    if not sudo:
        print('✗ Для настройки изоляции Electron в Linux приложению Корры нужен sudo.')
        return False

    print('→ Настраиваем изоляцию Electron в Linux; нужен sudo…')
    for command in ([sudo, "chown", "root:root", str(sandbox)], [sudo, "chmod", "4755", str(sandbox)]):
        if subprocess.run(command, check=False).returncode != 0:
            print(f'✗ Не удалось настроить изоляцию Electron в Linux: {sandbox}')
            return False
    return True


_LINUX_PASSWORD_STORES = frozenset({"gnome-libsecret", "kwallet", "kwallet5", "kwallet6", "basic"})


def _detect_linux_password_store() -> str | None:
    """Detect the Chromium password-store backend for the current Linux session.

    Electron's safeStorage only reports encryption as available when Chromium
    selects the right keychain backend, and Chromium's own detection routinely
    fails under `hermes desktop` because the launcher environment doesn't look
    like a full desktop session. Probe order: KDE session env vars, GNOME
    Keyring's control socket, then a D-Bus ping of org.freedesktop.secrets
    (covers any Secret Service implementation, e.g. KeePassXC). Returns None
    when no keychain daemon is reachable.
    """
    kde_version = os.environ.get("KDE_SESSION_VERSION", "").strip()
    if kde_version == "6":
        return "kwallet6"
    if kde_version == "5":
        return "kwallet5"
    if kde_version:
        return "kwallet"
    if os.environ.get("KDE_FULL_SESSION"):
        return "kwallet"
    if os.environ.get("GNOME_KEYRING_CONTROL"):
        return "gnome-libsecret"
    try:
        result = subprocess.run(
            [
                "dbus-send", "--session", "--print-reply", "--reply-timeout=2000",
                "--dest=org.freedesktop.secrets",
                "/org/freedesktop/secrets",
                "org.freedesktop.DBus.Peer.Ping",
            ],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "gnome-libsecret"
    except Exception:
        pass
    return None


def _desktop_launch_options() -> tuple[list[str], str, str, str]:
    """Read `desktop.*` launch options from config.yaml.

    Returns ``(electron_flags, disable_gpu, password_store, ozone_hint)`` where
    ``electron_flags`` is a list of extra Electron CLI flags, ``disable_gpu``
    is one of "auto"/"1"/"0" (normalized for the HERMES_DESKTOP_DISABLE_GPU
    env var the Electron app reads), ``password_store`` is "auto" or one
    of the Chromium password-store backends (unknown values normalize to
    "auto"), and ``ozone_hint`` is one of "auto"/"x11"/"wayland" (normalized
    for ``ELECTRON_OZONE_PLATFORM_HINT``). Best-effort: any config error
    yields the safe defaults ``([], "auto", "auto", "auto")`` so a malformed
    config never blocks the launch.
    """
    flags: list[str] = []
    disable_gpu = "auto"
    password_store = "auto"
    ozone_hint = "auto"
    try:
        from korra_cli.config import load_config

        desktop_cfg = (load_config() or {}).get("desktop") or {}
    except Exception:
        return flags, disable_gpu, password_store, ozone_hint

    raw_flags = desktop_cfg.get("electron_flags")
    if isinstance(raw_flags, str):
        flags = shlex.split(raw_flags, posix=(os.name != "nt"))
    elif isinstance(raw_flags, (list, tuple)):
        flags = [str(f) for f in raw_flags if str(f).strip()]

    raw_gpu = desktop_cfg.get("disable_gpu", "auto")
    if isinstance(raw_gpu, bool):
        disable_gpu = "1" if raw_gpu else "0"
    elif isinstance(raw_gpu, str):
        low = raw_gpu.strip().lower()
        if low in ("1", "true", "yes", "on"):
            disable_gpu = "1"
        elif low in ("0", "false", "no", "off"):
            disable_gpu = "0"
        else:
            disable_gpu = "auto"

    raw_store = desktop_cfg.get("password_store", "auto")
    if isinstance(raw_store, str):
        low_store = raw_store.strip().lower()
        if low_store in _LINUX_PASSWORD_STORES:
            password_store = low_store

    raw_ozone = desktop_cfg.get("ozone_platform_hint", "auto")
    if isinstance(raw_ozone, str):
        low_ozone = raw_ozone.strip().lower()
        if low_ozone in ("auto", "x11", "wayland"):
            ozone_hint = low_ozone
    return flags, disable_gpu, password_store, ozone_hint


def _register_linux_desktop_entry() -> None:
    """Install the XDG desktop entry for Hermes Desktop (Linux only, best-effort).

    Gives the Electron app a launcher presence: a menu item and an icon.
    ``Exec`` and ``Icon`` are absolute, so the entry works outside a login
    shell. ``hermes uninstall --gui`` removes it.
    """
    try:
        from korra_cli.linux_desktop_entry import install_desktop_entry, is_supported

        if not is_supported():
            return
        entry = install_desktop_entry(PROJECT_ROOT)
        if entry:
            print(f'✓ Ярлык приложения установлен: {entry}')
    except Exception as exc:  # never block a launch on launcher plumbing
        print(f'⚠ Не удалось установить ярлык приложения: {exc}')


def cmd_gui(args: argparse.Namespace):
    """Build and launch the native Electron desktop GUI."""
    desktop_dir = PROJECT_ROOT / "apps" / "desktop"
    if not (desktop_dir / "package.json").exists():
        print(f'Исходники приложения не найдены: {desktop_dir}')
        sys.exit(1)

    try:
        from korra_logging import setup_logging as _setup_logging_gui
        _setup_logging_gui(mode="gui")
    except Exception:
        pass

    from korra_constants import with_hermes_node_path

    # with_hermes_node_path() copies os.environ when called with no arg.
    env = with_hermes_node_path()
    if getattr(args, "fake_boot", False):
        korra_env_set(env, "KORRA_DESKTOP_BOOT_FAKE", "1")
    if getattr(args, "ignore_existing", False):
        korra_env_set(env, "KORRA_DESKTOP_IGNORE_EXISTING", "1")
    if getattr(args, "hermes_root", None):
        korra_env_set(env, "KORRA_DESKTOP_HERMES_ROOT", str(Path(args.hermes_root).expanduser().resolve()))
    if getattr(args, "cwd", None):
        korra_env_set(env, "KORRA_DESKTOP_CWD", str(Path(args.cwd).expanduser().resolve()))
    else:
        korra_env_set(env, "KORRA_DESKTOP_CWD", os.getcwd())

    # Desktop launch options from config.yaml (`desktop.electron_flags`,
    # `desktop.disable_gpu`, `desktop.ozone_platform_hint`). The GPU policy
    # and ozone hint are bridged to env vars the Electron/Chromium process
    # already reads; an explicit env var still wins over config so
    # `HERMES_DESKTOP_DISABLE_GPU=... hermes desktop` and
    # `ELECTRON_OZONE_PLATFORM_HINT=... hermes desktop` keep working.
    config_electron_flags, config_disable_gpu, config_password_store, config_ozone_hint = (
        _desktop_launch_options()
    )
    if config_disable_gpu != "auto" and not korra_env_present("KORRA_DESKTOP_DISABLE_GPU"):
        korra_env_set(env, "KORRA_DESKTOP_DISABLE_GPU", config_disable_gpu)
    if config_ozone_hint != "auto" and "ELECTRON_OZONE_PLATFORM_HINT" not in os.environ:
        env["ELECTRON_OZONE_PLATFORM_HINT"] = config_ozone_hint

    # Linux keychain backend for safeStorage (`desktop.password_store`).
    # Chromium needs the --password-store switch to pick the right keychain;
    # without it safeStorage.isEncryptionAvailable() is often false and the
    # desktop app refuses to persist remote gateway tokens. Config wins over
    # detection; an explicit env var wins over both so
    # `HERMES_DESKTOP_PASSWORD_STORE=... hermes desktop` keeps working.
    if sys.platform == "linux" and not korra_env_present("KORRA_DESKTOP_PASSWORD_STORE"):
        password_store = (
            config_password_store
            if config_password_store != "auto"
            else _detect_linux_password_store()
        )
        if password_store:
            korra_env_set(env, "KORRA_DESKTOP_PASSWORD_STORE", password_store)

    source_mode = getattr(args, "source", False)
    skip_build = getattr(args, "skip_build", False)
    force_build = getattr(args, "force_build", False)

    # macOS-only one-shot: create a self-signed code-signing identity so TCC
    # grants survive rebuilds, then exit without building/launching.
    if getattr(args, "setup_tcc_identity", False):
        identity = getattr(args, "identity", None) or "Hermes Local Signing"
        ok = _desktop_macos_setup_tcc_identity(identity)
        sys.exit(0 if ok else 1)

    packaged_executable = _desktop_packaged_executable(desktop_dir)

    if source_mode or not skip_build:
        npm = _resolve_node_runtime_npm()
        if not npm:
            print('Для приложения нужны Node.js и npm, но npm не найден в PATH.')
            print('Установите Node.js, затем выполните korra gui.')
            sys.exit(1)
    else:
        npm = None

    if skip_build:
        if source_mode:
            if not _desktop_dist_exists(desktop_dir):
                print(f"✗ Указаны --skip-build --source, но dist приложения не найден: {desktop_dir / 'dist'}")
                print('  Сначала соберите: cd apps/desktop && npm run build')
                print('  Или уберите --skip-build для автоматической установки зависимостей и сборки.')
                sys.exit(1)
            if not (_electron_dir(PROJECT_ROOT) / "package.json").exists():
                print('✗ Для --skip-build --source нужны уже установленные зависимости приложения.')
                print(f'  Сначала установите: cd {PROJECT_ROOT} && npm ci')
                print('  Или уберите --skip-build для автоматической установки зависимостей и сборки.')
                sys.exit(1)
            print(f"→ Сборка исходников пропущена (--skip-build --source); используем dist из {desktop_dir / 'dist'}")
        elif packaged_executable is None:
            print(f"✗ Указан --skip-build, но готовое приложение не найдено: {desktop_dir / 'release'}")
            print('  Сначала соберите: cd apps/desktop && npm run pack')
            print('  Или уберите --skip-build для автоматической упаковки.')
            sys.exit(1)
        else:
            print(f'→ Упаковка приложения пропущена (--skip-build); используем {packaged_executable}')
    else:
        # Check the content-hash stamp before doing any build work.
        # If the source tree hasn't changed since the last successful build,
        # skip the npm install + build entirely (saves a ton of useless work).
        # --force-build overrides the stamp and always rebuilds.
        build_needed = force_build or _desktop_build_needed(
            desktop_dir, PROJECT_ROOT, source_mode=source_mode
        )
        if not build_needed:
            build_label = 'сборка исходников' if source_mode else 'готовое приложение'
            print(f'✓ Приложение {build_label} актуально; содержимое не изменилось')
        else:
            print('→ Устанавливаем зависимости приложения…')
            # Put the Hermes-managed Node on PATH so npm's child scripts (which
            # shell out to bare `node`, e.g. electron-winstaller's
            # select-7z-arch.js) resolve it even when the parent PATH is
            # stripped — the desktop updater chain (Desktop → hermes-setup →
            # hermes update) loses shell PATH customizations. Wrapping the
            # NixOS build env keeps its PYTHON hint while restoring managed Node
            # ahead of a bare PATH (same idiom as the `hermes update` path).
            nixos_env = with_hermes_node_path(_nixos_build_env())
            install_result = _run_npm_install_deterministic(npm, PROJECT_ROOT, capture_output=False, env=nixos_env)
            if install_result.returncode != 0:
                if not _electron_pkg_staged_missing_dist(PROJECT_ROOT):
                    print('✗ Не удалось установить зависимости приложения')
                    print(f'  Выполните вручную: cd {PROJECT_ROOT} && npm ci')
                    sys.exit(install_result.returncode or 1)
                repaired = _try_redownload_electron_dist(PROJECT_ROOT, env)
                if repaired:
                    print('  ⚠ При установке отсутствовал dist Electron; он восстановлен, продолжаем.')
                else:
                    print('  ⚠ При установке отсутствовал dist Electron. Продолжаем сборку, чтобы electron-builder попробовал загрузить его сам.')

            build_label = 'сборка исходников' if source_mode else 'готовое приложение'
            print(f'→ Собираем приложение {build_label}…')
            build_script = "build" if source_mode else "pack"
            if _force_adhoc_macos_signing(env, source_mode=source_mode):
                print('  → Developer ID не настроен; временная подпись локальной сборки, CSC_IDENTITY_AUTO_DISCOVERY=false')
            npm_build_env = _npm_lifecycle_env(env)
            if not source_mode:
                # A running desktop instance launched from release/win-unpacked
                # holds Hermes.exe locked on Windows, so the pack can't replace
                # it ("Access is denied" / ERR_ELECTRON_BUILDER_CANNOT_EXECUTE).
                # Stop it first so the rebuild — including the installer's
                # headless --update rebuild — succeeds instead of failing cryptically.
                stopped = _stop_desktop_processes_locking_build(desktop_dir)
                if stopped:
                    print(f"  ⚠ Работающее приложение остановлено, чтобы освободить файлы сборки; PID {', '.join(map(str, stopped))}")
            build_result = subprocess.run(
                [npm, "run", build_script], cwd=desktop_dir, env=npm_build_env, check=False
            )
            if (
                build_result.returncode != 0
                and not source_mode
                and _desktop_packaged_executable(desktop_dir) is None
            ):
                # Corrupt cached Electron zip → partial unpack → ENOENT on rename.
                # stdlib zipfile won't catch the common concat-junk case, so purge
                # and retry once; @electron/get SHASUM is the real gate.
                #
                # Gate on a MISSING packaged executable: that is the signature of
                # the corrupt-download class this recovery exists for. A late
                # failure such as macOS code signing leaves the executable in
                # place — redownloading Electron can't repair it, so the purge +
                # retry would only add another slow, identical failure (#40187).
                purged: list[Path] = []
                restored = False
                if not _electron_dist_ok(PROJECT_ROOT):
                    purged = _purge_electron_build_cache(desktop_dir)
                    restored = _redownload_electron_dist(PROJECT_ROOT, env)
                if restored:
                    print('  ⚠ Сборка приложения не удалась. Обновили загрузку Electron и пробуем ещё раз…')
                    for p in purged:
                        print(f"    - {p}")
                    # The purge can't remove a win-unpacked tree whose Hermes.exe
                    # is still locked by a running instance; stop it before retry.
                    _stop_desktop_processes_locking_build(desktop_dir)
                    build_result = subprocess.run(
                        [npm, "run", build_script], cwd=desktop_dir, env=npm_build_env, check=False
                    )
            if (
                build_result.returncode != 0
                and not source_mode
                and not env.get("ELECTRON_MIRROR")
                and _desktop_packaged_executable(desktop_dir) is None
            ):
                print('  ⚠ Сборка снова не удалась: похоже, GitHub блокирует загрузку Electron. Загружаем через зеркало npmmirror.com. Другое зеркало можно указать в ELECTRON_MIRROR.')
                mirror = _ELECTRON_FALLBACK_MIRROR
                mirror_env = dict(npm_build_env)
                mirror_env["ELECTRON_MIRROR"] = mirror
                if not _electron_dist_ok(PROJECT_ROOT):
                    _redownload_electron_dist(PROJECT_ROOT, env, mirror=mirror)
                _stop_desktop_processes_locking_build(desktop_dir)
                build_result = subprocess.run([npm, "run", build_script], cwd=desktop_dir, env=mirror_env, check=False)
            if build_result.returncode != 0:
                print('✗ Не удалось собрать приложение')
                print(f'  Выполните вручную: cd apps/desktop && npm run {build_script}')
                if sys.platform == "win32":
                    print('  Если в ошибке указано «Access is denied» для файла приложения,')
                    print('  закройте все окна приложения Корры и повторите попытку.')
                print('  Если журнал показывает повторные загрузки Electron, используйте зеркало:')
                print('    ELECTRON_MIRROR=<mirror-base-url> korra desktop --force-build')
                sys.exit(build_result.returncode or 1)
            packaged_executable = _desktop_packaged_executable(desktop_dir)
            if not source_mode:
                # Locally-built apps are ad-hoc signed; make them relaunchable after
                # an in-place self-update (otherwise macOS reports "Hermes is
                # damaged"). No-op on non-macOS and on real-identity builds.
                _desktop_macos_relaunchable_fixup(desktop_dir)

                # Windows integrity gate (#69179): never declare the rebuild a
                # success on a Hermes.exe Windows cannot load (truncated PE from
                # a corrupt cached Electron zip, wrong-arch tree, interrupted
                # rcedit rewrite). Roll back to the .bak tree preserved by
                # before-pack.mjs when possible, then fail loudly so the
                # updater's retry-once rebuilds from a fresh Electron download
                # instead of silently shipping the broken exe.
                verified_executable, rolled_back = _ensure_desktop_exe_launchable(
                    desktop_dir, packaged_executable
                )
                if packaged_executable is not None and (
                    rolled_back or verified_executable is None
                ):
                    sys.exit(1)
                packaged_executable = verified_executable

            # Build succeeded — write the stamp so next run can skip
            _write_desktop_build_stamp(PROJECT_ROOT, source_mode=source_mode)

    # Linux: register the app in the desktop launcher, so Hermes shows up
    # in the application menu with its icon. Best-effort and idempotent.
    # A failure must never stop the app from launching.
    _register_linux_desktop_entry()

    # --build-only: produce the artifact but do NOT launch. The installer's
    # --update flow drives the rebuild headlessly and then launches the desktop
    # itself (detached, after the old exe has exited), so the launch must NOT
    # happen here — it would block the installer and, on Windows, the old exe
    # is still being replaced. Verify the expected artifact exists so a silent
    # "built nothing" can't slip past, then return success.
    if getattr(args, "build_only", False):
        if source_mode:
            if not _desktop_dist_exists(desktop_dir):
                print(f"✗ --build-only --source не создал dist: {desktop_dir / 'dist'}")
                sys.exit(1)
            print(f"✓ Исходники приложения собраны в {desktop_dir / 'dist'}; без запуска из-за --build-only")
        elif packaged_executable is None:
            print(f"✗ --build-only не создал готовое к запуску приложение: {desktop_dir / 'release'}")
            print('  Ожидалось распакованное приложение Electron для текущей системы.')
            sys.exit(1)
        else:
            print(f'✓ Приложение готово: {packaged_executable}; не запускается из-за --build-only')
        return

    if source_mode:
        print('→ Запускаем приложение Корры из сборки исходников…')
        launch_result = subprocess.run([npm, "exec", "--", "electron", "."], cwd=desktop_dir, env=env, check=False)
        sys.exit(launch_result.returncode)

    if packaged_executable is None:
        print(f"✗ Сборка завершена, но готовое к запуску приложение не найдено: {desktop_dir / 'release'}")
        print('  Ожидалось распакованное приложение Electron для текущей системы.')
        sys.exit(1)

    launch_command = [str(packaged_executable)]
    if not _desktop_linux_sandbox_fixup(packaged_executable):
        if _desktop_linux_needs_no_sandbox() and _desktop_linux_sandbox_helper_is_regular_file(packaged_executable):
            print('⚠ Используется --no-sandbox: эта система Linux ограничивает пользовательские пространства имён, а компонент изоляции Electron настроить не удалось.')
            launch_command.append("--no-sandbox")
        else:
            sys.exit(1)

    launch_command.extend(config_electron_flags)
    print(f"→ Запускаем приложение Корры: {' '.join(launch_command)}")
    launch_result = subprocess.run(launch_command, cwd=desktop_dir, env=env, check=False)
    sys.exit(launch_result.returncode)


# Dashboard process-hygiene helpers live in korra_cli/dashboard_procs.py
# (main.py decomposition, mechanical move). Re-exported lazily through the
# module-level __getattr__ above so callers and test monkeypatches on
# korra_cli.main.<name> keep resolving unchanged.

def _find_stale_dashboard_pids(
    *,
    exclude_pids: set[int] | None = None,
) -> list[int]:
    """Return PIDs of stale ``dashboard``/``serve`` processes for update cleanup."""
    return [pid for pid, _cmd in _self()._scan_dashboard_processes(exclude_pids=exclude_pids)]


def _parse_dashboard_runtime(command: str) -> tuple[str, str, int] | None:
    """Best-effort parse of a dashboard/server cmdline into mode, host, and port."""
    mode = None
    if any(
        pattern in command
        for pattern in (
            "hermes dashboard",
            "korra_cli.main dashboard",
            "korra_cli/main.py dashboard",
        )
    ):
        mode = "dashboard"
    elif any(
        pattern in command
        for pattern in (
            "hermes serve",
            "korra_cli.main serve",
            "korra_cli/main.py serve",
        )
    ):
        mode = "serve"
    if mode is None:
        return None

    port = 9119
    host = "127.0.0.1"

    port_match = re.search(r"(?:^|\s)--port(?:=|\s+)(\d+)", command)
    if port_match:
        try:
            port = int(port_match.group(1))
        except ValueError:
            return None

    host_match = re.search(r"(?:^|\s)--host(?:=|\s+)(\"[^\"]+\"|'[^']+'|\S+)", command)
    if host_match:
        host = host_match.group(1).strip("\"'") or "127.0.0.1"

    return mode, host, port


def _dashboard_probe_host(host: str | None) -> str:
    """Map wildcard binds to a loopback address suitable for local probing."""
    normalized = (host or "127.0.0.1").strip().strip("[]")
    if normalized in {"", "0.0.0.0", "::"}:
        return "127.0.0.1"
    return normalized


_DASHBOARD_SYSTEMD_UNIT = "hermes-dashboard.service"


def _restart_managed_dashboard_service(
    reason: str,
    unit: str = _DASHBOARD_SYSTEMD_UNIT,
) -> bool:
    """Restart a systemd-managed dashboard instead of raw-killing its PID.

    Returns True when a dashboard unit was found and handled (successfully or
    with a printed actionable failure).  Returning True deliberately prevents
    the caller from falling back to ``os.kill``: systemd treats a direct
    SIGTERM of the service's main PID as a clean stop, so ``Restart=on-failure``
    will not bring the dashboard back.
    """
    if sys.platform == "win32":
        return False

    def _systemctl(*args: str, timeout: int = 10) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )

    # Probe the user manager first: Hermes installs Linux services in the
    # user's systemd scope by default.  Only fall back to the system manager
    # when the unit is not present there, preserving root/system deployments.
    # Crucially, keep the selected scope for *all* probes and the restart — a
    # user unit must never be restarted through the system manager (or raw-killed).
    scope: tuple[str, ...] | None = None
    listed: subprocess.CompletedProcess | None = None
    for candidate in (("--user",), ()):
        try:
            result = _systemctl(
                *candidate, "list-unit-files", unit, "--no-legend", "--no-pager"
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode != 0:
            continue
        unit_rows = (result.stdout or "").splitlines()
        if any(row.split()[0:1] == [unit] for row in unit_rows if row.split()):
            scope = candidate
            listed = result
            break

    if scope is None or listed is None:
        return False

    try:
        active = _systemctl(*scope, "is-active", unit)
        enabled = _systemctl(*scope, "is-enabled", unit)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

    active_state = (active.stdout or "").strip()
    enabled_state = (enabled.stdout or "").strip()
    if active_state != "active" and enabled_state not in {
        "enabled",
        "enabled-runtime",
        "linked",
        "linked-runtime",
        "static",
        "generated",
    }:
        return False

    print()
    print(f'⟲ Перезапускаем управляемую службу веб-панели: {reason}')

    scope_label = "systemctl --user" if scope else "sudo systemctl"
    restart = ("systemctl", *scope, "restart", unit)
    commands = [restart]
    if not scope:
        # System units may require privilege escalation; user units must use
        # the user manager directly and never prompt for sudo.
        commands.append(("sudo", "-n", "systemctl", "restart", unit))

    errors: list[str] = []
    for command in commands:
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            errors.append(f"{' '.join(command)}: {e}")
            continue
        if result.returncode == 0:
            print(f'    ✓ Перезапущено: {unit}')
            return True
        errors.append(
            f"{' '.join(command)}: {(result.stderr or result.stdout or '').strip()}"
        )

    print(f'    ✗ Не удалось перезапустить {unit}')
    for err in errors:
        if err.strip():
            print(f"      {err}")
    print(
        '  Веб-панель управляется systemd. Для корректного перезапуска используйте команду службы.'
    )
    print(f'  Перезапуск вручную: {scope_label} restart {unit}')
    return True


def _get_systemd_service_for_pid(pid: int) -> str | None:
    """If *pid* belongs to a systemd service unit, return the unit name.

    Reads ``/proc/<pid>/cgroup`` and extracts the service name (e.g.
    ``hermes-serve.service``).  Returns ``None`` when the PID is not
    part of a systemd service, when the file is unreadable, or on
    non-Linux platforms.
    """
    try:
        cgroup_path = Path(f"/proc/{pid}/cgroup")
        if not cgroup_path.is_file():
            return None
        text = cgroup_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            # Format: 0::/system.slice/hermes-serve.service
            #         0::/user.slice/user-1000.slice/session-42.scope
            parts = line.split("::", 1)
            if len(parts) != 2:
                continue
            cg_path = parts[1]
            if cg_path.endswith(".service"):
                svc_name = cg_path.rsplit("/", 1)[-1]
                if svc_name:
                    return svc_name
    except (OSError, PermissionError):
        pass
    return None


def _extract_scope_from_cgroup(cgroup_entry: str) -> str | None:
    """Extract the systemd scope (``user`` or ``system``) from a cgroup path.

    The cgroup path format is ``/system.slice/<name>.service`` for system
    services and ``/user.slice/user-<uid>.slice/<name>.service`` for user
    services.  Returns ``None`` when the scope cannot be determined.
    """
    if "/system.slice/" in cgroup_entry:
        return "system"
    if "/user.slice/" in cgroup_entry:
        return "user"
    return None


def _get_pid_cgroup_path(pid: int) -> str | None:
    """Return the cgroup path from ``/proc/<pid>/cgroup``, or ``None``.

    Only the unified (``0::``) hierarchy cgroup entry is examined.
    """
    try:
        cgroup_path = Path(f"/proc/{pid}/cgroup")
        if not cgroup_path.is_file():
            return None
        text = cgroup_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            parts = line.split("::", 1)
            if len(parts) == 2:
                return parts[1]
    except (OSError, PermissionError):
        pass
    return None


def _try_restart_systemd_service(svc_name: str, cgroup_path: str | None = None) -> bool:
    """Attempt to restart *svc_name* via systemctl.

    Uses ``systemctl --user`` for user-scope services and ``systemctl``
    for system-scope services.  Returns ``True`` on success.
    """
    scope = _extract_scope_from_cgroup(cgroup_path) if cgroup_path else None
    if scope == "user":
        cmd = ["systemctl", "--user", "restart", svc_name]
    elif scope == "system":
        cmd = ["systemctl", "restart", svc_name]
    else:
        # Unknown scope — try system first, then user
        cmd = None
        for candidate in (
            ["systemctl", "restart", svc_name],
            ["systemctl", "--user", "restart", svc_name],
        ):
            try:
                r = subprocess.run(
                    candidate,
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=15,
                )
                if r.returncode == 0:
                    return True
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                continue
        return False

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=15,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _dashboard_cmdline_for_pid(pid: int) -> list[str] | None:
    """Return the exact argv of a running process, when recoverable.

    Linux: reads ``/proc/<pid>/cmdline`` (NUL-separated, lossless).
    macOS: falls back to ``ps -o command=`` + shlex (best effort — quoting
    is reconstructed, but hermes launch commands don't embed exotic args).
    Windows: returns ``None``; taskkill /F gives no graceful window and the
    desktop app manages its own backend there.
    """
    if sys.platform == "win32":
        return None
    try:
        cmdline_path = f"/proc/{pid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path, "rb") as f:
                raw = f.read()
            argv = [
                part.decode("utf-8", errors="replace")
                for part in raw.split(b"\x00")
                if part
            ]
            return argv or None
        # macOS (no /proc): best-effort via ps.
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
        if result.returncode != 0:
            return None
        command = (result.stdout or "").strip()
        if not command:
            return None
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = command.split()
        return argv or None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _respawn_dashboard_processes(commands: list[list[str]]) -> list[list[str]]:
    """Best-effort respawn of manually-started dashboards after ``hermes update``.

    Spawns each recovered argv detached (new session, output to the profile's
    ``logs/dashboard-restart.log``).  Returns the commands that failed to
    spawn; the caller prints the manual hint for those.

    Callers must pre-filter via ``_filter_dashboard_respawn_candidates`` so
    Desktop ``serve|dashboard --port 0`` backends are not replayed and
    duplicates are capped per profile (#78821).
    """
    from korra_constants import get_hermes_home

    respawned: list[list[str]] = []
    failed: list[tuple[list[str], str]] = []
    log_path = get_hermes_home() / "logs" / "dashboard-restart.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    for command in commands:
        try:
            # Keep restarted dashboards headless; reopening a browser after a
            # background update is noisy and fails in SSH/headless sessions.
            if "dashboard" in command and "--no-open" not in command:
                command = [*command, "--no-open"]
            with open(log_path, "ab") as log_f:
                subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            respawned.append(command)
        except (OSError, ValueError) as exc:
            failed.append((command, str(exc)))

    for command in respawned:
        print(f'    ✓ Перезапущено: {shlex.join(command)}')
    for command, err_msg in failed:
        print(f'    ✗ Не удалось перезапустить ({shlex.join(command)}): {err_msg}')
    return [command for command, _ in failed]


# Back-compat alias: some tests and any external callers may import the old
# warn-only name.  The new behaviour (kill stale processes) replaces it.
# Resolved lazily via _LAZY_COMMAND_ALIASES near the module __getattr__.


# =========================================================================
# Fork detection and upstream management for `hermes update`
# =========================================================================


def _load_installable_optional_extras(group: str = "all") -> list[str]:
    """Return optional extras referenced by a dependency group.

    ``group`` is usually ``all`` (desktop/server broad install) or
    ``termux-all`` (Termux-compatible broad install).
    """
    try:
        import tomllib

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
    except Exception:
        return []

    optional_deps = project.get("optional-dependencies", {})
    if not isinstance(optional_deps, dict):
        return []

    refs = optional_deps.get(group, [])
    referenced: list[str] = []
    for ref in refs:
        if "[" in ref and "]" in ref:
            name = ref.split("[", 1)[1].split("]", 1)[0]
            if name in optional_deps:
                referenced.append(name)

    return referenced


# Install-scoped breadcrumbs live next to the venv (not under $HERMES_HOME)
# because the venv is shared across profiles.
#
# ``.update-incomplete`` — generic core ``.[all]`` install was interrupted.
# Cleared only after a confirmed full dependency reinstall/recovery.
#
# ``.lazy-refresh-incomplete`` — lazy-backend refresh phase may have corrupted
# packages. Cleared only after import-probe repair confirms healthy (not when
# probes are unavailable/indeterminate). Narrow lazy probes must NEVER clear
# the generic core marker (#58004 review).
def _update_marker_path() -> Path:
    return PROJECT_ROOT / ".update-incomplete"


def _lazy_refresh_marker_path() -> Path:
    return PROJECT_ROOT / ".lazy-refresh-incomplete"


def _pytest_owns_live_checkout(root: Path) -> bool:
    """True when running under pytest AND ``root`` is this checkout itself.

    Tests that drive update/recovery without sandboxing ``PROJECT_ROOT``
    must neither litter the live repo root with recovery breadcrumbs
    (a leftover ``.lazy-refresh-incomplete`` / ``.update-incomplete``
    false-arms recovery on the developer's next real launch) nor run a real
    reinstall against the executing venv. Sandboxed tests point at a
    tmp_path and are unaffected (same posture as
    ``managed_scope._under_pytest``)."""
    return (
        "PYTEST_CURRENT_TEST" in os.environ
        and root == Path(__file__).resolve().parent.parent
    )


def _clear_marker_file(path: Path, *, label: str) -> None:
    """Remove an update-recovery breadcrumb. Never raises."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not clear %s marker: %s", label, exc)


def _clear_update_incomplete_marker() -> None:
    """Remove the interrupted core-install breadcrumb. Never raises."""
    _clear_marker_file(_update_marker_path(), label="update-incomplete")


def _clear_lazy_refresh_incomplete_marker() -> None:
    """Remove the interrupted lazy-refresh breadcrumb. Never raises."""
    _clear_marker_file(_lazy_refresh_marker_path(), label="lazy-refresh-incomplete")


def _recover_from_interrupted_install() -> None:
    """Finish update work left half-done by a prior ``hermes update``.

    Handles two independent breadcrumbs:

    - ``.update-incomplete`` — core ``.[all]`` install interrupted. Recovers
      via full quarantined reinstall. Never cleared by the narrow lazy-refresh
      import probes alone.
    - ``.lazy-refresh-incomplete`` — lazy-backend refresh may have corrupted
      packages. Recovers via package-only import probes; cleared only when
      probes confirm healthy/repaired (indeterminate keeps the marker).

    Never raises: a recovery failure must not block launch.  If it can't
    self-heal it prints the manual command and leaves the relevant marker so
    the next launch tries again.

    Concurrency: markers live next to the shared venv, so a gateway start
    plus a CLI launch (or two profiles starting at once) can both see them.
    An ``O_EXCL`` lockfile ensures only one process runs recovery; the
    others skip and let the winner clear markers.

    Output: everything — our status lines AND the streamed pip/uv install
    (which inherits fd 1) — is routed to stderr.  Launches whose stdout is a
    protocol stream (``hermes acp`` speaks JSON-RPC on stdout) must never get
    install noise on stdout.
    """
    if _pytest_owns_live_checkout(PROJECT_ROOT):
        return
    core_marker = _update_marker_path().exists()
    lazy_marker = _lazy_refresh_marker_path().exists()
    if not core_marker and not lazy_marker:
        return

    # Skip in managed/Docker installs and on PyPI installs with no git checkout:
    # those don't run the source-tree update path, so a stray marker is not ours
    # to act on. Just clear it.
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        _clear_update_incomplete_marker()
        _clear_lazy_refresh_incomplete_marker()
        return

    # Single-flight guard: atomically claim the recovery lock. If another
    # process holds it, skip — it is running the same reinstall into the same
    # shared venv right now. A crashed holder leaves a stale lock; break it
    # after an hour (well past any realistic install) so recovery can't be
    # wedged forever.
    lock_path = PROJECT_ROOT / ".update-incomplete.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
    except FileExistsError:
        try:
            if _time.time() - lock_path.stat().st_mtime > 3600:
                lock_path.unlink()
        except OSError:
            pass
        return
    except OSError as exc:
        # Couldn't create the lock (read-only fs, perms). Proceed unlocked —
        # the install itself will surface the real problem.
        logger.debug("Could not create install-recovery lock: %s", exc)

    saved_stdout_fd = None
    saved_sys_stdout = sys.stdout
    try:
        # Route Python-level prints AND subprocess-inherited fd 1 to stderr
        # for the duration of recovery (see docstring: ACP stdout safety).
        try:
            saved_stdout_fd = os.dup(1)
            os.dup2(2, 1)
        except OSError:
            saved_stdout_fd = None
        sys.stdout = sys.stderr

        if lazy_marker:
            _recover_lazy_refresh_marker_locked()

        if _update_marker_path().exists():
            _recover_core_update_marker_locked()
    finally:
        sys.stdout = saved_sys_stdout
        if saved_stdout_fd is not None:
            try:
                os.dup2(saved_stdout_fd, 1)
                os.close(saved_stdout_fd)
            except OSError:
                pass
        try:
            lock_path.unlink()
        except OSError:
            pass


def _recover_lazy_refresh_marker_locked() -> None:
    """Heal ``.lazy-refresh-incomplete`` via confirmed import-probe repair."""
    print(
        '⚠ Предыдущее обновление инструментов могло повредить окружение Python. Проверяем загрузку пакетов и восстанавливаем…'
    )
    install_prefix, install_env = _default_venv_install_target()
    status = _repair_venv_via_import_probes(install_prefix, env=install_env)
    if status in ("healthy", "repaired"):
        _clear_lazy_refresh_incomplete_marker()
        print('✓ Окружение после обновления инструментов восстановлено и исправно.')
        return
    if status == "indeterminate":
        print(
            '  ⚠ Проверка загрузки пакетов недоступна. Сохраняем .lazy-refresh-incomplete для повторной проверки при запуске.'
        )
    else:
        print(
            '  ⚠ Пакеты восстановлены не полностью. Сохраняем .lazy-refresh-incomplete для следующего запуска.'
        )
        print('  Восстановление вручную:')
        all_specs = _lazy_refresh_repair_specs(
            sorted(set(_LAZY_REFRESH_REPAIR_PACKAGES.values()))
        )
        print(
            f"    {' '.join(install_prefix)} install --force-reinstall "
            + " ".join(shlex.quote(s) for s in all_specs)
        )


def _recover_core_update_marker_locked() -> None:
    """Heal ``.update-incomplete`` via full ``.[all]`` reinstall only.

    Narrow lazy-refresh import probes are not sufficient proof that a generic
    interrupted core install finished — a missing dep outside that probe set
    would otherwise look healthy and clear the breadcrumb too early.
    """
    print(
        '⚠ Предыдущее korra update прервалось при установке. Завершаем установку зависимостей…'
    )

    # Windows: a normal ``hermes.exe`` launch always has the launcher as an
    # ancestor. Full editable reinstall uses quarantine so the live shim can
    # still be replaced. Package-only import repair may help as first aid but
    # must NEVER clear this core marker on its own (#58004 review).
    self_locked = _windows_running_hermes_launcher_locked()
    if self_locked:
        install_prefix, install_env = _default_venv_install_target()
        print(
            '  → Запуск из hermes.exe: сначала восстанавливаем пакеты, затем переустанавливаем полностью с резервной копией. Метка незавершённого обновления сохранится до успеха.'
        )
        _repair_venv_via_import_probes(install_prefix, env=install_env)

    try:
        from korra_cli import _install_repair as _ir

        # ensure_uv bootstraps the installer itself when missing (the early
        # pass's stdlib-only lookup cannot); keeping it here means the late
        # path still self-heals a venv whose uv vanished mid-update.
        from korra_cli.managed_uv import ensure_uv

        ensure_uv()

        # Delegate the install itself to the shared stdlib executor so both
        # this late path and the pre-import early pass run exactly the same
        # reinstall.  Called inside the same stdout→stderr redirect already
        # established by _recover_from_interrupted_install, so
        # run_core_install's own redirect nests harmlessly.
        _ir.run_core_install(PROJECT_ROOT)

        _clear_update_incomplete_marker()
        print('✓ Установка зависимостей восстановлена. Корра снова исправна.')
    except Exception as exc:
        # Leave the marker in place so the next launch retries. Give the user
        # the exact manual recovery command in the meantime.
        logger.debug("Interrupted-install recovery failed: %s", exc)
        print('✗ Не удалось автоматически восстановить прерванную установку.')
        if self_locked:
            print(
                '  Корра ещё работает через команду, которую нужно заменить. Закройте другие окна, откройте другой терминал и выполните:'
            )
            print(f'    cd /d "{PROJECT_ROOT}"')
            print(
                f'    "{sys.executable}" -m pip install -e ".[all]"'
            )
        else:
            print('  Восстановление вручную:')
            print(f"    cd {PROJECT_ROOT}")
            print(f"    {sys.executable} -m ensurepip --upgrade")
            print(f"    {sys.executable} -m pip install -e '.[all]'")


def _norm_exe_path(path) -> str:
    """Case-folded resolved path, for comparing executables on Windows."""
    try:
        return str(Path(path).resolve()).lower()
    except OSError:
        return str(path).lower()


def _windows_shim_in_process_chain() -> Path | None:
    """The venv console shim this process runs from or under, if any.

    ``venv\\Scripts\\hermes.exe`` is a launcher that runs the interpreter with
    the shim itself as its script, and that keeps the shim open — without
    ``FILE_SHARE_DELETE`` — for the whole process lifetime. So every
    ``hermes ...`` command holds its own shim, and an editable install run
    from one can never rewrite it (#88838, #89599).

    Two independent probes, because either can come up empty. Process
    ancestry finds the launcher when it is a separate parent process, but
    needs psutil. This process's own launch paths (``sys.argv[0]``,
    ``__main__.__file__``, the module spec origin) cover the rest — the
    runpy/zipapp launch puts ``<shim>\\__main__.py`` there, which a plain
    argv[0] check misses.

    Candidates are intersected with the project venv's own shims, so a
    ``hermes.exe`` belonging to some other install never matches.
    """
    if not _is_windows():
        return None
    scripts_dir = _venv_scripts_dir()
    if scripts_dir is None:
        return None
    shims = {_norm_exe_path(shim): shim for shim in _hermes_exe_shims(scripts_dir)}
    if not shims:
        return None

    def _match(candidate) -> Path | None:
        path = Path(candidate)
        if path.name.lower() == "__main__.py":
            path = path.parent
        return shims.get(_norm_exe_path(path))

    candidates: list[str] = list(sys.argv[:1])
    main_mod = sys.modules.get("__main__")
    for attr in (getattr(main_mod, "__file__", None),
                 getattr(getattr(main_mod, "__spec__", None), "origin", None)):
        if attr:
            candidates.append(attr)
    for candidate in candidates:
        matched = _match(candidate)
        if matched is not None:
            return matched

    try:
        import psutil

        me = psutil.Process()
        for proc in [me] + list(me.parents()):
            try:
                matched = _match(proc.exe())
            except Exception:
                continue
            if matched is not None:
                return matched
    except Exception:
        return None
    return None


def _windows_running_hermes_launcher_locked() -> bool:
    """True when a venv ``hermes*.exe`` shim is this process or an ancestor.

    Best-effort: returns False when psutil is unavailable or inspection fails.
    """
    return _windows_shim_in_process_chain() is not None


# Set on the re-exec'd child so it can never spawn another one.
_UPDATE_REEXEC_ENV = "KORRA_UPDATE_REEXEC"


def _reexec_dependency_sync_off_windows_shim() -> bool:
    """Hand the dependency sync to the venv interpreter, off the console shim.

    Returns True when a child was spawned and the caller must exit at once,
    releasing the shim before the child reaches ``pip install -e .``. Returns
    False to continue the sync in-process.

    Called at the dependency-sync boundary, NOT at the top of the command —
    the same placement rule as the native-module deferral beside it, and for
    the same reason (#86735): a hand-off that fires before the fetch detaches
    every run, including the ``Already up to date!`` no-op that never touches
    the venv at all, and it takes the interactive prompts with it. By the time
    we reach here the code swap is done and every question — stash, branch
    switch, config migration — has already been asked and answered in the
    user's own console. Only the venv rewrite is left, and that is the single
    step that genuinely cannot run from inside the shim.

    ``venv\\Scripts\\hermes.exe`` is a launcher that runs the interpreter with
    the shim as its script and holds it open without ``FILE_SHARE_DELETE`` for
    the whole command, so the quarantine rename is refused and uv fails to
    replace it with os error 32 (#88838, #89599).

    A child is required, and waiting on it cannot work: this process holds the
    handle the child needs released, so a parent that waits deadlocks against
    the work it is waiting for. Windows has no exec to escape with either.
    The shell therefore returns while the install runs on; the child keeps the
    console and prints its own result, and ``--gateway`` writes the true exit
    code to ``.update_exit_code`` for the gateway watcher.

    The child re-runs ``hermes update``, so the whole remaining flow — the
    dependency sync and the node/web/lazy-refresh tail behind it — still
    happens exactly once. ``_UPDATE_REEXEC_ENV`` marks it so it cannot spawn
    another child, and so the "already up to date" early return does not
    swallow the sync it was spawned to perform (the checkout is current by
    now; that is the point).

    The caller has already written ``.update-incomplete``, so a child that
    dies mid-install is finished by the next launch's recovery instead of
    leaving a half-synced venv. Anything that stops the hand-off (no venv
    python, spawn refused) returns False and syncs in-process, where the
    pre-existing os-error-32 path and its marker recovery still apply.
    """
    if korra_env(_UPDATE_REEXEC_ENV) == "1":
        return False
    shim = _windows_shim_in_process_chain()
    if shim is None:
        return False

    from korra_constants import venv_python_path

    python_exe = venv_python_path(shim.parent.parent, windows=True)
    cmd = [str(python_exe), "-m", "korra_cli.main", *sys.argv[1:]]
    if python_exe.is_file():
        try:
            subprocess.Popen(
                cmd,
                env={**os.environ, _UPDATE_REEXEC_ENV: "1"},
                stdin=subprocess.DEVNULL,
            )
            print(
                f'→ Windows: {shim.name} не может заменить себя во время работы. Завершаем установку зависимостей через Python виртуального окружения.'
            )
            print(
                '  Код уже обновлён. Установка продолжается ниже, терминал сразу вернёт управление.'
            )
            return True
        except OSError as exc:
            logger.debug("Dependency-sync hand-off via %s failed: %s", python_exe, exc)
        print(f'  ⚠ Не удалось передать установку зависимостей {shim.name}.')
        print('    Продолжаем в текущем процессе. Если не удастся заменить команду, выполните:')
        print(f"    {subprocess.list2cmdline(cmd)}")
    return False


def _default_venv_install_target() -> tuple[list[str], dict[str, str] | None]:
    """Return ``(install_cmd_prefix, env)`` for the project venv when possible."""
    try:
        from korra_cli.managed_uv import ensure_uv

        uv_bin = ensure_uv()
    except Exception:
        uv_bin = None
    if uv_bin:
        from korra_constants import project_venv_dir

        venv_dir = project_venv_dir(PROJECT_ROOT) or PROJECT_ROOT / "venv"
        env = {**os.environ, "VIRTUAL_ENV": str(venv_dir)}
        if _is_termux_env(env):
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
        return [uv_bin, "pip"], env
    return [sys.executable, "-m", "pip"], None


def _run_install_with_heartbeat(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    heartbeat_interval_seconds: int = 30,
) -> None:
    """Run dependency install command with periodic heartbeat output.

    Some resolvers/build backends (especially when compiling Rust/C extensions)
    can stay quiet for minutes. Emit a simple elapsed-time heartbeat so users
    know ``hermes update`` is still progressing even if pip/uv itself is silent.
    """
    done = threading.Event()
    start = _time.time()

    def _heartbeat() -> None:
        # Wait first, then print, so short installs don't emit noise.
        while not done.wait(heartbeat_interval_seconds):
            elapsed = int(_time.time() - start)
            print(
                f'  … Зависимости ещё устанавливаются: прошло {elapsed} с. Сборка расширений Rust/C может занять несколько минут.',
                flush=True,
            )

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    try:
        subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            check=True,
            env=env,
        )
    finally:
        done.set()
        t.join(timeout=0.2)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _venv_scripts_dir() -> Path | None:
    """Return the venv Scripts directory if we're running inside the project venv."""
    from korra_constants import project_venv_dir, venv_bin_dir

    venv_dir = project_venv_dir(PROJECT_ROOT)
    if venv_dir is None:
        return None

    scripts = venv_bin_dir(venv_dir, windows=_is_windows())
    return scripts if scripts.is_dir() else None


def _hermes_exe_shims(scripts_dir: Path) -> list[Path]:
    """Entry-point shims that uv may try to rewrite during ``pip install -e .``.

    On Windows these are .exe launchers generated by setuptools/uv. On POSIX
    they're regular Python scripts which can be replaced atomically — no
    self-replacement hazard exists outside Windows.
    """
    if not _is_windows():
        return []

    names = set(_load_console_script_names()) or {"hermes", "hermes-agent", "hermes-acp"}
    # The gateway shim is not a [project.scripts] entry point, but older
    # update/install paths still rewrite and quarantine it.
    names.add("hermes-gateway")
    return [scripts_dir / f"{name}.exe" for name in sorted(names)]


def _quarantine_running_hermes_exe(
    scripts_dir: Path, *, max_attempts: int = 4,
    failed_out: list[str] | None = None,
) -> list[tuple[Path, Path]]:
    """Pre-empt Windows file lock on the running ``hermes.exe``.

    Windows allows RENAMING a mapped/running executable (the kernel tracks the
    file by handle, not path), but blocks DELETE/REPLACE while it's loaded. uv
    needs to overwrite the entry-point shims during ``pip install -e .``;
    when ``hermes update`` runs, ``hermes.exe`` IS the live process, and uv
    fails with ``Access is denied. (os error 5)``.

    We rename live shims to ``hermes.exe.old.<unix-ms>`` first. uv then writes
    fresh shims at the original paths. The ``.old`` files are cleaned up on
    the next hermes invocation by ``_cleanup_quarantined_exes``.

    Rename can still fail when *another* process has opened the .exe without
    ``FILE_SHARE_DELETE`` — typically AV real-time scanners with transient
    handles (recovers in <1s), or the Hermes Desktop backend child process
    (won't recover until the user closes it). We mitigate:

    1. Retry up to ``max_attempts`` times with exponential backoff
       (100/250/500/1000 ms). Handles the AV-scanner case.
    2. If all retries fail, print a clear warning naming the most likely
       culprit (running Hermes Desktop / gateway / REPL).

    The updater's own launcher is no longer one of those culprits: an update
    started from ``hermes.exe`` re-runs itself under the venv Python before
    reaching here (``_reexec_dependency_sync_off_windows_shim``).

    Returns the list of (original, quarantined) pairs so the caller can roll
    back if the install itself fails before uv writes a replacement.

    ``failed_out``: when provided, the names of shims whose rename failed on
    every attempt are appended — callers that must not mutate a contended
    venv (the update dependency sync, #87331) check it and refuse instead of
    letting the install run into a half-broken state.
    """
    moved: list[tuple[Path, Path]] = []
    if not _is_windows():
        return moved

    import time

    stamp = int(time.time() * 1000)
    # Backoff schedule: first attempt is immediate, subsequent ones sleep.
    # 100ms / 250ms / 500ms covers the typical AV scanner re-scan window.
    backoff_ms = [0, 100, 250, 500, 1000]
    attempts = max(1, min(max_attempts, len(backoff_ms)))

    for shim in _hermes_exe_shims(scripts_dir):
        if not shim.exists():
            continue
        target = shim.with_suffix(shim.suffix + f".old.{stamp}")

        last_exc: OSError | None = None
        for attempt in range(attempts):
            delay = backoff_ms[attempt] / 1000.0
            if delay:
                time.sleep(delay)
            try:
                shim.rename(target)
                moved.append((shim, target))
                last_exc = None
                break
            except OSError as e:
                last_exc = e
                continue

        if last_exc is None:
            continue

        # Every rename failed. Deferring one to next boot via
        # MOVEFILE_DELAY_UNTIL_REBOOT used to be the fallback here, but it
        # cannot help: it needs elevation we don't have, and when it does
        # land it frees nothing for the install running right now while
        # queueing an operation that will move a later, freshly repaired shim
        # aside at next boot. Report and let uv try its luck instead —
        # sometimes its own retry handling pulls through.
        print(
            f'  ⚠ Не удалось переместить {shim.name} в резервную папку: {last_exc.__class__.__name__}; файл открыт другим процессом.'
        )
        print(
            '    Закройте приложение Корры и другие её терминалы, остановите шлюз или приостановите проверку антивирусом, затем повторите korra update.'
        )
        if failed_out is not None:
            failed_out.append(shim.name)

    return moved


_PENDING_RENAME_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager"
_PENDING_RENAME_VALUE = "PendingFileRenameOperations"


def _filter_pending_shim_renames(
    entries: list[str], shims: list[Path]
) -> tuple[list[str], int]:
    """Drop shim-quarantine pairs from a PendingFileRenameOperations value.

    The value is a flat REG_MULTI_SZ of (source, target) pairs, and other
    installers share it, so only pairs matching our own
    ``<shim>`` -> ``<shim>.old.<stamp>`` naming are removed. Returns the
    entries to keep and how many pairs were dropped.
    """
    import ntpath

    def _norm(value: str) -> str:
        path = str(value).lstrip("!")
        if path.startswith("\\??\\"):
            path = path[4:]
        return ntpath.normcase(ntpath.normpath(path))

    shim_paths = {_norm(str(shim)) for shim in shims}
    kept: list[str] = []
    removed = 0
    for index in range(0, len(entries) - 1, 2):
        source, target = entries[index], entries[index + 1]
        source_norm = _norm(source)
        if source_norm in shim_paths and _norm(target).startswith(f"{source_norm}.old."):
            removed += 1
        else:
            kept.extend((source, target))
    if len(entries) % 2:
        kept.append(entries[-1])
    return kept, removed


def _cleanup_pending_shim_renames(scripts_dir: Path) -> int:
    """Drop reboot renames older Hermes versions queued for our shims.

    Hermes used to fall back to ``MoveFileExW(MOVEFILE_DELAY_UNTIL_REBOOT)``
    when the quarantine rename failed. Those entries outlive the update that
    queued them, so at the next boot they move away whatever now sits at the
    shim path — including a shim a later repair just wrote. Needs elevation
    to remove (same as it needed to create); a no-op otherwise.
    """
    if not _is_windows():
        return 0
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            _PENDING_RENAME_KEY,
            0,
            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            entries, value_type = winreg.QueryValueEx(key, _PENDING_RENAME_VALUE)
            if value_type != winreg.REG_MULTI_SZ or not isinstance(entries, list):
                return 0
            kept, removed = _filter_pending_shim_renames(
                entries, _hermes_exe_shims(scripts_dir)
            )
            if not removed:
                return 0
            if kept:
                winreg.SetValueEx(key, _PENDING_RENAME_VALUE, 0, winreg.REG_MULTI_SZ, kept)
            else:
                winreg.DeleteValue(key, _PENDING_RENAME_VALUE)
            return removed
    except (OSError, ValueError):
        return 0


def _restore_quarantined_exes(moved: list[tuple[Path, Path]]) -> None:
    """Roll back ``_quarantine_running_hermes_exe`` if uv didn't write replacements.

    This is the safety-critical direction. A failed *quarantine* only aborts an
    update; a failed *restore* leaves the install with no ``hermes`` on PATH,
    and therefore no way to run the command that would repair it (#75584). The
    outbound rename already retries a lock, so this one must too rather than
    swallow the first ``OSError`` in silence.

    Delegates to the stdlib-only helper that the early-recovery copy in
    ``_install_repair`` also uses, so the two cannot drift apart.
    """
    _early_recovery_mod.restore_quarantined_shims(moved)


class ShimQuarantineError(RuntimeError):
    """A live ``hermes*.exe`` shim could not be renamed aside (#87331).

    Raised by :func:`_run_quarantined_install` in ``strict_quarantine`` mode
    BEFORE the install command runs. A shim that cannot even be renamed means
    another process holds the venv hard enough that the dependency sync would
    die partway and strand the install half-updated — the update must refuse,
    not warn-and-continue.
    """

    def __init__(self, failed_shims: list[str]):
        self.failed_shims = list(failed_shims)
        super().__init__(
            'Не удалось переместить используемые команды в резервную папку: ' + ", ".join(self.failed_shims)
        )


def _run_quarantined_install(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    scripts_dir: Path | None = None,
    strict_quarantine: bool = False,
) -> None:
    """Run an editable install, quarantining the running ``hermes.exe`` first.

    Any ``pip install -e .`` (or ``--reinstall``) rewrites the entry-point
    shims, and on Windows the live ``hermes.exe`` is the running process —
    pip can neither delete nor overwrite it, so without quarantine the shim
    is left missing and ``hermes`` drops off PATH. This wraps
    :func:`_run_install_with_heartbeat` with the same rename-out-of-the-way /
    restore-on-failure dance that the primary install path uses, so EVERY
    install that touches the shims is protected — including the
    verification-repair reinstalls in
    :func:`_verify_core_dependencies_installed`, which previously called
    ``_run_install_with_heartbeat`` directly and bypassed quarantine.

    ``strict_quarantine=True`` (the update dependency sync, #87331): a shim
    whose rename failed every retry means a process is holding the venv
    without ``FILE_SHARE_DELETE`` — the install WILL hit the same lock on
    .pyd files and strand the venv between versions. Roll the successful
    renames back and raise :class:`ShimQuarantineError` WITHOUT running the
    install. Non-strict callers (post-sync entry-point repair) keep the old
    warn-and-try behavior: their venv is already mutated, so refusing buys
    nothing.

    Off-Windows (``scripts_dir is None``) this is a thin pass-through.
    """
    moved: list[tuple[Path, Path]] = []
    failed: list[str] = []
    if scripts_dir is not None:
        moved = _quarantine_running_hermes_exe(scripts_dir, failed_out=failed)
    if strict_quarantine and failed:
        _restore_quarantined_exes(moved)
        raise ShimQuarantineError(failed)
    try:
        _run_install_with_heartbeat(cmd, env=env)
    finally:
        # Restore shims when the installer didn't write replacements — on
        # FAILURE (install died before the entry-points step) and on SUCCESS
        # too: uv audits an already-satisfied editable install as a no-op and
        # rewrites no entry points, which would otherwise leave the shims
        # quarantined aside and `hermes` missing from PATH after a green
        # install (#75584). _restore_quarantined_exes skips any shim the
        # installer actually replaced, so this never clobbers fresh output.
        # Errors are not swallowed — the finally re-raises whatever escaped.
        if scripts_dir is not None:
            _restore_quarantined_exes(moved)


# A quarantine file younger than this may belong to an update running RIGHT
# NOW in another process, whose restore step still needs it. Deleting one
# mid-flight destroys the only copy of that shim.
_QUARANTINE_GRACE_SECONDS = 15 * 60


def _quarantine_stamp_ms(stale: Path) -> int | None:
    """The ``.old.<unix-ms>`` stamp in a quarantine filename, or ``None``.

    ``None`` means the name was not produced by
    :func:`_quarantine_running_hermes_exe`. We neither rescue nor delete those:
    the sweep should not destroy files whose provenance it cannot establish, and
    they are not ours to put back.

    Parsed from the NAME rather than ``st_mtime`` because ``rename`` preserves
    the original shim's mtime, which records when uv wrote the shim — days
    earlier, in general — not when it was quarantined.
    """
    try:
        return int(stale.name.rsplit(".old.", 1)[1])
    except (IndexError, ValueError):
        return None


def _cleanup_quarantined_exes(scripts_dir: Path | None = None) -> None:
    """Sweep — and where necessary RESCUE — ``hermes.exe.old.*`` from updates.

    Called early on every hermes invocation. Two cases the old unconditional
    ``unlink()`` got wrong, both ending with ``hermes`` gone from PATH:

    1. **Orphan rescue.** If ``hermes.exe`` is missing while
       ``hermes.exe.old.*`` is present, that .old file is the ONLY surviving
       copy of the shim — an update died, or its restore failed, between
       the rename and uv writing a replacement (#75584). Deleting it converts a
       one-rename recovery into a full reinstall. Put it back instead, through
       the same retry-and-report helper the update-time restore uses.
    2. **Concurrency.** A fresh quarantine file may belong to an update in
       flight in another process (the desktop update button racing a shell
       ``hermes update`` does exactly this). Leave anything inside the grace
       window alone; a later run sweeps it.

    Silent no-op on non-Windows, when there is nothing to do, or on
    file-locked / permission errors.
    """
    if not _is_windows():
        return
    if scripts_dir is None:
        scripts_dir = _venv_scripts_dir()
    if scripts_dir is None:
        return
    _cleanup_pending_shim_renames(scripts_dir)

    now = _time.time()

    try:
        candidates = [
            (stamp, stale)
            for stale, stamp in (
                (p, _quarantine_stamp_ms(p)) for p in scripts_dir.glob("*.exe.old.*")
            )
            if stamp is not None
        ]
    except OSError:
        return

    # Newest first by PARSED stamp. Sorting the raw filenames lexicographically
    # only tracks recency while every stamp shares a digit width: a stray
    # ``.old.999`` sorts above a 13-digit epoch-ms stamp and would be the copy
    # rescued onto the live shim name.
    candidates.sort(key=lambda pair: pair[0], reverse=True)

    for stamp, stale in candidates:
        try:
            original = stale.with_name(stale.name.rsplit(".old.", 1)[0])

            if not original.exists():
                # Orphan rescue: this is the last copy of the shim, so it gets
                # the retry ladder and the recovery message, not a bare rename.
                _early_recovery_mod.restore_quarantined_shims([(original, stale)])
                continue

            if now - stamp / 1000.0 < _QUARANTINE_GRACE_SECONDS:
                continue  # may be a live quarantine from a concurrent update

            stale.unlink()
        except OSError:
            pass  # still locked or in use — try again next run


# Import probes for venv corruption after a failed lazy ``uv pip install``.
# Metadata can look fine while ``.py`` files were removed mid-install (#57828).
# Canonical tables live in the stdlib-only ``_early_recovery`` module (which
# also probes/repairs BEFORE this module's third-party imports can run) so the
# early and full recovery layers can never drift apart.
_LAZY_REFRESH_IMPORT_PROBES: tuple[tuple[str, str], ...] = (
    _early_recovery_mod.LAZY_REFRESH_IMPORT_PROBES
)

_LAZY_REFRESH_REPAIR_PACKAGES: dict[str, str] = (
    _early_recovery_mod.LAZY_REFRESH_REPAIR_PACKAGES
)


def _run_package_only_install(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Run a package-only pip/uv install without quarantining entry-point shims.

    ``pip install --upgrade pip`` and ``--force-reinstall <pkg>`` do not
    rewrite ``hermes.exe``. The editable-install quarantine path would rename
    shims without uv recreating them on Windows (#57828).
    """
    _run_install_with_heartbeat(cmd, env=env)


def _lazy_refresh_repair_specs(packages: list[str]) -> list[str]:
    """Map repair package names to their declared pin specs in pyproject.toml."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover
        return packages

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return packages

    try:
        with open(pyproject, "rb") as f:
            raw_deps = tomllib.load(f).get("project", {}).get("dependencies", []) or []
    except Exception as exc:
        logger.debug("lazy refresh repair spec lookup failed: %s", exc)
        return packages

    name_to_spec: dict[str, str] = {}
    try:
        from packaging.requirements import Requirement  # type: ignore

        for spec in raw_deps:
            try:
                req = Requirement(spec)
                name_to_spec[req.name.lower()] = spec.split(";", 1)[0].strip()
            except Exception:
                continue
    except Exception:
        for spec in raw_deps:
            head = spec.split(";", 1)[0].strip()
            bare = head
            for op in ("==", ">=", "<=", "~=", ">", "<", "!="):
                if op in bare:
                    bare = bare.split(op, 1)[0]
                    break
            key = bare.strip().split("[", 1)[0].strip().lower()
            if key:
                name_to_spec[key] = head

    return [name_to_spec.get(pkg.lower(), pkg) for pkg in packages]


def _detect_broken_lazy_refresh_imports(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> list[str] | None:
    """Probe lazy-refresh packages via real imports.

    Returns:
      - ``[]`` when probes ran and every package imported cleanly
      - ``[dist, ...]`` when probes ran and some packages failed
      - ``None`` when the probe could not run (missing venv Python, subprocess
        failure, non-zero probe exit) — this is *indeterminate*, not healthy
    """
    venv_python = _resolve_install_target_python(install_cmd_prefix, env)
    if venv_python is None:
        return None

    probe_lines = "\n".join(
        f"    ({mod!r}, {attr!r})," for mod, attr in _LAZY_REFRESH_IMPORT_PROBES
    )
    check_script = (
        "import os\n"
        "import sys\n"
        "probes = [\n"
        f"{probe_lines}\n"
        "]\n"
        "broken = []\n"
        "for mod, attr in probes:\n"
        "    try:\n"
        "        imported = __import__(mod)\n"
        "        if not hasattr(imported, attr):\n"
        "            broken.append(mod)\n"
        "        elif mod == 'certifi':\n"
        "            # The module can import cleanly while cacert.pem is\n"
        "            # missing/corrupt (brew Python upgrade, interrupted venv\n"
        "            # rebuild) - every TLS call then fails (#29866).\n"
        "            bundle = imported.where()\n"
        "            if not os.path.isfile(bundle) or os.path.getsize(bundle) < 1024:\n"
        "                broken.append(mod)\n"
        "    except Exception:\n"
        "        broken.append(mod)\n"
        "print('\\n'.join(broken))\n"
    )
    try:
        result = subprocess.run(
            [str(venv_python), "-c", check_script],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            check=False,
            env=env,
        )
    except Exception as exc:
        logger.debug("lazy refresh import probe failed: %s", exc)
        return None

    if result.returncode != 0:
        logger.debug(
            "lazy refresh import probe exited %s: %s",
            result.returncode,
            (result.stderr or "")[:200],
        )
        return None

    broken_modules = [
        line.strip() for line in result.stdout.splitlines() if line.strip()
    ]
    packages: list[str] = []
    seen: set[str] = set()
    for mod in broken_modules:
        pkg = _LAZY_REFRESH_REPAIR_PACKAGES.get(mod)
        if pkg and pkg not in seen:
            seen.add(pkg)
            packages.append(pkg)
    return packages


def _repair_broken_lazy_refresh_imports(
    install_cmd_prefix: list[str],
    packages: list[str],
    *,
    env: dict[str, str] | None = None,
) -> bool:
    """Force-reinstall ``packages`` and re-probe imports. Never raises."""
    if not packages:
        return True

    specs = _lazy_refresh_repair_specs(packages)
    try:
        _run_package_only_install(
            install_cmd_prefix + ["install", "--force-reinstall", *specs],
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("lazy refresh venv repair failed: %s", exc)
        return False

    after = _detect_broken_lazy_refresh_imports(install_cmd_prefix, env=env)
    # Indeterminate re-probe is not confirmed success.
    return after == []


def _repair_venv_via_import_probes(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> str:
    """Probe imports and force-reinstall any broken lazy-refresh packages.

    Uses real ``import`` checks (not distribution metadata) so a venv where
    METADATA remains but ``.py`` files were wiped mid-install is still
    detected (#57828). Package-only reinstall — never rewrites ``hermes.exe``.

    Never raises. Returns one of:
      - ``"healthy"`` — probes ran and found nothing broken
      - ``"repaired"`` — probes found breakage and force-reinstall confirmed clean
      - ``"failed"`` — probes found breakage and repair did not confirm clean
      - ``"indeterminate"`` — probes could not run; do NOT treat as healthy
    """
    broken = _detect_broken_lazy_refresh_imports(install_cmd_prefix, env=env)
    if broken is None:
        print(
            '  ⚠ Проверка загрузки пакетов недоступна; исправность окружения не подтверждена.'
        )
        return "indeterminate"
    if not broken:
        return "healthy"
    print(
        f"  → Найдены повреждённые пакеты окружения Python: {', '.join(broken)}. Восстанавливаем…"
    )
    if _repair_broken_lazy_refresh_imports(
        install_cmd_prefix, broken, env=env
    ):
        print('  ✓ Окружение Python восстановлено')
        return "repaired"
    manual = " ".join(
        shlex.quote(s) for s in _lazy_refresh_repair_specs(broken)
    )
    print('  ⚠ Восстановление окружения не завершено. Выполните вручную, затем korra update:')
    print(
        f"    {' '.join(install_cmd_prefix)} install --force-reinstall {manual}"
    )
    return "failed"


def _is_uv_command(install_cmd_prefix: list[str]) -> bool:
    """True when the install command is a uv/uvx invocation.

    Handles a bare uv binary (``uv`` / ``uvx``, any extension), a path to
    one, and ``python -m uv`` / ``python -m uvx`` — the naive basename check
    misses the module form and launcher wrappers whose name does not contain
    "uv".
    """
    if not install_cmd_prefix:
        return False
    first = str(install_cmd_prefix[0]).lower()
    if "uv" in Path(first).name:
        return True
    # python -m uv / python -m uvx
    if len(install_cmd_prefix) >= 3 and first.endswith(("python", "python.exe")):
        return install_cmd_prefix[1] == "-m" and install_cmd_prefix[2] in (
            "uv",
            "uvx",
        )
    return False


def _insert_python_pin(args: list[str]) -> list[str]:
    """Insert ``--python <sys.executable>`` into a uv command line.

    If the caller already passed ``--python``, its value wins (uv's last-wins
    semantics are ambiguous; the explicit caller intent should not be
    overridden by the fallback pin).
    """
    if "--python" in args:
        return args
    return [args[0], "--python", str(sys.executable), *args[1:]]


def _interpreter_scripts_dir() -> Path | None:
    """Scripts/bin directory of the running interpreter (sys.executable).

    Used when pinning an install to ``sys.executable`` on a site-packages
    install where ``PROJECT_ROOT / "venv"`` does not exist: the entry-point
    shims uv rewrites live next to the interpreter, not under a project venv.
    Layout comes from the canonical ``venv_bin_dir`` helper (#76105 —
    hand-rolling Scripts/bin is lint-tested against).
    """
    from korra_constants import venv_bin_dir

    exe = Path(sys.executable)
    # sys.executable lives IN the bin/Scripts dir; its parent.parent is the
    # env root venv_bin_dir derives from.
    cand = venv_bin_dir(exe.parent.parent, windows=_is_windows())
    if cand.is_dir():
        return cand
    return exe.parent if exe.parent.is_dir() else None


def _install_python_dependencies_with_optional_fallback(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
    group: str = "all",
) -> None:
    """Install base deps plus as many optional extras as the environment supports.

    By default this targets ``.[all]``; Termux callers can pass
    ``group='termux-all'`` to use the curated Android-compatible profile.

    On Windows, pre-renames live ``hermes.exe`` / ``hermes-gateway.exe`` shims
    in the venv Scripts dir before each install attempt so uv can write fresh
    copies (Windows blocks REPLACE on a running .exe but allows RENAME). See
    ``_quarantine_running_hermes_exe`` for the rationale.

    When ``env`` carries a ``VIRTUAL_ENV`` that does not exist (a pip /
    site-packages install whose ``PROJECT_ROOT`` is the interpreter's
    ``site-packages`` directory, where ``PROJECT_ROOT / "venv"`` is never
    created), ``uv pip`` fails with ``Failed to inspect Python interpreter from
    active virtual environment`` before doing any work.  Pin the install at the
    running interpreter instead so the update/recovery path succeeds on those
    installs (#71510 fixed the ZIP path, #83335 fixed lazy-deps; this closes the
    shared helper for the remaining callers).
    """
    scripts_dir = _venv_scripts_dir() if _is_windows() else None

    # A pip / site-packages install has no PROJECT_ROOT/venv; the caller still
    # passes VIRTUAL_ENV=PROJECT_ROOT/venv, which does not exist. uv would fail
    # before installing anything ("Failed to inspect Python interpreter from
    # active virtual environment"). Detect the stale pointer and pin the target
    # interpreter explicitly instead of trusting the nonexistent venv.
    pin_python = False
    if (
        env
        and env.get("VIRTUAL_ENV")
        and not Path(env["VIRTUAL_ENV"]).is_dir()
        and install_cmd_prefix
        and _is_uv_command(install_cmd_prefix)
    ):
        # Only uv needs the explicit pin; pip resolves the target from
        # sys.executable itself and has no --python flag.
        pin_python = True
        env = {**env}
        env.pop("VIRTUAL_ENV", None)
        # When we pin to sys.executable, the entry-point shims that uv will
        # rewrite live in that interpreter's Scripts/bin directory, NOT in
        # PROJECT_ROOT/venv (which does not exist on a site-packages install).
        # Quarantining the wrong dir means the running hermes.exe stays locked
        # on Windows and the install fails exactly like the original bug. Only
        # override when the venv-derived dir is missing; otherwise keep it.
        if scripts_dir is None and _is_windows():
            scripts_dir = _interpreter_scripts_dir()

    def _install(args: list[str]) -> None:
        if pin_python:
            args = _insert_python_pin(args)
        # strict_quarantine: this is the UPDATE dependency sync. A shim that
        # cannot be renamed aside proves a hard venv hold; running uv anyway
        # is how installs strand half-updated (#87331). ShimQuarantineError
        # propagates to the update's sync boundary, which defers via the
        # update-incomplete marker instead of mutating a contended venv.
        _run_quarantined_install(
            install_cmd_prefix + args, env=env, scripts_dir=scripts_dir,
            strict_quarantine=True,
        )

    try:
        _install(["install", "-e", f".[{group}]"])
        _verify_console_scripts_installed(install_cmd_prefix, env=env)
        return
    except subprocess.CalledProcessError:
        print(
            '  ⚠ Ошибка дополнительных пакетов. Переустанавливаем основные зависимости и пробуем дополнительные по одному…'
        )

    _install(["install", "-e", "."])

    failed_extras: list[str] = []
    installed_extras: list[str] = []
    for extra in _load_installable_optional_extras(group=group):
        try:
            _install(["install", "-e", f".[{extra}]"])
            installed_extras.append(extra)
        except subprocess.CalledProcessError:
            failed_extras.append(extra)

    if installed_extras:
        print(
            f"  ✓ Дополнительные пакеты переустановлены по отдельности: {', '.join(installed_extras)}"
        )
    if failed_extras:
        print(
            f"  ⚠ Пропущены дополнительные пакеты с ошибками: {', '.join(failed_extras)}"
        )

    # Belt-and-suspenders: verify every declared core dependency from
    # pyproject.toml's [project.dependencies] is actually importable in the
    # target venv. uv's incremental resolver has — in the wild — produced
    # partial installs where a newly added base dep (e.g. ``pathspec``)
    # silently fails to land on top of a half-stale venv, and the only
    # symptom is a downstream subprocess crashing with ModuleNotFoundError
    # hours later inside ``hermes update``'s desktop-rebuild or skill-sync
    # stage. Reinstall with --reinstall to force resolution if anything is
    # missing, then re-verify so the failure surfaces here instead of
    # downstream.
    _verify_core_dependencies_installed(install_cmd_prefix, env=env, group=group)
    _verify_console_scripts_installed(install_cmd_prefix, env=env)


def _load_console_script_names() -> list[str]:
    """Return ``[project.scripts]`` entry-point names from pyproject.toml."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover
        return []

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return []

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        scripts = data.get("project", {}).get("scripts", {}) or {}
        return [str(name) for name in scripts if name]
    except Exception as e:
        logger.debug("console script verification: failed to read pyproject.toml: %s", e)
        return []


def _verify_console_scripts_installed(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Ensure every declared console_script shim exists on disk after install.

    On Windows, ``uv pip install -e .`` can register ``hermes.exe`` in the
    wheel RECORD while the file never lands on disk — typically when the live
    ``hermes.exe`` shim is locked during ``hermes update``, or when uv/distlib
    skips a launcher write. The symptom is ``hermes-agent.exe`` and
    ``hermes-acp.exe`` present but ``hermes.exe`` missing, so ``hermes`` drops
    off PATH even though the install reported success (issue #52931).

    If any shim is missing we reinstall with ``--reinstall -e .`` under the
    same quarantine dance as the primary install path, then re-check.
    """
    if not _is_windows():
        return

    scripts_dir = _venv_scripts_dir()
    if scripts_dir is None:
        return

    names = _load_console_script_names()
    if not names:
        return

    def _missing() -> list[str]:
        return [
            name
            for name in names
            if not (scripts_dir / f"{name}.exe").is_file()
        ]

    missing = _missing()
    if not missing:
        return

    print(
        f"  ⚠ Проверка: команд, отсутствующих на диске, — {len(missing)}: {', '.join(missing)}"
    )
    print('  → Восстанавливаем команды через --reinstall…')

    try:
        _run_quarantined_install(
            install_cmd_prefix + ["install", "--reinstall", "-e", "."],
            env=env,
            scripts_dir=scripts_dir,
        )
    except subprocess.CalledProcessError as e:
        logger.warning("console script verification: repair install failed: %s", e)
        print(
            '  ⚠ Команды не восстановлены. Закройте остальные процессы Корры и выполните korra update --force.'
        )
        return

    still_missing = _missing()
    if still_missing:
        print(
            f"  ⚠ После восстановления всё ещё отсутствуют: {', '.join(still_missing)}. Временный запуск: python -m korra_cli.main <command>"
        )
    else:
        print('  ✓ Все команды восстановлены')


def _verify_core_dependencies_installed(
    install_cmd_prefix: list[str],
    *,
    env: dict[str, str] | None = None,
    group: str = "all",
) -> None:
    """Check that every base dep from pyproject.toml is importable; if not, retry.

    Reads ``pyproject.toml`` directly (so we don't trust the venv's stale
    metadata), filters out deps gated by ``;`` environment markers that don't
    apply to this platform, and runs ``importlib.metadata.version()`` in the
    venv interpreter for each one. If anything is missing we reinstall the
    base group with ``--reinstall`` to force uv to re-resolve, then check
    again. We treat the final state as a warning rather than a hard failure
    so a single broken-on-PyPI dep can't block an otherwise-successful
    update — but the warning makes the partial install visible at the spot
    that caused it, instead of hours later in a downstream subprocess.
    """
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover — Python < 3.11 unsupported but be safe
        return

    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return

    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        raw_deps = data.get("project", {}).get("dependencies", []) or []
    except Exception as e:
        logger.debug("dep verification: failed to read pyproject.toml: %s", e)
        return

    # Parse each "name OP version ; marker" string into (dist_name, marker_obj).
    # We use packaging.requirements when available (it ships with pip/uv envs),
    # falling back to a naive split that's good enough for the canonical
    # ``name==version[; marker]`` style this repo uses.
    deps: list[tuple[str, "object | None"]] = []
    try:
        from packaging.requirements import Requirement  # type: ignore

        for spec in raw_deps:
            try:
                req = Requirement(spec)
                deps.append((req.name, req.marker))
            except Exception:
                continue
    except Exception:
        for spec in raw_deps:
            head = spec.split(";", 1)[0]
            for op in ("==", ">=", "<=", "~=", ">", "<", "!="):
                if op in head:
                    head = head.split(op, 1)[0]
                    break
            name = head.strip().split("[", 1)[0].strip()
            if name:
                deps.append((name, None))

    # Apply environment markers to drop deps that don't apply on this platform
    # (e.g. ``ptyprocess ; sys_platform != 'win32'`` is correctly skipped on
    # Windows). Without markers we'd false-positive every cross-platform exclusion.
    applicable: list[str] = []
    for name, marker in deps:
        if marker is None:
            applicable.append(name)
            continue
        try:
            if marker.evaluate():  # type: ignore[union-attr]
                applicable.append(name)
        except Exception:
            applicable.append(name)

    if not applicable:
        return

    # Run the check inside the venv Python — sys.executable here may be the
    # outer Python that drove ``hermes update``, not the venv we just wrote
    # to. The uv install_cmd_prefix encodes which environment we targeted
    # (either ``[uv, pip]`` with VIRTUAL_ENV in env, or
    # ``[sys.executable, -m, pip]`` for the in-process Python); resolve the
    # right interpreter for the verification.
    venv_python = _resolve_install_target_python(install_cmd_prefix, env)
    if venv_python is None:
        return

    def _missing_deps() -> list[str]:
        check_script = (
            "import importlib.metadata as md, sys\n"
            "missing=[]\n"
            "for name in sys.argv[1:]:\n"
            "    try: md.version(name)\n"
            "    except md.PackageNotFoundError: missing.append(name)\n"
            "print('\\n'.join(missing))\n"
        )
        try:
            result = subprocess.run(
                [str(venv_python), "-c", check_script, *applicable],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                check=False,
                env=env,
            )
        except Exception as e:
            logger.debug("dep verification: subprocess failed: %s", e)
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    missing = _missing_deps()
    if not missing:
        return

    print(
        f"  ⚠ Проверка: после установки отсутствуют зависимости ({len(missing)}): {', '.join(missing[:8])}{('...' if len(missing) > 8 else '')}"
    )
    print('  → Восстанавливаем основные зависимости через --reinstall…')

    # Reinstall base group with --reinstall so uv re-resolves from scratch
    # against the current pyproject. We don't pass ``[{group}]`` here on
    # purpose — the missing dep is in *base* deps; rerunning the full all-
    # extras install can cost minutes and trips on whatever optional extra
    # was already broken upstream. Base is fast and is what's actually wrong.
    #
    # Quarantine the running ``hermes.exe`` first: ``--reinstall -e .``
    # rewrites the entry-point shims, and on Windows pip can't overwrite the
    # live launcher, which would leave ``hermes`` off PATH.
    scripts_dir = _venv_scripts_dir() if _is_windows() else None
    repair_args = ["install", "--reinstall", "-e", "."]
    try:
        _run_quarantined_install(
            install_cmd_prefix + repair_args, env=env, scripts_dir=scripts_dir
        )
    except subprocess.CalledProcessError as e:
        logger.warning("dep verification: repair install failed: %s", e)
        print('  ⚠ Восстановление не удалось. Проверьте вывод korra update выше.')
        return

    still_missing = _missing_deps()
    if not still_missing:
        print('  ✓ Все основные зависимости установлены')
        return

    # Last-ditch: install each remaining missing dep with its pin directly.
    # Useful when uv's resolver thinks the env is satisfied but the on-disk
    # package metadata says otherwise (rare but observed).
    name_to_spec = {}
    for spec in raw_deps:
        head = spec.split(";", 1)[0].strip()
        bare = head
        for op in ("==", ">=", "<=", "~=", ">", "<", "!="):
            if op in bare:
                bare = bare.split(op, 1)[0]
                break
        name_to_spec[bare.strip().split("[", 1)[0].strip()] = head

    specs = [name_to_spec.get(n, n) for n in still_missing]
    print(
        f"  → Принудительно устанавливаем оставшиеся зависимости: {', '.join(specs)}"
    )
    try:
        _run_install_with_heartbeat(
            install_cmd_prefix + ["install", "--reinstall", *specs], env=env
        )
    except subprocess.CalledProcessError as e:
        logger.warning("dep verification: per-package repair failed: %s", e)
        print(
            f"  ⚠ Не удалось установить: {', '.join(still_missing)}. Закройте остальные процессы Корры и выполните korra update --force."
        )
        return

    final_missing = _missing_deps()
    if final_missing:
        print(
            f"  ⚠ После восстановления всё ещё отсутствуют: {', '.join(final_missing)}. Закройте остальные процессы Корры и выполните korra update --force."
        )
    else:
        print('  ✓ Все основные зависимости установлены')


def _resolve_install_target_python(
    install_cmd_prefix: list[str], env: dict[str, str] | None
) -> Path | None:
    """Figure out which Python interpreter the install just targeted.

    ``_install_python_dependencies_with_optional_fallback`` is called with
    either ``[uv, pip]`` (and a ``VIRTUAL_ENV`` env var pointing at the
    target venv) or ``[sys.executable, -m, pip]`` (the in-process Python).
    The verification step needs the *resulting* environment's Python so
    ``importlib.metadata`` queries the right site-packages.
    """
    if env and "VIRTUAL_ENV" in env:
        from korra_constants import venv_python_path

        venv_root = Path(env["VIRTUAL_ENV"])
        candidate = venv_python_path(venv_root, windows=_is_windows())
        if candidate.exists():
            return candidate

    # Fallback: assume install_cmd_prefix[0] is the python interpreter (the
    # ``[sys.executable, -m, pip]`` shape). Skip if it looks like ``uv``.
    if install_cmd_prefix:
        first = Path(install_cmd_prefix[0])
        if first.exists() and "uv" not in first.name.lower():
            return first

    return None


def _is_termux_env(env: dict[str, str] | None = None) -> bool:
    return _is_termux_startup_environment(env)


def _is_windows_npm_path(npm_path: str) -> bool:
    """Return True if ``npm_path`` points at a Windows npm shim.

    On WSL the Windows install dir is exposed through the ``/mnt/c`` drive
    mount and PATH interop, so ``shutil.which("npm")`` can hand back
    ``/mnt/c/Program Files/nodejs/npm`` (or the ``npm.cmd`` / ``npm.exe``
    shim). Those are detected here by their ``.exe``/``.cmd``/``.bat``
    suffix, a ``/mnt/`` drive-mount prefix, or an embedded backslash (a UNC
    path). Callers use this only on a POSIX host — on native Windows an
    ``npm.cmd`` shim is the correct executable.
    """
    low = npm_path.lower()
    return (
        low.endswith((".exe", ".cmd", ".bat"))
        or low.startswith("/mnt/")
        or "\\" in npm_path
    )


def _resolve_node_runtime_npm() -> str | None:
    """Resolve an npm executable that belongs to the host's Node runtime.

    On WSL/Linux ``shutil.which("npm")`` may resolve a Windows npm exposed
    through PATH interop. Running that Windows npm against the Linux checkout
    operates over ``\\wsl.localhost\\...`` UNC paths and fails with EISDIR /
    symlink errors in symlink-heavy trees like ``ui-tui`` (#30271). Refuse a
    Windows npm on a POSIX host and re-scan PATH (skipping ``/mnt/*`` interop
    entries) for a Linux-native npm. Returns the npm path, or ``None`` when
    no suitable npm is reachable.
    """
    from korra_constants import find_node_executable

    npm = find_node_executable("npm")

    # On native Windows the platform npm (``npm.cmd``) is exactly what we
    # want — only reject Windows shims when we're a POSIX/WSL process.
    if _is_windows():
        return npm

    if not npm:
        return None

    if not _is_windows_npm_path(npm):
        return npm

    # The first resolution was a Windows npm. Re-scan PATH skipping the
    # ``/mnt/*`` Windows drive mounts WSL injects, so a Linux-native npm that
    # came later on PATH is still found.
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory or directory.lower().startswith("/mnt/"):
            continue
        candidate = shutil.which("npm", path=directory)
        if candidate and not _is_windows_npm_path(candidate):
            return candidate
    return None


class _UpdateOutputStream:
    """Stream wrapper used during ``hermes update`` to survive terminal loss.

    Wraps the process's original stdout/stderr so that:

    * Every write is also mirrored to an append-only log file
      (``~/.hermes/logs/update.log``) that users can inspect after the
      terminal disconnects.
    * Writes to the original stream that fail with ``BrokenPipeError`` /
      ``OSError`` / ``ValueError`` (closed file) no longer cascade into
      process exit — the update keeps going, only the on-screen output
      stops.

    Combined with ``SIGHUP -> SIG_IGN`` installed by
    ``_install_hangup_protection``, this makes ``hermes update`` safe to
    run in a plain SSH session that might disconnect mid-install.
    """

    def __init__(self, original, log_file):
        self._original = original
        self._log = log_file
        self._original_broken = False

    def write(self, data):
        # Mirror to the log file first — it's the most reliable destination.
        if self._log is not None:
            try:
                self._log.write(data)
            except Exception:
                # Log errors should never abort the update.
                pass

        if self._original_broken:
            return len(data) if isinstance(data, (str, bytes)) else 0

        try:
            return self._original.write(data)
        except (BrokenPipeError, OSError, ValueError):
            # Terminal vanished (SSH disconnect, shell close).  Stop trying
            # to write to it, but keep the update running.
            self._original_broken = True
            return len(data) if isinstance(data, (str, bytes)) else 0

    def flush(self):
        if self._log is not None:
            try:
                self._log.flush()
            except Exception:
                pass
        if self._original_broken:
            return
        try:
            self._original.flush()
        except (BrokenPipeError, OSError, ValueError):
            self._original_broken = True

    def isatty(self):
        if self._original_broken:
            return False
        try:
            return self._original.isatty()
        except Exception:
            return False

    def fileno(self):
        # Some tools probe fileno(); defer to the underlying stream and let
        # callers handle failures (same behaviour as the unwrapped stream).
        return self._original.fileno()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _install_hangup_protection(gateway_mode: bool = False):
    """Protect ``cmd_update`` from SIGHUP and broken terminal pipes.

    Users commonly run ``hermes update`` in an SSH session or a terminal
    that may close mid-install.  Without protection, ``SIGHUP`` from the
    terminal kills the Python process during ``pip install`` and leaves
    the venv half-installed; the documented workaround ("use screen /
    tmux") shouldn't be required for something as routine as an update.

    Protections installed:

    1. ``SIGHUP`` is set to ``SIG_IGN``.  POSIX preserves ``SIG_IGN``
       across ``exec()``, so pip and git subprocesses also stop dying on
       hangup.
    2. ``sys.stdout`` / ``sys.stderr`` are wrapped to mirror output to
       ``~/.hermes/logs/update.log`` and to silently absorb
       ``BrokenPipeError`` when the terminal vanishes.

    ``SIGINT`` (Ctrl-C) and ``SIGTERM`` (systemd shutdown) are
    **intentionally left alone** — those are legitimate cancellation
    signals the user or OS sent on purpose.

    In gateway mode (``hermes update --gateway``) the update is already
    spawned detached from a terminal, so this function is a no-op.

    Returns a dict that ``cmd_update`` can pass to
    ``_finalize_update_output`` on exit.  Returning a dict rather than a
    tuple keeps the call site forward-compatible with future additions.
    """
    state = {
        "prev_stdout": sys.stdout,
        "prev_stderr": sys.stderr,
        "log_file": None,
        "installed": False,
    }

    if gateway_mode:
        return state

    import signal as _signal

    # (1) Ignore SIGHUP for the remainder of this process.
    if hasattr(_signal, "SIGHUP"):
        try:
            _signal.signal(_signal.SIGHUP, _signal.SIG_IGN)
        except (ValueError, OSError):
            # Called from a non-main thread — not fatal.  The update still
            # runs, just without hangup protection.
            pass

    # (2) Mirror output to update.log and wrap stdio for broken-pipe
    # tolerance.  Any failure here is non-fatal; we just skip the wrap.
    try:
        # Late-bound import so tests can monkeypatch
        # korra_cli.config.get_hermes_home to simulate setup failure.
        from korra_cli.config import get_hermes_home as _get_hermes_home

        logs_dir = _get_hermes_home() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "update.log"
        log_file = open(log_path, "a", buffering=1, encoding="utf-8")

        import datetime as _dt

        log_file.write(
            f"\n=== hermes update started "
            f"{_dt.datetime.now().isoformat(timespec='seconds')} ===\n"
        )

        state["log_file"] = log_file
        sys.stdout = _UpdateOutputStream(state["prev_stdout"], log_file)
        sys.stderr = _UpdateOutputStream(state["prev_stderr"], log_file)
        state["installed"] = True
    except Exception:
        # Leave stdio untouched on any setup failure.  Update continues
        # without mirroring.
        state["log_file"] = None

    return state


def _finalize_update_output(state):
    """Restore stdio and close the update.log handle opened by ``_install_hangup_protection``."""
    if not state:
        return
    if state.get("installed"):
        try:
            sys.stdout = state.get("prev_stdout", sys.stdout)
        except Exception:
            pass
        try:
            sys.stderr = state.get("prev_stderr", sys.stderr)
        except Exception:
            pass
    log_file = state.get("log_file")
    if log_file is not None:
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass


def _resolve_update_branch(args) -> str:
    """Normalize ``args.branch`` into a non-empty branch name.

    Centralizes the "default to main, accept --branch override, treat empty
    or whitespace-only values as the default" parsing so every consumer of
    ``--branch`` (check path, git-update path, ZIP-fallback path) agrees on
    the same answer.
    """
    return (getattr(args, "branch", None) or "main").strip() or "main"


def _size_delta_label(saved_mb: float) -> str:
    """Human label for a before/after database size delta, in MB.

    A negative delta means the file GREW — concurrent session writes during a
    long optimize can outweigh what the rebuild freed. Printing
    "reclaimed -163.0 MB" for that reads as data loss, so say "grew by"
    instead.
    """
    if saved_mb >= 0:
        return f'освобождено {saved_mb:.1f} МБ'
    return f'увеличилось на {-saved_mb:.1f} МБ'


def cmd_update(args):
    """Update Hermes Agent to the latest version.

    Thin wrapper around ``_cmd_update_impl``: installs hangup protection,
    runs the update, then restores stdio on the way out (even on
    ``sys.exit`` or unhandled exceptions).
    """
    from korra_cli.config import (
        is_managed,
        managed_error,
    )

    if is_managed():
        managed_error('обновить Корру')
        return

    # --plan is read-only and deployment-kind aware, so it runs BEFORE the
    # docker/nix/apt refusal gates: on an image-managed or package-managed
    # install the plan itself reports "not updatable in place" plus the
    # right mechanism — strictly more useful than the bare refusal text.
    if getattr(args, "plan", False):
        # Read-only plan phase (#91277 Phase 2): inventory every running
        # Hermes runtime across profiles, its supervisor, and its running
        # code version — without mutating anything. Safe on a live fleet.
        from korra_cli.update_inventory import (
            collect_runtime_inventory,
            print_update_plan,
        )

        print_update_plan(collect_runtime_inventory())
        return

    # Image-managed / package-managed admission gate (#91277 Phase 3): one
    # shared decision for every mutation surface. Consults the baked image
    # provenance marker first (authoritative, fail-closed on malformed),
    # then the pre-existing docker/nix/apt heuristics. Prints the real
    # update command, records a `refused` receipt so fleet tooling sees the
    # blocked attempt, and exits 2 (refused-by-contract, distinct from
    # exit 1 errors).
    from korra_cli.update_contract import (
        evaluate_update_admission,
        record_refusal_receipt,
    )

    refusal = evaluate_update_admission(PROJECT_ROOT)
    if refusal is not None:
        print(refusal.message)
        record_refusal_receipt(refusal)
        sys.exit(2)

    if getattr(args, "check", False):
        # --check honors --branch so the "any new commits?" answer matches
        # what a subsequent `hermes update --branch=<x>` would actually pull.
        branch = _resolve_update_branch(args)
        _self()._cmd_update_check(
            branch=branch,
            branch_explicit=bool(getattr(args, "branch", None)),
        )
        return

    gateway_mode = getattr(args, "gateway", False)

    # Protect against mid-update terminal disconnects (SIGHUP) and tolerate
    # writes to a closed stdout.  No-op in gateway mode.  See
    # _install_hangup_protection for rationale.
    _update_io_state = _install_hangup_protection(gateway_mode=gateway_mode)
    # Cross-process mutual exclusion. The dashboard's Update button spawns
    # this same command detached, and the desktop hands off to the Tauri
    # updater / install-mode bootstrap — all three mutate one checkout. Two of
    # them running together rewrite source under a live interpreter and strand
    # the tree half-updated. Share the marker the Tauri updater and Electron
    # already use rather than inventing a second lock.
    from korra_cli.update_lock import (
        UPDATE_EXIT_CONCURRENT,
        UpdateLock,
        describe_holder,
    )

    _update_lock = UpdateLock()
    if not _update_lock.acquire():
        print(describe_holder(_update_lock.holder))
        _finalize_update_output(_update_io_state)
        sys.exit(UPDATE_EXIT_CONCURRENT)

    # Exit code for the Windows hand-off child's hard exit (see finally).
    # None = not a SystemExit-shaped outcome; real exceptions keep the
    # normal raise path so their traceback still prints.
    _update_handoff_exit_code: int | None = None
    try:
        _self()._cmd_update_impl(args, gateway_mode=gateway_mode)
    except SystemExit as _update_exit:
        # Receipt boundary (#91283 review): the impl has many early
        # sys.exit paths (concurrent-instance preflight, venv-holder
        # refusal, head-pinned no-op, fetch failure) that never reach an
        # inner finalize. Persist any still-open receipt with the real
        # exit code, then let the exit proceed unchanged. No-op when an
        # inner path already finalized (exactly-once by construction).
        try:
            from korra_cli.update_receipt import finalize_pending_update_receipt

            _code = _update_exit.code if isinstance(_update_exit.code, int) else 1
            finalize_pending_update_receipt(_code, f"sys.exit({_code})")
        except Exception:
            pass
        _update_handoff_exit_code = (
            _update_exit.code if isinstance(_update_exit.code, int) else 0
        )
        raise
    except BaseException as _update_exc:
        try:
            from korra_cli.update_receipt import finalize_pending_update_receipt

            finalize_pending_update_receipt(
                1, f"{type(_update_exc).__name__}: {_update_exc}"
            )
        except Exception:
            pass
        raise
    else:
        try:
            from korra_cli.update_receipt import finalize_pending_update_receipt

            finalize_pending_update_receipt(0, "completed at command boundary")
        except Exception:
            pass
        _update_handoff_exit_code = 0
    finally:
        _update_lock.release()
        _finalize_update_output(_update_io_state)
        # Windows hand-off child (#93581): the re-exec'd venv child cannot
        # rely on graceful interpreter shutdown — a leftover non-daemon
        # thread from the update tail keeps the console busy long after
        # the receipt is durable (success, exit 0, "completed at command
        # boundary"), freezing the PowerShell window for minutes. By this
        # point every durable step is done (receipt finalized above, lock
        # released, stdio restored), so on the hand-off path only, flush
        # and exit hard instead of waiting for the interpreter to unwind
        # — the same treatment #79040's cron workaround applies. No-op on
        # every non-hand-off invocation: the marker env is set solely by
        # _reexec_dependency_sync_off_windows_shim when it spawns the child.
        if _update_handoff_exit_code is not None and korra_env(_UPDATE_REEXEC_ENV) == "1":
            logger.debug(
                "Update hand-off child %s exiting via os._exit(%s)",
                os.getpid(), _update_handoff_exit_code,
            )
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(_update_handoff_exit_code)


def _coalesce_session_name_args(argv: list) -> list:
    """Join unquoted multi-word session names after -c/--continue and -r/--resume.

    When a user types ``hermes -c Pokemon Agent Dev`` without quoting the
    session name, argparse sees three separate tokens.  This function merges
    them into a single argument so argparse receives
    ``['-c', 'Pokemon Agent Dev']`` instead.

    Tokens are collected after the flag until we hit another flag (``-*``)
    or a known top-level subcommand.
    """
    _SUBCOMMANDS = {
        "chat",
        "model",
        "gateway",
        "setup",
        "whatsapp",
        "whatsapp-cloud",
        "login",
        "logout",
        "auth",
        "status",
        "cron",
        "doctor",
        "config",
        "pairing",
        "skills",
        "tools",
        "mcp",
        "sessions",
        "insights",
        "update",
        "uninstall",
        "profile",
        "dashboard",
        "serve",
        "desktop",
        "gui",
        "honcho",
        "claw",
        "plugins",
        "security",
        "acp",
        "webhook",
        "peer",
        "memory",
        "dump",
        "debug",
        "backup",
        "import",
        "completion",
        "logs",
    }
    _SESSION_FLAGS = {"-c", "--continue", "-r", "--resume"}

    result = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _SESSION_FLAGS:
            result.append(token)
            i += 1
            # Collect subsequent non-flag, non-subcommand tokens as one name
            parts: list = []
            while (
                i < len(argv)
                and not argv[i].startswith("-")
                and argv[i] not in _SUBCOMMANDS
            ):
                parts.append(argv[i])
                i += 1
            if parts:
                result.append(" ".join(parts))
        else:
            result.append(token)
            i += 1
    return result


def cmd_profile(args):
    """Profile management — create, delete, list, switch, alias."""
    from korra_cli.profiles import (
        list_profiles,
        create_profile,
        delete_profile,
        seed_profile_skills,
        set_active_profile,
        get_active_profile_name,
        check_alias_collision,
        create_wrapper_script,
        remove_wrapper_script,
        _is_wrapper_dir_in_path,
        _get_wrapper_dir,
    )
    from korra_constants import display_hermes_home

    action = getattr(args, "profile_action", None)

    if action is None:
        # Bare `hermes profile` — show current profile status
        from korra_cli.profiles import format_profile_label

        profile_name = get_active_profile_name()
        dhh = display_hermes_home()

        profiles = list_profiles()
        current = next(
            (
                p
                for p in profiles
                if p.name == profile_name
                or (profile_name == "default" and p.is_default)
            ),
            None,
        )
        label = format_profile_label(
            profile_name, current.display_name if current else ""
        )
        print(f'Текущий профиль: {label}')
        print(f'Путь:           {dhh}')

        if current is not None:
            p = current
            if p.model:
                print(
                    f'Модель:         {p.model}'
                    + (f" ({p.provider})" if p.provider else "")
                )
            print(
                f"Шлюз:           {('работает' if p.gateway_running else 'остановлен')}"
            )
            print(f'Навыков:        {p.skill_count} установлено')
            if p.alias_path:
                alias_display = p.alias_name or p.name
                print(f'Команда:        {alias_display} → korra -p {p.name}')
        print()
        return

    if action == "list":
        from korra_cli.profiles import format_profile_label

        profiles = list_profiles()
        active = get_active_profile_name()

        if not profiles:
            print('Профили не найдены.')
            return

        # Header
        print(
            f"\n {'Profile':<16} {'Model':<28} {'Gateway':<12} "
            f"{'Alias':<12} {'Distribution'}"
        )
        print(
            f" {'─' * 15}    {'─' * 27}    {'─' * 11}    "
            f"{'─' * 11}    {'─' * 20}"
        )

        for p in profiles:
            marker = (
                " ◆"
                if (p.name == active or (active == "default" and p.is_default))
                else "  "
            )
            name = format_profile_label(p.name, p.display_name)
            model = (p.model or "—")[:26]
            gw = "running" if p.gateway_running else "stopped"
            alias = (p.alias_name or p.name) if p.alias_path else "—"
            if p.is_default:
                alias = "—"
            if p.distribution_name:
                dist = f"{p.distribution_name}@{p.distribution_version or '?'}"
                dist = dist[:30]
            else:
                dist = "—"
            print(f"{marker}{name:<15} {model:<28} {gw:<12} {alias:<12} {dist}")
        print()

    elif action == "use":
        name = args.profile_name
        try:
            set_active_profile(name)
            if name == "default":
                print('Выбран основной профиль default')
            else:
                print(f'Выбран профиль: {name}')
        except (ValueError, FileNotFoundError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "create":
        name = args.profile_name
        clone = getattr(args, "clone", False)
        clone_all = getattr(args, "clone_all", False)
        no_alias = getattr(args, "no_alias", False)
        no_skills = getattr(args, "no_skills", False)

        try:
            clone_from = getattr(args, "clone_from", None)
            clone_config = clone or clone_from is not None

            profile_dir = create_profile(
                name=name,
                clone_from=clone_from,
                clone_all=clone_all,
                clone_config=clone_config,
                no_alias=no_alias,
                no_skills=no_skills,
                description=getattr(args, "description", None),
            )
            print(f'Профиль «{name}» создан в {profile_dir}')

            if clone_config or clone_all:
                source_label = (
                    getattr(args, "clone_from", None) or get_active_profile_name()
                )
                if clone_all:
                    print(
                        f'Полная копия {source_label} без истории бесед, резервных копий и снимков.'
                    )
                else:
                    print(
                        f'Настройки, .env, SOUL.md и навыки скопированы из {source_label}.'
                    )

            # Auto-clone Honcho config for the new profile (only with clone operations)
            if clone_config or clone_all:
                try:
                    from plugins.memory.honcho.cli import clone_honcho_for_profile

                    if clone_honcho_for_profile(name):
                        print(f'Настройки Honcho скопированы; участник: {name}')
                except Exception:
                    pass  # Honcho plugin not installed or not configured

            # Seed bundled skills for fresh profiles only. Clone operations
            # already copied the source profile's skills, including any
            # user-installed or intentionally removed skills.
            if not (clone_config or clone_all):
                result = seed_profile_skills(profile_dir)
                if result and result.get("skipped_opt_out"):
                    print(
                        'Встроенные навыки не добавлены (--no-skills). Для включения удалите .no-bundled-skills из профиля.'
                    )
                elif result:
                    copied = len(result.get("copied", []))
                    print(f'Синхронизировано встроенных навыков: {copied}.')
                else:
                    print(
                        '⚠ Не удалось добавить навыки. Повторите: {} update.'.format(
                            name
                        )
                    )

            # Create wrapper alias
            if not no_alias:
                collision = check_alias_collision(name)
                if collision:
                    print(f'⚠ Нельзя создать команду «{name}»: {collision}')
                    print(
                        f'  Выберите другое имя: korra profile alias {name} --name <custom>'
                    )
                    print(f'  Или запускайте через параметр: korra -p {name} chat')
                else:
                    wrapper_path = create_wrapper_script(name)
                    if wrapper_path:
                        print(f'Команда-обёртка создана: {wrapper_path}')
                        if not _is_wrapper_dir_in_path():
                            print(f'⚠ Папка {_get_wrapper_dir()} не входит в PATH.')
                            print(
                                '  Добавьте в настройки оболочки ~/.bashrc или ~/.zshrc:'
                            )
                            print('    export PATH="$HOME/.local/bin:$PATH"')

            # Profile dir for display
            try:
                profile_dir_display = "~/" + profile_dir.relative_to(Path.home()).as_posix()
            except ValueError:
                profile_dir_display = str(profile_dir)

            # Next steps
            print('Дальше:')
            print(f'  {name} setup              Настроить ключи API и модель')
            print(f'  {name} chat               Начать беседу')
            print(f'  {name} gateway start      Запустить шлюз мессенджеров')
            if clone or clone_all:
                print(f'  Другие ключи API задаются в {profile_dir_display}/.env')
                print(f'  Другой характер общения задаётся в {profile_dir_display}/SOUL.md')
            else:
                print(
                    f'  ⚠ У этого профиля пока нет ключей API. Сначала выполните {name} setup,'
                )
                print('    иначе будут использованы ключи из среды вашего терминала.')
                print(f'  Характер общения можно настроить в {profile_dir_display}/SOUL.md')
            print()

        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "delete":
        name = args.profile_name
        yes = getattr(args, "yes", False)
        try:
            delete_profile(name, yes=yes)
        except (ValueError, FileNotFoundError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "describe":
        # Read or write a profile's description. The description is
        # consumed by the kanban decomposer to route tasks based on
        # role instead of name alone.
        from korra_cli import profiles as _profiles_mod

        all_flag = bool(getattr(args, "all_missing", False))
        auto_flag = bool(getattr(args, "auto", False))
        overwrite_flag = bool(getattr(args, "overwrite", False))
        text_value = getattr(args, "text", None)
        name = getattr(args, "profile_name", None)

        if all_flag and not auto_flag:
            print('profile describe: для --all нужен --auto', file=sys.stderr)
            sys.exit(2)
        if all_flag and (text_value or name):
            print(
                'profile describe: --all нельзя использовать с именем профиля или --text',
                file=sys.stderr,
            )
            sys.exit(2)
        if not all_flag and not name:
            print('profile describe: укажите имя профиля либо --all --auto', file=sys.stderr)
            sys.exit(2)
        if text_value and auto_flag:
            print(
                'profile describe: --text и --auto нельзя использовать вместе',
                file=sys.stderr,
            )
            sys.exit(2)

        # Show current description if no operation requested.
        if name and not text_value and not auto_flag:
            try:
                if _profiles_mod.normalize_profile_name(name) == "default":
                    from korra_constants import get_hermes_home as _hh
                    profile_dir = Path(_hh())
                else:
                    profile_dir = _profiles_mod.get_profile_dir(name)
            except Exception as exc:
                print(f'Ошибка: {exc}', file=sys.stderr)
                sys.exit(1)
            if not profile_dir.is_dir():
                print(f'Ошибка: профиль «{name}» не найден', file=sys.stderr)
                sys.exit(1)
            meta = _profiles_mod.read_profile_meta(profile_dir)
            desc = meta.get("description") or ""
            if not desc:
                print(f'Для «{name}» описание не задано')
            else:
                tag = "[auto] " if meta.get("description_auto") else ""
                print(f"{tag}{desc}")
            sys.exit(0)

        # --text path: just write the user-authored description.
        if text_value:
            try:
                if _profiles_mod.normalize_profile_name(name) == "default":
                    from korra_constants import get_hermes_home as _hh
                    profile_dir = Path(_hh())
                else:
                    profile_dir = _profiles_mod.get_profile_dir(name)
                _profiles_mod.write_profile_meta(
                    profile_dir,
                    description=text_value,
                    description_auto=False,
                )
                print(f'Описание «{name}» обновлено.')
            except Exception as exc:
                print(f'Ошибка: {exc}', file=sys.stderr)
                sys.exit(1)
            sys.exit(0)

        # --auto path: invoke the LLM describer.
        from korra_cli import profile_describer as _pd

        if all_flag:
            targets = _pd.list_describable_profiles(missing_only=True)
            if not targets:
                print('У всех профилей уже есть описания.')
                sys.exit(0)
        else:
            targets = [name]

        ok_count = 0
        fail_count = 0
        for tgt in targets:
            outcome = _pd.describe_profile(tgt, overwrite=overwrite_flag)
            if outcome.ok:
                ok_count += 1
                print(f'Описание «{outcome.profile_name}»: {outcome.description}')
            else:
                fail_count += 1
                print(
                    f"profile describe {outcome.profile_name}: {outcome.reason}",
                    file=sys.stderr,
                )
        if not all_flag:
            sys.exit(0 if ok_count == 1 else 1)
        sys.exit(0 if ok_count > 0 else 1)

    elif action == "show":
        name = args.profile_name
        from korra_cli.profiles import (
            get_profile_dir,
            profile_exists,
            _read_config_model,
            _check_gateway_running,
            _count_skills,
            _read_distribution_meta,
            _get_wrapper_dir,
            find_alias_for_profile,
            format_profile_label,
            read_profile_meta,
        )

        if not profile_exists(name):
            print(f'Ошибка: профиль «{name}» не существует.')
            sys.exit(1)
        profile_dir = get_profile_dir(name)
        model, provider = _read_config_model(profile_dir)
        gw = _check_gateway_running(profile_dir)
        skills = _count_skills(profile_dir)
        dist_name, dist_version, dist_source = _read_distribution_meta(profile_dir)
        alias_name = find_alias_for_profile(name)
        display = read_profile_meta(profile_dir).get("display_name", "")

        print(f'Профиль: {format_profile_label(name, display)}')
        print(f'Путь:    {profile_dir}')
        if model:
            print(f'Модель:  {model}' + (f" ({provider})" if provider else ""))
        print(f"Шлюз:    {('работает' if gw else 'остановлен')}")
        print(f'Навыки:  {skills}')
        print(
            f".env:    {'есть' if (profile_dir / '.env').exists() else 'не настроено'}"
        )
        print(
            f"SOUL.md: {'есть' if (profile_dir / 'SOUL.md').exists() else 'не настроено'}"
        )
        if dist_name:
            print(f"Готовый профиль: {dist_name}@{dist_version or '?'}")
            if dist_source:
                print(f'Установлено из: {dist_source}')
            print(f'  Полный манифест: korra profile info {name}')
        if alias_name:
            is_windows = sys.platform == "win32"
            wrapper = _get_wrapper_dir() / (f"{alias_name}.bat" if is_windows else alias_name)
            print(f'Команда: {alias_name} → korra -p {name} ({wrapper})')
        print()

    elif action == "alias":
        name = args.profile_name
        remove = getattr(args, "remove", False)
        custom_name = getattr(args, "alias_name", None)

        from korra_cli.profiles import profile_exists, validate_alias_name

        if not profile_exists(name):
            print(f'Ошибка: профиль «{name}» не существует.')
            sys.exit(1)

        alias_name = custom_name or name

        try:
            validate_alias_name(alias_name)
        except ValueError as exc:
            print(f'Ошибка: {exc}')
            sys.exit(1)

        if remove:
            if remove_wrapper_script(alias_name):
                print(f'✓ Команда «{alias_name}» удалена')
            else:
                print(f'Команда «{alias_name}» для удаления не найдена.')
        else:
            collision = check_alias_collision(alias_name)
            if collision:
                print(f'Ошибка: {collision}')
                sys.exit(1)
            wrapper_path = create_wrapper_script(
                alias_name, target=name if custom_name else None
            )
            if wrapper_path:
                print(f'✓ Команда создана: {wrapper_path}')
                if not _is_wrapper_dir_in_path():
                    print(f'⚠ Папка {_get_wrapper_dir()} не входит в PATH.')

    elif action == "rename":
        from korra_cli.profiles import normalize_profile_name, rename_profile

        try:
            new_dir = rename_profile(args.old_name, args.new_name)
            if normalize_profile_name(args.old_name) != "default":
                print(f'Профиль переименован: {args.old_name} → {args.new_name}')
                print(f'Путь: {new_dir}')
        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "export":
        from korra_cli.profiles import export_profile

        name = args.profile_name
        output = args.output or f"{name}.tar.gz"
        try:
            result_path = export_profile(name, output)
            print(f'✓ Профиль «{name}» сохранён в {result_path}')
        except (ValueError, FileNotFoundError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "import":
        from korra_cli.profiles import import_profile

        try:
            profile_dir = import_profile(
                args.archive, name=getattr(args, "import_name", None)
            )
            name = profile_dir.name
            print(f'✓ Профиль «{name}» загружен в {profile_dir}')

            # Offer to create alias
            collision = check_alias_collision(name)
            if not collision:
                wrapper_path = create_wrapper_script(name)
                if wrapper_path:
                    print(f'  Команда-обёртка создана: {wrapper_path}')
            print()
        except (ValueError, FileExistsError, FileNotFoundError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "install":
        import tempfile
        from korra_cli.profile_distribution import (
            plan_install,
            install_distribution,
            DistributionError,
        )

        try:
            # Preview: stage the distribution into a scratch dir, show the
            # manifest, then do the real install.  The double-stage avoids
            # any side-effects if the user declines.
            with tempfile.TemporaryDirectory(prefix="hermes_dist_preview_") as tmp:
                plan = plan_install(
                    args.source,
                    Path(tmp),
                    override_name=getattr(args, "install_name", None),
                )
                _render_distribution_plan(plan)

                if not getattr(args, "yes", False):
                    try:
                        answer = input('Продолжить установку? [y/N] ').strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        answer = ""
                    if answer not in {"y", "yes"}:
                        print('Установка отменена.')
                        return

            plan = install_distribution(
                args.source,
                name=getattr(args, "install_name", None),
                force=getattr(args, "force", False),
                create_alias=getattr(args, "alias", False),
            )
            print(f'✓ Установлен профиль «{plan.manifest.name}» v{plan.manifest.version}')
            print(f'  Папка профиля: {plan.target_dir}')
            if plan.manifest.env_requires:
                print(
                    f'  Скопируйте .env.EXAMPLE в .env и заполните обязательные ключи: {plan.target_dir}/.env.EXAMPLE'
                )
            if plan.has_cron:
                print(
                    f'  Задачи по расписанию включены в поставку, но не запускаются автоматически. Проверьте: korra -p {plan.manifest.name} cron list'
                )
            print(f'  Запуск: korra -p {plan.manifest.name} chat')
        except (DistributionError, ValueError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "update":
        from korra_cli.profile_distribution import (
            update_distribution,
            read_manifest,
            DistributionError,
        )
        from korra_cli.profiles import get_profile_dir, normalize_profile_name

        name = args.profile_name
        try:
            canon = normalize_profile_name(name)
            current = read_manifest(get_profile_dir(canon))
            if current is None:
                print(
                    f'Профиль «{canon}» не является готовой поставкой: нет distribution.yaml. Обновлять можно только профили, установленные через korra profile install.'
                )
                sys.exit(1)

            force_config = getattr(args, "force_config", False)
            if not getattr(args, "yes", False):
                print(f"Обновление «{canon}» из {current.source or '(нет источника)'}")
                print(f'  Текущая версия: {current.version}')
                if force_config:
                    print('  Указан --force-config: config.yaml будет заменён.')
                else:
                    print('  config.yaml сохранится; для замены добавьте --force-config.')
                print('  Память, беседы, данные входа и .env сохранятся.')
                try:
                    answer = input('Продолжить? [y/N] ').strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer not in {"y", "yes"}:
                    print('Обновление отменено.')
                    return

            plan = update_distribution(canon, force_config=force_config)
            print(f'✓ Профиль «{plan.manifest.name}» обновлён до v{plan.manifest.version}')
            if plan.has_cron:
                print(
                    f'  Файлы расписания обновлены. Проверьте: korra -p {plan.manifest.name} cron list'
                )
        except (DistributionError, ValueError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)

    elif action == "info":
        from korra_cli.profile_distribution import describe_distribution, DistributionError

        try:
            data = describe_distribution(args.profile_name)
        except (DistributionError, ValueError) as e:
            print(f'Ошибка: {e}')
            sys.exit(1)
        if not data:
            print(
                f'Профиль «{args.profile_name}» не является готовой поставкой: нет distribution.yaml.'
            )
            return
        print(f"Готовый профиль: {data.get('name')}")
        print(f"Версия:         {data.get('version', '?')}")
        if data.get("description"):
            print(f"Описание:       {data['description']}")
        if data.get("author"):
            print(f"Автор:          {data['author']}")
        if data.get("license"):
            print(f"Лицензия:       {data['license']}")
        if data.get("hermes_requires"):
            print(f"Нужна Корра     {data['hermes_requires']}")
        if data.get("source"):
            print(f"Источник:       {data['source']}")
        if data.get("installed_at"):
            print(f"Установлено:    {data['installed_at']}")
        env_reqs = data.get("env_requires") or []
        if env_reqs:
            print('Переменные среды:')
            for er in env_reqs:
                tag = "required" if er.get("required", True) else "optional"
                line = f"  {er['name']} ({tag})"
                if er.get("description"):
                    line += f" — {er['description']}"
                print(line)
                if er.get("default") is not None:
                    print(f"      По умолчанию: {er['default']}")
        print()


def _render_distribution_plan(plan) -> None:
    """Print a human-readable summary of a pending distribution install."""
    from korra_cli.profile_distribution import MANIFEST_FILENAME
    mf = plan.manifest
    print(f'Готовый профиль: {mf.name} v{mf.version}')
    if mf.description:
        print(f"  {mf.description}")
    if mf.author:
        print(f'  Автор:    {mf.author}')
    if mf.hermes_requires:
        print(f'  Нужна Корра {mf.hermes_requires}')
    print(f'  Источник: {plan.provenance}')
    print(f'  Папка:    {plan.target_dir}')
    if plan.existing:
        # Distinguish "updating an existing distribution" (well-understood
        # semantics — dist-owned overwritten, config preserved, user data
        # untouched) from "overwriting a hand-built plain profile" (same
        # mechanics but the user didn't sign up for this when they created
        # the profile manually).
        existing_is_distribution = (plan.target_dir / MANIFEST_FILENAME).is_file()
        if existing_is_distribution:
            print('  Профиль уже существует; будут заменены только файлы поставки.')
        else:
            print(
                '  ⚠ Этот профиль уже существует и не относится к готовой поставке. Будут заменены SOUL.md, skills/, cron/ и mcp.json; ручные правки этих файлов потеряются. Память, беседы, auth.json и .env сохранятся.'
            )
    if mf.env_requires:
        print('  Переменные среды:')
        for er in mf.env_requires:
            tag = "required" if er.required else "optional"
            # Check both the current shell environment and the target profile's
            # .env file so we don't nag about keys the user already has set up.
            already = os.environ.get(er.name) is not None
            if not already and plan.target_dir.is_dir():
                env_path = plan.target_dir / ".env"
                if env_path.is_file():
                    try:
                        # .env is written as UTF-8 everywhere in the codebase,
                        # but a Notepad-edited file can carry a BOM — read as
                        # utf-8-sig so the first key isn't hidden behind
                        # U+FEFF (#62617).
                        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
                            line = raw.strip()
                            if not line or line.startswith("#"):
                                continue
                            key = line.split("=", 1)[0].strip()
                            if key == er.name:
                                already = True
                                break
                    except (OSError, UnicodeDecodeError):
                        # UnicodeDecodeError is a ValueError, not an OSError, so
                        # the old guard let a mis-encoded .env abort the whole
                        # install preview. Skip the pre-check instead.
                        pass
            status = "✓ set" if already else ('нужно настроить' if er.required else "—")
            line = f"    • {er.name} ({tag}, {status})"
            if er.description:
                line += f" — {er.description}"
            print(line)
    if plan.has_cron:
        print(
            '  ⚠ В готовом профиле есть задачи по расписанию. Они не запустятся автоматически: проверьте и включите их вручную.'
        )


def _report_dashboard_status() -> int:
    """Print live listening dashboard/serve processes and return the count.

    Serve-mode backends are INCLUDED (#81564): `--stop` kills them, so
    `--status` hiding them left Desktop SSH backends invisible to the CLI —
    an operator could kill what they couldn't see. Ledger-registered serves
    (profiled launches the argv scan can't match) surface via the
    spawn-ledger augmentation in _scan_dashboard_processes.
    """
    from gateway.status import _pid_exists

    live: list[tuple[int, str, str]] = []
    for pid, command in _self()._scan_dashboard_processes():
        runtime = _parse_dashboard_runtime(command)
        if runtime is None:
            continue
        mode, host, port = runtime
        if port <= 0 or not _pid_exists(pid):
            continue
        if not _dashboard_listening(host, port):
            continue
        live.append((pid, command, mode))

    if not live:
        print('Запущенных процессов веб-панели или сервера Корры нет.')
        return 0

    print(f'Работает процессов веб-сервера Корры: {len(live)}')
    for pid, command, mode in live:
        print(f"    PID {pid} [{mode}]: {command}")
    return len(live)


def _dashboard_listening(host: str, port: int) -> bool:
    """True when something is accepting TCP connections at host:port.

    Any listener counts — even a 401 response proves a dashboard is up.
    Used by the unified profile-launch routing to decide attach-vs-start.
    """
    import socket

    try:
        with socket.create_connection((_dashboard_probe_host(host), port), timeout=1.5):
            return True
    except OSError:
        return False


def _maybe_setup_dashboard_auth_interactively(args) -> None:
    """Offer to configure dashboard auth when the gate engages and none exists.

    Called from ``cmd_dashboard`` just before ``start_server``. The auth
    gate engages on every non-loopback bind (``--insecure`` is a no-op since
    the June 2026 hardening) and whenever ``dashboard.public_url`` declares a
    non-loopback browser-facing hostname. ``start_server`` fails closed when no
    ``DashboardAuthProvider`` is registered. Rather than greet an interactive
    operator with that hard error, prompt them to set up the bundled password
    provider on the spot — or point them at ``hermes dashboard register`` for
    OAuth.

    No-ops (so the existing fail-closed ``SystemExit`` remains the backstop)
    when:
      * neither the bind nor configured public URL engages the gate, or
      * a provider is already registered, or
      * stdin/stdout isn't a TTY (Docker/s6, CI, piped ``--no-open`` runs).
    """
    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"

    try:
        from korra_cli.web_server import should_require_dashboard_auth
        if not should_require_dashboard_auth(host):
            return  # local-only bind and URL — gate does not engage
    except Exception:
        return  # if we can't tell, defer to start_server's own gate

    try:
        from korra_cli.dashboard_auth import list_providers
        if list_providers():
            return  # a provider is already configured/registered
    except Exception:
        return

    # Only prompt an interactive operator. Non-TTY callers fall through to
    # start_server's fail-closed SystemExit (with the corrected fix hint).
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    print()
    print(f'⚠ Для этой настройки веб-панели требуется вход: {host}.')
    print(
        '  Публичные адреса и внешний dashboard.public_url требуют авторизации. --insecure её не отключает.'
    )
    print()
    print('  Выберите способ входа в веб-панель:')
    print('    [1] Имя и пароль — быстро, для доверенной локальной сети или VPN')
    print('    [2] OAuth через Nous Portal: korra dashboard register')
    print('    [3] Отмена')
    print()

    try:
        choice = input('  Выберите [1]: ').strip() or "1"
    except (EOFError, KeyboardInterrupt):
        print('  Отменено.')
        sys.exit(1)

    if choice == "2":
        print()
        print(
            '  На компьютере с веб-панелью выполните korra dashboard register, затем запустите панель снова. Команда создаст клиента OAuth Nous и сохранит HERMES_DASHBOARD_OAUTH_CLIENT_ID в .env профиля. Документация: https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard#authentication-gated-mode'
        )
        sys.exit(0)

    if choice not in ("1",):
        print('  Отменено.')
        sys.exit(1)

    # ── Username/password setup ──────────────────────────────────────────
    import getpass
    import secrets

    print()
    try:
        username = line_input('  Имя пользователя [admin]: ').strip() or "admin"
        password = getpass.getpass("  Password: ")
        confirm = getpass.getpass('  Повторите пароль: ')
    except (EOFError, KeyboardInterrupt):
        print('  Отменено.')
        sys.exit(1)

    if not password:
        print('  ✗ Пароль пуст. Настройка отменена.')
        sys.exit(1)
    if password != confirm:
        print('  ✗ Пароли не совпадают. Настройка отменена.')
        sys.exit(1)

    try:
        from plugins.dashboard_auth.basic import hash_password
    except Exception as exc:
        print(f'  ✗ Не удалось загрузить модуль входа по паролю: {exc}')
        sys.exit(1)

    password_hash = hash_password(password)
    # A stable token-signing secret so sessions survive a dashboard restart.
    secret = secrets.token_urlsafe(32)

    try:
        from korra_cli.config import load_config, save_config
        from korra_cli.plugins_cmd import ensure_basic_auth_plugin_enabled_in_config

        cfg = load_config()
        dash = cfg.setdefault("dashboard", {})
        basic = dash.setdefault("basic_auth", {})
        basic["username"] = username
        basic["password_hash"] = password_hash
        # Never persist plaintext: clear any stale plaintext password key.
        basic["password"] = ""
        if not str(basic.get("secret", "") or "").strip():
            basic["secret"] = secret
        # The bundled basic provider is a backend plugin that still honours
        # plugins.disabled. Unblock it when we just wrote basic_auth so the
        # discover_plugins(force=True) call below can register the provider
        # (#54489). Surface the mutation so an operator who deliberately
        # disabled it isn't surprised.
        if ensure_basic_auth_plugin_enabled_in_config(cfg):
            print(
                '  ✓ Встроенный плагин входа basic включён повторно; ранее он был в plugins.disabled'
            )
        save_config(cfg)
    except Exception as exc:
        print(f'  ✗ Не удалось сохранить config.yaml: {exc}')
        sys.exit(1)

    # Re-run plugin discovery so the basic provider registers from the
    # just-written config before start_server's gate check runs.
    try:
        from korra_cli.plugins import discover_plugins

        discover_plugins(force=True)
    except Exception as exc:
        print(f'  ⚠ Не удалось обновить список плагинов: {exc}. Вход может остаться заблокированным. Задайте пароль заново или перезапустите панель.')

    print()
    print(f'  ✓ Вход по имени и паролю настроен; пользователь: {username}.')
    print('    Сохранено в dashboard.basic_auth файла config.yaml.')
    print('    Войдите в веб-панель с этими данными.')
    print()


def _read_ssh_session_token_file(path: str) -> str:
    """Read and unlink a Desktop SSH token from its private runtime directory."""
    if sys.platform == "win32":
        from korra_cli.windows_ssh_runtime import read_token
        return read_token(path)

    import stat as _stat
    from pathlib import Path as _Path

    if not os.path.isabs(path):
        raise SystemExit('--ssh-session-token-file: нужен абсолютный путь')

    token_path = _Path(path)
    # The Desktop client writes the token under $HOME/.hermes/desktop-ssh: a
    # literal "~/.hermes/desktop-ssh" in apps/desktop/electron/remote-lifecycle.ts
    # expanded against the account's $HOME, independent of HERMES_HOME and the
    # active profile. Anchor validation to that same OS-home path, NOT to
    # get_hermes_home(): a non-default sticky profile (or any HERMES_HOME pointing
    # elsewhere, e.g. a Docker /opt/data root) re-homes get_hermes_home() and
    # would otherwise reject every token the client legitimately wrote (#69551).
    token_root = _Path.home() / ".hermes" / "desktop-ssh"
    try:
        relative = token_path.relative_to(token_root)
    except ValueError as exc:
        raise SystemExit('--ssh-session-token-file должен находиться внутри desktop-ssh') from exc
    if len(relative.parts) != 2 or not re.fullmatch(r"[0-9a-f]{32}", relative.parts[0]):
        raise SystemExit('--ssh-session-token-file: неверный путь среды выполнения')
    if not re.fullmatch(r"[0-9a-f]{16}\.token", relative.parts[1]):
        raise SystemExit('--ssh-session-token-file: неверное имя файла')

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    root_fd = -1
    directory_fd = -1
    file_fd = -1
    try:
        try:
            root_fd = os.open(token_root, directory_flags)
            root_stat = os.fstat(root_fd)
            if not _stat.S_ISDIR(root_stat.st_mode):
                raise SystemExit('--ssh-session-token-file: небезопасная корневая папка среды')
            if hasattr(os, "getuid") and root_stat.st_uid != os.getuid():
                raise SystemExit('--ssh-session-token-file: корневая папка среды принадлежит другому пользователю')
            directory_fd = os.open(relative.parts[0], directory_flags, dir_fd=root_fd)
            directory_stat = os.fstat(directory_fd)
            if not _stat.S_ISDIR(directory_stat.st_mode):
                raise SystemExit('--ssh-session-token-file: небезопасная родительская папка')
            if hasattr(os, "getuid") and directory_stat.st_uid != os.getuid():
                raise SystemExit('--ssh-session-token-file: родительская папка принадлежит другому пользователю')
            if (directory_stat.st_mode & 0o777) != 0o700:
                raise SystemExit('--ssh-session-token-file: небезопасные права родительской папки')
            file_fd = os.open(relative.parts[1], file_flags, dir_fd=directory_fd)
        except SystemExit:
            raise
        except OSError as exc:
            if exc.errno == getattr(__import__("errno"), "ELOOP", -1):
                raise SystemExit('--ssh-session-token-file является символической ссылкой') from exc
            raise SystemExit('--ssh-session-token-file недоступен') from exc

        file_stat = os.fstat(file_fd)
        if not _stat.S_ISREG(file_stat.st_mode):
            raise SystemExit('--ssh-session-token-file не является обычным файлом')
        if file_stat.st_size != 64:
            raise SystemExit('--ssh-session-token-file содержит неверный токен')
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            raise SystemExit('--ssh-session-token-file принадлежит другому пользователю')
        if hasattr(os, "getuid") and (file_stat.st_mode & 0o777) & ~0o600:
            raise SystemExit('--ssh-session-token-file имеет небезопасные права доступа')

        with os.fdopen(file_fd, "r", encoding="utf-8") as token_stream:
            file_fd = -1
            token = token_stream.read(65)

        if not re.fullmatch(r"[0-9a-f]{64}", token):
            raise SystemExit('--ssh-session-token-file содержит неверный токен')
        return token
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            try:
                os.unlink(relative.parts[1], dir_fd=directory_fd)
            except OSError:
                pass
            os.close(directory_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _is_electron_packaged_web_dist(path: str) -> bool:
    """True when *path* looks like an Electron-packaged renderer dist.

    Packaged Desktop sets ``HERMES_WEB_DIST`` to ``.../app.asar/dist`` or
    ``.../app.asar.unpacked/dist``. A standalone ``hermes dashboard`` that
    inherits that value serves the desktop frontend in the browser
    (issue #52945 — "Desktop IPC bridge is unavailable").
    """
    if not path:
        return False
    # Both app.asar and app.asar.unpacked contain this marker; normalize
    # separators so Windows paths match too.
    return "app.asar" in path.replace("\\", "/")


def cmd_dashboard(args):
    """Start the web UI server, or (with --stop/--status) manage running ones."""
    _token_file = getattr(args, "ssh_session_token_file", None)
    if _token_file and (
        getattr(args, "status", False) or getattr(args, "stop", False)
    ):
        raise SystemExit('--ssh-session-token-file нельзя использовать с --status или --stop')

    # --status: report running dashboards and exit, no deps needed.
    if getattr(args, "status", False):
        count = _report_dashboard_status()
        sys.exit(0 if count == 0 else 0)  # status is informational, always 0

    # --stop: kill any running dashboards and exit, no deps needed.
    if getattr(args, "stop", False):
        pids = _find_stale_dashboard_pids()
        if not pids:
            print('Запущенных процессов веб-панели Корры нет.')
            sys.exit(0)
        # Reuse the same SIGTERM-grace-SIGKILL path used after `hermes update`.
        _self()._kill_stale_dashboard_processes(reason="requested via --stop")
        # _kill_stale_dashboard_processes prints outcomes itself.  Exit 0 if
        # we killed at least one, 1 if they were all unkillable.
        remaining = _find_stale_dashboard_pids()
        sys.exit(1 if remaining else 0)

    # `serve` is the headless backend: no UI build, no SPA mount, neutral
    # ready sentinel. Resolved once and threaded through the re-exec, the
    # build gate, and start_server.
    _headless_backend = getattr(args, "headless_backend", False)
    _ssh_owner_nonce = getattr(args, "ssh_owner_nonce", None)
    if _ssh_owner_nonce and not re.fullmatch(r"[0-9a-f]{16}", _ssh_owner_nonce):
        raise SystemExit('--ssh-owner-nonce должен содержать 16 шестнадцатеричных символов в нижнем регистре')
    _ssh_session_token = None
    if _token_file and not _headless_backend:
        raise SystemExit('--ssh-session-token-file доступен только с korra serve')

    # ── Sanitize Desktop-inherited env that hijacks a standalone launch ─
    # Desktop Electron spawns its backend with HERMES_DESKTOP=1 plus
    # HERMES_WEB_DIST=<packaged app.asar[/unpacked]/dist> (and often
    # HERMES_SERVE_HEADLESS=1 on the serve path). A shell that inherits
    # those vars then runs `hermes dashboard` would otherwise:
    #   - serve the desktop renderer → "Desktop IPC bridge is unavailable"
    #     (issue #52945), or
    #   - disable the SPA via inherited HERMES_SERVE_HEADLESS.
    # Only strip Electron-packaged WEB_DIST contamination — caller-managed
    # HERMES_WEB_DIST overrides (dev / custom builds) must still work.
    # The desktop-spawned backend itself (HERMES_DESKTOP=1) keeps its dist.
    # Intentionally headless `serve` re-sets HERMES_SERVE_HEADLESS below.
    if korra_env("KORRA_DESKTOP") != "1":
        _inherited_web_dist = korra_env("KORRA_WEB_DIST", "")
        if _is_electron_packaged_web_dist(_inherited_web_dist):
            korra_env_pop(os.environ, "KORRA_WEB_DIST")
    if not _headless_backend:
        korra_env_pop(os.environ, "KORRA_SERVE_HEADLESS")

    # ── Unified profile launch routing ────────────────────────────────
    # The dashboard is a MACHINE management surface: it can read/write any
    # profile via the per-request ?profile= scoping. Running one dashboard
    # per profile just fragments that (port collisions, N processes, and a
    # "which dashboard am I on?" guessing game). So when a NAMED profile
    # launches the dashboard (`worker dashboard` → HERMES_HOME points into
    # profiles/), default to the machine dashboard:
    #   - already running → open the browser at ?profile=<name> and exit
    #   - not running     → re-exec as the machine dashboard (pinned to the
    #     default profile so _apply_profile_override can't re-route through
    #     the sticky active_profile file) with the launching profile
    #     preselected in the UI's switcher.
    # `--isolated` opts out and preserves the old per-profile behavior.
    try:
        from korra_cli.profiles import get_active_profile_name
        _launch_profile = get_active_profile_name()
    except Exception:
        _launch_profile = "default"

    if (
        _launch_profile not in ("default", "custom")
        and not getattr(args, "isolated", False)
        and not getattr(args, "open_profile", "")
        # Desktop pool backends are intentionally per-profile.
        and korra_env("KORRA_DESKTOP") != "1"
    ):
        url = f"http://{args.host or '127.0.0.1'}:{args.port}/?profile={_launch_profile}"
        if _dashboard_listening(args.host, args.port):
            print(f'Общий сервер панели уже работает на порту {args.port}.')
            print(f'  Управление профилем «{_launch_profile}»: {url}')
            if not args.no_open:
                try:
                    import webbrowser
                    webbrowser.open(url)
                except Exception:
                    pass
            sys.exit(0)

        print(
            f'Подключаемся к общему серверу панели с выбранным профилем «{_launch_profile}». Для отдельного сервера профиля используйте --isolated.'
        )
        reexec_argv = [
            sys.executable, "-m", "korra_cli.main",
            "-p", "default",
            # Preserve the lean serve path across the re-exec so a named-profile
            # `serve` doesn't silently rebuild the UI as `dashboard`.
            "serve" if _headless_backend else "dashboard",
            "--port", str(args.port),
            "--host", args.host,
            "--open-profile", _launch_profile,
        ]
        if _ssh_owner_nonce:
            reexec_argv.extend(["--ssh-owner-nonce", _ssh_owner_nonce])
        if _token_file:
            reexec_argv.extend(["--ssh-session-token-file", _token_file])
        if args.no_open:
            reexec_argv.append("--no-open")
        if getattr(args, "insecure", False):
            reexec_argv.append("--insecure")
        if getattr(args, "skip_build", False):
            reexec_argv.append("--skip-build")
        from tools.environments.local import build_subprocess_env
        # Exact env preservation: HERMES_HOME is explicitly pinned to the
        # machine root below — the factory must not re-inject a profile home.
        env = build_subprocess_env(scrub_secrets=False, inherit_profile_home=False)
        # Pin the child to the machine ROOT, not the launching profile's
        # HERMES_HOME.  We must resolve the root explicitly instead of just
        # dropping HERMES_HOME: in the Docker layout the machine root is
        # /opt/data (set via `ENV HERMES_HOME=/opt/data`), so an unset
        # HERMES_HOME falls back to $HOME/.hermes = /opt/data/.hermes — an
        # empty, auto-seeded home where the dashboard sees only the default
        # profile and the install-method stamp is missing (so the Docker
        # update-button guard also misfires).  get_default_hermes_root()
        # returns the root for both layouts: ~/.hermes for a standard install
        # and /opt/data for Docker (it strips a trailing profiles/<name>).
        # See the support report for the double-mount workaround this avoids.
        try:
            from korra_constants import get_default_hermes_root
            korra_env_set(env, "KORRA_HOME", str(get_default_hermes_root()))
        except Exception:
            # Best-effort: if root resolution fails, fall back to the prior
            # behaviour (drop HERMES_HOME) rather than block the reroute.
            korra_env_pop(env, "KORRA_HOME")
        # On Windows, os.execvpe() does not truly replace the process — it
        # spawns via CreateProcess then the parent exits.  Under Python 3.14+
        # this can crash with STATUS_ACCESS_VIOLATION (0xC0000005) when
        # re-executing the dashboard for a non-default profile.  Use
        # subprocess.Popen + sys.exit() on Windows to avoid the crash.
        if sys.platform == "win32":
            proc = subprocess.Popen(reexec_argv, env=env)
            sys.exit(proc.wait())
        else:
            os.execvpe(sys.executable, reexec_argv, env)

    # Apply the final process/profile policy after dashboard routing, but before
    # importing the web server or opening dashboard state. Applying it before a
    # named-profile re-exec could leak that profile's higher limit into the
    # machine/default dashboard, whose lower policy intentionally cannot undo it.
    # This also covers Desktop SSH's isolated `serve` child, which does not route.
    from korra_cli.resource_limits import apply_nofile_soft_limit

    apply_nofile_soft_limit()

    if _token_file:
        _ssh_session_token = _read_ssh_session_token_file(_token_file)

    # Attach gui.log early so dashboard startup/build failures are captured in
    # the same logs directory as every other Hermes surface.
    try:
        from korra_logging import setup_logging as _setup_logging_gui
        _setup_logging_gui(mode="gui")
    except Exception:
        pass

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as e:
        print('Не установлены зависимости веб-панели: fastapi и uvicorn.')
        print(
            f'Переустановите пакет в этот интерпретатор для обновления метаданных: cd {PROJECT_ROOT}, затем {sys.executable} -m pip install -e . Если pip отсутствует, используйте uv pip install -e .'
        )
        print(f'Ошибка загрузки модуля: {e}')
        sys.exit(1)

    # Seed bundled skills on first dashboard launch so the desktop GUI's
    # skills picker / agent skill discovery sees the bundled library.
    # cmd_chat does this in its own pre-dispatch block; the dashboard
    # backend is the desktop's primary entrypoint and needs the same.
    _sync_bundled_skills_quietly()

    # Bridge terminal.* config into the TERMINAL_* env vars for THIS process,
    # mirroring the CLI (cli.py env_mappings) and gateway (gateway/run.py
    # _terminal_env_map) startup bridges. The dashboard/serve backend runs
    # agents in-process (tui_gateway.ws → server._make_agent) and ticks cron
    # jobs itself when desktop-spawned — without this bridge those consumers
    # saw an unset TERMINAL_ENV and silently ran every command on the host
    # even when config.yaml selects `terminal.backend: docker`
    # (#63141, #54449, #61115, #65696). PTY chat spawns already bridge their
    # child env copy; this covers the in-process consumers.
    try:
        from korra_cli.config import apply_terminal_config_to_env

        apply_terminal_config_to_env()
    except Exception:
        logger.debug("terminal config → env bridge failed for dashboard/serve",
                     exc_info=True)

    if _headless_backend:
        # Don't build the SPA, and tell mount_spa() (read at web_server import
        # below) to disable it even if a stray dist exists. Set it first.
        korra_env_set(os.environ, "KORRA_SERVE_HEADLESS", "1")
    elif not korra_env_present("KORRA_WEB_DIST") and not getattr(args, "skip_build", False):
        if not _build_web_ui(PROJECT_ROOT / "web", fatal=True):
            sys.exit(1)
    elif getattr(args, "skip_build", False):
        # --build-mode skip trusts the caller to have pre-built the web UI.
        # Verify the dist actually exists; otherwise the server will start
        # and serve 404s with no obvious cause (issue #23817).
        _dist_root = (
            Path(korra_env("KORRA_WEB_DIST"))
            if korra_env_present("KORRA_WEB_DIST")
            else PROJECT_ROOT / "korra_cli" / "web_dist"
        )
        if not (_dist_root / "index.html").exists():
            # The caller promised a pre-built dist but there isn't one.
            # Instead of hard-failing (issue #59288 — desktop launches with
            # --build-mode skip after a wipe of web_dist), warn and attempt
            # ONE recovery build through the normal build path. Only the
            # default dist location is recoverable: a custom HERMES_WEB_DIST
            # points at a caller-managed directory the build cannot populate.
            _recoverable = not korra_env_present("KORRA_WEB_DIST")
            if _recoverable:
                print(f'⚠ Указан --skip-build, но веб-сборка не найдена: {_dist_root}')
                print('  Пробуем один раз восстановить веб-сборку…')
                _build_web_ui(PROJECT_ROOT / "web", fatal=True)
            if not (_dist_root / "index.html").exists():
                print(f'✗ Указан --skip-build, но веб-сборка не найдена: {_dist_root}')
                if _recoverable:
                    print('  Восстановительная сборка не создала пригодный dist.')
                print('  Сначала соберите: npm install --workspace web && npm run build -w web')
                print('  Или уберите --skip-build для автоматической сборки.')
                sys.exit(1)
            print('  ✓ Восстановительная сборка создала веб-интерфейс')
        print(f'→ Сборка веб-панели пропущена (--skip-build); используем dist из {_dist_root}')
    else:
        # HERMES_WEB_DIST is set without --skip-build: the build is skipped
        # (the env var points at a caller-managed dist), so validate it the
        # same way the --skip-build branch does — otherwise the server starts
        # and serves 404s with no obvious cause (same failure mode as #23817,
        # via the env-var path).
        _dist_root = Path(korra_env("KORRA_WEB_DIST", "")).expanduser()
        if not (_dist_root / "index.html").exists():
            print(f'✗ HERMES_WEB_DIST задан, но веб-сборка не найдена: {_dist_root}')
            print('  Сначала соберите: npm install --workspace web && npm run build -w web')
            print('  Или снимите HERMES_WEB_DIST для сборки и использования обычной веб-панели.')
            sys.exit(1)
        # Write the expanded path back: web_server reads HERMES_WEB_DIST raw
        # at import (no expanduser), so a validated "~/dist" would otherwise
        # pass here and still 404 there.
        korra_env_set(os.environ, "KORRA_WEB_DIST", str(_dist_root))
        print(f'→ Используем веб-сборку из HERMES_WEB_DIST: {_dist_root}')

    # Discover and load plugins so any DashboardAuthProvider plugin
    # (e.g. plugins/dashboard_auth/nous) registers BEFORE start_server's
    # fail-closed gate check runs. The top-level argparse setup skips
    # plugin discovery for built-in subcommands like ``dashboard`` to
    # save ~500ms startup; we have to trigger it explicitly here because
    # the dashboard's server-side runtime depends on plugin-registered
    # providers (image_gen, web, dashboard_auth, …).
    try:
        from korra_cli.plugins import discover_plugins
        discover_plugins()
    except Exception as exc:
        # Discovery failures must not block dashboard startup outright —
        # log and proceed; the gate's fail-closed branch will surface
        # the missing-provider state if it matters.
        print(f'⚠ Не удалось найти плагины: {exc}', file=sys.stderr)

    # Desktop chat uses the dashboard's in-process /api/ws gateway, which builds
    # agents via tui_gateway.server._make_agent.  That path only snapshots the
    # tool registry — it never starts MCP discovery (the stdio TUI does that in
    # tui_gateway/entry.py, which the dashboard process doesn't run).  Without
    # this, a profile's configured MCP servers never connect, so desktop
    # sessions show no MCP tools.  Spawn discovery in the background here so a
    # slow/dead server can't block dashboard startup.
    try:
        from korra_cli.mcp_startup import start_background_mcp_discovery

        start_background_mcp_discovery(
            logger=logger,
            thread_name="dashboard-mcp-discovery",
        )
    except Exception:
        logger.debug(
            "Background MCP tool discovery failed at dashboard startup",
            exc_info=True,
        )

    from korra_cli.web_server import start_server

    # Interactive auth setup: if this bind will engage the auth gate but no
    # provider is registered yet, offer to configure one here (TTY only)
    # instead of hard-failing inside start_server. Non-interactive callers
    # (Docker/s6, CI, --no-open pipelines) fall through to start_server's
    # fail-closed SystemExit unchanged.
    _maybe_setup_dashboard_auth_interactively(args)

    # The in-browser Chat tab (the embedded TUI over PTY/WebSocket) is always
    # available — the desktop app and the dashboard's own Chat tab both rely on
    # the `/api/ws` + `/api/pty` sockets, so there is no reason to gate them.
    start_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_public=getattr(args, "insecure", False),
        initial_profile=getattr(args, "open_profile", "") or "",
        headless=_headless_backend,
        ssh_session_token=_ssh_session_token,
        ssh_owner_nonce=_ssh_owner_nonce,
    )


def cmd_dashboard_register(args):
    """Register a self-hosted dashboard OAuth client with Nous Portal."""
    from korra_cli.dashboard_register import cmd_dashboard_register as _impl

    _impl(args)


def cmd_gateway_enroll(args):
    """Enroll a self-hosted gateway with a relay connector."""
    from korra_cli.gateway_enroll import cmd_gateway_enroll as _impl

    _impl(args)


def cmd_completion(args, parser=None):
    """Print shell completion script."""
    from korra_cli.completion import generate_bash, generate_zsh, generate_fish

    shell = getattr(args, "shell", "bash")
    if shell == "zsh":
        print(generate_zsh(parser))
    elif shell == "fish":
        print(generate_fish(parser))
    else:
        print(generate_bash(parser))


def cmd_prompt_size(args):
    """Show a byte/char breakdown of the system prompt + tool schemas."""
    from korra_cli.prompt_size import cmd_prompt_size as _impl

    _impl(args)


def cmd_logs(args):
    """View and filter Hermes log files."""
    from korra_cli.logs import tail_log, list_logs

    log_name = getattr(args, "log_name", "agent") or "agent"

    if log_name == "list":
        list_logs()
        return

    tail_log(
        log_name,
        num_lines=getattr(args, "lines", 50),
        follow=getattr(args, "follow", False),
        level=getattr(args, "level", None),
        session=getattr(args, "session", None),
        since=getattr(args, "since", None),
        component=getattr(args, "component", None),
    )


def cmd_console(args):
    """Open the safe Hermes command console."""
    from korra_cli.console_engine import run_console_repl

    return run_console_repl()


def _build_provider_choices() -> list[str]:
    """Build the --provider choices list from CANONICAL_PROVIDERS + 'auto'."""
    try:
        from korra_cli.models import CANONICAL_PROVIDERS as _cp
        return ["auto"] + [p.slug for p in _cp]
    except Exception:
        # Fallback: static list guarantees the CLI always works
        return [
            "auto", "openrouter", "nous", "openai-codex", "xai-oauth", "copilot-acp", "copilot",
            "anthropic", "gemini", "vertex", "xai", "bedrock", "azure-foundry",
            "ollama-cloud", "huggingface", "zai", "kimi-coding", "kimi-coding-cn",
            "stepfun", "minimax", "minimax-cn", "kilocode", "novita", "xiaomi", "arcee",
            "nvidia", "deepseek", "alibaba", "qwen-oauth", "opencode-zen", "opencode-go",
        ]


# Top-level subcommands that argparse knows about WITHOUT running plugin
# discovery.  Used to short-circuit eager plugin imports (which can take
# 500ms+ pulling in google.cloud.pubsub_v1, aiohttp, grpc, etc.) when the
# user's invocation clearly doesn't need any plugin-registered subcommand.
#
# Keep this in sync with the ``subparsers.add_parser("NAME", ...)`` calls
# below in ``main()``. Missing an entry here only costs a one-time
# discovery; extra entries here would let a plugin command silently fail
# to parse.
_BUILTIN_SUBCOMMANDS = frozenset(
    {
        "acp", "approvals", "auth", "backup", "bundles", "checkpoints", "claw", "completion",
        "computer-use",
        "config", "console", "cron", "curator", "dashboard", "serve", "debug", "doctor",
        "dump", "egress", "fallback", "gateway", "hooks", "import", "import-agent", "insights",
        "gui", "desktop", "kanban", "login", "logout", "logs", "lsp", "mcp", "memory", "migrate", "moa",
        "journey", "memory-graph", "learning",
        "model", "monitoring", "pairing", "pause", "peer", "pets", "plugins", "portal", "profile",
        "project", "proxy",
        "prompt-size",
        "resume",
        "send", "sessions", "setup",
        "skin", "skills", "slack", "status", "sync", "tools", "uninstall", "update",
        "webhook", "whatsapp", "whatsapp-cloud", "worktree", "chat", "secrets", "security",
        "browser",
        "verify",
        # Help-ish invocations — plugin commands not being listed in
        # top-level --help is an acceptable trade-off for skipping an
        # expensive eager import of every bundled plugin module.
        "help",
    }
)


def _first_positional_argv() -> str | None:
    """Return the first non-flag, non-flag-value token in ``sys.argv[1:]``.

    Used by ``main()`` to decide whether plugin discovery has to run at
    argparse-setup time. Handles common invocations like
    ``hermes -m gpt5 --provider openai chat "msg"`` by skipping the
    values attached to known top-level flags.

    Does NOT fully simulate argparse — unknown ``--foo=bar`` / ``--foo
    bar`` flags degrade gracefully (``bar`` may be wrongly classified as
    a positional, which at worst forces a one-time plugin discovery).
    """
    from korra_cli._parser import top_level_value_flag_sets

    required_value_flags, optional_value_flags = top_level_value_flag_sets()
    value_flags = required_value_flags | optional_value_flags
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            # Everything after ``--`` is positional.
            if i + 1 < len(argv):
                return argv[i + 1]
            return None
        if tok.startswith("-"):
            # ``--flag=value`` carries its value inline — single token.
            if "=" in tok:
                i += 1
                continue
            if tok in value_flags and i + 1 < len(argv):
                i += 2
                continue
            i += 1
            continue
        return tok
    return None


def _plugin_cli_discovery_needed() -> bool:
    """True when the CLI might be invoking a plugin-registered subcommand.

    Returning False lets ``main()`` skip plugin discovery entirely during
    argparse setup, saving ~500-650ms per invocation for users whose
    enabled plugins don't contribute any CLI command.
    """
    first = _first_positional_argv()
    if first is None:
        # Bare ``hermes`` or only flags → defaults to ``chat``.
        return False
    if first in _BUILTIN_SUBCOMMANDS:
        return False
    # Unknown token — could be a plugin subcommand, OR a chat prompt
    # starting with a non-flag word. Either way we need discovery: if it
    # IS a plugin command, argparse needs the subparser; if it's a chat
    # prompt, argparse will route it via positional handling and the
    # extra discovery cost is amortized over a full agent run anyway.
    return True


def _resolve_deferred_platform_cli_command(command_name: str | None) -> None:
    """Materialize the deferred platform whose top-level CLI command matches.

    Bundled platform plugins are cheap-registered as *deferred* entries to
    avoid importing every gateway SDK during normal startup. A platform that
    registers a top-level ``hermes <name>`` command (e.g. Photon ->
    ``ctx.register_cli_command(name="photon", ...)``) only runs that side
    effect when its module is imported. On the unknown-top-level-command slow
    path, ``discover_plugins()`` records the deferred loader but does not
    import it, so the CLI registration never happens and ``hermes photon``
    fails with argparse ``invalid choice`` (issue #54678).

    Resolving only the platform whose name matches the first positional token
    keeps normal startup cheap while making the targeted command available.
    """
    if not command_name:
        return
    try:
        from gateway.platform_registry import platform_registry

        platform_registry.get(command_name)
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Deferred platform CLI resolution failed for %s: %s",
            command_name,
            exc,
        )


_AGENT_COMMANDS = {None, "chat", "acp", "rl"}
_AGENT_SUBCOMMANDS = {
    "cron": ("cron_command", {"run", "tick"}),
    "gateway": ("gateway_command", {"run"}),
    "mcp": ("mcp_action", {"serve"}),
}


def _is_tui_chat_launch(args) -> bool:
    return bool(getattr(args, "tui", False) or korra_env("KORRA_TUI") == "1")


def _command_has_dedicated_mcp_startup(args) -> bool:
    if args.command == "acp":
        return True
    if args.command == "gateway" and getattr(args, "gateway_command", None) == "run":
        return True
    if args.command == "cron" and getattr(args, "cron_command", None) in {"run", "tick"}:
        return True
    return False


def _should_background_mcp_startup(args) -> bool:
    if _is_tui_chat_launch(args):
        return False
    return args.command in {None, "chat", "rl"}


def _prepare_agent_startup(args) -> None:
    """Discover plugins/MCP/hooks for commands that can run an agent turn."""
    # --yolo: chokepoint guarantee that HERMES_YOLO_MODE is set before ANY
    # plugin/tool discovery below imports tools.approval, which freezes
    # _YOLO_MODE_FROZEN at import time (PR #7994 security design).  main()'s
    # dispatch path also sets this earlier, but _prepare_agent_startup() is
    # reachable from other launchers too (e.g. the Termux fast-CLI path),
    # so the guarantee lives here where the import is actually triggered
    # (#60328).
    if getattr(args, "yolo", False):
        korra_env_set(os.environ, "KORRA_YOLO_MODE", "1")
    _apply_safe_mode(args)

    _sub_attr, _sub_set = _AGENT_SUBCOMMANDS.get(args.command, (None, None))
    if not (
        args.command in _AGENT_COMMANDS
        or (_sub_attr and getattr(args, _sub_attr, None) in _sub_set)
    ):
        return

    _accept_hooks = bool(getattr(args, "accept_hooks", False))
    if not _is_tui_chat_launch(args):
        # The TUI backend process does its own plugin discovery; the launcher
        # only spawns Node, so discovery here would be thrown-away work.
        try:
            from korra_cli.plugins import start_background_plugin_discovery

            # Discovery runs in a daemon thread so its ~150ms of manifest
            # scanning + plugin imports overlaps the rest of startup (cli /
            # prompt_toolkit imports, worktree git calls). Correctness is
            # unchanged: every synchronous reader goes through
            # discover_plugins(), which joins this thread first — including
            # the discover_plugins() call model_tools makes at import time,
            # which happens before any tool list is built.
            start_background_plugin_discovery()
        except Exception:
            logger.warning(
                "plugin discovery failed at CLI startup",
                exc_info=True,
            )
    _run_inline_mcp_discovery = True
    if _is_tui_chat_launch(args):
        # The TUI launcher hands off to a dedicated startup path that already
        # backgrounds MCP discovery with a bounded join before the first tool
        # snapshot.
        _run_inline_mcp_discovery = False
    elif _command_has_dedicated_mcp_startup(args):
        # These entrypoints already do their own MCP startup later on the real
        # runtime path (gateway executor, ACP launcher, cron job runner).
        _run_inline_mcp_discovery = False
    elif _should_background_mcp_startup(args):
        try:
            from korra_cli.mcp_startup import start_background_mcp_discovery

            start_background_mcp_discovery(
                logger=logger,
                thread_name="cli-mcp-discovery",
            )
        except Exception:
            logger.debug(
                "Background MCP tool discovery failed at CLI startup",
                exc_info=True,
            )
        _run_inline_mcp_discovery = False
    if _run_inline_mcp_discovery:
        try:
            # MCP tool discovery remains synchronous for entrypoints that do
            # not own a later bounded/executor startup path.
            from tools.mcp_tool import discover_mcp_tools

            discover_mcp_tools()
        except Exception:
            logger.debug(
                "MCP tool discovery failed at CLI startup",
                exc_info=True,
            )
    try:
        from korra_cli.config import load_config
        from agent.shell_hooks import register_from_config

        _hooks_cfg = load_config()
        register_from_config(_hooks_cfg, accept_hooks=_accept_hooks)

        from agent.outbound_webhooks import (
            register_from_config as register_outbound_webhooks,
        )

        register_outbound_webhooks(_hooks_cfg)
    except Exception:
        logger.debug(
            "shell-hook registration failed at CLI startup",
            exc_info=True,
        )


def _apply_safe_mode(args) -> None:
    if not getattr(args, "safe_mode", False):
        return
    korra_env_set(os.environ, "KORRA_SAFE_MODE", "1")
    korra_env_set(os.environ, "KORRA_IGNORE_USER_CONFIG", "1")
    korra_env_set(os.environ, "KORRA_IGNORE_RULES", "1")


def _set_chat_arg_defaults(args) -> None:
    for attr, default in [
        ("query", None),
        ("model", None),
        ("provider", None),
        ("toolsets", None),
        ("verbose", False),
        ("resume", None),
        ("continue_last", None),
        ("worktree", False),
    ]:
        if not hasattr(args, attr):
            setattr(args, attr, default)


def _try_fast_chat_launch() -> bool:
    """Fast path for unambiguous interactive chat launches (all hosts).

    ``hermes`` / ``hermes -w -s foo --yolo`` / ``hermes chat`` don't need the
    full argparse tree: building all ~40 subcommand parsers costs ~140ms of
    pure-Python argparse setup plus their module imports, none of which the
    chat path uses. Parse the lightweight top-level/chat parser instead and
    dispatch straight to ``cmd_chat``.

    Bails out (returns False) whenever the invocation is not certainly a
    chat launch — a subcommand positional, ``--help``, unknown flags — so
    every other path still goes through the full parser unchanged. Mirrors
    ``_try_termux_fast_cli_launch`` minus the Termux-specific deferred
    startup; kept separate so phone-tuned behavior doesn't leak to desktops.
    """
    if korra_env("KORRA_DISABLE_FAST_CHAT_LAUNCH") == "1":
        return False
    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        return False
    # Container-aware routing must win: when NixOS container mode is
    # active, EVERY invocation is forwarded into the managed container.
    try:
        from korra_cli.config import get_container_exec_info
        if get_container_exec_info():
            return False
    except Exception:
        return False
    # TUI launches have their own startup path (bounded MCP joins etc.) —
    # keep them on full dispatch outside Termux.
    if _wants_tui_early(argv):
        return False
    if _first_positional_argv() not in {None, "chat"}:
        return False

    from korra_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)
    try:
        args, unknown = parser.parse_known_args(_coalesce_session_name_args(argv))
    except SystemExit:
        return False
    if unknown:
        # Flags the light parser doesn't know — could belong to a plugin
        # subcommand or a newer full-parser flag. Fall back to full dispatch.
        return False
    if getattr(args, "version", False):
        return False
    if getattr(args, "command", None) not in {None, "chat"}:
        return False

    if getattr(args, "yolo", False):
        korra_env_set(os.environ, "KORRA_YOLO_MODE", "1")
    _prepare_agent_startup(args)

    if getattr(args, "oneshot", None):
        _confirm_startup_expensive_model_override(args)
        _run_and_exit_oneshot(
            args.oneshot,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
            skills=getattr(args, "skills", None),
            usage_file=getattr(args, "usage_file", None),
        )

    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"

    _set_chat_arg_defaults(args)
    cmd_chat(args)
    return True


def _try_termux_fast_cli_launch() -> bool:
    """Run obvious Termux non-TUI chat/oneshot/version paths on a light parser."""
    if not _is_termux_startup_environment():
        return False
    if korra_env("KORRA_TERMUX_DISABLE_FAST_CLI") == "1":
        return False

    argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        return False
    # Let the TUI fast path (or full dispatch) handle anything that resolves to
    # the TUI — explicit --tui/env or display.interface=tui. `--cli` forces this
    # to stay False so the classic fast path still runs.
    if _wants_tui_early(argv):
        return False

    if _is_termux_fast_version_argv(argv):
        _print_version_info(check_updates=True)
        return True

    first = _first_positional_argv()
    has_oneshot = any(
        arg == "-z" or arg == "--oneshot" or arg.startswith("--oneshot=")
        for arg in argv
    )
    if not has_oneshot and first not in {None, "chat"}:
        return False

    from korra_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)
    args = parser.parse_args(_coalesce_session_name_args(argv))

    if getattr(args, "version", False):
        _print_version_info(check_updates=True)
        return True

    if getattr(args, "oneshot", None):
        _prepare_agent_startup(args)
        _confirm_startup_expensive_model_override(args)
        _run_and_exit_oneshot(
            args.oneshot,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
            skills=getattr(args, "skills", None),
            usage_file=getattr(args, "usage_file", None),
        )

    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"

    if args.command in {None, "chat"}:
        _set_chat_arg_defaults(args)
        interactive_prompt = not getattr(args, "query", None) and not getattr(args, "image", None)
        if interactive_prompt:
            # Bare Termux CLI should reach the prompt first and do agent-only
            # discovery on the first submitted turn instead of before input.
            setattr(args, "compact", True)
            korra_env_set(os.environ, "KORRA_DEFER_AGENT_STARTUP", "1")
            korra_env_set(os.environ, "KORRA_FAST_STARTUP_BANNER", "1")
            if getattr(args, "accept_hooks", False):
                korra_env_set(os.environ, "KORRA_ACCEPT_HOOKS", "1")
        else:
            _prepare_agent_startup(args)
        cmd_chat(args)
        return True

    return False


def _try_termux_fast_tui_launch() -> bool:
    """Launch obvious Termux TUI invocations before building every subparser.

    `hermes --tui` is the hot path on phones. The full parser setup imports
    command modules for model, fallback, migrate, kanban, bundles, plugins,
    etc. even though the TUI immediately execs Node. On Termux only, parse the
    lightweight top-level/chat parser and hand off to ``cmd_chat`` when the
    invocation is unambiguously the built-in TUI/chat path.
    """
    if not _is_termux_startup_environment():
        return False

    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        return False

    wants_tui = _wants_tui_early(sys.argv[1:])
    if not wants_tui:
        return False

    first = _first_positional_argv()
    if first not in {None, "chat"}:
        return False

    from korra_cli._parser import build_top_level_parser

    parser, _subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)
    args = parser.parse_args(_coalesce_session_name_args(sys.argv[1:]))

    # Preserve top-level behaviours whose semantics are not "launch chat/TUI".
    if getattr(args, "version", False) or getattr(args, "oneshot", None):
        return False
    if getattr(args, "command", None) not in {None, "chat"}:
        return False
    if not _resolve_use_tui(args):
        return False

    cmd_chat(args)
    return True


def cmd_memory(args):
    sub = getattr(args, "memory_command", None)
    if sub == "off":
        from korra_cli.config import load_config, save_config

        config = load_config()
        if not isinstance(config.get("memory"), dict):
            config["memory"] = {}
        config["memory"]["provider"] = ""
        save_config(config)
        print('  ✓ Память: только встроенная')
        print('  Сохранено в config.yaml')
    elif sub == "reset":
        from korra_constants import get_hermes_home, display_hermes_home

        mem_dir = get_hermes_home() / "memories"
        target = getattr(args, "target", "all")
        files_to_reset = []
        if target in {"all", "memory"}:
            files_to_reset.append(("MEMORY.md", 'заметки агента'))
        if target in {"all", "user"}:
            files_to_reset.append(("USER.md", 'профиль пользователя'))

        # Check what exists
        existing = [
            (f, desc) for f, desc in files_to_reset if (mem_dir / f).exists()
        ]
        if not existing:
            print(
                f'  Очищать нечего: файлы памяти в {display_hermes_home()}/memories/ не найдены'
            )
            return

        print('  Эти файлы памяти будут удалены безвозвратно:')
        for f, desc in existing:
            path = mem_dir / f
            size = path.stat().st_size
            print(f'    ◆ {f} ({desc}) — {size:,} байт')

        if not getattr(args, "yes", False):
            try:
                answer = input('  Для подтверждения введите yes: ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                print('  Отменено.')
                return
            if answer != "yes":
                print('  Отменено.')
                return

        for f, desc in existing:
            (mem_dir / f).unlink()
            print(f'  ✓ Удалён {f} ({desc})')

        print(
            '  Память очищена. Новые беседы начнутся с чистого листа.'
        )
        print(f'  Файлы находились в {display_hermes_home()}/memories/')
    else:
        from korra_cli.memory_setup import memory_command

        memory_command(args)


def cmd_acp(args):
    """Launch Hermes Agent as an ACP server."""
    try:
        from acp_adapter.entry import main as acp_main

        acp_argv = []
        if getattr(args, "acp_version", False):
            acp_argv.append("--version")
        if getattr(args, "check", False):
            acp_argv.append("--check")
        if getattr(args, "setup", False):
            acp_argv.append("--setup")
        if getattr(args, "setup_browser", False):
            acp_argv.append("--setup-browser")
        if getattr(args, "assume_yes", False):
            acp_argv.append("--yes")
        acp_main(acp_argv)
    except ImportError:
        print('Зависимости ACP не установлены.', file=sys.stderr)
        print("Установите их: pip install -e '.[acp]'", file=sys.stderr)
        sys.exit(1)


def cmd_tools(args):
    action = getattr(args, "tools_action", None)
    if action in {"list", "disable", "enable"}:
        from korra_cli.tools_config import tools_disable_enable_command

        tools_disable_enable_command(args)
    elif action == "post-setup":
        from korra_cli.tools_config import run_post_setup_command

        sys.exit(run_post_setup_command(args))
    else:
        _require_tty("tools")
        from korra_cli.tools_config import tools_command

        tools_command(args)


def cmd_insights(args):
    db = None
    try:
        from korra_state import SessionDB
        from agent.insights import InsightsEngine

        db = SessionDB()
        engine = InsightsEngine(db)
        report = engine.generate(days=args.days, source=args.source)
        print(engine.format_terminal(report))
    except Exception as e:
        print(f'Не удалось подготовить статистику: {e}')
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def cmd_monitoring(args):
    """Gateway monitoring status: health & diagnostics export posture."""
    from korra_cli.config import load_config

    action = getattr(args, "monitoring_action", None) or "status"
    config = load_config()
    mon_raw = config.get("monitoring")
    mon: dict = mon_raw if isinstance(mon_raw, dict) else {}

    if action == "status":
        from agent.monitoring import otlp_exporter

        gh_raw = mon.get("gateway_health_export")
        gh: dict = gh_raw if isinstance(gh_raw, dict) else {}
        export_raw = mon.get("export")
        export_cfg: dict = export_raw if isinstance(export_raw, dict) else {}
        otlp_raw = export_cfg.get("otlp")
        otlp: dict = otlp_raw if isinstance(otlp_raw, dict) else {}

        print('Мониторинг шлюза')
        print(f"  Выгрузка состояния: {('включена' if gh.get('включена') else 'отключена')} (monitoring.gateway_health_export.enabled)")
        if gh.get("enabled"):
            print(f"    Показатели:           {('вкл.' if gh.get('metrics_enabled', True) else 'выкл.')}; интервал {gh.get('export_interval_seconds', 60)} с")
            print(f"    События диагностики:  {('вкл.' if gh.get('diagnostic_events_enabled', True) else 'выкл.')}")
            print(f"    Предупреждения и ошибки: {('вкл.' if gh.get('warning_error_events_enabled', True) else 'выкл.')}; интервал {gh.get('logs_export_interval_seconds', 5)} с")
            print('    Защита содержимого всегда включена: тексты сообщений не выгружаются, отключить нельзя')
        endpoint = otlp.get("endpoint") or ""
        if otlp.get("enabled") and endpoint:
            print(f'  Адрес OTLP:     {endpoint}')
        else:
            print('  Адрес OTLP не настроен: monitoring.export.otlp')
        print(f"  SDK OTel:      {('установлен' if otlp_exporter.is_available() else 'не установлен')}; дополнительный пакет hermes-agent[otlp]")
        print('  Состав: только состояние шлюза и диагностика со скрытыми секретами.')
        print('  Без запросов, сообщений, аргументов и результатов инструментов, аналитики использования и трассировок.')
        return

    print(f'Неизвестное действие monitoring: {action}', file=sys.stderr)
    sys.exit(2)


def cmd_skills(args):
    # Route 'config' action to skills_config module
    if getattr(args, "skills_action", None) == "config":
        _require_tty("skills config")
        from korra_cli.skills_config import skills_command as skills_config_command

        skills_config_command(args)
    elif getattr(args, "skills_action", None) in ("trust", "untrust"):
        _cmd_skills_trust(args)
    else:
        from korra_cli.skills_hub import skills_command

        skills_command(args)


def _cmd_skills_trust(args):
    """``hermes skills trust [path]`` / ``hermes skills untrust [path]``.

    Manages ``skills.trusted_project_dirs`` in config.yaml. With no path,
    operates on the project root enclosing the current directory (nearest
    ancestor with ``.git``).
    """
    from pathlib import Path

    from agent.skill_utils import (
        PROJECT_SKILLS_SUBDIRS,
        _candidate_project_skills_dirs,
        find_project_root,
        iter_skill_index_files,
    )
    from korra_cli.config import load_config, save_config

    action = args.skills_action
    raw_path = getattr(args, "path", None)
    if raw_path:
        root = Path(raw_path).expanduser().resolve()
        if not root.is_dir():
            print(f'Это не папка: {root}')
            return
    else:
        root = find_project_root()
        if root is None:
            print(
                'Текущая папка не в репозитории Git. Откройте папку проекта или укажите путь к корню.'
            )
            return

    config = load_config()
    skills_cfg = config.setdefault("skills", {})
    trusted = skills_cfg.get("trusted_project_dirs") or []
    if not isinstance(trusted, list):
        trusted = [trusted]
    trusted = [str(t) for t in trusted]
    root_str = str(root)

    if action == "untrust":
        kept = [t for t in trusted if str(Path(t).expanduser().resolve()) != root_str]
        if len(kept) == len(trusted):
            print(f'Для {root} доверие не было задано.')
            return
        skills_cfg["trusted_project_dirs"] = kept
        save_config(config)
        print(f'Доверие отозвано: {root}')
        print('Навыки из этого проекта больше не будут загружаться.')
        return

    # trust
    if any(str(Path(t).expanduser().resolve()) == root_str for t in trusted):
        print(f'Уже доверенный проект: {root}')
    else:
        trusted.append(root_str)
        skills_cfg["trusted_project_dirs"] = trusted
        save_config(config)
        print(f'Доверенный проект: {root}')

    # Show what this unlocks
    count = 0
    for d in _candidate_project_skills_dirs(root):
        count += sum(1 for _ in iter_skill_index_files(d, "SKILL.md"))
    if count:
        print(
            f'Навыков проекта для загрузки в новых беседах внутри репозитория: {count}. Они имеют приоритет над одноимёнными навыками профиля.'
        )
    else:
        subdirs = " or ".join(PROJECT_SKILLS_SUBDIRS)
        print(f'Навыков проекта пока нет. Добавьте их в {subdirs}.')


def cmd_pairing(args):
    from korra_cli.pairing import pairing_command

    pairing_command(args)


def cmd_plugins(args):
    from korra_cli.plugins_cmd import plugins_command

    plugins_command(args)


def cmd_mcp(args):
    from korra_cli.mcp_config import mcp_command

    mcp_command(args)


def cmd_claw(args):
    from korra_cli.claw import claw_command

    claw_command(args)


def _advertise_agent_env() -> None:
    """Advertise the agent harness to child processes.

    ``AI_AGENT`` is the emerging cross-agent standard (huggingface_hub's agent
    detection reads it; pi and other agents set it — earendil-works/pi#7493)
    so generic tooling can attribute subprocesses to the harness that spawned
    them. The value must be our id in the public agent-harness registry
    (``hermes-agent`` in huggingface.js ``agent-harnesses.ts``): standard-var
    matching is exact, so any other value is counted as "unknown".
    ``HERMES_AGENT`` is the Hermes-specific marker. setdefault: never
    clobber an outer harness (e.g. Hermes running inside another agent's
    terminal).
    """
    os.environ.setdefault("AI_AGENT", "hermes-agent")
    korra_env_setdefault(os.environ, "KORRA_AGENT", "true")


def main():
    """Main entry point for hermes CLI."""
    # Cosmetic: make the process show up as 'hermes' instead of 'python3.11'
    # in ps/top/htop.  Non-fatal — just a nicer UX.
    _set_process_title()

    # Let child processes (and tools like huggingface_hub) detect they run
    # under an AI agent harness.
    _advertise_agent_env()

    # Force UTF-8 stdio on Windows before anything prints.  No-op elsewhere.
    try:
        from korra_cli.stdio import configure_windows_stdio
        configure_windows_stdio()
    except Exception:
        pass

    # Sweep stale ``hermes.exe.old.*`` quarantine files left by previous
    # ``hermes update`` runs on Windows. Silent no-op on non-Windows or when
    # there's nothing to clean. See ``_quarantine_running_hermes_exe``.
    try:
        _cleanup_quarantined_exes()
    except Exception:
        pass

    # If the checkout changed since the last launch (hermes update, manual
    # git pull, old-updater update that predates newer clears), sweep stale
    # __pycache__ once so no process — this one's lazy imports included —
    # resolves fresh source against old bytecode. Never raises.
    _sweep_stale_bytecode_if_checkout_changed()

    # Self-heal a venv left half-built by an interrupted ``hermes update``
    # (Ctrl-C, terminal close, WSL OOM mid-install). Skip when the user is
    # *running* update — that flow writes and clears its own marker, and we
    # don't want a recovery install racing the real one. Never raises.
    #
    # The substring match is deliberately loose: argv isn't parsed yet at this
    # point, and the failure modes are asymmetric. Over-matching (e.g.
    # ``hermes skills install update``) merely defers recovery one launch;
    # under-matching (missing ``hermes -p work update``) would race a recovery
    # install against the real one. Loose wins.
    try:
        if "update" not in sys.argv[1:]:
            _recover_from_interrupted_install()
    except Exception:
        pass

    # Cheap hint only (#95294): an interrupted update that pulled code but
    # never restarted the fleet. Do NOT restart here — that is ``hermes
    # update`` catch-up work. Skip when the user is already running update.
    try:
        if "update" not in sys.argv[1:]:
            _warn_pending_fleet_restart_on_startup()
    except Exception:
        pass

    if _try_termux_fast_tui_launch():
        return
    if _try_termux_fast_cli_launch():
        return
    if _try_fast_chat_launch():
        return

    from korra_cli._parser import build_top_level_parser

    parser, subparsers, chat_parser = build_top_level_parser()
    chat_parser.set_defaults(func=cmd_chat)

    # =========================================================================
    # model command  (parser built in korra_cli/subcommands/model.py)
    # =========================================================================
    build_model_parser(subparsers, cmd_model=cmd_model)

    from korra_cli.moa_cmd import cmd_moa

    moa_parser = subparsers.add_parser(
        "moa",
        help='Настроить провайдеров и модели для совместной работы MoA',
        description='Настроить набор провайдеров и моделей для /moa <prompt>.',
    )
    moa_subparsers = moa_parser.add_subparsers(dest="moa_command")
    moa_subparsers.add_parser("list", aliases=["ls"], help='Показать текущие модели MoA')
    moa_configure = moa_subparsers.add_parser("configure", aliases=["config"], help='Выбрать модели MoA в меню')
    moa_configure.add_argument("name", nargs="?", help='Имя создаваемого или изменяемого набора')
    moa_delete = moa_subparsers.add_parser("delete", aliases=["rm"], help='Удалить набор MoA')
    moa_delete.add_argument("name", help='Имя удаляемого набора')
    moa_parser.set_defaults(func=cmd_moa)

    # =========================================================================
    # fallback command — manage the fallback provider chain
    # =========================================================================
    from korra_cli.fallback_cmd import cmd_fallback

    fallback_parser = subparsers.add_parser(
        "fallback",
        help='Настроить резервных провайдеров на случай отказа основной модели',
        description=(
            'Резервные провайдеры проверяются по очереди, если основная модель недоступна из-за лимита, перегрузки или ошибки подключения. Документация: https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers'
        ),
    )
    fallback_subparsers = fallback_parser.add_subparsers(dest="fallback_command")
    fallback_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help='Показать цепочку резервных провайдеров; действие по умолчанию',
    )
    fallback_subparsers.add_parser(
        "add",
        help='Выбрать провайдера и модель, как в korra model, и добавить в цепочку',
    )
    fallback_subparsers.add_parser(
        "remove",
        aliases=["rm"],
        help='Выбрать запись для удаления из цепочки',
    )
    fallback_subparsers.add_parser(
        "clear",
        help='Удалить всех резервных провайдеров',
    )
    fallback_parser.set_defaults(func=cmd_fallback)

    # =========================================================================
    # worktree command — audit/reclaim accumulated git worktrees + branches
    # =========================================================================
    worktree_parser = subparsers.add_parser(
        "worktree",
        help='Проверить и очистить накопившиеся рабочие копии Git и слитые ветки',
        description=(
            'Очистить .worktrees/, накопленные запусками korra -w. Незакоммиченные изменения, неотправленные уникальные коммиты и используемые копии сохраняются. Неотслеживаемые файлы перед удалением архивируются в archive/worktree-prune/ профиля. Документация: https://hermes-agent.nousresearch.com/docs/user-guide/cli#worktree-cleanup'
        ),
    )
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_action")
    worktree_list = worktree_subparsers.add_parser(
        "list",
        aliases=["ls", "audit"],
        help='Показать возраст, размер, решение и причину для каждой рабочей копии; действие по умолчанию',
    )
    worktree_list.add_argument("--repo", help='Корень репозитория; по умолчанию текущий')
    worktree_prune = worktree_subparsers.add_parser(
        "prune",
        help='Удалить безопасные рабочие копии и полностью слитые локальные ветки',
    )
    worktree_prune.add_argument("--repo", help='Корень репозитория; по умолчанию текущий')
    worktree_prune.add_argument(
        "--dry-run", action="store_true",
        help='Показать план без изменений',
    )
    worktree_prune.add_argument(
        "--trees-only", action="store_true",
        help='Удалить только рабочие копии, сохранив локальные ветки',
    )
    worktree_prune.add_argument(
        "--branches-only", action="store_true",
        help='Удалить только слитые локальные ветки, сохранив рабочие копии',
    )

    def _dispatch_worktree(_args):
        from korra_cli.worktree_cmd import cmd_worktree

        # argparse aliases set dest to the literal typed string ("ls"/"audit").
        action = getattr(_args, "worktree_action", None)
        if action in ("ls", "audit"):
            _args.worktree_action = "list"
        return cmd_worktree(_args)

    worktree_parser.set_defaults(func=_dispatch_worktree)


    # =========================================================================
    # browser command — real-profile helpers (agent-invoked, user-approved)
    # =========================================================================
    browser_parser = subparsers.add_parser(
        "browser",
        help='Работа с вашим профилем браузера: закрыть браузер, блокирующий копирование профиля',
        description=(
            'Средства browser.use_real_profile. close-profile завершает процессы браузера, чтобы Корра могла скопировать профиль. Несохранённые вкладки будут потеряны. Агент запускает это только после вашего разрешения закрыть браузер.'
        ),
    )
    browser_subparsers = browser_parser.add_subparsers(dest="browser_action")
    browser_close = browser_subparsers.add_parser(
        "close-profile",
        help='Закрыть браузер, блокирующий профиль, без запроса подтверждения. Только с явного разрешения пользователя: несохранённые вкладки будут потеряны.',
    )
    browser_close.add_argument(
        "--browser",
        help='Использовать указанный браузер: chrome, edge, brave, brave-origin или chromium',
    )

    def _dispatch_browser(_args):
        from korra_cli.browser_connect import (
            UNSUPPORTED_CHANNEL,
            close_browser_holding_profile,
            detect_default_chromium,
            real_profile_data_dir,
        )

        action = getattr(_args, "browser_action", None)
        if action != "close-profile":
            browser_parser.print_help()
            return 2
        browser = getattr(_args, "browser", None) or detect_default_chromium()
        if not browser or browser == UNSUPPORTED_CHANNEL:
            print('✗ Поддерживаемый браузер Chromium по умолчанию не найден.', file=sys.stderr)
            return 1
        src = real_profile_data_dir(browser)
        if not src:
            print(f'✗ Не удалось определить папку профиля {browser}.', file=sys.stderr)
            return 1
        closed, msg = close_browser_holding_profile(src)
        if closed:
            print(f"✓ {msg}")
            return 0
        print(f"✗ {msg}", file=sys.stderr)
        return 1

    browser_parser.set_defaults(func=_dispatch_browser)


    # =========================================================================
    # secrets command — external secret managers (Bitwarden, 1Password)
    # =========================================================================
    secrets_parser = subparsers.add_parser(
        "secrets",
        help='Внешние хранилища секретов: Bitwarden и 1Password',
        description=(
            'Загружать ключи API при запуске из Bitwarden Secrets Manager или 1Password вместо хранения в .env профиля. Документация: https://hermes-agent.nousresearch.com/docs/user-guide/secrets/'
        ),
    )
    secrets_subparsers = secrets_parser.add_subparsers(dest="secrets_command")

    secrets_bw = secrets_subparsers.add_parser(
        "bitwarden",
        aliases=["bw"],
        help='Подключение Bitwarden Secrets Manager',
    )

    secrets_op = secrets_subparsers.add_parser(
        "onepassword",
        aliases=["op", "1password"],
        help='Подключение 1Password через ссылки op://',
    )

    # Lazy-import secrets_cli: the module imports agent.secret_sources.bitwarden
    # which loads cryptography._rust.pyd.  On Windows this maps the native
    # extension into the updater process, causing the self-lock preflight to
    # defer (#86781).  secrets_cli defers its backend import to first use
    # (module-level __getattr__ + handler-level _load_bw()), so register_cli
    # at parse time only wires argparse structure with no crypto cost.
    from korra_cli import secrets_cli as _secrets_cli
    from korra_cli import onepassword_secrets_cli as _op_secrets_cli

    _secrets_cli.register_cli(secrets_bw)
    _op_secrets_cli.register_cli(secrets_op)

    def _dispatch_secrets(args):  # noqa: ANN001
        sub = getattr(args, "secrets_command", None)
        if sub is None:
            secrets_parser.print_help()
            return 0
        return args.func(args)

    secrets_parser.set_defaults(func=_dispatch_secrets)

    # =========================================================================
    # egress command — iron-proxy outbound credential-injection firewall
    # =========================================================================
    # NOTE: this is the OUTBOUND egress firewall (ironsh/iron-proxy).
    # `hermes proxy` (defined elsewhere in this file) is a separate INBOUND
    # OAuth-aggregator reverse proxy.  Different direction, different purpose.
    egress_parser = subparsers.add_parser(
        "egress",
        help='Управление сетевой защитой iron-proxy с подстановкой ключей',
        description=(
            'iron-proxy перехватывает исходящий TLS и заменяет временные токены настоящими ключами API до выхода запроса из изолированной среды. По умолчанию выключен. Документация: https://hermes-agent.nousresearch.com/docs/user-guide/egress/iron-proxy'
        ),
    )

    from korra_cli import proxy_cli as _proxy_cli
    _proxy_cli.register_cli(egress_parser)

    def _dispatch_egress(args):  # noqa: ANN001
        # The egress subparser uses dest='egress_command' to stay disjoint
        # from the inbound OAuth ``hermes proxy`` subparser (dest='proxy_command').
        sub = getattr(args, "egress_command", None)
        if sub is not None and hasattr(args, "func") and args.func is not _dispatch_egress:
            return args.func(args)
        egress_parser.print_help()
        return 0

    egress_parser.set_defaults(func=_dispatch_egress)

    # =========================================================================
    # migrate command
    # =========================================================================
    from korra_cli.migrate import cmd_migrate, cmd_migrate_xai

    migrate_parser = subparsers.add_parser(
        "migrate",
        help='Заменить снятые с поддержки модели и устаревшие настройки',
        description=(
            'Проверить config.yaml и при необходимости заменить ссылки на снятые с поддержки модели или устаревшие настройки'
        ),
    )
    migrate_subparsers = migrate_parser.add_subparsers(dest="migrate_type")

    migrate_xai = migrate_subparsers.add_parser(
        "xai",
        help='Заменить модели xAI, снятые с поддержки 15 мая 2026 года',
        description=(
            'Найти в config.yaml модели xAI, снятые с поддержки 15 мая 2026 года. С --apply заменить официальными преемниками по инструкции xAI. Перед записью создаётся резервная копия.'
        ),
    )
    migrate_xai.add_argument(
        "--apply",
        action="store_true",
        help='Записать изменения в config.yaml; по умолчанию только предварительный просмотр',
    )
    migrate_xai.add_argument(
        "--no-backup",
        action="store_true",
        help='Не создавать резервную копию config.yaml с датой и временем',
    )
    migrate_xai.set_defaults(func=cmd_migrate_xai)
    migrate_parser.set_defaults(func=cmd_migrate)

    # =========================================================================
    # gateway + proxy commands  (parsers built in korra_cli/subcommands/gateway.py)
    # =========================================================================
    build_gateway_parser(
        subparsers, cmd_gateway=cmd_gateway, cmd_proxy=cmd_proxy, cmd_gateway_enroll=cmd_gateway_enroll
    )

    # =========================================================================
    # lsp command
    # =========================================================================
    try:
        from agent.lsp.cli import register_subparser as _lsp_register
        _lsp_register(subparsers)
    except Exception as _lsp_err:  # noqa: BLE001
        # LSP is optional infrastructure — never let a registration
        # failure break the CLI overall.
        logger.debug("LSP CLI registration failed: %s", _lsp_err)

    # =========================================================================
    # setup command  (parser built in korra_cli/subcommands/setup.py)
    # =========================================================================
    build_setup_parser(subparsers, cmd_setup=cmd_setup)


    # =========================================================================
    # whatsapp command  (parser built in korra_cli/subcommands/whatsapp.py)
    # =========================================================================
    build_whatsapp_parser(subparsers, cmd_whatsapp=cmd_whatsapp)

    # =========================================================================
    # whatsapp-cloud command (official Meta Cloud API; complement to Baileys)
    # =========================================================================
    whatsapp_cloud_parser = subparsers.add_parser(
        "whatsapp-cloud",
        help='Настроить WhatsApp Business Cloud API',
        description=(
            'Настроить официальный адаптер Meta WhatsApp Business Cloud API. Нужны бизнес-аккаунт и публичный адрес вебхука. Для личного аккаунта через мост Baileys используйте korra whatsapp.'
        ),
    )
    whatsapp_cloud_parser.set_defaults(func=cmd_whatsapp_cloud)

    # =========================================================================
    # slack command  (parser built in korra_cli/subcommands/slack.py)
    # =========================================================================
    build_slack_parser(subparsers, cmd_slack=cmd_slack)

    # =========================================================================
    # send command — pipe shell-script output to any configured platform
    # =========================================================================
    from korra_cli.send_cmd import register_send_subparser
    register_send_subparser(subparsers)

    # =========================================================================
    # login command  (parser built in korra_cli/subcommands/login.py)
    # =========================================================================
    build_login_parser(subparsers, cmd_login=cmd_login)

    # =========================================================================
    # logout command  (parser built in korra_cli/subcommands/logout.py)
    # =========================================================================
    build_logout_parser(subparsers, cmd_logout=cmd_logout)

    # =========================================================================
    # auth command  (parser built in korra_cli/subcommands/auth.py)
    # =========================================================================
    build_auth_parser(subparsers, cmd_auth=cmd_auth)

    # =========================================================================
    # status command  (parser built in korra_cli/subcommands/status.py)
    # =========================================================================
    build_status_parser(subparsers, cmd_status=cmd_status)

    # =========================================================================
    # pause / resume commands  (parser built in korra_cli/subcommands/pause.py)
    # =========================================================================
    build_pause_parser(subparsers)

    # =========================================================================
    # cron command  (parser built in korra_cli/subcommands/cron.py)
    # =========================================================================
    build_cron_parser(subparsers, cmd_cron=cmd_cron)
    build_sync_parser(subparsers, cmd_sync=cmd_sync)

    # =========================================================================
    # webhook command  (parser built in korra_cli/subcommands/webhook.py)
    # =========================================================================
    build_webhook_parser(subparsers, cmd_webhook=cmd_webhook)

    # =========================================================================
    # peer command — bot-to-bot DMs across machines (peer Hermes gateways)
    # =========================================================================
    from korra_cli.subcommands.peer import build_peer_parser

    build_peer_parser(subparsers)

    # =========================================================================
    # portal command — Nous Portal status + Tool Gateway routing
    # =========================================================================
    from korra_cli.portal_cli import add_parser as _add_portal_parser
    _add_portal_parser(subparsers)

    # =========================================================================
    # kanban command — multi-profile collaboration board
    # =========================================================================
    from korra_cli.kanban import build_parser as _build_kanban_parser

    kanban_parser = _build_kanban_parser(subparsers)
    kanban_parser.set_defaults(func=cmd_kanban)

    # =========================================================================
    # project command — named, multi-folder workspaces
    # =========================================================================
    from korra_cli.projects_cmd import build_parser as _build_project_parser

    project_parser = _build_project_parser(subparsers)
    project_parser.set_defaults(func=cmd_project)

    # =========================================================================
    # hooks command — shell-hook inspection and management
    # =========================================================================
    # hooks command  (parser built in korra_cli/subcommands/hooks.py)
    # =========================================================================
    build_hooks_parser(subparsers, cmd_hooks=cmd_hooks)

    # =========================================================================
    # doctor command  (parser built in korra_cli/subcommands/doctor.py)
    # =========================================================================
    build_doctor_parser(subparsers, cmd_doctor=cmd_doctor)

    # =========================================================================
    # verify command  (parser built in korra_cli/subcommands/verify.py)
    # =========================================================================
    build_verify_parser(subparsers, cmd_verify=cmd_verify)

    # =========================================================================
    # security command — on-demand supply-chain audit
    # =========================================================================
    # security command  (parser built in korra_cli/subcommands/security.py)
    # =========================================================================
    build_security_parser(subparsers, cmd_security=cmd_security)

    # =========================================================================
    # approvals command  (parser built in korra_cli/subcommands/approvals.py)
    # =========================================================================
    build_approvals_parser(subparsers, cmd_approvals=cmd_approvals)

    # =========================================================================
    # dump command  (parser built in korra_cli/subcommands/dump.py)
    # =========================================================================
    build_dump_parser(subparsers, cmd_dump=cmd_dump)

    # =========================================================================
    # debug command  (parser built in korra_cli/subcommands/debug.py)
    # =========================================================================
    build_debug_parser(subparsers, cmd_debug=cmd_debug)

    # =========================================================================
    # backup command  (parser built in korra_cli/subcommands/backup.py)
    # =========================================================================
    build_backup_parser(subparsers, cmd_backup=cmd_backup)

    # =========================================================================
    # checkpoints command
    # =========================================================================
    checkpoints_parser = subparsers.add_parser(
        "checkpoints",
        help='Просмотреть, очистить или удалить точки восстановления профиля',
        description='Управление точками восстановления файлов: внутренний Git-репозиторий сохраняет рабочие папки перед write_file, patch и terminal. Просмотр занятого места, очистка старого или полное удаление.',
    )
    from korra_cli.checkpoints import register_cli as _register_checkpoints_cli
    _register_checkpoints_cli(checkpoints_parser)

    # =========================================================================
    # import command  (parser built in korra_cli/subcommands/import_cmd.py)
    # =========================================================================
    build_import_cmd_parser(subparsers, cmd_import=cmd_import)

    # =========================================================================
    # import-agent command  (parser: korra_cli/subcommands/import_agent.py)
    # =========================================================================
    def cmd_import_agent(args):
        from korra_cli.agent_import import import_agent_command
        import_agent_command(args)

    build_import_agent_parser(subparsers, cmd_import_agent=cmd_import_agent)

    # =========================================================================
    # config command  (parser built in korra_cli/subcommands/config.py)
    # =========================================================================
    build_config_parser(subparsers, cmd_config=cmd_config)

    # =========================================================================
    # skin command  (parser built in korra_cli/subcommands/skin.py)
    # =========================================================================
    build_skin_parser(subparsers, cmd_skin=cmd_skin)

    # =========================================================================
    # console command  (parser built in korra_cli/subcommands/console.py)
    # =========================================================================
    build_console_parser(subparsers, cmd_console=cmd_console)

    # =========================================================================
    # pairing command  (parser built in korra_cli/subcommands/pairing.py)
    # =========================================================================
    build_pairing_parser(subparsers, cmd_pairing=cmd_pairing)

    # =========================================================================
    # skills command  (parser built in korra_cli/subcommands/skills.py)
    # =========================================================================
    build_skills_parser(subparsers, cmd_skills=cmd_skills)

    # =========================================================================
    # bundles command — skill bundles (alias /<name> for multiple skills)
    # =========================================================================
    bundles_parser = subparsers.add_parser(
        "bundles",
        help='Создать, показать и настроить комплекты навыков',
        description=(
            'Комплект объединяет несколько навыков в одну команду /<bundle> для CLI или мессенджера и загружает их одновременно'
        ),
    )
    from korra_cli.bundles import register_cli as _bundles_register, bundles_command
    _bundles_register(bundles_parser)
    bundles_parser.set_defaults(func=bundles_command)

    # =========================================================================
    # plugins command  (parser built in korra_cli/subcommands/plugins.py)
    # =========================================================================
    build_plugins_parser(subparsers, cmd_plugins=cmd_plugins)

    # =========================================================================
    # Plugin CLI commands — dynamically registered by memory/general plugins.
    # Plugins provide a register_cli(subparser) function that builds their
    # own argparse tree.  No hardcoded plugin commands in main.py.
    #
    # Skipped when the invocation is already targeting a known built-in
    # subcommand — ``hermes --help``, ``hermes logs``,
    # etc.  This avoids eagerly importing every bundled plugin module
    # (google.cloud.pubsub_v1, aiohttp, grpc, PIL …) which costs
    # 500-650ms on typical installs.
    # =========================================================================
    if _plugin_cli_discovery_needed():
        try:
            from plugins.memory import discover_plugin_cli_commands
            from korra_cli.plugins import discover_plugins, get_plugin_manager

            seen_plugin_commands = set()
            for cmd_info in discover_plugin_cli_commands():
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
                seen_plugin_commands.add(cmd_info["name"])

            discover_plugins()
            # A bundled platform whose top-level CLI command is the one being
            # invoked is still only a deferred entry at this point; import it
            # so its register_cli_command side effect runs before we read
            # _cli_commands (issue #54678).
            _resolve_deferred_platform_cli_command(_first_positional_argv())
            for cmd_info in get_plugin_manager()._cli_commands.values():
                if cmd_info["name"] in seen_plugin_commands:
                    continue
                plugin_parser = subparsers.add_parser(
                    cmd_info["name"],
                    help=cmd_info["help"],
                    description=cmd_info.get("description", ""),
                    formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
                )
                cmd_info["setup_fn"](plugin_parser)
                if cmd_info.get("handler_fn") is not None:
                    plugin_parser.set_defaults(func=cmd_info["handler_fn"])
        except Exception as _exc:
            logging.getLogger(__name__).debug("Plugin CLI discovery failed: %s", _exc)

    # =========================================================================
    # curator command — background skill maintenance
    # =========================================================================
    curator_parser = subparsers.add_parser(
        "curator",
        help='Фоновое обслуживание навыков: состояние, запуск, пауза и закрепление',
        description=(
            'Вспомогательная модель периодически проверяет созданные агентом навыки, объединяет пересечения и архивирует устаревшие. Встроенные навыки и установленные из каталогов не затрагиваются. Архив можно восстановить; автоматического удаления нет.'
        ),
    )
    try:
        from korra_cli.curator import register_cli as _register_curator_cli

        _register_curator_cli(curator_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("curator CLI wiring failed: %s", _exc)

    # =========================================================================
    # pets command — petdex animated mascots (CLI / TUI / desktop display)
    # =========================================================================
    pets_parser = subparsers.add_parser(
        "pets",
        help='Просмотр, установка и выбор анимированных питомцев petdex',
        description=(
            'Petdex (https://github.com/crafter-station/petdex) — открытая галерея анимированных питомцев для агентов. Выбранный питомец реагирует на работу Корры в CLI, TUI и приложении.'
        ),
    )
    try:
        from korra_cli.pets import register_cli as _register_pets_cli

        _register_pets_cli(pets_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("pets CLI wiring failed: %s", _exc)

    # =========================================================================
    # journey command — learned skills + memories over time, in the terminal
    # =========================================================================
    journey_parser = subparsers.add_parser(
        "journey",
        aliases=["learning", "memory-graph"],
        help='История освоенных навыков и записей памяти',
        description=(
            'Терминальная версия карты памяти: навыки и записи памяти по времени от старых к новым, с прокруткой истории. Соответствует /journey в TUI и панели приложения.'
        ),
    )
    try:
        from korra_cli.journey import register_cli as _register_journey_cli

        _register_journey_cli(journey_parser)
    except Exception as _exc:
        logging.getLogger(__name__).debug("journey CLI wiring failed: %s", _exc)

    # =========================================================================
    # memory command  (parser built in korra_cli/subcommands/memory.py)
    # =========================================================================
    build_memory_parser(subparsers, cmd_memory=cmd_memory)

    # =========================================================================
    # tools command  (parser built in korra_cli/subcommands/tools.py)
    # =========================================================================
    build_tools_parser(subparsers, cmd_tools=cmd_tools)

    # =========================================================================
    # computer-use command — manage Computer Use (cua-driver)
    # =========================================================================
    computer_use_parser = subparsers.add_parser(
        "computer-use",
        help='Управление Computer Use через cua-driver в macOS, Windows и Linux',
        description=(
            'Установить или проверить cua-driver для набора computer_use в macOS, Windows и Linux. Установка или повторная установка зависимостей: korra computer-use install. Проверка разрешений, версии и совместимости через health_report: korra computer-use doctor.'
        ),
    )
    computer_use_sub = computer_use_parser.add_subparsers(dest="computer_use_action")

    computer_use_install = computer_use_sub.add_parser(
        "install",
        help='Установить или восстановить cua-driver для macOS, Windows и Linux',
    )
    computer_use_install.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            'Повторить официальный установщик, даже если cua-driver уже в PATH. Он загружает последнюю версию и обновляет существующую установку.'
        ),
    )
    computer_use_sub.add_parser(
        "status",
        help='Показать, установлен ли cua-driver и доступен ли в PATH',
    )
    computer_use_doctor = computer_use_sub.add_parser(
        "doctor",
        help='Выполнить health_report cua-driver и показать результаты проверок',
        description=(
            'Выполнить health_report cua-driver: разрешения TCC, подпись приложения, версия, совместимость и пробный снимок экрана. Новые проверки драйвера показываются автоматически. Код выхода: 0 — исправно, 1 — проблемы, 2 — программа отсутствует или недоступна.'
        ),
    )
    computer_use_doctor.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="CHECK",
        help=(
            'Выполнить только указанные проверки; параметр можно повторять, например --include tcc_accessibility --include bundle_identity. Неизвестные имена сообщает cua-driver.'
        ),
    )
    computer_use_doctor.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="CHECK",
        help='Пропустить указанные проверки; параметр можно повторять. Имеет приоритет над --include.',
    )
    computer_use_doctor.add_argument(
        "--json",
        action="store_true",
        help='Вывести исходные структурированные данные JSON в формате tools/call',
    )
    computer_use_perms = computer_use_sub.add_parser(
        "permissions",
        help='Проверить или выдать разрешения управления и записи экрана в macOS',
        description=(
            'Computer Use управляет Mac через cua-driver; разрешения TCC привязаны к com.trycua.driver. status показывает их состояние; grant открывает запрос разрешений именно для CuaDriver через LaunchServices.'
        ),
    )
    computer_use_perms_sub = computer_use_perms.add_subparsers(
        dest="computer_use_perms_action"
    )
    computer_use_perms_status = computer_use_perms_sub.add_parser(
        "status",
        help='Показать разрешения управления и записи экрана без изменений',
    )
    computer_use_perms_status.add_argument(
        "--json",
        action="store_true",
        help='Вывести нормализованные сведения о разрешениях в JSON',
    )
    computer_use_perms_sub.add_parser(
        "grant",
        help='Запросить разрешения, открыв диалог для CuaDriver',
    )
    def cmd_computer_use(args):
        action = getattr(args, "computer_use_action", None)
        if action == "install":
            from korra_cli.tools_config import (
                _cua_driver_contract_status,
                install_cua_driver,
            )
            if not install_cua_driver(upgrade=bool(getattr(args, "upgrade", False))):
                return 1
            return 0 if _cua_driver_contract_status().get("ready") else 1
        if action == "status":
            import subprocess
            from korra_cli.tools_config import _cua_driver_contract_status
            from tools.computer_use.cua_backend import (
                cua_driver_update_check,
                resolve_cua_driver_cmd,
            )
            # Must match the runtime resolver: Desktop/TUI processes can omit
            # ~/.local/bin even though the official installer put the driver there.
            path = resolve_cua_driver_cmd()
            override = korra_env("KORRA_CUA_DRIVER_CMD", "").strip()
            if path:
                version = ""
                try:
                    from korra_cli.tools_config import _cua_driver_env
                    version = subprocess.run(
                        [path, "--version"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                        env=_cua_driver_env(),
                    ).stdout.strip()
                except Exception:
                    pass
                from korra_cli.tools_config import _cua_version_summary
                version = _cua_version_summary(version)
                # Name the override here too. Without it the operator is told
                # to repair an install that `hermes computer-use install` will
                # (correctly) refuse to touch, with nothing pointing at the
                # env var that actually selected the binary.
                origin = ' [своя программа из HERMES_CUA_DRIVER_CMD]' if override else ""
                if version:
                    print(f'cua-driver установлен: {path}{origin} ({version})')
                else:
                    print(f'cua-driver установлен: {path}{origin}')
                contract = _cua_driver_contract_status(path)
                if not contract.get("ready"):
                    print(
                        '  ⚠ Требуется восстановление: '
                        + (contract.get("reason") or 'среда выполнения настроена не полностью')
                    )
                    if override:
                        print(
                            '    Обновите программу из HERMES_CUA_DRIVER_CMD либо снимите эту настройку и выполните korra computer-use install --upgrade.'
                        )
                    else:
                        print('    Выполните: korra computer-use install')
                    return 1
                try:
                    st = cua_driver_update_check()
                    if st and st.get("update_available"):
                        latest = st.get("latest_version") or "?"
                        print(f'  ⬆ Доступно обновление cua-driver {latest}.')
                        print('    Выполните: korra computer-use install --upgrade')
                    elif st:
                        print('  ✓ Установлена последняя версия.')
                    else:
                        # Older driver (no check-update verb) or offline.
                        print('  Обновить до последней версии: korra computer-use install --upgrade')
                except Exception:
                    print('  Обновить до последней версии: korra computer-use install --upgrade')
                return 0
            print('cua-driver не установлен')
            print('  Выполните: korra computer-use install')
            return 1
        if action == "doctor":
            from tools.computer_use.doctor import run_doctor
            code = run_doctor(
                include=list(getattr(args, "include", []) or []),
                skip=list(getattr(args, "skip", []) or []),
                json_output=bool(getattr(args, "json", False)),
            )
            sys.exit(code)
        if action == "permissions":
            perms_action = getattr(args, "computer_use_perms_action", None)
            if perms_action == "grant":
                from tools.computer_use.permissions import request_permissions_grant
                sys.exit(request_permissions_grant())
            if perms_action == "status":
                import json as _json
                from tools.computer_use.permissions import computer_use_status
                st = computer_use_status()
                if bool(getattr(args, "json", False)):
                    print(_json.dumps(st, indent=2, sort_keys=True))
                    sys.exit(0 if st["ready"] else 1)
                if not st["platform_supported"]:
                    print(f"Computer Use не поддерживается в {st['platform']}.")
                    sys.exit(1)
                if not st["installed"]:
                    print('cua-driver не установлен. Выполните korra computer-use install.')
                    sys.exit(1)
                glyph = lambda v: "✅" if v is True else ("❌" if v is False else "•")  # noqa: E731
                print(f"cua-driver: {st['version'] or 'установлен'} ({st['platform']})")
                if st["can_grant"]:  # macOS TCC permissions
                    print(f"  {glyph(st['accessibility'])} Управление компьютером")
                    print(f"  {glyph(st['screen_recording'])} Запись экрана")
                    if not st["ready"]:
                        print('  Выдать разрешения: korra computer-use permissions grant')
                else:  # no TCC model — readiness is driver health
                    print(f"  {glyph(st['ready'])} Состояние драйвера; в {st['platform']} нет отдельных переключателей разрешений")
                for c in st["checks"]:
                    if c["status"] != "ok":
                        print(f"  ⚠ {c['label']}: {c['message']}")
                if st["error"]:
                    print(f"  ⚠ {st['error']}")
                sys.exit(0 if st["ready"] else 1)
            computer_use_perms.print_help()
            return
        # No subcommand → show help
        computer_use_parser.print_help()

    computer_use_parser.set_defaults(func=cmd_computer_use)
    # =========================================================================
    # mcp command  (parser built in korra_cli/subcommands/mcp.py)
    # =========================================================================
    build_mcp_parser(subparsers, cmd_mcp=cmd_mcp)

    # =========================================================================
    # sessions command
    # =========================================================================
    sessions_parser = subparsers.add_parser(
        "sessions",
        help='Управление историей бесед: просмотр, переименование, экспорт и удаление',
        description='Просмотр и управление базой бесед SQLite',
    )
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_action")

    sessions_list = sessions_subparsers.add_parser("list", help='Показать последние беседы')
    sessions_list.add_argument(
        "--source", help='Фильтр по источнику: cli, telegram, discord и другие'
    )
    sessions_list.add_argument(
        "--limit", type=int, default=20, help='Максимум бесед в списке'
    )
    sessions_list.add_argument(
        "--workspace",
        metavar="NEEDLE",
        help='Только беседы указанного проекта: корень Git или папка, совпадающая по части пути или имени',
    )

    def _add_session_filter_args(p, default_older_help):
        p.add_argument(
            "--older-than",
            metavar="AGE",
            help=default_older_help,
        )
        p.add_argument(
            "--newer-than",
            metavar="AGE",
            help='Только беседы, активные за период AGE, например 5h или 2d, либо после даты ISO',
        )
        p.add_argument(
            "--before",
            metavar="TIME",
            help='Только беседы, начатые до TIME: период назад, например 5h, или дата ISO, например 2026-07-05 14:30',
        )
        p.add_argument(
            "--after",
            metavar="TIME",
            help='Только беседы, начатые не раньше TIME: период назад, например 5h, или дата ISO',
        )
        p.add_argument("--source", help='Только беседы из указанного источника')
        p.add_argument(
            "--title", help='Только беседы, чьи названия содержат этот текст'
        )
        p.add_argument(
            "--end-reason", help='Только беседы с указанной причиной завершения'
        )
        p.add_argument(
            "--cwd", help='Только беседы, рабочая папка которых находится внутри этого пути'
        )
        p.add_argument(
            "--min-messages", type=int, help='Только беседы минимум с N сообщениями'
        )
        p.add_argument(
            "--max-messages", type=int, help='Только беседы максимум с N сообщениями'
        )
        p.add_argument(
            "--model",
            help='Только беседы, имя модели которых содержит этот текст, например sonnet или gpt-5',
        )
        p.add_argument(
            "--provider",
            help='Только беседы через указанного провайдера, например openrouter, anthropic или nous',
        )
        p.add_argument(
            "--user", help='Только беседы указанного пользователя по ID'
        )
        p.add_argument(
            "--chat-id", help='Только беседы указанного чата или канала по ID'
        )
        p.add_argument(
            "--chat-type",
            help='Только беседы указанного типа, например dm или group',
        )
        p.add_argument(
            "--branch",
            help='Только беседы, имя ветки Git которых содержит этот текст',
        )
        p.add_argument(
            "--min-tokens", type=int,
            help='Только беседы с общим расходом от N токенов, включая вход и выход',
        )
        p.add_argument(
            "--max-tokens", type=int,
            help='Только беседы с общим расходом до N токенов, включая вход и выход',
        )
        p.add_argument(
            "--min-cost", type=float,
            help='Только беседы стоимостью от N долларов, фактической или расчётной',
        )
        p.add_argument(
            "--max-cost", type=float,
            help='Только беседы стоимостью до N долларов, фактической или расчётной',
        )
        p.add_argument(
            "--min-tool-calls", type=int,
            help='Только беседы минимум с N вызовами инструментов',
        )
        p.add_argument(
            "--max-tool-calls", type=int,
            help='Только беседы максимум с N вызовами инструментов',
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            help='Показать подходящие беседы без изменений',
        )
        p.add_argument(
            "--yes", "-y", action="store_true", help='Пропустить подтверждение'
        )

    sessions_export = sessions_subparsers.add_parser(
        "export", help='Экспортировать беседы в JSONL, Markdown или QMD'
    )
    sessions_export.add_argument(
        "output",
        nargs="?",
        help=(
            'Путь экспорта. JSONL: обязательный путь к файлу, - для stdout. md/qmd: папка, по умолчанию session-exports в папке профиля.'
        ),
    )
    sessions_export.add_argument(
        "--format",
        choices=["jsonl", "md", "qmd", "html", "trace"],
        default="jsonl",
        help=(
            'Формат экспорта (по умолчанию jsonl). trace — JSONL Claude Code для Hugging Face Agent Trace Viewer.'
        ),
    )
    sessions_export.add_argument(
        "--upload",
        action="store_true",
        help=(
            'Только trace: загрузить в ваш набор трассировок Hugging Face вместо локального файла; нужен HF_TOKEN'
        ),
    )
    sessions_export.add_argument(
        "--public",
        action="store_true",
        help='Только trace --upload: создать или обновить публичный набор данных вместо закрытого',
    )
    sessions_export.add_argument(
        "--no-redact",
        action="store_true",
        help=(
            'Только trace: не скрывать секреты; используйте лишь после ручной проверки'
        ),
    )
    sessions_export.add_argument(
        "--only",
        choices=["user-prompts"],
        help=(
            'Экспортировать только выбранную часть. user-prompts: по запросу на строку в JSONL, разделы с заголовками в Markdown.'
        ),
    )
    sessions_export.add_argument(
        "--session-id", help='ID беседы или его уникальное начало для экспорта'
    )
    _add_session_filter_args(
        sessions_export,
        'Экспортировать только беседы старше AGE: период 5h/2d, число дней или дата ISO',
    )
    sessions_export.add_argument(
        "--redact",
        action="store_true",
        help='Скрыть секреты в экспорте: ключи API, токены и данные входа',
    )
    sessions_export.add_argument(
        "--lineage",
        choices=["single", "logical"],
        default="single",
        help='Только md/qmd: экспортировать одну запись беседы или всю цепочку её сжатий',
    )
    sessions_export.add_argument(
        "--delete-after-verified",
        action="store_true",
        help='Только md/qmd: после проверки экспорта одной беседы удалить её; нужен --yes',
    )
    sessions_export.add_argument(
        "--force",
        action="store_true",
        help='Только md/qmd: заменить существующий файл экспорта',
    )

    sessions_delete = sessions_subparsers.add_parser(
        "delete", help='Удалить выбранную беседу'
    )
    sessions_delete.add_argument("session_id", help='ID удаляемой беседы')
    sessions_delete.add_argument(
        "--yes", "-y", action="store_true", help='Пропустить подтверждение'
    )

    sessions_prune = sessions_subparsers.add_parser(
        "prune",
        help='Удалить старые беседы с фильтрами по времени, источнику, названию и другим полям',
    )
    _add_session_filter_args(
        sessions_prune,
        'Удалить беседы старше AGE: число дней, период 5h/2d/1w или дата ISO. Без фильтров prune удаляет старше 90 дней; с фильтром по умолчанию возраст не ограничен.',
    )
    sessions_prune.add_argument(
        "--include-archived",
        action="store_true",
        help='Также удалить архивные беседы; по умолчанию сохраняются',
    )
    sessions_prune.add_argument(
        "--include-pinned",
        action="store_true",
        help='Также удалить закреплённые беседы; по умолчанию закрепление защищает от удаления',
    )
    sessions_prune.add_argument(
        "--never-active",
        action="store_true",
        help=(
            'Удалить неиспользованные записи шлюза старше AGE (по умолчанию 30 дней), без сообщений, токенов, инструментов и названия. Обычная очистка выбирает только завершённые беседы и такие записи не затрагивает.'
        ),
    )

    sessions_archive = sessions_subparsers.add_parser(
        "archive",
        help='Переместить беседы по фильтрам в архив без удаления',
    )
    _add_session_filter_args(
        sessions_archive,
        'Архивировать только беседы старше AGE: период 5h/2d, число дней или дата ISO',
    )

    sessions_subparsers.add_parser(
        "optimize",
        help='Освободить место: объединить сегменты FTS5 и выполнить VACUUM без изменения данных',
    )

    sessions_clean_markers = sessions_subparsers.add_parser(
        "clean-markers",
        help='Удалить устаревшее содержимое-маркеры вызовов инструментов из старых бесед',
        description=(
            'Исправить старые записи, где вместо ответа сохранился только маркер вроде [memory]. При загрузке беседы это уже исправляется в памяти; команда однократно сохраняет исправление в базе. Изменяется только content; tool_calls и остальные поля сохраняются.'
        ),
    )
    sessions_clean_markers.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help='Показать число затронутых строк без записи',
    )
    sessions_clean_markers.add_argument(
        "--no-backup",
        action="store_true",
        default=False,
        help='Не создавать резервную копию state.db перед записью; не рекомендуется',
    )

    sessions_optimize_storage = sessions_subparsers.add_parser(
        "optimize-storage",
        help='Перестроить поисковый индекс в компактный формат v23 и освободить место',
        description=(
            'Перестроить полнотекстовый индекс в компактный формат v23 без дублирования сообщений и индексации вывода инструментов. Показывает прогресс, ограничивает нагрузку для работы шлюза и завершает VACUUM. Можно прервать и продолжить позже. Беседы не изменяются.'
        ),
    )
    sessions_optimize_storage.add_argument(
        "--no-vacuum",
        action="store_true",
        default=False,
        help='Пропустить финальный VACUUM; освободившиеся страницы вернут место системе только при следующем VACUUM',
    )
    sessions_optimize_storage.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help='Пропустить подтверждение свободного места',
    )

    sessions_repair = sessions_subparsers.add_parser(
        "repair",
        help='Восстановить структуру state.db, чтобы скрытые беседы снова появились',
        description=(
            'Восстановить state.db с повреждённой структурой, из-за которой приложение или панель не показывают беседы. Сначала создаётся копия; беседы и сообщения сохраняются, поисковый индекс при необходимости перестраивается.'
        ),
    )
    sessions_repair.add_argument(
        "--check-only",
        action="store_true",
        help='Только проверить открытие базы без изменений',
    )
    sessions_repair.add_argument(
        "--no-backup",
        action="store_true",
        help='Не создавать резервную копию с датой и временем; не рекомендуется',
    )

    sessions_repair_routing = sessions_subparsers.add_parser(
        "repair-routing",
        help='Восстановить сведения о маршрутизации бесед шлюза',
        description=(
            'Найти беседы шлюза без session_key, chat_id или origin, которые не восстанавливаются после перезапуска. Восстановить сведения из предыдущей связанной беседы, только если связь однозначна. Без --apply только показать результат.'
        ),
    )
    sessions_repair_routing.add_argument(
        "--apply",
        action="store_true",
        help='Применить восстановление связей; по умолчанию только отчёт',
    )
    sessions_repair_routing.add_argument(
        "--max-gap-seconds",
        type=float,
        default=None,
        help=(
            'Максимальный промежуток между последней активностью прежней беседы и началом потерявшей связь, в секундах (по умолчанию 900)'
        ),
    )

    sessions_recover = sessions_subparsers.add_parser(
        "recover",
        help='Восстановить данные бесед в отдельную чистую базу',
        description=(
            'Восстановление повреждённой state.db без изменения исходника. База и файлы WAL, SHM и журнала копируются до открытия SQLite. Записи восстанавливаются в новую базу, поисковые индексы создаются заново. Рабочая база автоматически не заменяется.'
        ),
    )
    sessions_recover.add_argument(
        "--source",
        type=Path,
        required=True,
        help='Исходная state.db или резервная копия для проверки и восстановления',
    )
    sessions_recover.add_argument(
        "--output",
        type=Path,
        help='Путь к новой восстановленной базе; обязателен без --inspect-only',
    )
    sessions_recover.add_argument(
        "--inspect-only",
        action="store_true",
        help='Только проверить читаемость основных таблиц без создания базы',
    )
    sessions_recover.add_argument(
        "--work-dir",
        type=Path,
        help='Существующая папка для временной копии исходника; по умолчанию рядом с результатом',
    )
    sessions_recover.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help='Строк в одной сохраняемой порции восстановления (по умолчанию 1000)',
    )
    sessions_recover.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            'Продолжать восстановление вокруг повреждённых участков; результат остаётся отдельно, все пропуски записываются в отчёт'
        ),
    )
    sessions_recover.add_argument(
        "--report",
        type=Path,
        help='Путь к отчёту JSON (по умолчанию <output>.recovery.json)',
    )

    sessions_subparsers.add_parser("stats", help='Показать статистику базы бесед')

    sessions_rename = sessions_subparsers.add_parser(
        "rename", help='Задать или изменить название беседы'
    )
    sessions_rename.add_argument("session_id", help='ID переименовываемой беседы')
    sessions_rename.add_argument("title", nargs="+", help='Новое название беседы')

    sessions_pin = sessions_subparsers.add_parser(
        "pin",
        help='Закрепить беседы и защитить от автоматического архивирования',
        description=(
            'Закрепить одну или несколько бесед. Они не попадут под sessions.auto_archive и всегда видны в списках. То же закрепление отображается в боковой панели приложения.'
        ),
    )
    sessions_pin.add_argument(
        "session_ids", nargs="+", help='ID бесед или уникальные начала ID для закрепления'
    )

    sessions_unpin = sessions_subparsers.add_parser(
        "unpin", help='Открепить беседы и снять защиту от автоматического архивирования'
    )
    sessions_unpin.add_argument(
        "session_ids", nargs="+", help='ID бесед или уникальные начала ID для открепления'
    )

    sessions_pinned = sessions_subparsers.add_parser(
        "pinned", help='Показать закреплённые беседы'
    )
    sessions_pinned.add_argument(
        "--json",
        action="store_true",
        help='Вывести машиночитаемый JSON для скриптов резервирования и восстановления',
    )

    sessions_retitle = sessions_subparsers.add_parser(
        "retitle-skills",
        help='Исправить названия бесед, полученные из текста навыка вместо запроса',
        description=(
            'Пересоздать названия бесед, начатых командой /skill, по настоящему тексту пользователя вместо описания навыка. Без --apply только показать план изменений.'
        ),
    )
    sessions_retitle.add_argument(
        "--apply",
        action="store_true",
        help='Сохранить новые названия; по умолчанию только просмотр',
    )
    sessions_retitle.add_argument(
        "--limit",
        type=int,
        default=200,
        help='Максимум бесед для проверки (по умолчанию 200)',
    )

    sessions_browse = sessions_subparsers.add_parser(
        "browse",
        help='Найти, выбрать и продолжить беседу в меню',
    )
    sessions_browse.add_argument(
        "--source", help='Фильтр по источнику: cli, telegram, discord и другие'
    )
    sessions_browse.add_argument(
        "--limit", type=int, default=500, help='Максимум загружаемых бесед (по умолчанию 500)'
    )

    sessions_import = sessions_subparsers.add_parser(
        "import",
        help='Перенести беседу Claude Code или Codex CLI в Корру',
        description=(
            'Загрузить беседу из ~/.claude/projects или ~/.codex/sessions в базу Корры для продолжения через korra --resume <id>. Исходные файлы только читаются.'
        ),
    )
    sessions_import.add_argument(
        "--from",
        dest="from_source",
        choices=["claude", "codex"],
        help='Источник импорта; по умолчанию выбор из обоих',
    )
    sessions_import.add_argument(
        "path",
        nargs="?",
        help='Путь к конкретному JSONL-файлу беседы без меню выбора',
    )


    # cmd_sessions lives in korra_cli/sessions_cmd.py (main.py decomposition).
    # sessions_parser is threaded in via functools.partial because the
    # fallthrough branch calls sessions_parser.print_help() (formerly a
    # closure capture of this main()-local). The indirection through _self()
    # keeps the sessions_cmd import lazy until the subcommand actually runs
    # and lets monkeypatches on korra_cli.main.cmd_sessions keep working.
    def _dispatch_sessions(_args, *, sessions_parser=sessions_parser):
        return _self().cmd_sessions(_args, sessions_parser=sessions_parser)

    sessions_parser.set_defaults(func=_dispatch_sessions)

    # =========================================================================
    # insights command  (parser built in korra_cli/subcommands/insights.py)
    # =========================================================================
    build_insights_parser(subparsers, cmd_insights=cmd_insights)
    build_monitoring_parser(subparsers, cmd_monitoring=cmd_monitoring)

    # =========================================================================
    # claw command  (parser built in korra_cli/subcommands/claw.py)
    # =========================================================================
    build_claw_parser(subparsers, cmd_claw=cmd_claw)

    # NOTE: the `hermes version` subcommand was removed — `hermes --version`
    # / `-V` now carries the full output including update status.

    # =========================================================================
    # update command  (parser built in korra_cli/subcommands/update.py)
    # =========================================================================
    build_update_parser(subparsers, cmd_update=cmd_update)

    # =========================================================================
    # uninstall command  (parser built in korra_cli/subcommands/uninstall.py)
    # =========================================================================
    build_uninstall_parser(subparsers, cmd_uninstall=cmd_uninstall)

    # =========================================================================
    # acp command  (parser built in korra_cli/subcommands/acp.py)
    # =========================================================================
    build_acp_parser(subparsers, cmd_acp=cmd_acp)

    # =========================================================================
    # profile command  (parser built in korra_cli/subcommands/profile.py)
    # =========================================================================
    build_profile_parser(subparsers, cmd_profile=cmd_profile)

    # =========================================================================
    # completion command
    # =========================================================================
    completion_parser = subparsers.add_parser(
        "completion",
        help='Вывести скрипт автодополнения для bash, zsh или fish',
    )
    completion_parser.add_argument(
        "shell",
        nargs="?",
        default="bash",
        choices=["bash", "zsh", "fish"],
        help='Оболочка (по умолчанию bash)',
    )
    completion_parser.set_defaults(func=lambda args: cmd_completion(args, parser))

    # =========================================================================
    # dashboard command  (parser built in korra_cli/subcommands/dashboard.py)
    # =========================================================================
    build_dashboard_parser(
        subparsers,
        cmd_dashboard=cmd_dashboard,
        cmd_dashboard_register=cmd_dashboard_register,
    )


    # =========================================================================
    # desktop (a.k.a. gui) command
    #
    # The canonical name is "desktop"; "gui" is kept as a deprecated alias
    # for one release. The Hermes-Setup.exe success screen tells users to
    # run `hermes desktop` from a terminal, so the canonical name needs
    # to be the one that appears in --help (argparse promotes the primary
    # name; aliases stay hidden).
    # =========================================================================
    # gui command  (parser built in korra_cli/subcommands/gui.py)
    # =========================================================================
    build_gui_parser(subparsers, cmd_gui=cmd_gui)

    # =========================================================================
    # logs command  (parser built in korra_cli/subcommands/logs.py)
    # =========================================================================
    build_logs_parser(subparsers, cmd_logs=cmd_logs)

    # =========================================================================
    # prompt-size command  (parser built in korra_cli/subcommands/prompt_size.py)
    # =========================================================================
    build_prompt_size_parser(subparsers, cmd_prompt_size=cmd_prompt_size)

    # =========================================================================
    # Parse and execute
    # =========================================================================
    # Pre-process argv so unquoted multi-word session names after -c / -r
    # are merged into a single token before argparse sees them.
    # e.g. ``hermes -c Pokemon Agent Dev`` → ``hermes -c 'Pokemon Agent Dev'``
    # ── Container-aware routing ────────────────────────────────────────
    # When NixOS container mode is active, route ALL subcommands into
    # the managed container.  This MUST run before parse_args() so that
    # --help, unrecognised flags, and every subcommand are forwarded
    # transparently instead of being intercepted by argparse on the host.
    from korra_cli.config import get_container_exec_info

    container_info = get_container_exec_info()
    if container_info:
        _exec_in_container(container_info, sys.argv[1:])
        # Unreachable: os.execvp never returns on success (process is replaced)
        # and raises OSError on failure (which propagates as a traceback).
        sys.exit(1)

    _processed_argv = _coalesce_session_name_args(sys.argv[1:])

    # ── Defensive subparser routing (bpo-9338 workaround) ───────────
    # On some Python versions (notably <3.11), argparse fails to route
    # subcommand tokens when the parent parser has nargs='?' optional
    # arguments (--continue).  The symptom: "unrecognized arguments: model"
    # even though 'model' is a registered subcommand.
    #
    # Fix: when argv contains a token matching a known subcommand, set
    # subparsers.required=True to force deterministic routing.  If that
    # fails (e.g. 'hermes -c model' where 'model' is consumed as the
    # session name for --continue), fall back to the default behaviour.
    import io as _io

    _known_cmds = (
        set(subparsers.choices.keys()) if hasattr(subparsers, "choices") else set()
    )
    _has_cmd_token = any(
        t in _known_cmds for t in _processed_argv if not t.startswith("-")
    )

    if _has_cmd_token:
        subparsers.required = True
        _saved_stderr = sys.stderr
        try:
            sys.stderr = _io.StringIO()
            args = parser.parse_args(_processed_argv)
            sys.stderr = _saved_stderr
        except SystemExit as exc:
            sys.stderr = _saved_stderr
            # Help/version flags (exit code 0) already printed output —
            # re-raise immediately to avoid a second parse_args printing
            # the same help text again (#10230).
            if exc.code == 0:
                raise
            # Subcommand name was consumed as a flag value (e.g. -c model).
            # Fall back to optional subparsers so argparse handles it normally.
            subparsers.required = False
            args = parser.parse_args(_processed_argv)
    else:
        subparsers.required = False
        args = parser.parse_args(_processed_argv)

    # Handle --version flag
    if args.version:
        cmd_version(args)
        return

    # --yolo: set HERMES_YOLO_MODE *before* plugin discovery.  The call to
    # _prepare_agent_startup() below triggers discover_plugins() → tool
    # imports, and tools.approval freezes _YOLO_MODE_FROZEN at module
    # import time (PR #7994, security hardening against prompt-injection).
    # If the env var is set only later (e.g. inside cmd_chat), the frozen
    # value is already False and --yolo silently does nothing.
    if getattr(args, "yolo", False):
        korra_env_set(os.environ, "KORRA_YOLO_MODE", "1")

    # Discover Python plugins and register shell hooks once, before any
    # command that can fire lifecycle hooks.  Both are idempotent; gated
    # so introspection/management commands (hermes hooks list, cron
    # list, gateway status, mcp add, ...) don't pay discovery cost or
    # trigger consent prompts for hooks the user is still inspecting.
    _prepare_agent_startup(args)

    # Handle top-level --oneshot / -z: single-shot mode, stdout = final
    # response only, nothing else. Bypasses cli.py entirely.
    if getattr(args, "oneshot", None):
        _confirm_startup_expensive_model_override(args)
        _run_and_exit_oneshot(
            args.oneshot,
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
            skills=getattr(args, "skills", None),
            usage_file=getattr(args, "usage_file", None),
        )

    # Handle top-level --resume / --continue as shortcut to chat
    if (args.resume or args.continue_last) and args.command is None:
        args.command = "chat"
        for attr, default in [
            ("query", None),
            ("model", None),
            ("provider", None),
            ("toolsets", None),
            ("verbose", None),
            ("worktree", False),
        ]:
            if not hasattr(args, attr):
                setattr(args, attr, default)
        cmd_chat(args)
        return

    # Default to chat if no command specified
    if args.command is None:
        for attr, default in [
            ("query", None),
            ("model", None),
            ("provider", None),
            ("toolsets", None),
            ("verbose", None),
            ("resume", None),
            ("continue_last", None),
            ("worktree", False),
        ]:
            if not hasattr(args, attr):
                setattr(args, attr, default)
        cmd_chat(args)
        return

    # Execute the command.  Propagate the handler's return code as the
    # process exit code so subcommands that signal failure (e.g.
    # ``hermes egress start`` refusing when credential_source=bitwarden
    # is misconfigured) actually exit non-zero.  Handlers that return
    # None are treated as success (exit 0).
    if hasattr(args, "func"):
        rc = args.func(args)
        if isinstance(rc, int) and rc != 0:
            sys.exit(rc)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
