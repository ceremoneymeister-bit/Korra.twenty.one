"""Hybrid encryption of secrets inside a Korra backup archive.

Only the Korra team holds the private key. A backup carries one member,
``secrets.korra-enc``: an inner ZIP of credential files, encrypted to the
operator's X25519 public key.

Stream layout (all integers big-endian)::

    magic      8   b"KORRAENC"
    version    1   0x01
    algorithm  1   0x01 = X25519 -> HKDF-SHA256 -> ChaCha20-Poly1305
    chunk_log  1   log2 of the plaintext chunk size
    reserved   1   0x00
    ephemeral 32   sender ephemeral X25519 public key
    nonce_base12   per-file random value, XORed with the chunk counter
    chunks     *   ChaCha20-Poly1305 sealed chunks; the last one is marked final

The header is authenticated as AAD of every chunk, and the final flag is part of
the AAD, so truncation and header tampering are detected.
"""

from __future__ import annotations

import base64
import binascii
import os
import struct
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SECRETS_MEMBER = "secrets.korra-enc"

# Public key of the Korra team. The private half never lives on a client server.
DEFAULT_OPERATOR_PUBLIC_KEY = "BvpA+gR/pm4zRQgcnk+ybtAjh1rx/eEg3vkAhfeuhVA="

MAGIC = b"KORRAENC"
VERSION = 1
ALG_X25519_CHACHA20 = 1
_CHUNK_LOG = 20
CHUNK_SIZE = 1 << _CHUNK_LOG
_TAG = 16
_HEADER = struct.Struct(">8sBBBB32s12s")
HEADER_SIZE = _HEADER.size
_HKDF_INFO = b"korra-backup-secrets/v1"


class SecretsCryptoError(RuntimeError):
    """Key, format or authentication problem; the message is safe to show."""


def parse_public_key(value: str | bytes) -> X25519PublicKey:
    """Accept base64 of the raw 32 bytes, or a PEM ``PUBLIC KEY`` block."""
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    text = value.strip()
    try:
        if "BEGIN" in text:
            key = serialization.load_pem_public_key(text.encode("ascii"))
            if not isinstance(key, X25519PublicKey):
                raise SecretsCryptoError("открытый ключ оператора должен быть X25519")
            return key
        return X25519PublicKey.from_public_bytes(base64.b64decode(text, validate=True))
    except SecretsCryptoError:
        raise
    except (ValueError, binascii.Error, UnicodeError) as exc:
        raise SecretsCryptoError("открытый ключ оператора не читается") from exc


