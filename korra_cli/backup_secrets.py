"""Which files of a Korra data folder are credentials.

Used only by the copy that goes to the user's Google Drive: these files are not
put into the archive in the clear, they travel inside ``secrets.korra-enc``.
The rule is "when in doubt, encrypt": a false positive costs nothing (the file
is restored by the Korra team together with the others), a false negative leaks.

Paths are POSIX paths relative to the Korra root (``profiles/<name>/.env``,
``home/.codex/auth.json``) or ``_external/<path relative to the user's home>``.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path, PurePosixPath

# Name of the archive member that carries the encrypted secrets.
SECRETS_MEMBER = "secrets.korra-enc"

# Any path component equal to one of these makes the whole subtree secret.
_SECRET_DIR_NAMES = frozenset({
    "secrets", ".secrets",
    ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".azure", ".gcloud", ".password-store",
    ".codex", ".claude", ".qwen", ".gemini",
    "mcp-tokens", "pairing", "crm-connection", "icloud-calendar",
    "google-workspace", "google",
    "weixin", "proxy",
    "browser_auth",
})

# Two consecutive components that make a subtree secret.
_SECRET_DIR_PAIRS = frozenset({
    (".config", "gh"), (".config", "gcloud"), (".config", "gws"),
    ("whatsapp", "session"), ("signal", "data"),
})

# platforms/<name>/... keeps platform login state; these sub-folders are only
# downloaded media and may stay open.
_PLATFORM_OPEN_DIRS = frozenset({"media", "cache", "downloads"})

_SECRET_FILE_PATTERNS = (
    ".env", ".env.*", "*.env", ".envrc",
    "auth.json", "auth.json.*", "auth.lock",
    ".anthropic_oauth.json",
    "google_token.json", "google_oauth*.json", "google_client_secret*.json",
    "client_secret*.json", "token.json", "tokens.json", "credentials", "credentials.json",
    "webhook_subscriptions.json", "bws_cache*.json", "slack_tokens.json",
    ".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc", ".pgpass", ".my.cnf",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.ppk", "*.kdbx",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "authorized_keys",
    "*.session", "*.session-journal", "*.session-wal", "*.session-shm",
    "honcho.json", "plugin_toolset_keys.json",
    "gateway.json", "mcp.json", ".mcp.json",
)

_SECRET_NAME_PARTS = (
    "token", "credential", "secret", "apikey", "api_key", "api-key",
    "password", "passwd", "private_key", "private-key",
)

# Source and documentation files are never credential stores, even if the name
# says "token": they are not encrypted by the substring rule.
_PLAIN_SUFFIXES = frozenset({
    ".py", ".pyc", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".md", ".rst",
    ".html", ".css", ".sh", ".go", ".rs", ".java", ".rb", ".php", ".c", ".h", ".cpp",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp3", ".mp4", ".wav", ".ogg", ".pdf",
})

# Settings files in which an inline literal key makes the whole file secret.
_CONFIG_SUFFIXES = frozenset({".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf"})
_SNIFF_MAX_BYTES = 2 * 1024 * 1024
_SNIFF_SKIP_DIRS = frozenset({
    "skills", "sessions", "logs", "cache", "memories", "state", "output", "session-exports",
    "profile-exports", "terminal-output", "terminal-sessions", "pending_messages",
})
_INLINE_SECRET_RE = re.compile(
    r"""["']?[\w.-]*(?:api[_-]?key|token|secret|passw(?:or)?d|credential|private[_-]?key|access[_-]?key)[\w.-]*["']?"""
    r"""[ \t]*[:=][ \t]*["']?([^\s"'#,}\]]{8,})""",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer[ \t]+(?!\$)[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_TOKEN_LITERAL_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}|\d{8,10}:[A-Za-z0-9_-]{35})\b"
)
_URL_CREDENTIALS_RE = re.compile(r"://[^/\s:@]+:[^/\s@]{3,}@")
_NOT_A_SECRET_VALUE = re.compile(r"^(?:[\d._-]+|\$\{.*|\$[A-Z_]+|true|false|null|none)$", re.IGNORECASE)

_EXTERNAL_PREFIX = "_external/"


def _is_engine_config(parts: tuple[str, ...]) -> bool:
    """config.yaml of the root or of a profile: MCP/platform connections live there."""
    if len(parts) == 1:
        name = parts[0]
    elif len(parts) == 3 and parts[0] == "profiles":
        name = parts[2]
    else:
        return False
    return name == "config.yaml" or name.startswith("config.yaml.")


def _secret_by_path(rel: str) -> bool:
    path = PurePosixPath(rel.lower())
    parts = path.parts
    if not parts:
        return False
    if _is_engine_config(parts):
        return True
    name = parts[-1]
    dirs = parts[:-1]
    if any(part in _SECRET_DIR_NAMES for part in dirs):
        return True
    if any(pair in _SECRET_DIR_PAIRS for pair in zip(dirs, dirs[1:])):
        return True
    if "platforms" in dirs:
        after = dirs[dirs.index("platforms") + 1:]
        if len(after) >= 1 and not any(part in _PLATFORM_OPEN_DIRS for part in after):
            return True
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in _SECRET_FILE_PATTERNS):
        return True
    suffix = path.suffix
    if suffix not in _PLAIN_SUFFIXES and any(part in name for part in _SECRET_NAME_PARTS):
        return True
    return False


def _may_hold_inline_secret(rel: str) -> bool:
    path = PurePosixPath(rel.lower())
    if path.suffix not in _CONFIG_SUFFIXES:
        return False
    if any(part in _SNIFF_SKIP_DIRS for part in path.parts[:-1]):
        return False
    return True


def has_inline_secret(path: Path) -> bool:
    """True if a small settings file holds a credential or a connection to one."""
    try:
        if path.stat().st_size > _SNIFF_MAX_BYTES:
            return False
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if _holds_connection_credentials(text):
        return True
    return any(
        not _NOT_A_SECRET_VALUE.match(match.group(1)) for match in _INLINE_SECRET_RE.finditer(text)
    )


def _holds_connection_credentials(text: str) -> bool:
    """Bearer tokens, token-shaped literals and passwords in URLs."""
    return bool(_BEARER_RE.search(text) or _TOKEN_LITERAL_RE.search(text) or _URL_CREDENTIALS_RE.search(text))


def is_secret(rel: str, abs_path: Path | None = None) -> bool:
    """Decide whether *rel* must be encrypted. *abs_path* enables content checks."""
    external = rel.startswith(_EXTERNAL_PREFIX)
    inner = rel[len(_EXTERNAL_PREFIX):] if external else rel
    if _secret_by_path(inner):
        return True
    if external and PurePosixPath(inner.lower()).suffix in _CONFIG_SUFFIXES | {".env", ".conf"}:
        # Memory-provider state outside Korra: its settings are credential-like.
        return True
    if abs_path is not None and _may_hold_inline_secret(inner):
        return has_inline_secret(abs_path)
    return False
