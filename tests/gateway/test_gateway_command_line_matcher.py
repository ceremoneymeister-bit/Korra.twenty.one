"""Tests for the strict gateway command-line matcher.

Regression guard for the Windows ``hermes gateway restart`` silent-outage bug:
the previous loose substring match (``"... gateway" in cmdline``) false-matched
``gateway status``/``dashboard`` siblings and unrelated processes such as
``python -m tui_gateway``, which let ``restart()`` race a still-draining old
process and ``status``/``start`` report false positives.
"""

from __future__ import annotations

import pytest

from gateway.status import (
    looks_like_gateway_command_line as matches,
    looks_like_gateway_runtime_command_line as matches_runtime,
)


ACCEPT = [
    "pythonw.exe -m korra_cli.main gateway run",
    r"C:\Users\me\hermes\venv\Scripts\pythonw.exe -m korra_cli.main gateway run",
    "python -m korra_cli.main --profile work gateway run",
    "python -m korra_cli.main gateway run --replace",
    "python -m korra_cli/main.py gateway run",
    "python gateway/run.py",
    "hermes-gateway.exe",
    "hermes gateway",          # bare `hermes gateway` defaults to run
    "hermes gateway run",
    # `korra` is the canonical console script of the same entrypoint (K21-331)
    "/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/korra gateway run --replace",
    "/opt/hermes/.venv/bin/korra -p work gateway run",
    "korra gateway",
    r"C:\Users\me\venv\Scripts\korra.exe gateway run",
    r'"C:\Program Files\Hermes\Scripts\korra.exe" gateway run',
    # profile selector AFTER the `gateway` token (argv is profile-position
    # agnostic — _apply_profile_override strips --profile/-p anywhere)
    "hermes gateway --profile work run",
    "python -m korra_cli.main gateway -p work run",
    "hermes gateway --profile=work run",
    # a profile literally NAMED "gateway"
    "hermes -p gateway gateway run",
    "python -m korra_cli.main --profile gateway gateway run",
    # quoted Windows paths with spaces (shlex-aware tokenization)
    r'"C:\Program Files\Hermes\hermes-gateway.exe"',
    r'"C:\Program Files\Hermes\gateway\run.py" run',
    r'"C:\Program Files\Py\pythonw.exe" -m korra_cli.main gateway run',
]

REJECT = [
    "python -m tui_gateway",                              # unrelated module
    "python -m korra_cli.main gateway status",           # other subcommand
    "python -m korra_cli.main gateway restart",
    "python -m korra_cli.main gateway stop",
    "/opt/hermes/.venv/bin/korra gateway status",       # korra, other subcommand
    "/opt/hermes/.venv/bin/korra dashboard",
    "/opt/hermes/.venv/bin/korrawatch gateway run",     # not the korra binary
    "python -m korra_cli.main --profile x dashboard",    # non-gateway subcommand
    "some random python -m mygateway thing",
    "",
    None,
]


@pytest.mark.parametrize("cmd", ACCEPT)
def test_accepts_real_gateway_run(cmd):
    assert matches(cmd) is True




def test_korra_binary_restart_counts_as_runtime_but_not_run():
    cmd = "/opt/hermes/.venv/bin/korra gateway restart"
    assert matches(cmd) is False
    assert matches_runtime(cmd) is True
