#!/usr/bin/env python3
"""Release artifact handoff and offline clean/smoke acceptance (stdlib only).

Clean bootstrap and missing-provider SSE checks follow the owner verifier in
projects/Korra 21/scripts/verify-image{,-checks.py}. Its legacy/smoke scenarios
read owner data and therefore must never run in CI. Here the only data volume
is fresh tmpfs, networking is disabled, and probes run inside the container.

pack -> accept -> load --receipt is a fail-closed chain: no rebuild, floating
tag, shared daemon state or owner fixture is used between CI jobs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid


class AcceptanceError(RuntimeError):
    pass


def deployment_needed(paths: list[str]) -> bool:
    # The shared classifier treats docs/ as prose. This one narrow exception
    # owns deploy executables and their tests; other lanes remain its concern.
    paths = [path for path in paths if path.strip()]
    return not paths or any(path.startswith(("docs/client-deploy/", "tests/deployment/")) for path in paths)


def run(*args: str, timeout: int = 60, input: str | None = None, combine_output: bool = False) -> str:
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # No container logs or config dumps: an accidentally contaminated image
        # must fail without copying its credentials into a public CI log.
        raise AcceptanceError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return (result.stdout + (result.stderr if combine_output else "")).strip()


def image_info(image: str) -> dict:
    info = json.loads(run("docker", "image", "inspect", image))[0]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", info.get("Id", "")):
        raise AcceptanceError("Docker did not return an exact image ID")
    return info


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_image(info: dict, manifest: dict) -> None:
    revision = (info.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision")
    if (info["Id"], info.get("Architecture"), revision) != (
        manifest["image_id"], manifest["arch"], manifest["revision"]
    ):
        raise AcceptanceError("Image ID, architecture or source revision differs from build artifact")


def pack(image: str, directory: Path, revision: str, arch: str) -> dict:
    info = image_info(image)
    manifest = {"schema": 1, "image_id": info["Id"], "revision": revision, "arch": arch}
    verify_image(info, manifest)
    directory.mkdir(parents=True, exist_ok=False)
    archive = directory / "image.tar"
    # Save by ID: no mutable :test tag is restored over another concurrent run.
    run("docker", "image", "save", "--output", str(archive), info["Id"], timeout=600)
    manifest["archive_sha256"] = file_hash(archive)
    (directory / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    return manifest


def load_artifact(directory: Path, revision: str, arch: str, receipt: Path | None = None) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema"), manifest.get("revision"), manifest.get("arch")) != (1, revision, arch):
        raise AcceptanceError("Artifact belongs to a different revision or architecture")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest.get("image_id", "")):
        raise AcceptanceError("Artifact has no exact image ID")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("archive_sha256", "")):
        raise AcceptanceError("Artifact has no archive checksum")
    if receipt is not None:
        accepted = json.loads(receipt.read_text(encoding="utf-8"))
        if accepted.get("accepted") is not True or accepted.get("artifact") != manifest:
            raise AcceptanceError("Missing successful acceptance for this exact artifact")
    archive = directory / "image.tar"
    if file_hash(archive) != manifest["archive_sha256"]:
        raise AcceptanceError("Build artifact checksum mismatch")
    # Always load the archive even when a local tag/image happens to exist.
    run("docker", "image", "load", "--input", str(archive), timeout=600)
    verify_image(image_info(manifest["image_id"]), manifest)
    return manifest


def validate_health(panel: dict, api: dict) -> None:
    if not isinstance(panel, dict) or panel.get("gateway_running") is not True or not panel.get("version"):
        raise AcceptanceError("Dashboard JSON does not describe a running gateway")
    if not isinstance(api, dict) or api.get("status") != "ok" or not api.get("version"):
        raise AcceptanceError("API health JSON is not ready")


def validate_sse(body: str) -> None:
    chunks = []
    done = False
    for line in body.splitlines():
        if line.startswith("data: "):
            payload = line[6:].strip()
            if payload == "[DONE]":
                done = True
            else:
                chunks.append(json.loads(payload))
    finals = [item for item in chunks if item.get("choices") and item["choices"][0].get("finish_reason")]
    if not done or not finals or finals[-1]["choices"][0]["finish_reason"] != "error":
        raise AcceptanceError("Missing-provider chat returned an empty/successful or incomplete stream")
    message = (finals[-1].get("error") or {}).get("message", "")
    text = "".join(item["choices"][0].get("delta", {}).get("content", "")
                   for item in chunks if item.get("choices"))
    if "Ключи" not in message + text:
        raise AcceptanceError("Missing-provider chat did not explain how to configure Ключи")


# Executed by the image's own Python, as the normal runtime UID. Only synthetic
# local API auth is read, and it is never returned to the runner.
PROBE = r'''
import json, os, pathlib, re, sys, urllib.request
data = pathlib.Path('/opt/data')
mode = sys.argv[1]
def get(url, **kwargs):
    with urllib.request.urlopen(urllib.request.Request(url, **kwargs), timeout=30) as response:
        return response.read().decode('utf-8')
if mode == 'health':
    panel = json.loads(get('http://127.0.0.1:9119/api/status'))
    api = json.loads(get('http://127.0.0.1:8642/health'))
    print(json.dumps({'panel': panel, 'api': api}))
elif mode == 'bootstrap':
    import yaml
    root = pathlib.Path(sys.executable).parent.parent.parent
    template = root / 'korra-config.yaml.example'
    assert template.is_file(), 'missing Korra config template'
    cfg = data / 'config.yaml'
    assert cfg.read_bytes() == template.read_bytes(), 'clean config differs from image template'
    config = yaml.safe_load(cfg.read_text())
    assert config == {'gateway': {'multiplex_profiles': True}}, 'unexpected enabled clean config'
    env_template = (root / 'korra-env.example').read_text()
    env = (data / '.env').read_text()
    env_without_key = re.sub(r'^API_SERVER_KEY=[^\n]*\n?', '', env, flags=re.M)
    assert env_without_key.strip() == env_template.strip(), 'unexpected seeded environment'
    assert re.search(r'^API_SERVER_KEY=[0-9a-f]{32,}$', env, re.M), 'missing generated API key'
    assert not list(data.rglob('auth.json')), 'auth must not be copied into clean installation'
    for path in data.rglob('jobs.json'):
        jobs = json.loads(path.read_text())
        assert not (jobs.get('jobs', jobs) if isinstance(jobs, dict) else jobs), 'clean image contains cron jobs'
    for path in data.rglob('.env'):
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                assert key == 'API_SERVER_KEY' or not value.strip(), 'unexpected nonempty environment value'
    web = pathlib.Path(os.environ.get('KORRA_WEB_DIST', os.environ.get('HERMES_WEB_DIST', '')))
    assert (web / 'index.html').is_file(), 'dashboard assets absent'
    assert len(list((web / 'assets').glob('index-*.js'))) == 1, 'stale dashboard entry chunks'
    assert (root / 'skills/autonomous-ai-agents/korra-agent/SKILL.md').is_file(), 'Korra agent skill absent'
    print('bootstrap, clean credentials/cron, bundled skill and dashboard assets passed')
elif mode == 'chat':
    env = (data / '.env').read_text()
    key = re.search(r'^API_SERVER_KEY=(.+)$', env, re.M).group(1)
    payload = json.dumps({'model': 'test', 'messages': [{'role': 'user', 'content': 'Привет!'}], 'stream': True}).encode()
    print(get('http://127.0.0.1:8642/v1/chat/completions', data=payload,
              headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}))
'''


def container_command(name: str, info: dict) -> list[str]:
    if not re.fullmatch(r"k21-release-ci-[0-9a-f]{32}", name):
        raise AcceptanceError("Refusing a container name outside the release CI namespace")
    env_names = {item.split("=", 1)[0] for item in info.get("Config", {}).get("Env", [])}
    prefix = "KORRA_" if "KORRA_HOME" in env_names else "HERMES_"
    env = {
        f"{prefix}UID": "10000", f"{prefix}GID": "10000",
        f"{prefix}HOME": "/opt/data", f"{prefix}DASHBOARD": "1",
        f"{prefix}DASHBOARD_HOST": "127.0.0.1", f"{prefix}DASHBOARD_PORT": "9119",
        f"{prefix}DISABLE_LAZY_INSTALLS": "1", "KORRA_UI_MODE": "fleet",
        "API_SERVER_HOST": "127.0.0.1", "API_SERVER_PORT": "8642",
        "API_SERVER_PROXY_TARGET": "http://127.0.0.1:8642",
    }
    command = ["docker", "run", "--detach", "--name", name, "--label", "korra.release-ci=true",
               "--pull", "never", "--network", "none", "--restart", "no",
               "--cpus", "4", "--memory", "4g", "--memory-swap", "4g", "--pids-limit", "256",
               "--tmpfs", "/opt/data:rw,size=512m,mode=0755"]
    for key, value in env.items():
        command.extend(["--env", f"{key}={value}"])
    return command + [info["Id"], "gateway", "run"]


def check_image(image: str, timeout: int = 180) -> list[str]:
    info = image_info(image)
    name = "k21-release-ci-" + uuid.uuid4().hex
    command = container_command(name, info)
    # PATH in the image includes its own venv; no host interpreter or source bind.
    def probe(mode: str) -> str:
        return run("docker", "exec", "--user", "10000:10000", name, "python", "-c", PROBE, mode,
                   timeout=40)
    try:
        run(*command)
        deadline = time.monotonic() + timeout
        while True:
            state = json.loads(run("docker", "inspect", name))[0]
            if not state.get("State", {}).get("Running") or state.get("Image") != info["Id"]:
                raise AcceptanceError("Candidate stopped or container is not running the selected image ID")
            try:
                health = json.loads(probe("health"))
                validate_health(health["panel"], health["api"])
                break
            except (AcceptanceError, ValueError, KeyError, subprocess.TimeoutExpired):
                if time.monotonic() >= deadline:
                    raise AcceptanceError("Timed out waiting for dashboard and API JSON readiness")
                time.sleep(2)
        probe("bootstrap")
        help_text = run("docker", "exec", "--user", "10000:10000", name, "korra", "--help")
        if "usage" not in help_text.lower():
            raise AcceptanceError("Korra CLI did not start")
        validate_sse(probe("chat"))
        logs = run("docker", "logs", name, combine_output=True)
        if re.search(r"Connecting Telegram|Telegram.*(?:connected|bot started)|api\.telegram\.org/bot", logs, re.I):
            raise AcceptanceError("Clean candidate tried to connect a Telegram bot")
        return ["dashboard and API JSON readiness", "clean bootstrap and empty credentials/cron",
                "Korra CLI, skill and dashboard assets", "missing-provider SSE error with Ключи hint",
                "no Telegram connection; network disabled"]
    finally:
        # The name is generated above, never supplied by the operator. --volumes
        # also removes any anonymous image VOLUME (tmpfs itself leaves no state).
        run("docker", "rm", "--force", "--volumes", name)


def accept(directory: Path, revision: str, arch: str, receipt: Path) -> dict:
    receipt.unlink(missing_ok=True)
    manifest = load_artifact(directory, revision, arch)
    checks = check_image(manifest["image_id"])
    result = {"accepted": True, "artifact": manifest, "checks": checks}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("deployment-needed", help="Read changed paths from stdin and print the deployment CI gate")
    for name in ("pack", "accept", "load"):
        command = sub.add_parser(name)
        command.add_argument("--directory", type=Path, required=True)
        command.add_argument("--revision", required=True)
        command.add_argument("--arch", choices=("amd64",), required=True)
        if name == "pack":
            command.add_argument("--image", required=True)
        else:
            command.add_argument("--receipt", type=Path, required=name == "accept")
    check = sub.add_parser("check")
    check.add_argument("--image", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "deployment-needed":
            print("true" if deployment_needed(sys.stdin.read().splitlines()) else "false")
            return 0
        elif args.command == "pack":
            result = pack(args.image, args.directory, args.revision, args.arch)
        elif args.command == "accept":
            result = accept(args.directory, args.revision, args.arch, args.receipt)
        elif args.command == "load":
            print(load_artifact(args.directory, args.revision, args.arch, args.receipt)["image_id"])
            return 0
        else:
            result = {"accepted": True, "checks": check_image(args.image)}
        print(json.dumps(result, indent=2))
        return 0
    except (AcceptanceError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"Release acceptance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # SIGTERM from the job timeout/cancellation unwinds the container finally.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    sys.exit(main())
