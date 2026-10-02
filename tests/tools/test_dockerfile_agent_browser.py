"""K21-271: the image ships agent-browser so built-in browser_* tools work
offline, with a pinned and verified package and an executable native binary."""
from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _agent_browser_run_block() -> str:
    text = DOCKERFILE.read_text()
    start = text.index("ARG AGENT_BROWSER_VERSION=")
    end = text.index("# ---------- Layer-cached Python dependency install", start)
    return text[start:end]


def test_agent_browser_is_pinned_with_integrity() -> None:
    block = _agent_browser_run_block()
    assert re.search(r"ARG AGENT_BROWSER_VERSION=\d+\.\d+\.\d+\n", block)
    assert re.search(r"ARG AGENT_BROWSER_INTEGRITY=sha512-[A-Za-z0-9+/]{86}==\n", block)
    assert '"$got" = "${AGENT_BROWSER_INTEGRITY}"' in block
    assert "exit 1" in block


def test_agent_browser_binary_is_executable_for_runtime_user_and_pruned() -> None:
    block = _agent_browser_run_block()
    # The wrapper chmods a non-executable binary at first run, which fails
    # on a read-only tree for the hermes user.
    assert 'chmod 0755 "$ab_bin/agent-browser-linux-${ab_arch}"' in block
    # Other platforms' binaries (~60 MB) are not shipped.
    assert "-delete" in block and "! -name \"agent-browser-linux-${ab_arch}\"" in block
    # Direct link: no node wrapper between the engine's kill and the real CLI.
    assert 'ln -sf "$ab_bin/agent-browser-linux-${ab_arch}" /usr/local/bin/agent-browser' in block
    # Build fails early if the CLI does not start.
    assert "agent-browser --version" in block


def test_agent_browser_matches_the_version_the_engine_resolves_via_npx() -> None:
    from tools.browser_tool import AGENT_BROWSER_NPX_SPEC

    version = re.search(r"AGENT_BROWSER_VERSION=(\d+)\.(\d+)\.", _agent_browser_run_block())
    assert version
    assert AGENT_BROWSER_NPX_SPEC == f"agent-browser@^{version.group(1)}.{version.group(2)}.0"
