#!/usr/bin/env python3
"""Bridge between Hermes OAuth token and gws CLI.

Refreshes the token if expired, then executes gws with the valid access token.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Ensure sibling modules (_hermes_home) are importable when run standalone.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from _hermes_home import get_hermes_home
from korra_cli import google_workspace as _native_google
from google_oauth_scopes import require_selected_service, validate_scope_contract


def get_token_path() -> Path:
    return _native_google._active_token_path(get_hermes_home())


def get_valid_token(api_name: str | None = None) -> str:
    """Return a valid access token, refreshing if needed."""
    token_path = get_token_path()
    if not token_path.exists():
        print(
            "ERROR: No Google token found. Run setup.py --auth-url --services LIST first.",
            file=sys.stderr,
        )
        sys.exit(1)

    import json

    token_data = json.loads(token_path.read_text(encoding="utf-8"))
    try:
        validate_scope_contract(token_data)
        if api_name is not None:
            require_selected_service(token_data, api_name)
    except ValueError as e:
        print(f"ERROR: Google token scope contract is invalid: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        return _native_google._credentials(get_hermes_home()).token
    except _native_google.GoogleWorkspaceError as native_error:
        print(f"ERROR: {native_error}", file=sys.stderr)
        sys.exit(1)


def main():
    """Refresh token if needed, then exec gws with remaining args."""
    if len(sys.argv) < 2:
        print("Usage: gws_bridge.py <gws args...>", file=sys.stderr)
        sys.exit(1)

    access_token = get_valid_token(sys.argv[1])
    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_TOKEN"] = access_token

    result = subprocess.run(["gws"] + sys.argv[1:], env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