def public_key_bytes(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def load_private_key(path: Path) -> X25519PrivateKey:
    """Read a PEM (PKCS#8) X25519 private key, or base64 of the raw 32 bytes."""
    try:
        data = Path(path).expanduser().read_bytes()
    except OSError as exc:
        raise SecretsCryptoError(f"не удалось прочитать ключ {path}: {exc.strerror or exc}") from exc
    try:
        if b"BEGIN" in data:
            key = serialization.load_pem_private_key(data, password=None)
            if not isinstance(key, X25519PrivateKey):
                raise SecretsCryptoError("закрытый ключ должен быть X25519")
            return key
        return X25519PrivateKey.from_private_bytes(base64.b64decode(data.strip(), validate=True))
    except SecretsCryptoError:
        raise
    except (ValueError, TypeError, binascii.Error) as exc:
        raise SecretsCryptoError("закрытый ключ не читается (нужен PEM X25519 без пароля)") from exc


def _derive_key(shared: bytes, ephemeral_pub: bytes, recipient_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=ephemeral_pub + recipient_pub,
        info=_HKDF_INFO,
    ).derive(shared)


def _chunk_nonce(base: bytes, counter: int) -> bytes:
    mask = counter.to_bytes(12, "big")
    return bytes(a ^ b for a, b in zip(base, mask))


def _chunk_aad(header: bytes, final: bool) -> bytes:
    return header + (b"\x01" if final else b"\x00")


class EncryptingWriter:
    """Write-only, non-seekable file object that seals everything written to it."""

    def __init__(self, sink: BinaryIO, recipient: X25519PublicKey) -> None:
        self._sink = sink
        ephemeral = X25519PrivateKey.generate()
        ephemeral_pub = public_key_bytes(ephemeral.public_key())
        recipient_pub = public_key_bytes(recipient)
        key = _derive_key(ephemeral.exchange(recipient), ephemeral_pub, recipient_pub)
        self._aead = ChaCha20Poly1305(key)
        self._nonce_base = os.urandom(12)
        self._header = _HEADER.pack(
            MAGIC, VERSION, ALG_X25519_CHACHA20, _CHUNK_LOG, 0, ephemeral_pub, self._nonce_base
        )
        self._buffer = bytearray()
        self._counter = 0
        self._closed = False
        self._position = 0
        self._sink.write(self._header)

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    @property
    def closed(self) -> bool:
        return self._closed

    def write(self, data) -> int:
        if self._closed:
            raise ValueError("write to closed EncryptingWriter")
        view = memoryview(data).cast("B")
        self._buffer += view
        self._position += len(view)
        # Hold back one full chunk so that close() always has a final chunk.
        while len(self._buffer) > CHUNK_SIZE:
            self._emit(bytes(self._buffer[:CHUNK_SIZE]), final=False)
            del self._buffer[:CHUNK_SIZE]
        return len(view)

    def _emit(self, plain: bytes, *, final: bool) -> None:
        sealed = self._aead.encrypt(
            _chunk_nonce(self._nonce_base, self._counter), plain, _chunk_aad(self._header, final)
        )
        self._counter += 1
        self._sink.write(sealed)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._emit(bytes(self._buffer), final=True)
        self._buffer.clear()


def decrypt_stream(
    source: BinaryIO, private: X25519PrivateKey, out: BinaryIO, max_bytes: int | None = None
) -> int:
    """Authenticate and decrypt a whole ``EncryptingWriter`` stream into *out*."""
    header = source.read(HEADER_SIZE)
    if len(header) != HEADER_SIZE:
        raise SecretsCryptoError("блок секретов в копии повреждён (короткий заголовок)")
    magic, version, algorithm, chunk_log, _reserved, ephemeral_pub, nonce_base = _HEADER.unpack(header)
    if magic != MAGIC:
        raise SecretsCryptoError("в копии нет блока секретов Korra (неверный формат)")
    if version != VERSION or algorithm != ALG_X25519_CHACHA20 or not 12 <= chunk_log <= 26:
        raise SecretsCryptoError("блок секретов создан неизвестной версией Korra")
    try:
        shared = private.exchange(X25519PublicKey.from_public_bytes(ephemeral_pub))
    except ValueError as exc:
        raise SecretsCryptoError("блок секретов повреждён (ключ отправителя)") from exc
    aead = ChaCha20Poly1305(
        _derive_key(shared, ephemeral_pub, public_key_bytes(private.public_key()))
    )
    sealed_size = (1 << chunk_log) + _TAG
    counter = 0
    total = 0
    pending = source.read(sealed_size)
    while True:
        sealed = pending
        pending = source.read(sealed_size)
        final = not pending
        if len(sealed) < _TAG:
            raise SecretsCryptoError("блок секретов обрезан")
        try:
            plain = aead.decrypt(_chunk_nonce(nonce_base, counter), sealed, _chunk_aad(header, final))
        except InvalidTag as exc:
            raise SecretsCryptoError(
                "не удалось расшифровать секреты: ключ не подходит к этой копии или она повреждена"
            ) from exc
        total += len(plain)
        if max_bytes is not None and total > max_bytes:
            raise SecretsCryptoError("блок секретов в копии слишком велик")
        out.write(plain)
        counter += 1
        if final:
            return total
