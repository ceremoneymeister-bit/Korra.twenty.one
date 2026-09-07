#!/usr/bin/env python3
"""Build only pinned code inputs; company data and credentials stay in volumes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
IMAGE = ROOT / "calculator/image"
BASE = "c9c42ecb375c56f853576f0d4a06519deeb96f78"
ENGINE = "ghcr.io/ceremoneymeister-bit/korra.twenty.one@sha256:02ea3ebc19051915ac220111c800bf6e12ff427d557f0f0310bfceb8d216f900"
CAD = "korra-calculator:v9-f383ea8651-a1"
CAD_ID = "sha256:40c85b6c14b2aac9795d8936cd26bb8a78e5908de14931babbb6a05ac5ea0eee"


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def copy_tree(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        ".venv", "__pycache__", ".pytest_cache", "*.egg-info", "build", "dist"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=("kernel", "pilot"), default="pilot")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    assert run("docker", "image", "inspect", CAD, "--format", "{{.Id}}") == CAD_ID
    assert run("docker", "image", "inspect", ENGINE, "--format", "{{.Id}}") == ENGINE.split("@")[1]
    context = IMAGE / ".context"
    if context.exists():
        shutil.rmtree(context)
    context.mkdir()
    for directory in ("metal_calc", "lib", "bin", "review", "handoff", "documents"):
        copy_tree(ROOT / "calculator" / directory, context / directory)
    for name in ("requirements.lock", "check_versions.py", "check_tool_surface.py"):
        shutil.copy2(IMAGE / name, context / name)
    copy_tree(IMAGE / "fixtures", context / "fixtures")
    overlay = context / "runtime-overlay"
    overlay.mkdir()
    paths = set(run("git", "diff", "--name-only", BASE).splitlines())
    paths.update(run("git", "ls-files", "--others", "--exclude-standard").splitlines())
    allowed = ("korra_cli/", "gateway/", "tools/", "plugins/", "agent/")
    for name in sorted(paths):
        if name.startswith(allowed) or name in {"run_agent.py", "toolsets.py", "model_tools.py"}:
            src = ROOT / name
            if not src.is_file() or src.suffix not in {".py", ".json", ".yaml"}:
                continue
            dst = overlay / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    web = ROOT / "korra_cli/web_dist"
    if args.target == "pilot":
        if not (web / "index.html").is_file():
            raise SystemExit("Build the current web interface before the pilot image")
        copy_tree(web, context / "web-dist")
    else:
        (context / "web-dist").mkdir()
    hashes = {str(p.relative_to(context)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(context.rglob("*")) if p.is_file()}
    receipt = {"engine": ENGINE, "engine_revision": BASE, "cad_image_id": CAD_ID,
               "target": args.target, "files": hashes}
    (context / "source-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    subprocess.run(["docker", "build", "--progress=plain", "--target", args.target,
                    "-f", str(IMAGE / "Dockerfile"), "-t", args.tag, str(context)],
                   cwd=ROOT, check=True)
    receipt["image_id"] = run("docker", "image", "inspect", args.tag, "--format", "{{.Id}}")
    (IMAGE / f"{args.target}-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(receipt["image_id"])


if __name__ == "__main__":
    main()
