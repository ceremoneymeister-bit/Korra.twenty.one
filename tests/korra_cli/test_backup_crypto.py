"""Hybrid encryption of the secrets block and the secret-file classifier."""

import base64
import io
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from korra_cli import backup_crypto as crypto
from korra_cli.backup_secrets import has_inline_secret, is_secret


def _seal(payload: bytes, recipient) -> bytes:
    sink = io.BytesIO()
    writer = crypto.EncryptingWriter(sink, recipient)
    for start in range(0, len(payload), 70_000):
        writer.write(payload[start:start + 70_000])
    writer.close()
    return sink.getvalue()


@pytest.mark.parametrize("size", [0, 1, crypto.CHUNK_SIZE - 1, crypto.CHUNK_SIZE, 2 * crypto.CHUNK_SIZE + 5])
def test_round_trip(size):
    private = X25519PrivateKey.generate()
    payload = bytes(range(256)) * (size // 256 + 1)
    payload = payload[:size]
    sealed = _seal(payload, private.public_key())
    assert payload[:64] not in sealed or size < 64
    out = io.BytesIO()
    assert crypto.decrypt_stream(io.BytesIO(sealed), private, out) == size
    assert out.getvalue() == payload


def test_wrong_key_truncation_and_tamper_are_rejected():
    private = X25519PrivateKey.generate()
    payload = b"x" * (crypto.CHUNK_SIZE * 2 + 10)
    sealed = _seal(payload, private.public_key())
    with pytest.raises(crypto.SecretsCryptoError):
        crypto.decrypt_stream(io.BytesIO(sealed), X25519PrivateKey.generate(), io.BytesIO())
    chunk = crypto.CHUNK_SIZE + 16
    truncated = sealed[: crypto.HEADER_SIZE + chunk]
    with pytest.raises(crypto.SecretsCryptoError):
        crypto.decrypt_stream(io.BytesIO(truncated), private, io.BytesIO())
    flipped = bytearray(sealed)
    flipped[crypto.HEADER_SIZE + 5] ^= 1
    with pytest.raises(crypto.SecretsCryptoError):
        crypto.decrypt_stream(io.BytesIO(bytes(flipped)), private, io.BytesIO())
    with pytest.raises(crypto.SecretsCryptoError):
        crypto.decrypt_stream(io.BytesIO(b"nope"), private, io.BytesIO())


def test_zip_on_top_of_non_seekable_encrypting_writer():
    private = X25519PrivateKey.generate()
    sink = io.BytesIO()
    writer = crypto.EncryptingWriter(sink, private.public_key())
    with zipfile.ZipFile(writer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(".env", "TOKEN=1")
        zf.writestr("a/b.pem", "k" * 10_000)
    writer.close()
    out = io.BytesIO()
    crypto.decrypt_stream(io.BytesIO(sink.getvalue()), private, out)
    with zipfile.ZipFile(out) as zf:
        assert zf.read(".env") == b"TOKEN=1"
        assert zf.testzip() is None


def test_default_key_parses_and_pem_and_raw_private_keys_load(tmp_path):
    public = crypto.parse_public_key(crypto.DEFAULT_OPERATOR_PUBLIC_KEY)
    assert len(crypto.public_key_bytes(public)) == 32
    private = X25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    (tmp_path / "k.pem").write_bytes(pem)
    raw = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    (tmp_path / "k.b64").write_bytes(base64.b64encode(raw))
    for name in ("k.pem", "k.b64"):
        loaded = crypto.load_private_key(tmp_path / name)
        assert crypto.public_key_bytes(loaded.public_key()) == crypto.public_key_bytes(private.public_key())
    with pytest.raises(crypto.SecretsCryptoError):
        crypto.parse_public_key("not a key")
    with pytest.raises(crypto.SecretsCryptoError):
        crypto.load_private_key(tmp_path / "missing.pem")


@pytest.mark.parametrize("rel", [
    ".env", "profiles/work/.env", "auth.json", "profiles/a/auth.json", ".anthropic_oauth.json",
    "google_token.json", "google-workspace/token.json", "mcp-tokens/x.json", "pairing/telegram.json",
    "secrets/anything", "home/.ssh/id_ed25519", "home/.codex/auth.json", "home/.claude/.credentials.json",
    "home/.config/gh/hosts.yml", "whatsapp/session/creds.json", "platforms/matrix/store/crypto.db",
    "keys/server.pem", "keys/server.key", "slack_tokens.json", "webhook_subscriptions.json",
    "crm-connection/connection.json", "_external/.honcho/config.json", "myapp_api_key.json",
    "config.yaml", "profiles/work/config.yaml", "config.yaml.bak", "gateway.json", "mcp.json",
    "profiles/work/mcp.json", ".claude.json", "home/.claude.json", "profiles/work/home/.claude.json.bak",
])
def test_secret_paths(rel):
    assert is_secret(rel)


@pytest.mark.parametrize("rel", [
    "state.db", "skills/ops/token_utils.py", "memories/MEMORY.md", "notes/idea.txt",
    "platforms/whatsapp_cloud/media/photo.jpg", "plugins/jev/README.md", "_external/.honcho/data.db",
])
def test_open_paths(rel, tmp_path):
    assert not is_secret(rel)


def test_inline_keys_in_settings_make_the_file_secret(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("model: x\nmax_tokens: 4096\napi_key: ''\nkey_env: ${OPENAI_API_KEY}\n")
    assert not has_inline_secret(cfg)
    assert not is_secret("plugins/p/settings.yaml", cfg)
    cfg.write_text("telegram:\n  bot_token: '123456:ABCdefGhIjK'\n")
    assert has_inline_secret(cfg)
    assert is_secret("plugins/p/settings.yaml", cfg)
