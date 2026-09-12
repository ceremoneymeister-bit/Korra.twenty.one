#!/usr/bin/env python3
"""Transactional host updater; only stdlib is required on the deployment host.

The deployment directory is a root-owned control plane, separate from DATA.
The native gateway owns admission/draining. Docker owns image identity. Each
operation retains full before/after state; rollback never overwrites newer auth.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
LOCK_ROOT = Path("/run/lock")
PYTHON = "/opt/hermes/.venv/bin/python"
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
IMAGE_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
VOLATILE = {"gateway.sock", "gateway.sock.path", "gateway.pid", "cron.pid",
            "gateway.lock", "processes.json", "gateway_state.json", ".drain_request.json"}
# gateway_state.json is volatile as a whole — it names the pid/argv of a
# container that no longer exists — but container boot reads exactly these two
# fields to decide which profile gateways to bring up. They are preserved
# across a restore; nothing else from that file is.
GATEWAY_INTENT = ("desired_state", "gateway_state")

# Runs with the image's own imports and no mounted data/network. The complete
# file map supplements the native (historically MD5) provenance marker.
IMAGE_PROBE = r'''
import hashlib, json
from pathlib import Path
from tools.skills_sync import _get_bundled_dir, _discover_bundled_skills, _dir_hash
from tools.lazy_deps import install_specs, activate_durable_lazy_target
from gateway.drain_control import write_drain_request
from gateway.control_socket import query_gateway_control
base = _get_bundled_dir()
skills = {}
for name, path in _discover_bundled_skills(base):
    files = {}
    for p in sorted(path.rglob('*')):
        if p.is_symlink():
            raise RuntimeError('Bundled skill contains a symlink')
        if p.is_file():
            files[p.relative_to(path).as_posix()] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size}
    skills[name] = {'path': path.relative_to(base).as_posix(), 'native_hash': _dir_hash(path), 'files': files}
print(json.dumps({'skills': skills}))
'''

DRAIN_CODE = r'''
import inspect, json, sys
from pathlib import Path
from gateway.control_socket import query_gateway_control
from gateway.drain_control import (write_drain_request, clear_drain_request,
                                   drain_requested, read_drain_request)
from gateway.run import GatewayRunner
from gateway.platforms.api_server import APIServerAdapter
action, principal = sys.argv[1:3]
watcher = inspect.getsource(GatewayRunner._drain_control_watcher)
counter = inspect.getsource(GatewayRunner._active_api_run_count)
if '_persist_active_agents' not in watcher or 'active_agent_work_count' not in counter:
    raise RuntimeError('Gateway lacks complete external API-work drain support')
root = Path('/opt/data')
homes = [root] + sorted((root/'profiles').glob('*'))
result = []
for home in homes:
    if home.is_symlink() or not home.is_dir():
        continue
    state = query_gateway_control(home, 'status')
    if action == 'cancel':
        body = read_drain_request(home=home)
        if body is not None and body.get('principal') == principal:
            clear_drain_request(home=home)
    if not state:
        if home == root:
            raise RuntimeError('Gateway control socket unavailable')
        continue
    if action == 'drain':
        # Чей это drain — решаем по тому же признаку, по которому сам движок
        # решает, действует ли drain (gateway/drain_control.drain_requested):
        # маркер прежней инстанции контейнера или переживший свой срок
        # движок уже игнорирует, и операции он мешать не должен тоже.
        # Голая проверка существования файла запирала установку навсегда.
        body = read_drain_request(home=home) or {}
        if drain_requested(home=home) and body.get('principal') != principal:
            raise RuntimeError('Drain is owned by another operation')
        write_drain_request(home=home, principal=principal, suppress_notification=True)
    result.append({'home': str(home), 'state': state})
print(json.dumps(result))
'''



# Execute only inside the selected container. Provider values and credentials
# never leave it; a typed native missing-provider error is the sole foundation
# classification. Invalid/expired configured credentials fail closed.
CAPABILITY_CODE = r'''
import hashlib, json
from dotenv import load_dotenv
from korra_constants import get_hermes_home
load_dotenv(get_hermes_home() / '.env', override=False)
from korra_cli.auth import AuthError
from korra_cli.runtime_provider import resolve_runtime_provider
try:
    runtime = resolve_runtime_provider()
except AuthError as exc:
    if exc.code != 'no_provider_configured':
        raise RuntimeError('Configured provider cannot resolve') from None
    result = {'mode': 'foundation'}
else:
    identity = [runtime.get(key) for key in ('provider', 'base_url', 'api_mode')]
    if not isinstance(identity[0], str) or not identity[0]:
        raise RuntimeError('Malformed provider identity')
    result = {'mode': 'configured', 'provider_hash': hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()).hexdigest()}
print(json.dumps(result))
'''

FOUNDATION_SMOKE_CODE = r'''
import json, os, urllib.request
from dotenv import dotenv_values
key = os.environ.get('API_SERVER_KEY') or dotenv_values('/opt/data/.env').get('API_SERVER_KEY')
if not key:
    raise RuntimeError('API authentication unavailable')
payload = {'messages': [{'role': 'user', 'content': 'Привет!'}], 'max_tokens': 24, 'stream': True}
request = urllib.request.Request('http://127.0.0.1:' + os.environ['API_SERVER_PORT'] + '/v1/chat/completions',
    data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=90) as response:
    body = response.read(2 * 1024 * 1024 + 1)
if len(body) > 2 * 1024 * 1024:
    raise RuntimeError('Oversized API readiness response')
chunks, done = [], False
for line in body.decode().splitlines():
    if line.startswith('data: '):
        value = line[6:].strip()
        if value == '[DONE]':
            done = True
        else:
            if done:
                raise RuntimeError('Data after terminal SSE event')
            chunks.append(json.loads(value))
finals = [item for item in chunks if item.get('choices') and item['choices'][0].get('finish_reason')]
if not done or len(finals) != 1 or finals[0]['choices'][0]['finish_reason'] != 'error':
    raise RuntimeError('Expected missing-provider SSE failure')
message = str((finals[0].get('error') or {}).get('message', ''))
if 'Провайдер ответа не настроен' not in message or 'Ключи' not in message:
    raise RuntimeError('Unexpected provider failure')
print('foundation-smoke-ok')
'''

# Judges every copied database with the engine that wrote it. Read-only and
# immutable: the verdict may not add a WAL/SHM sidecar to a verified snapshot.
SQLITE_JUDGE_CODE = r'''
import json, sqlite3, sys
from pathlib import Path
relatives = json.loads(sys.argv[1])
bad = []
for relative in relatives:
    try:
        connection = sqlite3.connect((Path('/opt/data') / relative).as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            ok = connection.execute('PRAGMA quick_check').fetchall() == [('ok',)]
        finally:
            connection.close()
    except sqlite3.Error:
        ok = False
    if not ok:
        bad.append(relative)
print(json.dumps({'bad': bad, 'checked': len(relatives), 'sqlite': sqlite3.sqlite_version}))
'''


class UpdateError(RuntimeError):
    pass


def validate_capability(value):
    if value == {"mode": "foundation"}:
        return value
    if (isinstance(value, dict) and set(value) == {"mode", "provider_hash"}
            and value["mode"] == "configured"
            and isinstance(value["provider_hash"], str)
            and re.fullmatch(r"[a-f0-9]{64}", value["provider_hash"])):
        return value
    raise UpdateError("Missing or malformed baseline provider capability")


def validate_readiness_health(status):
    if not isinstance(status, dict) or status.get("overall") != "ok":
        raise UpdateError("Dashboard health is degraded or malformed")
    components = status.get("components")
    if not isinstance(components, dict) or any(
        not isinstance(components.get(name), dict) or components[name].get("status") != "ok"
        for name in ("gateway", "dashboard", "storage", "platforms")
    ):
        raise UpdateError("Mandatory readiness component is unavailable")
    current, latest = status.get("config_version"), status.get("latest_config_version")
    if type(current) is not int or type(latest) is not int or current <= 0 or current != latest:
        raise UpdateError("Configuration schema is not current")



def validate_runtime_env(value):
    if not isinstance(value, dict) or set(value) != {"ENGINE_UID", "ENGINE_GID", "AGENT_SUDO"}:
        raise UpdateError("Missing original container root/ownership contract")
    for key in ("ENGINE_UID", "ENGINE_GID"):
        if (not isinstance(value[key], str) or not value[key].isdigit()
                or not 1 <= int(value[key]) <= 65534):
            raise UpdateError("Invalid original container UID/GID")
    if value["AGENT_SUDO"] not in {"0", "1"}:
        raise UpdateError("Invalid original container admin mode")
    return dict(value)


def runtime_env_from_info(info):
    environment = dict(item.split("=", 1) for item in info["Config"].get("Env", []) if "=" in item)
    # Match the shipped stage2 shell's nonempty alias precedence and exact
    # opt-out spellings. HERMES_AGENT_SUDO is not a native stage2 setting.
    def first(*names):
        return next((environment[name] for name in names if environment.get(name)), "10000")
    sudo = "0" if environment.get("KORRA_AGENT_SUDO") in {
        "0", "false", "FALSE", "False", "no", "NO", "No", "off", "OFF", "Off",
    } else "1"
    return validate_runtime_env({
        "ENGINE_UID": first("KORRA_UID", "HERMES_UID", "PUID"),
        "ENGINE_GID": first("KORRA_GID", "HERMES_GID", "PGID"),
        "AGENT_SUDO": sudo,
    })


def utc():
    return datetime.now(timezone.utc).isoformat()


def process_epoch(pid):
    try:
        return Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, TypeError, ValueError):
        return None


def target_lock_path(data):
    identity = hashlib.sha256(str(data).encode()).hexdigest()[:24]
    return LOCK_ROOT / ("korra-update-" + identity + ".lock")


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".update-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def checked_identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise UpdateError("Invalid container/job identifier")
    return value


def canonical_data(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path or len(path.parts) < 4:
        raise UpdateError("DATA must be an absolute, non-symlink dedicated directory")
    if not path.is_dir():
        raise UpdateError("DATA does not exist")
    for parent in path.parents:
        info = parent.lstat()
        if info.st_uid != 0 or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
            raise UpdateError("DATA ancestors must prevent an agent from replacing the deployment directory")
    if not (path / "config.yaml").is_file():
        raise UpdateError("DATA is not an initialized Korra home (config.yaml missing)")
    return path


def trusted_control(path):
    """No engine-writable executable, receipt or config in the host control plane."""
    path = Path(path)
    for entry in (path, *path.parents):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise UpdateError(f"Control path must be root-owned and not group/world writable: {entry}")


def archive_image_ids(path):
    """Verified IDs for one saved image, OCI index first, classic config last.

    Docker's containerd store exposes the OCI index as .Id; the classic store
    exposes the config digest. Both must be proven by the actual archive.
    Metadata is read in-place: no archive path is extracted onto the host.
    """
    with tarfile.open(path) as archive:
        def read(name):
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise UpdateError("Unsafe image metadata path")
            entry = archive.getmember(name)
            if not entry.isfile() or entry.size > 16 * 1024 * 1024:
                raise UpdateError("Invalid/oversized image metadata")
            return archive.extractfile(entry).read()

        config_id = None
        try:
            manifest = json.loads(read("manifest.json"))
        except KeyError:
            manifest = None
        if manifest is not None:
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise UpdateError("Docker archive must contain exactly one image")
            config_id = "sha256:" + hashlib.sha256(read(manifest[0]["Config"])).hexdigest()
        try:
            index = json.loads(read("index.json"))
        except KeyError:
            if config_id:
                return [config_id]
            raise UpdateError("Archive contains no Docker/OCI image metadata")
        roots = index.get("manifests", [])
        if len(roots) != 1:
            raise UpdateError("OCI archive must contain exactly one root image")
        verified = []
        configs = []
        visited = set()

        def walk(descriptor, depth=0):
            digest = descriptor.get("digest", "")
            if not IMAGE_ID.fullmatch(digest) or depth > 8 or len(visited) > 128:
                raise UpdateError("Invalid OCI descriptor graph")
            if digest in visited:
                return
            visited.add(digest)
            payload = read("blobs/sha256/" + digest.split(":")[1])
            if ("sha256:" + hashlib.sha256(payload).hexdigest() != digest
                    or descriptor.get("size") != len(payload)):
                raise UpdateError("OCI descriptor checksum/size mismatch")
            node = json.loads(payload)
            if node.get("artifactType") or descriptor.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest":
                return
            verified.append(digest)
            if "manifests" in node:
                for child in node["manifests"]:
                    walk(child, depth + 1)
            elif "config" in node:
                config = node["config"]
                config_digest = config.get("digest", "")
                if not IMAGE_ID.fullmatch(config_digest):
                    raise UpdateError("Invalid OCI config digest")
                data = read("blobs/sha256/" + config_digest.split(":")[1])
                if "sha256:" + hashlib.sha256(data).hexdigest() != config_digest or config.get("size") != len(data):
                    raise UpdateError("OCI config checksum/size mismatch")
                configs.append(config_digest)
            else:
                raise UpdateError("OCI node is neither an image nor an index")

        walk(roots[0])
        if not configs or (config_id is not None and config_id not in configs):
            raise UpdateError("Docker config is not linked from the OCI image")
        return list(dict.fromkeys([*verified, *configs]))


def tree_manifest(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in list(dirs) + files:
            entry = parent / name
            rel = entry.relative_to(root).as_posix()
            if entry.is_symlink():
                result[rel] = {"link": os.readlink(entry)}
            elif entry.is_file():
                with entry.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                result[rel] = {"sha256": digest, "size": entry.stat().st_size}
    return result


def sqlite_files(root):
    """Relative paths of every SQLite database in a snapshot, by file header."""
    result = []
    for directory, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            target = Path(directory) / name
            if target.is_symlink() or not target.is_file():
                continue
            with target.open("rb") as stream:
                if stream.read(16) == b"SQLite format 3\x00":
                    result.append(target.relative_to(root).as_posix())
    return sorted(result)


def snapshot(source, destination):
    """Complete offline copy, with a page-level SQLite backup for every DB.

    Source MUST be stopped by the caller. Unlike the portable native archive,
    this includes browser auth, custom venvs and lazy-packages needed by rollback.
    Symlinks are copied as links and are never followed by the host updater.
    The copy stays stdlib-only on the host; the integrity verdict belongs to the
    engine that wrote the databases (:meth:`Updater.judge_sqlite`).
    """
    if destination.exists():
        raise UpdateError("Refusing to overwrite an existing snapshot")
    shutil.copytree(source, destination, symlinks=True,
                    ignore=lambda directory, names: [n for n in names
                        if n in VOLATILE or stat.S_ISSOCK((Path(directory) / n).lstat().st_mode)])
    for directory, _dirs, files in os.walk(destination, followlinks=False):
        for name in files:
            target = Path(directory) / name
            # os.walk caches the directory listing. A previous SQLite file
            # may have removed its copied WAL/SHM in this same iteration.
            if target.is_symlink() or not target.exists():
                continue
            with target.open("rb") as stream:
                is_db = stream.read(16) == b"SQLite format 3\x00"
            if not is_db:
                continue
            original = source / target.relative_to(destination)
            temp = target.with_name(target.name + ".update-sqlite")
            deadline = time.monotonic() + 60
            def progress(*_):
                if time.monotonic() > deadline:
                    raise UpdateError("SQLite snapshot timed out")
            # Even mode=ro can create WAL/SHM beside its source. Open only the
            # already copied offline DB and its copied sidecars, never DATA or
            # a verified before snapshot whose manifest must remain immutable.
            with contextlib.closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as src:
                with contextlib.closing(sqlite3.connect(temp)) as dst:
                    src.backup(dst, pages=256, progress=progress)
            shutil.copystat(target, temp)
            owner = original.stat()
            os.chown(temp, owner.st_uid, owner.st_gid)
            os.replace(temp, target)
            for suffix in ("-wal", "-shm", "-journal"):
                target.with_name(target.name + suffix).unlink(missing_ok=True)
    # copytree preserves modes, not owners. Restore every lstat owner without
    # following symlinks (including the root).
    for directory, dirs, files in os.walk(destination, followlinks=False):
        for target in [Path(directory), *(Path(directory) / n for n in dirs + files)]:
            original = source / target.relative_to(destination)
            owner = original.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    manifest = tree_manifest(destination)
    if not manifest:
        raise UpdateError("Empty snapshot")
    return manifest


def gateway_intent(root):
    """Durable start/stop intent of the root and per-profile gateways.

    Source MUST be stopped by the caller, so these files hold the last state the
    engine persisted. Read only the intent fields: the runtime identity beside
    them belongs to a container that is already gone.
    """
    homes = [root]
    profiles = root / "profiles"
    if profiles.resolve() == profiles and profiles.is_dir():
        homes.extend(sorted(p for p in profiles.glob("*") if p.is_dir() and not p.is_symlink()))
    intent = {}
    for home in homes:
        path = home / "gateway_state.json"
        if home.resolve() != home or path.is_symlink() or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        recorded = {key: value[key] for key in GATEWAY_INTENT if isinstance(value.get(key), str)}
        if recorded:
            intent[path.relative_to(root).as_posix()] = recorded
    return intent


def credential_path(relative):
    p = Path(relative)
    low = p.name.lower()
    parts = tuple(part.lower() for part in p.parts)
    home_relative = parts[2:] if len(parts) > 2 and parts[0] == "profiles" else parts
    # The native Bitwarden credential cache is an explicit exception to the
    # regeneratable-cache exclusion; library/package caches are not auth.
    if home_relative in {("cache", "bws_cache.json"), ("cache", "bws_cache.enc.json")}:
        return True
    if any(part in {"lazy-packages", ".venv", "venv", "site-packages", "dist-packages",
                    "node_modules", ".cache", "cache", "__pycache__", ".npm", ".yarn", ".bun"}
           for part in parts):
        return False
    if p.suffix.lower() in {".py", ".pyc", ".pyo", ".js", ".mjs", ".cjs", ".ts",
                            ".tsx", ".jsx", ".sh", ".so", ".dll", ".dylib", ".exe"}:
        return False
    return (low in {".env", "auth.json", "storage_state.json", "session.json", "login data", "web data",
                    "webhook_subscriptions.json", "bws_cache.json", "bws_cache.enc.json", "feishu_comment_pairing.json"}
            or low.endswith(".session")
            or any(term in low for term in ("credential", "token", "cookie", "oauth"))
            or any(part.lower() in {"auth", "secrets", ".secrets", ".ssh", "browser-profile",
                                   "browser-profiles", "sessions-auth", "mcp-tokens", "pairing"} for part in p.parts))


def merge_config_credentials(before, now):
    """Overlay credential leaves; a removed parent/provider revokes its secrets."""
    import re
    secret = re.compile(r"(?:^|_)(?:api_key|key|token|secret|password|credentials?|access_key|private_key|refresh_token|authorization|auth|headers|client_id)(?:$|_)", re.I)
    if isinstance(before, dict):
        now = now if isinstance(now, dict) else {}
        for key in set(before) | set(now):
            if secret.search(str(key)):
                if key in now:
                    before[key] = now[key]
                else:
                    before.pop(key, None)
            elif key in before:
                merge_config_credentials(before[key], now.get(key))
    elif isinstance(before, list):
        now = now if isinstance(now, list) else []
        for entry in before:
            if isinstance(entry, dict):
                identity = next((k for k in ("name", "id", "provider") if k in entry), None)
                other = next((x for x in now if isinstance(x, dict) and identity and x.get(identity) == entry[identity]), None)
                merge_config_credentials(entry, other)


def overlay_credentials(latest, restored):
    """Keep latest credential stores, including deliberate deletions/revocations."""
    paths = set(tree_manifest(latest)) | set(tree_manifest(restored))
    changed = []
    for rel in sorted(paths):
        if not credential_path(rel):
            continue
        old, new = latest / rel, restored / rel
        # Ancestor symlinks must never become a host write outside the stage.
        for ancestor in new.parents:
            if ancestor == restored:
                break
            if ancestor.is_symlink():
                raise UpdateError("Credential destination has a symlink ancestor")
        if new.exists() or new.is_symlink():
            if new.is_dir() and not new.is_symlink():
                continue
            new.unlink()
        if old.exists() or old.is_symlink():
            missing = []
            parent = new.parent
            while parent != restored and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                source_parent = latest / parent.relative_to(restored)
                if source_parent.is_symlink() or not source_parent.is_dir():
                    raise UpdateError("Credential source parent is not a real directory")
                owner = source_parent.stat()
                parent.mkdir(mode=stat.S_IMODE(owner.st_mode))
                os.chmod(parent, stat.S_IMODE(owner.st_mode))
                os.chown(parent, owner.st_uid, owner.st_gid, follow_symlinks=False)
            if old.is_symlink():
                new.symlink_to(os.readlink(old))
            else:
                shutil.copy2(old, new)
            info = old.lstat()
            os.chown(new, info.st_uid, info.st_gid, follow_symlinks=False)
        changed.append(rel)
    return changed


class Updater:
    def __init__(self, home=HERE):
        self.home = Path(home)
        self.name = checked_identifier(os.environ.get("NAME", "korra"))
        self.data = canonical_data(os.environ.get("DATA", "/opt/korra/data"))
        self.panel = int(os.environ.get("PANEL_PORT", "9119"))
        self.api = int(os.environ.get("API_PORT", "8650"))
        if not all(1024 <= p <= 65535 for p in (self.panel, self.api)) or self.panel == self.api:
            raise UpdateError("Invalid panel/API ports")
        self.jobs = self.home / "updates"
        self.job = None
        self.receipt = {}

    def log(self, message):
        line = f"{utc()} {self.receipt.get('job_id', '-')} {message}\n"
        sys.stderr.write(line)
        with (self.home / "updates.log").open("a") as stream:
            stream.write(line)

    def phase(self, phase, **fields):
        self.receipt.update(phase=phase, updated_at=utc(), **fields)
        atomic_json(self.job / "status.json", self.receipt)
        self.log(f"phase={phase} status={self.receipt['status']}")

    def command(self, args, *, timeout=120, input=None):
        result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            # docker may print credentials/config in an error; retain them in
            # root-only operation.log, never in the public JSON receipt.
            if self.job:
                with (self.job / "operation.log").open("a") as stream:
                    stream.write(result.stdout + result.stderr)
            raise UpdateError(f"{Path(args[0]).name} {args[1]} failed (exit {result.returncode})")
        return result.stdout.strip()

    def docker(self, *args, **kwargs):
        return self.command(["docker", *map(str, args)], **kwargs)

    def runtime_user(self):
        value = validate_runtime_env(self.receipt.get("old_runtime"))
        return value["ENGINE_UID"] + ":" + value["ENGINE_GID"]

    def execute(self, code, *args, timeout=120):
        return self.docker("exec", "-u", self.runtime_user(), "-w", "/opt/hermes", self.name,
                           PYTHON, "-c", code, *args, timeout=timeout)

    def google_oauth_source(self):
        return self.home / "google" / "oauth_client.json"

    def validate_google_oauth_source(self, runtime_gid):
        path = self.google_oauth_source()
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise UpdateError("Google OAuth host credential is missing") from None
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_gid != int(runtime_gid) or stat.S_IMODE(info.st_mode) != 0o640):
            raise UpdateError("Google OAuth host credential must be regular root:runtime-gid mode 0640")

    def google_oauth_mount_contract(self, info):
        destination = "/run/korra-secrets/google-oauth-client.json"
        mount = next(
            (item for item in info.get("Mounts", []) if item.get("Destination") == destination),
            None,
        )
        return {
            "present": mount is not None,
            "source": mount.get("Source") if mount is not None else None,
        }

    def inspect_target(self, expected=None, running=True, timeout=120):
        info = json.loads(self.docker("inspect", self.name, timeout=timeout))[0]
        mounts = info.get("Mounts", [])
        expected_mounts = {
            "/opt/data": (str(self.data), True),
            "/run/korra-secrets/google-oauth-client.json": (
                str(self.home / "google" / "oauth_client.json"),
                False,
            ),
        }
        seen_mounts = set()
        mounts_ok = len(mounts) in (1, 2)
        for mount in mounts:
            destination = mount.get("Destination")
            expected_mount = expected_mounts.get(destination)
            if (
                expected_mount is None
                or destination in seen_mounts
                or mount.get("Type") != "bind"
                or mount.get("Source") != expected_mount[0]
                or mount.get("RW") is not expected_mount[1]
            ):
                mounts_ok = False
                break
            seen_mounts.add(destination)
        mounts_ok = mounts_ok and "/opt/data" in seen_mounts
        google_destination = "/run/korra-secrets/google-oauth-client.json"
        google_present = google_destination in seen_mounts
        recorded_google = self.receipt.get("google_oauth_mount")
        if recorded_google is not None:
            expected_contract = {
                "present": google_present,
                "source": str(self.google_oauth_source()) if google_present else None,
            }
            mounts_ok = (
                mounts_ok
                and isinstance(recorded_google, dict)
                and set(recorded_google) == {"present", "source"}
                and recorded_google == expected_contract
            )
        if google_present:
            try:
                runtime_gid = runtime_env_from_info(info)["ENGINE_GID"]
                self.validate_google_oauth_source(runtime_gid)
            except UpdateError:
                mounts_ok = False
        if (info.get("Name") != "/" + self.name or not mounts_ok
                or info.get("Config", {}).get("Cmd") != ["gateway", "run"]
                or info.get("HostConfig", {}).get("NetworkMode") != "host"
                or info.get("HostConfig", {}).get("Privileged")):
            raise UpdateError("Container identity/mount/command differs from this deployment")
        env = dict(item.split("=", 1) for item in info["Config"].get("Env", []) if "=" in item)
        if env.get("KORRA_DASHBOARD_PORT") != str(self.panel) or env.get("API_SERVER_PORT") != str(self.api):
            raise UpdateError("Container panel/API ports differ from this deployment")
        if expected and info["Image"] != expected:
            raise UpdateError("Container image changed since operation began")
        if running and not info.get("State", {}).get("Running"):
            raise UpdateError("Target is not running")
        return info

    def initialize(self, job_id, reference, dry_run=False, expected_current=None,
                   expected_target=None, expected_archive_sha256=None):
        self.jobs.mkdir(mode=0o700, exist_ok=True)
        self.job = self.jobs / checked_identifier(job_id)
        self.job.mkdir(mode=0o700)
        self.receipt = dict(job_id=job_id, action="update", phase="pending", status="pending", error=None,
                            old_image_id=None, target_image_id=None, backup_path=None,
                            started_at=utc(), updated_at=utc(), name=self.name,
                            data=str(self.data), reference=reference, dry_run=bool(dry_run),
                            expected_current=expected_current, expected_target=expected_target,
                            expected_archive_sha256=expected_archive_sha256)
        self.phase("pending")

    def free_space(self, image_bytes=0):
        size = sum(p.lstat().st_size for root, _dirs, files in os.walk(self.data)
                   for p in (Path(root) / n for n in files) if not p.is_symlink())
        # Before + after + restoration staging, plus dependency/image headroom.
        required = 3 * size + image_bytes + 2 * 1024**3
        for location in {self.home, self.data.parent, Path("/var/lib/docker")}:
            if location.exists() and shutil.disk_usage(location).free < required:
                raise UpdateError(f"Insufficient space at {location}; need {required} bytes")

    def resolve_image(self, reference):
        path = Path(reference)
        permitted_ids = None
        if self.receipt.get("expected_archive_sha256") and not path.is_file():
            self.receipt["error_code"] = "archive_required"
            raise UpdateError("Expected archive checksum requires an existing tar file")
        if path.is_file():
            with path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if self.receipt.get("expected_archive_sha256") not in (None, checksum):
                self.receipt["error_code"] = "archive_checksum_mismatch"
                raise UpdateError("Archive checksum differs from the approved artifact")
            permitted_ids = archive_image_ids(path)
            self.docker("load", "--input", str(path.resolve()), timeout=1800)
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                    self.receipt["error_code"] = "archive_changed"
                    raise UpdateError("Docker archive changed during load")
            self.receipt["archive_sha256"] = checksum
            self.receipt["archive_image_ids"] = permitted_ids
            # Prefer OCI identity on Docker's containerd store, with a verified
            # classic config fallback for older Docker image stores.
            info = None
            for image in permitted_ids:
                try:
                    info = json.loads(self.docker("image", "inspect", image))[0]
                    break
                except UpdateError:
                    continue
            if info is None:
                raise UpdateError("Loaded image has no identity matching its archive")
        elif IMAGE_ID.fullmatch(reference):
            image = reference
        else:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/@:-]{0,350}", reference):
                raise UpdateError("Invalid image reference")
            self.docker("pull", reference, timeout=1800)
            image = reference
        if permitted_ids is None:
            info = json.loads(self.docker("image", "inspect", image))[0]
        if not IMAGE_ID.fullmatch(info.get("Id", "")):
            raise UpdateError("Docker did not resolve an immutable image ID")
        if permitted_ids is not None and info["Id"] not in permitted_ids:
            raise UpdateError("Docker resolved an image outside the verified archive graph")
        if self.receipt.get("expected_target") not in (None, info["Id"]):
            self.receipt["error_code"] = "expected_target_mismatch"
            self.receipt["target_image_id"] = info["Id"]
            raise UpdateError("Resolved Docker image differs from the approved target image")
        self.free_space(int(info.get("Size", 0)))
        return info["Id"]

    def protect_image(self, image, purpose):
        identity = hashlib.sha256(f"{self.name}:{self.data}:{self.receipt['job_id']}".encode()).hexdigest()[:24]
        reference = f"korra-local-{purpose}:{identity}"
        self.docker("image", "tag", image, reference)
        actual = json.loads(self.docker("image", "inspect", reference))[0]["Id"]
        if actual != image:
            raise UpdateError("Protected image reference differs from the pinned image")
        self.receipt[purpose + "_image_ref"] = reference
        return reference

    def own_image_refs(self):
        """Protective tags this deployment created, from its own receipts."""
        refs = set()
        for path in sorted(self.jobs.glob("*/status.json")):
            try:
                receipt = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if (not isinstance(receipt, dict) or receipt.get("name") != self.name
                    or receipt.get("data") != str(self.data)):
                continue
            for key in ("candidate_image_ref", "rollback_image_ref"):
                value = receipt.get(key)
                if isinstance(value, str) and value.startswith("korra-local-"):
                    refs.add(value)
        return refs

    def gc(self):
        """Drop the protective tags that no longer pin this deployment's images.

        Every operation tags its candidate and its predecessor so Docker cannot
        drop an image out from under a running job, and nothing ever removed
        those tags again: one 59 GB client host carried 18.9 GB of dead images
        with 16 GB free. Exactly two images stay — the running one (IMAGE) and
        the one `--rollback` needs (IMAGE.prev). Tags of another installation on
        the same host are reported, never removed: it has its own IMAGE pair.
        """
        keep = set()
        for name in ("IMAGE", "IMAGE.prev"):
            path = self.home / name
            if not path.is_file():
                continue
            try:
                keep.add(json.loads(self.docker("image", "inspect", path.read_text().strip()))[0]["Id"])
            except (UpdateError, ValueError, KeyError, IndexError):
                self.log(f"gc: {name} does not resolve to a local image")
        own = self.own_image_refs()
        result = {"action": "gc", "kept": [], "removed": [], "skipped": [], "failed": []}
        listed = self.docker("image", "ls", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}}|{{.ID}}")
        for line in sorted(listed.splitlines()):
            reference, _, image_id = line.strip().partition("|")
            if not reference.startswith("korra-local-"):
                continue
            if image_id in keep:
                result["kept"].append(reference)
            elif reference not in own:
                result["skipped"].append(reference)
            else:
                try:
                    self.docker("image", "rm", reference)
                    result["removed"].append(reference)
                except UpdateError as exc:
                    # A stopped neighbour's container may still reference the
                    # image; housekeeping does not stop at the first refusal.
                    result["failed"].append(reference)
                    self.log(f"gc: {reference} not removed: {exc}")
        self.log("gc: kept {}, removed {}, skipped {}, failed {}".format(
            *(len(result[key]) for key in ("kept", "removed", "skipped", "failed"))))
        return result

    def probe_image(self, image):
        output = self.docker("run", "--rm", "--network", "none", "--cpus", "1",
                             "--memory", "768m", "--entrypoint", PYTHON,
                             image, "-c", IMAGE_PROBE, timeout=180)
        return json.loads(output.splitlines()[-1])

    def native_states(self, action="status", timeout=120):
        return json.loads(self.execute(DRAIN_CODE, action, "host-updater:" + self.receipt["job_id"], timeout=timeout).splitlines()[-1])

    def drain(self):
        self.native_states("drain")
        deadline = time.monotonic() + int(os.environ.get("DRAIN_TIMEOUT", "300"))
        while time.monotonic() < deadline:
            states = self.native_states()
            if states and all(x["state"].get("gateway_state") == "draining"
                              and type(x["state"].get("active_agents")) is int
                              and x["state"]["active_agents"] == 0 for x in states):
                return states
            time.sleep(1)
        self.native_states("cancel")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if all(x["state"].get("gateway_state") == "running" for x in self.native_states()):
                raise UpdateError("Drain timed out; normal admission restored; no tasks killed")
            time.sleep(1)
        raise UpdateError("Drain timed out; cancellation sent but admission restoration unconfirmed")

    def release_drain(self):
        """Убрать собственный маркер drain после удавшегося переключения.

        Уборка, а не часть транзакции: обновление к этому моменту уже прошло
        приёмку, и отменять его из-за неубранного файла нельзя. Оставшийся
        маркер безвреден сам по себе — он от прежней инстанции контейнера, и
        движок его игнорирует, — но в DATA клиента ему делать нечего.
        """
        try:
            self.native_states("cancel")
        except Exception as exc:  # noqa: BLE001 — уборка не отменяет результат
            self.log(f"Drain marker cleanup skipped: {exc}")

    def prune_skills(self, old, target):
        removed = []
        homes = [self.data]
        profiles = self.data / "profiles"
        if profiles.resolve() == profiles and profiles.is_dir():
            homes.extend(sorted(profiles.glob("*")))
        for home in homes:
            if (home.resolve() != home or not home.is_relative_to(self.data)
                    or not home.is_dir() or (home / ".no-bundled-skills").exists()):
                continue
            base = home / "skills"
            marker = base / ".bundled_manifest"
            if (base.resolve() != base or marker.resolve() != marker
                    or not marker.is_relative_to(self.data) or not marker.is_file()):
                continue
            home_removed = False
            lines = marker.read_text().splitlines()
            entries = dict(line.partition(":")[::2] for line in lines if ":" in line)
            for name, skill in old["skills"].items():
                if name in target["skills"] or entries.get(name) != skill["native_hash"]:
                    continue
                relative = Path(skill["path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise UpdateError("Unsafe bundled skill path")
                path = base / relative
                if not path.is_dir() or path.resolve() != path:
                    continue
                if any((path / pin).exists() for pin in (".pin", ".pinned")):
                    continue
                if tree_manifest(path) != skill["files"]:
                    continue
                shutil.rmtree(path)
                entries.pop(name)
                removed.append(path.relative_to(self.data).as_posix())
                home_removed = True
            if home_removed:
                # Preserve marker ownership/mode, unlike replacing it as root.
                marker.write_text("\n".join(f"{k}:{v}" for k, v in sorted(entries.items())) + "\n")
        atomic_json(self.job / "pruned-skills.json", removed)
        return removed

    def start_image(self, image):
        rollback = self.receipt["phase"] == "rollback_recreate"
        resources = self.launch_resource_env(rollback=rollback)
        runtime = self.launch_runtime_env(rollback=rollback)
        google_contract = self.receipt.get("google_oauth_mount")
        if isinstance(google_contract, dict) and google_contract.get("present") is True:
            self.validate_google_oauth_source(runtime["ENGINE_GID"])
        (self.home / "IMAGE").write_text(image + "\n")
        env = {**os.environ, "NAME": self.name, "DATA": str(self.data),
               "PANEL_PORT": str(self.panel), "API_PORT": str(self.api),
               "KORRA_UPDATER_JOB": self.receipt["job_id"],
               "KORRA_UPDATER_ROLLBACK": "1" if rollback else "0", **resources, **runtime}
        result = subprocess.run(["bash", str(self.home / "up.sh")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=1500)
        with (self.job / "operation.log").open("a") as stream:
            stream.write(result.stdout)
        if result.returncode:
            raise UpdateError("up.sh failed; see private operation.log")

    def launch_resource_env(self, rollback=False):
        baseline = self.receipt.get("old_resources")
        if not isinstance(baseline, dict):
            raise UpdateError("Operation lacks original resource limits; retain its original host launcher for recovery")
        nanos = int(baseline["nano_cpus"])
        whole, fraction = divmod(nanos, 1_000_000_000)
        cpus = f"{whole}.{fraction:09d}".rstrip("0").rstrip(".") if nanos else ""
        memory = int(baseline["memory_bytes"])
        env = {"CONTAINER_CPUS": cpus, "CONTAINER_MEMORY": str(memory) if memory else ""}
        if not rollback:
            for key in env:
                if os.environ.get(key):
                    env[key] = os.environ[key]
        return env

    def launch_runtime_env(self, rollback=False):
        value = validate_runtime_env(self.receipt.get("old_runtime"))
        if not rollback:
            for key in ("ENGINE_UID", "ENGINE_GID"):
                if os.environ.get(key) and os.environ[key] != value[key]:
                    raise UpdateError("Changing DATA UID/GID requires separate operator preparation")
            if "AGENT_SUDO" in os.environ:
                value["AGENT_SUDO"] = os.environ["AGENT_SUDO"]
        return validate_runtime_env(value)

    def capability(self, timeout=30):
        try:
            value = json.loads(self.execute(CAPABILITY_CODE, timeout=timeout))
            return validate_capability(value)
        except (UpdateError, OSError, ValueError, subprocess.SubprocessError):
            raise UpdateError("Provider capability cannot be verified") from None

    def smoke(self, expected):
        wait_seconds = max(1, int(os.environ.get("WAIT_SECONDS", "120")))
        deadline = time.monotonic() + wait_seconds
        baseline = self.receipt.get("old_resources", {})
        rollback = self.receipt["phase"] == "rollback_recreate"
        expected_profiles = set(self.receipt.get("served_profiles", ["default"]))
        base = f"http://127.0.0.1:{self.panel}"
        while time.monotonic() < deadline:
            # Target/resource changes and a stopped container are never treated
            # as a transient startup delay.
            info = self.inspect_target(expected, timeout=min(10, max(0.1, deadline - time.monotonic())))
            actual = {"nano_cpus": int(info["HostConfig"].get("NanoCpus", 0)),
                      "memory_bytes": int(info["HostConfig"].get("Memory", 0))}
            for key, environment in (("nano_cpus", "CONTAINER_CPUS"), ("memory_bytes", "CONTAINER_MEMORY")):
                if key in baseline and (rollback or not os.environ.get(environment)) and actual[key] != baseline[key]:
                    raise UpdateError("Container resource limits differ from the preserved baseline")
            self.receipt["active_resources"] = actual
            if runtime_env_from_info(info) != self.launch_runtime_env(rollback=rollback):
                raise UpdateError("Container root/ownership differs from the preserved contract")
            try:
                def budget():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Readiness deadline")
                    return min(10, remaining)
                with urllib.request.urlopen(base + "/", timeout=budget()) as response:
                    html = response.read(2 * 1024 * 1024).decode()
                match = re.search(r'window\.__(?:HERMES|KORRA)_SESSION_TOKEN__\s*=\s*"([^"\r\n]+)"', html)
                if not match:
                    raise UpdateError("Dashboard session token is not ready")
                values = []
                for route in ("/api/status", "/api/profiles"):
                    request = urllib.request.Request(base + route, headers={"X-Hermes-Session-Token": match[1], "X-Korra-Session-Token": match[1]})
                    with urllib.request.urlopen(request, timeout=budget()) as response:
                        values.append(json.load(response))
                status, profiles = values
                if not isinstance(status, dict) or not isinstance(profiles, dict):
                    raise UpdateError("Dashboard JSON is not ready")
                validate_readiness_health(status)
                present = {p.get("name") for p in profiles.get("profiles", []) if isinstance(p, dict)}
                states = self.native_states(timeout=budget())
                served = set()
                for item in states:
                    state = item["state"]
                    if state.get("gateway_state") != "running":
                        raise UpdateError("Native gateway is still starting")
                    served.update(state.get("served_profiles") or ["default" if item["home"] == "/opt/data" else Path(item["home"]).name])
                if (states and status.get("gateway_running") is True and status.get("gateway_state") == "running"
                        and expected_profiles.issubset(present) and expected_profiles.issubset(served)):
                    break
            except (UpdateError, OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired):
                pass
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        else:
            self.receipt["error_code"] = "native_readiness_timeout"
            raise UpdateError(f"Native gateway readiness timed out after {wait_seconds}s; see private operation.log")
        expected_capability = validate_capability(self.receipt.get("baseline_capability"))
        actual_capability = self.capability()
        if actual_capability != expected_capability:
            self.receipt["error_code"] = "provider_capability_mismatch"
            raise UpdateError("Provider capability differs from the preserved baseline")
        self.receipt["active_capability"] = actual_capability
        if actual_capability["mode"] == "foundation":
            try:
                self.execute(FOUNDATION_SMOKE_CODE)
            except (UpdateError, OSError, subprocess.SubprocessError):
                self.receipt["error_code"] = "foundation_smoke_failed"
                raise UpdateError("Foundation API readiness failed; see private operation.log") from None
            return
        # A stateless API turn exercises model resolution and generation without
        # sending to a person/channel. Test fixtures provide a local fake model.
        code = r'''
import json, os, urllib.request
from dotenv import dotenv_values
key = os.environ.get('API_SERVER_KEY') or dotenv_values('/opt/data/.env').get('API_SERVER_KEY')
if not key:
    raise RuntimeError('API_SERVER_KEY missing; model smoke unavailable')
request = urllib.request.Request('http://127.0.0.1:' + os.environ['API_SERVER_PORT'] + '/v1/chat/completions', data=json.dumps({'messages': [{'role': 'user', 'content': 'Reply with exactly KORRA_UPDATE_OK. Do not use tools.'}], 'max_tokens': 24, 'stream': False}).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=90) as response:
    value = json.load(response)
content = value['choices'][0]['message']['content']
if 'KORRA_UPDATE_OK' not in str(content):
    raise RuntimeError('Model smoke response missing acknowledgement')
print('model-smoke-ok')
'''
        try:
            self.execute(code)
        except (UpdateError, OSError, subprocess.SubprocessError) as exc:
            self.receipt["error_code"] = "model_smoke_failed"
            raise UpdateError("Model smoke failed; see private operation.log") from exc

    def judge_image(self):
        """The image that wrote this DATA: this operation's protected old image."""
        image = self.receipt.get("rollback_image_ref") or self.receipt.get("old_image_id")
        if not image:
            raise UpdateError("No previous image is available to judge SQLite integrity")
        return image

    def judge_sqlite(self, image, root):
        """PRAGMA quick_check for every database of a snapshot, run by its writer.

        The host carries SQLite 3.45 and the engine 3.53, and they disagree about
        a Cyrillic trigram FTS5 index on a byte-identical file: the image answers
        ok, the host malformed. A host verdict therefore blocked two healthy
        installations in the 0.21.4 release. Judge and writer must be the same
        version, so the verdict moves into the old image — the same disposable
        container trick as the schema rehearsal. The snapshot is mounted
        read-only: the judge cannot alter what it verifies.
        """
        relatives = sqlite_files(root)
        if not relatives:
            return {"bad": [], "checked": 0}
        try:
            output = self.docker("run", "--rm", "--network", "none", "--cpus", "1", "--memory", "2g",
                                 "--user", self.runtime_user(), "--entrypoint", PYTHON,
                                 "-v", str(root) + ":/opt/data:ro", image,
                                 "-c", SQLITE_JUDGE_CODE, json.dumps(relatives), timeout=600)
        except (UpdateError, OSError, subprocess.SubprocessError) as exc:
            raise UpdateError(f"SQLite integrity cannot be judged by the engine that wrote it: {exc}") from exc
        try:
            result = json.loads(output.splitlines()[-1])
            bad = result["bad"]
        except (ValueError, KeyError, IndexError) as exc:
            raise UpdateError("Malformed SQLite integrity verdict") from exc
        if not isinstance(bad, list) or not set(bad) <= set(relatives):
            raise UpdateError("Malformed SQLite integrity verdict")
        self.log(f"sqlite judge: {len(relatives)} databases, {len(bad)} failed, "
                 f"engine sqlite {result.get('sqlite')}")
        if bad:
            raise UpdateError("SQLite integrity check failed: " + ", ".join(sorted(bad)[:5]))
        return result

    def verified_snapshot(self, name):
        destination = self.job / name
        manifest = snapshot(self.data, destination)
        self.judge_sqlite(self.judge_image(), destination)
        atomic_json(self.job / (name + ".manifest.json"), manifest)
        if tree_manifest(destination) != manifest:
            raise UpdateError("Snapshot verification mismatch")
        return destination

    def latest_snapshot(self):
        """Resume with a verified export only while stopped DATA is identical.

        If the container restarted/wrote since the last attempt, retain the old
        export and create another one. No snapshot is overwritten by a retry.
        """
        source_manifest = {k: v for k, v in tree_manifest(self.data).items()
                           if Path(k).name not in VOLATILE}
        candidates = [self.job / "after", *sorted(p for p in self.job.glob("after-retry-*") if p.is_dir())]
        for path in reversed(candidates):
            if not path.exists():
                continue
            if path.is_symlink():
                raise UpdateError("Export snapshot is a symlink")
            recorded = json.loads((self.job / (path.name + ".manifest.json")).read_text())
            if tree_manifest(path) != recorded:
                raise UpdateError("Export snapshot checksum mismatch")
            source_file = self.job / (path.name + ".source.json")
            if source_file.exists() and json.loads(source_file.read_text()) == source_manifest:
                return path
        name = "after" if not (self.job / "after").exists() else "after-retry-" + uuid.uuid4().hex[:8]
        path = self.verified_snapshot(name)
        # A SQLite reader may clean up idle sidecars; fingerprint only after
        # snapshot connections have closed, matching what a retry will see.
        source_manifest = {k: v for k, v in tree_manifest(self.data).items()
                           if Path(k).name not in VOLATILE}
        atomic_json(self.job / (name + ".source.json"), source_manifest)
        return path

    def verify_backup(self):
        backup = self.job / "before"
        if backup.is_symlink() or str(backup) != self.receipt.get("backup_path"):
            raise UpdateError("Backup path is not the operation's own snapshot")
        manifest = json.loads((self.job / "before.manifest.json").read_text())
        if tree_manifest(backup) != manifest:
            raise UpdateError("Backup checksum mismatch; refusing restore")
        return backup, manifest

    def rehearse_schema(self, image):
        """Exercise native SessionDB migrations on disposable DB-only copies."""
        backup, manifest = self.verify_backup()
        stage = self.job / "schema-rehearsal"
        stage.mkdir(mode=0o750)
        owner = self.data.stat()
        os.chown(stage, owner.st_uid, owner.st_gid)
        paths = [backup / "state.db"]
        profiles = backup / "profiles"
        if profiles.resolve() == profiles and profiles.is_dir():
            paths.extend(sorted(profiles.glob("*/state.db")))
        copied = []
        for source in paths:
            if not source.exists():
                continue
            if source.resolve() != source or not source.is_file():
                raise UpdateError("Schema rehearsal requires regular in-home state.db files")
            relative = source.relative_to(backup)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            for parent in [target.parent, *target.parent.parents]:
                if parent == stage.parent:
                    break
                os.chmod(parent, 0o750)
                os.chown(parent, owner.st_uid, owner.st_gid)
            shutil.copy2(source, target)
            os.chown(target, owner.st_uid, owner.st_gid)
            copied.append(relative.as_posix())
        if copied:
            code = r'''
import json, sqlite3, sys
from pathlib import Path
from korra_state import SessionDB, SCHEMA_VERSION
from korra_state_schema import schema_read_probe_statements
results = []
for relative in json.loads(sys.argv[1]):
    path = Path('/opt/data') / relative
    with sqlite3.connect(path) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
        row = conn.execute('SELECT version FROM schema_version LIMIT 1').fetchone() if exists else None
        before = row[0] if row else None
        if before is not None and before > SCHEMA_VERSION:
            raise RuntimeError('Candidate is older than native schema: ' + relative)
    db = SessionDB(db_path=path)
    db.close()
    with sqlite3.connect(path) as conn:
        for query in schema_read_probe_statements():
            conn.execute(query)
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('Migrated SQLite failed integrity: ' + relative)
        version = conn.execute('SELECT version FROM schema_version LIMIT 1').fetchone()[0]
    results.append({'path': relative, 'schema_before': before, 'schema_after': version, 'integrity': 'ok'})
print(json.dumps({'scope': 'native_session_db', 'candidate_schema': SCHEMA_VERSION, 'databases': results}))
'''
            output = self.docker("run", "--rm", "--network", "none", "--cpus", "1", "--memory", "2g",
                                 "--user", str(owner.st_uid), "--entrypoint", PYTHON,
                                 "-v", str(stage) + ":/opt/data", image, "-c", code, json.dumps(copied), timeout=240)
            result = json.loads(output.splitlines()[-1])
        else:
            result = {"scope": "native_session_db", "databases": [], "note": "No existing native state.db"}
        if tree_manifest(backup) != manifest:
            raise UpdateError("Schema rehearsal altered the verified backup")
        atomic_json(self.job / "schema-rehearsal.json", result)
        self.receipt["schema_rehearsal"] = result
        return result

    def preserve_config_credentials(self, latest, stage):
        # Parse YAML with the old image's native library. Only credential
        # leaves move forward: schema/model/behavior settings stay from before.
        code = inspect.getsource(merge_config_credentials) + r'''
import json, re
from pathlib import Path
import yaml
changed = []
root = Path('/opt/data')
for path in [root / 'config.yaml', *sorted((root/'profiles').glob('*/config.yaml'))]:
    current = Path('/latest') / path.relative_to(root)
    if not path.is_file() or path.resolve() != path or current.is_symlink():
        continue
    before = yaml.safe_load(path.read_text()) or {}
    now = (yaml.safe_load(current.read_text()) or {}) if current.is_file() else {}
    original = json.dumps(before, sort_keys=True)
    merge_config_credentials(before, now)
    if json.dumps(before, sort_keys=True) != original:
        path.write_text(yaml.safe_dump(before, allow_unicode=True, sort_keys=False))
        changed.append(path.relative_to(root).as_posix())
print(json.dumps(changed))
'''
        output = self.docker("run", "--rm", "--network", "none", "--cpus", "1", "--memory", "768m",
                             "--user", self.runtime_user(), "--entrypoint", PYTHON,
                             "-v", str(stage) + ":/opt/data", "-v", str(latest) + ":/latest:ro",
                             self.receipt["old_image_id"], "-c", code)
        return json.loads(output.splitlines()[-1])

    def apply_gateway_intent(self, stage):
        """Return the recorded gateway intent to a restored tree.

        Without this file a named profile is registered down on container boot
        and its gateway never comes back: after a rollback four of five profiles
        stayed down for a designer whose Telegram bots live in them. A profile
        the owner had deliberately stopped keeps its own recorded intent, so an
        automatic rollback does not start anything nobody asked for.
        """
        intent = self.receipt.get("gateway_intent")
        if not isinstance(intent, dict):
            return []
        restored = []
        for relative, value in sorted(intent.items()):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or path.name != "gateway_state.json":
                raise UpdateError("Unsafe gateway intent path")
            recorded = {key: value[key] for key in GATEWAY_INTENT
                        if isinstance(value, dict) and isinstance(value.get(key), str)}
            if not recorded:
                continue
            destination = stage / path
            for ancestor in destination.parents:
                if ancestor == stage:
                    break
                if ancestor.is_symlink():
                    raise UpdateError("Gateway intent destination has a symlink ancestor")
            if not destination.parent.is_dir():
                continue
            atomic_json(destination, recorded)
            owner = destination.parent.stat()
            os.chown(destination, owner.st_uid, owner.st_gid)
            restored.append(relative)
        return restored

    def restore(self):
        backup, manifest = self.verify_backup()
        latest = self.latest_snapshot()
        after_manifest = tree_manifest(latest)
        changes = sorted(k for k in set(manifest) | set(after_manifest)
                         if manifest.get(k) != after_manifest.get(k))
        atomic_json(self.job / "post-update-changes.json", changes)
        stage = self.data.with_name(self.data.name + ".restore-" + self.receipt["job_id"])
        stage_record = self.job / "restore-stage.json"
        if stage.exists() or stage.is_symlink():
            if stage.is_symlink() or not stage_record.exists():
                raise UpdateError("Unowned restore staging directory")
            record = json.loads(stage_record.read_text())
            if record.get("path") != str(stage):
                raise UpdateError("Restore staging identity mismatch")
            # A failed credential step may have partially transformed this
            # derived stage. Keep it for diagnosis, then rebuild from verified
            # before/after; never trust a partly applied stage on retry.
            retired = stage.with_name(stage.name + ".attempt-" + uuid.uuid4().hex[:8])
            atomic_json(self.job / (retired.name + ".manifest.json"), tree_manifest(stage))
            os.replace(stage, retired)
        atomic_json(stage_record, {"path": str(stage), "source": str(backup), "phase": "preparing"})
        snapshot(backup, stage)
        if tree_manifest(stage) != manifest:
            raise UpdateError("Restore stage does not match verified backup")
        self.judge_sqlite(self.judge_image(), stage)
        credentials = overlay_credentials(latest, stage)
        config_credentials = self.preserve_config_credentials(latest, stage)
        gateways = self.apply_gateway_intent(stage)
        atomic_json(self.job / "restored-gateway-intent.json", gateways)
        atomic_json(self.job / "restore-stage.manifest.json", tree_manifest(stage))
        atomic_json(stage_record, {"path": str(stage), "source": str(backup), "phase": "verified"})
        atomic_json(self.job / "preserved-credentials.json", credentials)
        atomic_json(self.job / "preserved-config-credentials.json", config_credentials)
        # The old directory is retained even after success. Nothing is erased;
        # every post-update write remains in after + the displaced directory.
        displaced = self.data.with_name(self.data.name + ".after-" + self.receipt["job_id"])
        if displaced.exists() or displaced.is_symlink():
            # A prior rollback may have swapped DATA successfully before
            # restarting the old image failed. Preserve every displaced tree
            # and repeat from the verified backup plus latest auth.
            displaced = displaced.with_name(displaced.name + ".retry-" + uuid.uuid4().hex[:8])
        os.replace(self.data, displaced)
        try:
            os.replace(stage, self.data)
        except BaseException:
            os.replace(displaced, self.data)
            raise
        self.receipt.update(post_update_export=str(latest), post_update_changes=len(changes),
                            displaced_data=str(displaced), preserved_credentials=len(credentials),
                            restored_gateways=len(gateways))
        history = self.receipt.setdefault("displaced_data_history", [])
        if str(displaced) not in history:
            history.append(str(displaced))
        self.receipt["post_update_exports"] = [str(self.job / "after"),
            *[str(p) for p in sorted(self.job.glob("after-retry-*")) if p.is_dir()]]
        self.log(f"Post-update writes exported: {len(changes)} paths; see post-update-changes.json")

    def update(self, reference, dry_run=False):
        stopped = False
        backed_up = False
        drained = False
        try:
            self.phase("preflight", status="running")
            initial = self.inspect_target()
            old = initial["Image"]
            if not IMAGE_ID.fullmatch(old):
                raise UpdateError("Old image has no immutable identity")
            self.receipt["old_image_id"] = old
            self.receipt["old_resources"] = {
                "nano_cpus": int(initial["HostConfig"].get("NanoCpus", 0)),
                "memory_bytes": int(initial["HostConfig"].get("Memory", 0)),
            }
            self.receipt["old_runtime"] = runtime_env_from_info(initial)
            self.receipt["google_oauth_mount"] = self.google_oauth_mount_contract(initial)
            self.launch_runtime_env()  # invalid ownership/admin overrides fail before pull/drain
            if self.receipt.get("expected_current") not in (None, old):
                self.receipt["error_code"] = "expected_current_mismatch"
                raise UpdateError("Expected current image differs from the running container; refresh installation state")
            configured = (self.home / "IMAGE").read_text().strip()
            configured_id = json.loads(self.docker("image", "inspect", configured))[0]["Id"]
            if configured_id != old:
                raise UpdateError("IMAGE file differs from running container")
            self.receipt["old_image_id"] = old
            self.free_space()
            if dry_run:
                self.phase("dry_run", status="succeeded", planned_reference=reference)
                return
            self.receipt["baseline_capability"] = self.capability()
            self.phase("protect_previous_image")
            self.protect_image(old, "rollback")
            self.phase("fetch")
            target = self.resolve_image(reference)
            self.receipt["target_image_id"] = target
            self.protect_image(target, "candidate")
            self.phase("candidate_check")
            candidate = self.probe_image(target)
            previous = self.probe_image(old)
            self.inspect_target(old)
            states = self.native_states()
            served = set()
            for item in states:
                served.update(item["state"].get("served_profiles") or ["default"])
            self.receipt["served_profiles"] = sorted(served)
            if target == old:
                self.phase("already_current", status="succeeded")
                return
            self.phase("draining")
            drained = True
            self.drain()
            self.inspect_target(old)
            self.phase("stopping")
            self.docker("stop", "--time", "60", self.name, timeout=90)
            stopped = True
            self.inspect_target(old, running=False)
            self.phase("backup")
            backup = self.verified_snapshot("before")
            self.receipt["backup_path"] = str(backup)
            # Read from stopped DATA, not from the snapshot: the file itself is
            # volatile and never copied, only the intent inside it survives.
            self.receipt["gateway_intent"] = gateway_intent(self.data)
            backed_up = True
            self.phase("schema_rehearsal")
            self.rehearse_schema(target)
            self.phase("cleanup")
            self.prune_skills(previous, candidate)
            self.phase("recreate")
            self.start_image(target)
            stopped = False
            self.phase("smoke")
            self.smoke(target)
            # Снять СВОЙ маркер drain: на успешном пути его до сих пор никто не
            # снимал, а в снимок он не попадает (VOLATILE) — поэтому после
            # отката данные приходили без него, а после удавшегося обновления
            # он оставался в DATA (найдено живым прогоном 08.09.2026).
            # Отмена сверяет principal, поэтому чужой маркер не тронет.
            self.release_drain()
            (self.home / "IMAGE.prev").write_text(old + "\n")
            self.phase("complete", status="succeeded")
        except Exception as exc:
            self.receipt["error"] = str(exc)
            if backed_up:
                try:
                    self.rollback(automatic=True)
                except Exception as rollback_error:
                    self.phase("rollback", status="rollback_failed", error=f"{exc}; rollback: {rollback_error}")
            else:
                if stopped:
                    self.docker("start", self.name)
                if drained:
                    with contextlib.suppress(Exception):
                        self.native_states("cancel")
                self.phase("failed", status="failed")
            raise UpdateError(self.receipt["error"]) from exc

    def rollback(self, automatic=False):
        if self.receipt.get("status") == "rolled_back":
            return
        validate_capability(self.receipt.get("baseline_capability"))
        self.launch_runtime_env(rollback=True)
        self.launch_resource_env(rollback=True)  # validate before stopping an older job's healthy container
        self.verify_backup()  # validate before quiescing a healthy newer gateway
        allowed = {self.receipt.get("old_image_id"), self.receipt.get("target_image_id")}
        try:
            info = self.inspect_target(running=False)
        except UpdateError:
            # up.sh can fail after removing the old stopped container. Only
            # recover absence; an existing but mismatched target stays blocked.
            present = self.docker("ps", "-aq", "--filter", "name=^/" + self.name + "$")
            if present:
                raise
            info = {"Image": self.receipt["target_image_id"], "State": {"Running": False}}
        if info["Image"] not in allowed:
            raise UpdateError("Rollback target has been changed by another operation")
        self.phase("rollback_draining", status="running")
        if info["State"]["Running"]:
            self.drain()
            self.docker("stop", "--time", "60", self.name, timeout=90)
        self.phase("rollback_restore")
        self.restore()
        self.phase("rollback_recreate")
        self.start_image(self.receipt["old_image_id"])
        self.smoke(self.receipt["old_image_id"])
        self.phase("rollback_complete", status="rolled_back")


