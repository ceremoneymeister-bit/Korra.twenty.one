"""Credentials that used to stay readable in the Google Drive copy (review of K21-349).

All values are synthetic. A secret counts as protected only if it is absent from
every *unpacked* open member of the archive (DEFLATE hides it from a raw search).
"""

import io
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from korra_cli import backup, backup_crypto as crypto
from korra_cli.backup_secrets import is_secret


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv("KORRA_HOME", str(root))
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.delenv("KORRA_PROFILE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: root)
    monkeypatch.setattr(backup, "_scan_external_files", lambda: ([], []))
    monkeypatch.setattr(backup, "_assert_import_offline", lambda *a, **kw: {})
    return root


def put(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def split(root, key):
    sink = io.BytesIO()
    files, _ = backup._scan_backup_files(root)
    stats = backup._write_split_archive(sink, files, [], key.public_key(), root.parent)
    assert not stats.errors, stats.errors
    return sink.getvalue()


def open_members_containing(archive: bytes, marker: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return [name for name in zf.namelist()
                if name != crypto.SECRETS_MEMBER and marker in zf.read(name)]


def secrets_zip(archive: bytes, private) -> zipfile.ZipFile:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        inner = io.BytesIO()
        crypto.decrypt_stream(io.BytesIO(zf.read(crypto.SECRETS_MEMBER)), private, inner)
    return zipfile.ZipFile(inner)


MCP_HEADERS = (b'mcp_servers:\n  remote:\n    url: https://mcp.invalid/mcp\n'
               b'    headers:\n      Authorization: "Bearer %s"\n')


@pytest.mark.parametrize("prefix", ["", "profiles/work/"])
def test_mcp_authorization_in_config_is_not_open(isolated, prefix):
    marker = b"REVIEW-SYNTHETIC-MCP-BEARER-7a83f9c1"
    put(isolated, prefix + "config.yaml", MCP_HEADERS % marker)
    put(isolated, ".env", b"TEST=synthetic-protected-env-1111\n")
    private = X25519PrivateKey.generate()
    archive = split(isolated, private)
    assert open_members_containing(archive, marker) == []
    with secrets_zip(archive, private) as inner:
        assert marker in inner.read(prefix + "config.yaml")


@pytest.mark.parametrize("text", [
    b"mcp_servers:\n  gh:\n    command: npx\n    env:\n      GITHUB_X: abc\n",
    b'{"mcp_servers": {"s": {"url": "https://x.invalid", "headers": {"X-Api-Key": "k-12345678"}}}}',
    b"proxy: https://user:hunter2pass@proxy.invalid:3128\n",
    b"note: calls use Bearer abcdefghijklmnop123\n",
    b"headers:\n  X-Api-Key: 0123456789abcdef\n",
    b"bot: 123456789:AAEhBP0av18Zm3sX0aGZxqTb3JpdGUxUq1_\n",
])
def test_connection_configs_with_credentials_are_secret(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_bytes(text)
    assert is_secret("config.yaml", path)


@pytest.mark.parametrize("text", [
    b"model: openrouter/auto\n",
    b"mcp_servers:\n  docs:\n    command: npx\n    args: [docs-server]\n",
    b"headers:\n  Authorization: ${MCP_AUTH}\n",
])
def test_plain_configs_stay_open(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_bytes(text)
    assert not is_secret("config.yaml", path)


def test_camofox_state_directory_is_secret():
    assert is_secret("browser_auth/camofox/hermes_ab12/cookies.json")
    assert is_secret("profiles/work/browser_auth/camofox/state.bin")
