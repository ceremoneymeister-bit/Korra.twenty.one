#!/usr/bin/env python3
"""Isolated document CLI; JSON/PNG artifacts remain unverified observations."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys

from adapter import DocumentError, Limits, add_error, base_artifact, inspect_document, verified_source


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def worker(request: dict) -> int:
    import resource

    os.umask(0o077)
    # Bound native-parser memory and CPU; the parent enforces a wall-clock limit.
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    seconds = math.ceil(request["timeout"])
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds + 1))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    limits = Limits(**request["limits"])
    artifact = base_artifact(request["command"], request["source"], request["sha256"], limits)
    path = Path(request["out"]) / f"{request['command']}.json"
    checkpoint = lambda value: write_json(path, value)
    checkpoint(artifact)
    try:
        data = verified_source(request["source"], request["sha256"], limits)
        artifact["source"].update(sha256=request["sha256"].lower(), sha256_verified=True,
                                  bytes=len(data))
        checkpoint(artifact)
        if request["command"] == "inspect":
            inspect_document(data, artifact, limits, request.get("pages"), checkpoint)
        else:
            from adapter import render_document
            render_document(data, artifact, limits, request["page"], request["dpi"],
                            request.get("crop"), Path(request["out"]))
    except DocumentError as exc:
        add_error(artifact, exc.code, str(exc))
    except Exception as exc:
        add_error(artifact, "document_processing_error", f"{type(exc).__name__}: {exc}")
    checkpoint(artifact)
    return 0 if artifact["complete"] else 2


def main() -> int:
    os.umask(0o077)
    if len(sys.argv) == 2 and sys.argv[1] == "_worker":
        return worker(json.loads(sys.stdin.read()))
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "render"):
        command = subcommands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("--sha256", required=True)
        command.add_argument("--out", type=Path, required=True, help="New or empty artifact directory")
        command.add_argument("--timeout", type=float, default=60, help="Worker wall-clock seconds, 1..180")
        command.add_argument("--max-pages", type=int, default=1000)
        command.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
        if name == "inspect":
            command.add_argument("--pages", help="Text pages, one-based: 1,3-5; default first eight; inventory is complete")
            command.add_argument("--max-text-chars", type=int, default=8000)
            command.add_argument("--max-total-text-chars", type=int, default=40000)
        else:
            command.add_argument("--page", required=True, type=int)
            command.add_argument("--dpi", type=int, default=180, help="Nominal DPI for 1/72-inch canvas units")
            command.add_argument("--crop", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                                 help="Displayed page units after intrinsic rotation; top-left origin, y down")
            command.add_argument("--max-pixels", type=int, default=16_000_000)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 180:
        parser.error("timeout must be 1..180 seconds")
    if not 1 <= args.max_pages <= 2000 or not 1 <= args.max_bytes <= 200 * 1024 * 1024:
        parser.error("max-pages must be 1..2000 and max-bytes 1..209715200")
    limits = Limits(max_bytes=args.max_bytes, max_pages=args.max_pages,
                    max_text_chars=getattr(args, "max_text_chars", 8000),
                    max_total_text_chars=getattr(args, "max_total_text_chars", 40000),
                    max_pixels=getattr(args, "max_pixels", 16_000_000))
    if not 1 <= limits.max_text_chars <= 20000 or not 1 <= limits.max_total_text_chars <= 200000:
        parser.error("text limits exceed allowed range")
    if not 1 <= limits.max_pixels <= 40_000_000:
        parser.error("max-pixels must be 1..40000000")
    destination = args.out.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(destination.iterdir()):
        parser.error("output directory must be empty; existing artifacts will not be overwritten")
    request = vars(args).copy()
    request.update(source=str(args.source.resolve()), out=str(destination), limits=vars(limits))
    with (destination / "request.json").open("x", encoding="utf-8") as stream:
        json.dump(request, stream, ensure_ascii=False, indent=2)
    artifact_path = destination / f"{args.command}.json"
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_worker"],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONUTF8": "1"})
    timed_out = False
    try:
        process.communicate(json.dumps(request).encode("utf-8"), timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # Worker exited between deadline detection and the kill.
        process.communicate()
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    else:
        artifact = base_artifact(args.command, str(args.source), args.sha256, limits)
    if timed_out or (process.returncode != 0 and not artifact["errors"]):
        code = "worker_timeout" if timed_out else "worker_exit"
        add_error(artifact, code, "Worker stopped before completion; retained inventory/text are partial")
        write_json(artifact_path, artifact)
    print(json.dumps({"artifact": str(artifact_path), "status": artifact["status"],
                      "complete": artifact["complete"], "errors": artifact["errors"]}, ensure_ascii=False))
    return 124 if timed_out else (0 if artifact["complete"] else 2)


if __name__ == "__main__":
    raise SystemExit(main())