def warm_dependencies(home=HERE):
    """Called by up.sh before first boot and every image switch."""
    manifest = json.loads((Path(home) / "dependencies.lock.json").read_text())
    packages = manifest["packages"]
    if not packages or not all(re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+", p["spec"]) for p in packages):
        raise UpdateError("Dependency manifest must contain exact package versions")
    image = (Path(home) / "IMAGE").read_text().strip()
    if not IMAGE_ID.fullmatch(image):
        image = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]["Id"]
    code = r'''
import importlib, importlib.metadata, json, sys
from tools.lazy_deps import install_specs, activate_durable_lazy_target
packages = json.loads(sys.argv[1])
verify_only = len(sys.argv) > 2 and sys.argv[2] == 'verify'
core_sys_path = list(sys.path)
activate_durable_lazy_target()
missing = []
for package in packages:
    name, version = package['spec'].split('==')
    try:
        present = importlib.metadata.version(name) == version
    except importlib.metadata.PackageNotFoundError:
        present = False
    if not present:
        missing.append(package['spec'])
if missing:
    if verify_only:
        raise RuntimeError('Fresh process cannot resolve pinned dependencies')
    # Native _core_constraints_file enumerates current sys.path. Inspect the
    # durable store above, but remove its activation before installing so its
    # OLD versions do not become immutable core constraints. The image's core
    # venv remains pinned by the native installer; custom durable packages stay.
    sys.path[:] = core_sys_path
    importlib.invalidate_caches()
    result = install_specs(missing, timeout=900)
    if not result.ok:
        raise RuntimeError('Pinned dependency warmup failed: ' + (result.reason or result.stderr[-500:]))
activate_durable_lazy_target()
for package in packages:
    name, version = package['spec'].split('==')
    if importlib.metadata.version(name) != version:
        raise RuntimeError('Dependency version mismatch: ' + name)
    importlib.import_module(package['module'])
print('Pinned dependencies verified')
'''
    args = ["docker", "run", "--rm", "--network", "host", "--cpus", "1", "--memory", "2g",
            "--user", os.environ.get("ENGINE_UID", "10000") + ":" + os.environ.get("ENGINE_GID", "10000"), "-w", "/opt/hermes",
            "--entrypoint", PYTHON, "-v", os.environ.get("DATA", "/opt/korra/data") + ":/opt/data",
            image, "-c", code, json.dumps(packages)]
    subprocess.run(args, check=True, timeout=1000)
    subprocess.run([*args, "verify"], check=True, timeout=180)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--update", metavar="REF_OR_TAR")
    mode.add_argument("--status", nargs="?", const="", metavar="JOB_ID")
    mode.add_argument("--rollback", metavar="JOB_ID")
    mode.add_argument("--worker", metavar="JOB_ID", help=argparse.SUPPRESS)
    mode.add_argument("--warm-deps", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--gc", action="store_true")
    mode.add_argument("--capabilities", action="store_true")
    parser.add_argument("--job-id")
    parser.add_argument("--expected-current", metavar="SHA256_IMAGE_ID")
    parser.add_argument("--expected-target", metavar="SHA256_IMAGE_ID")
    parser.add_argument("--expected-archive-sha256", metavar="HEX_SHA256")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--rollback-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.job_id and not args.update:
        parser.error("--job-id requires --update")
    if args.expected_current and (not args.update or not IMAGE_ID.fullmatch(args.expected_current)):
        parser.error("--expected-current requires --update and a full sha256 image ID")
    if args.expected_target and (not args.update or not IMAGE_ID.fullmatch(args.expected_target)):
        parser.error("--expected-target requires --update and a full sha256 image ID")
    if args.expected_archive_sha256:
        if not args.update or not re.fullmatch(r"[a-fA-F0-9]{64}", args.expected_archive_sha256):
            parser.error("--expected-archive-sha256 requires --update and 64 hexadecimal characters")
        args.expected_archive_sha256 = args.expected_archive_sha256.lower()
    if args.dry_run and not args.update:
        parser.error("--dry-run requires --update")
    if args.detach and not (args.update or args.rollback):
        parser.error("--detach requires --update or --rollback")
    if args.dry_run and args.detach:
        parser.error("--dry-run is synchronous")
    if (args.lock_fd is not None or args.rollback_worker) and not args.worker:
        parser.error("Worker flags require --worker")
    return args


def load_job(updater, job_id):
    updater.job = updater.jobs / checked_identifier(job_id)
    trusted_control(updater.job)
    updater.receipt = json.loads((updater.job / "status.json").read_text())
    if (updater.receipt.get("job_id") != job_id or updater.receipt.get("name") != updater.name
            or updater.receipt.get("data") != str(updater.data)):
        raise UpdateError("Operation belongs to another deployment")


def reconcile_interrupted(updater):
    receipt = updater.receipt
    if receipt.get("status") != "running" or not receipt.get("worker_pid"):
        return
    if process_epoch(receipt["worker_pid"]) == receipt.get("worker_epoch"):
        return
    with target_lock_path(updater.data).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        latest = json.loads((updater.job / "status.json").read_text())
        if latest != receipt:
            updater.receipt = latest
            return
        updater.phase("interrupted", status="failed", interrupted_phase=receipt["phase"],
                      error="Host worker ended unexpectedly; inspect saved snapshots and phase before recovery")


def main(argv=None):
    os.umask(0o077)
    args = parse_args(argv)
    if args.capabilities:
        print(json.dumps({"protocol": 1, "update": True, "detach": True, "status": True,
                          "rollback_detach": True, "expected_current": True, "artifact_verification": True,
                          "gc": True,
                          "files": ["update.sh", "updater.py", "up.sh", "backup.sh", "dependencies.lock.json"]}))
        return 0
    if os.geteuid() != 0:
        raise UpdateError("Host updater requires root")
    trusted_control(HERE)
    for name in ("update.sh", "updater.py", "up.sh", "dependencies.lock.json"):
        trusted_control(HERE / name)
    if args.warm_deps:
        warm_dependencies()
        return 0
    updater = Updater()
    if updater.home == updater.data or updater.data in updater.home.parents or updater.home in updater.data.parents:
        # DATA is normally a sibling of deploy/, or a child of the deployment
        # root; the latter is safe provided updater control remains root-owned.
        if updater.home == updater.data or updater.data in updater.home.parents:
            raise UpdateError("Host control files cannot be inside DATA")
    if args.status is not None:
        job_id = args.status
        if not job_id:
            entries = sorted(updater.jobs.glob("*/status.json"), key=lambda p: p.stat().st_mtime)
            if not entries:
                print(json.dumps({"job_id": None, "phase": "idle", "status": "pending", "error": None,
                                  "old_image_id": None, "target_image_id": None, "backup_path": None,
                                  "started_at": None, "updated_at": utc()}))
                return 0
            job_id = entries[-1].parent.name
        load_job(updater, job_id)
        reconcile_interrupted(updater)
        print(json.dumps(updater.receipt))
        return 0
    updater.jobs.mkdir(mode=0o700, exist_ok=True)
    trusted_control(updater.jobs)
    if args.gc:
        # Housekeeping runs under the same target lock as an update: a live
        # operation's candidate tag is exactly what must not be removed.
        with target_lock_path(updater.data).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UpdateError("Another update holds the target lock") from exc
            print(json.dumps(updater.gc()))
        return 0
    if args.worker:
        if args.lock_fd is None:
            raise UpdateError("Worker requires inherited target lock")
        lock = os.fdopen(args.lock_fd, "a")
        load_job(updater, args.worker)
    else:
        lock = target_lock_path(updater.data).open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            # Duplicate requests return the existing receipt, never start a
            # second worker. A different job receives a clear lock conflict.
            if args.job_id and (updater.jobs / checked_identifier(args.job_id) / "status.json").exists():
                load_job(updater, args.job_id)
                if (updater.receipt.get("reference") == args.update
                        and bool(updater.receipt.get("dry_run")) == args.dry_run
                        and all(updater.receipt.get(key) == getattr(args, key) for key in
                                ("expected_current", "expected_target", "expected_archive_sha256"))):
                    print(json.dumps(updater.receipt))
                    return 0
            if args.rollback and (updater.jobs / checked_identifier(args.rollback) / "status.json").exists():
                load_job(updater, args.rollback)
                if updater.receipt.get("action") == "rollback" and updater.receipt.get("status") in {"pending", "running"}:
                    print(json.dumps(updater.receipt))
                    return 0
            raise UpdateError("Another update holds the target lock") from exc
        if args.rollback:
            load_job(updater, args.rollback)
            if updater.receipt.get("status") == "rolled_back":
                print(json.dumps(updater.receipt))
                return 0
        else:
            job_id = checked_identifier(args.job_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
            if (updater.jobs / job_id).exists():
                load_job(updater, job_id)
                if updater.receipt.get("reference") != args.update:
                    raise UpdateError("Job ID already belongs to a different request")
                if bool(updater.receipt.get("dry_run")) != args.dry_run:
                    raise UpdateError("Job ID belongs to a different dry-run/execution mode")
                if updater.receipt.get("expected_current") != args.expected_current:
                    raise UpdateError("Job ID belongs to a different expected-current image")
                if (updater.receipt.get("expected_target") != args.expected_target
                        or updater.receipt.get("expected_archive_sha256") != args.expected_archive_sha256):
                    raise UpdateError("Job ID belongs to different artifact expectations")
                print(json.dumps(updater.receipt))
                return 0
            updater.initialize(job_id, args.update, dry_run=args.dry_run, expected_current=args.expected_current,
                               expected_target=args.expected_target, expected_archive_sha256=args.expected_archive_sha256)
        if args.detach:
            # start_new_session severs the SSH/browser controlling terminal;
            # the inherited flock survives until the actual operation exits.
            command = [sys.executable, str(HERE / "updater.py"), "--worker", updater.receipt["job_id"],
                       "--lock-fd", str(lock.fileno())]
            if args.rollback:
                command.append("--rollback-worker")
                # Persist the new operation before spawning. Returning the
                # previous update's terminal receipt would falsely tell the
                # caller rollback is already complete.
                updater.phase("rollback_pending", status="pending", action="rollback",
                              previous_error=updater.receipt.get("error"), error=None,
                              worker_pid=None, worker_epoch=None)
            if args.dry_run:
                raise UpdateError("Use --dry-run synchronously")
            try:
                with (updater.job / "worker.log").open("a") as output:
                    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                            start_new_session=True, pass_fds=(lock.fileno(),))
            except Exception as exc:
                updater.phase("spawn_failed", status="rollback_failed" if args.rollback else "failed",
                              error=f"Detached worker could not start: {exc}", error_code="worker_spawn_failed")
                print(json.dumps(updater.receipt))
                return 1
            # Child owns further status writes; parent must not race it.
            receipt = dict(updater.receipt, worker_pid=proc.pid)
            print(json.dumps(receipt))
            return 0
    try:
        updater.phase("worker_started", status="running", action="rollback" if args.rollback or args.rollback_worker else "update", worker_pid=os.getpid(),
                      worker_epoch=process_epoch(os.getpid()))
        if args.rollback or args.rollback_worker:
            updater.rollback()
        else:
            updater.update(updater.receipt["reference"], dry_run=args.dry_run)
        print(json.dumps(updater.receipt))
        return 0
    except Exception as exc:
        if args.rollback or args.rollback_worker:
            updater.phase("rollback", status="rollback_failed", error=str(exc))
        print(json.dumps(updater.receipt))
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UpdateError, OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        raise SystemExit(1)
