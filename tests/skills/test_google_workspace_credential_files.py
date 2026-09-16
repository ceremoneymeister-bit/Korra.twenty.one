"""Regression test: google-workspace SKILL.md must declare required_credential_files.

PR #9931 accidentally removed the required_credential_files header, which broke
credential file mounting in Docker/Modal remote backends (#16452). This test
prevents the regression from silently reappearing.

Korra 0.21.1 moved the grant from the two flat ``$HERMES_HOME`` files
(``google_token.json`` + ``google_client_secret.json``) to a profile-local
``google-workspace/token.json``, with the operator's OAuth app on a read-only
host mount outside ``HERMES_HOME``. The passthrough contract survives the move:
the profile grant is still declared, the operator app file deliberately is not
— a sandbox never receives the installation's client secret.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch


SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "skills/productivity/google-workspace/SKILL.md"
)

_EXPECTED_PATHS = {"google-workspace/token.json"}
# The operator app credential is host state on a read-only mount; declaring it
# here would copy the installation's client secret into every remote sandbox.
_FORBIDDEN_PATHS = {"google_client_secret.json"}


def _parse_frontmatter(content: str) -> dict:
    from agent.skill_utils import parse_frontmatter

    fm, _ = parse_frontmatter(content)
    return fm


def _declared_paths(fm: dict) -> set:
    entries = fm.get("required_credential_files") or []
    return {(e["path"] if isinstance(e, dict) else e) for e in entries}


class TestGoogleWorkspaceCredentialFiles:
    def test_required_credential_files_present_in_skill_md(self):
        content = SKILL_MD.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        entries = fm.get("required_credential_files")
        assert entries, "required_credential_files missing from google-workspace SKILL.md"
        assert isinstance(entries, list), "required_credential_files must be a list"
        paths = _declared_paths(fm)
        assert _EXPECTED_PATHS <= paths, (
            f"Missing entries in required_credential_files: {_EXPECTED_PATHS - paths}"
        )
        leaked = _FORBIDDEN_PATHS & paths
        assert not leaked, (
            "operator-managed OAuth app must not be passed through to sandboxes: "
            f"{leaked}"
        )

    def test_entries_are_registered_when_files_exist(self, tmp_path):
        hermes_home = tmp_path / ".hermes"
        (hermes_home / "google-workspace").mkdir(parents=True)
        (hermes_home / "google-workspace" / "token.json").write_text("{}")

        from tools.credential_files import (
            clear_credential_files,
            get_credential_file_mounts,
            register_credential_files,
        )

        clear_credential_files()
        try:
            content = SKILL_MD.read_text(encoding="utf-8")
            fm = _parse_frontmatter(content)
            entries = fm.get("required_credential_files", [])

            with patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}):
                missing = register_credential_files(entries)

            assert missing == [], f"Unexpected missing files: {missing}"
            mounts = get_credential_file_mounts()
            container_paths = {m["container_path"] for m in mounts}
            assert "/root/.hermes/google-workspace/token.json" in container_paths
        finally:
            clear_credential_files()

    def test_shared_grant_is_mounted_for_consumer_without_token_copy(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "install"
        source = root / "profiles" / "assistant"
        consumer = root / "profiles" / "rop"
        source_token = source / "google-workspace" / "token.json"
        source_token.parent.mkdir(parents=True)
        consumer.mkdir(parents=True)
        source_token.write_text('{"refresh_token":"secret"}', encoding="utf-8")
        policy = root / "google-workspace" / "shared-access.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(
            json.dumps({"version": 1, "profile_sources": {"rop": "assistant"}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_HOME", str(consumer))

        from tools.credential_files import (
            clear_credential_files,
            get_credential_file_mounts,
            register_credential_files,
        )

        clear_credential_files()
        try:
            content = SKILL_MD.read_text(encoding="utf-8")
            entries = _parse_frontmatter(content).get("required_credential_files", [])
            missing = register_credential_files(entries)
            mounts = get_credential_file_mounts()

            assert missing == []
            assert mounts == [{
                "host_path": str(source_token),
                "container_path": "/root/.hermes/google-workspace/token.json",
            }]
            assert not (consumer / "google-workspace" / "token.json").exists()
        finally:
            clear_credential_files()

    def test_invalid_shared_policy_fails_closed(self, tmp_path, monkeypatch):
        root = tmp_path / "install"
        consumer = root / "profiles" / "rop"
        consumer.mkdir(parents=True)
        policy = root / "google-workspace" / "shared-access.json"
        policy.parent.mkdir(parents=True)
        policy.write_text('{"version":1,"profile_sources":{"rop":"rop"}}', encoding="utf-8")
        monkeypatch.setenv("HERMES_HOME", str(consumer))

        from tools.credential_files import (
            clear_credential_files,
            get_credential_file_mounts,
            register_credential_files,
        )

        clear_credential_files()
        try:
            content = SKILL_MD.read_text(encoding="utf-8")
            entries = _parse_frontmatter(content).get("required_credential_files", [])
            assert register_credential_files(entries) == ["google-workspace/token.json"]
            assert get_credential_file_mounts() == []
        finally:
            clear_credential_files()
