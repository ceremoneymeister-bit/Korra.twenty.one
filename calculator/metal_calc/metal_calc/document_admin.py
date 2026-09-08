"""Authenticated operator commands; no model-facing writer is registered."""
import json
import os
import sqlite3
import sys

from .config import Settings
from .document_jobs import DocumentJobs
from .document_runtime import load_reader
from .errors import InvalidState, MetalCalcError, OrderScopeDenied

# Framed download protocol for the operator panel: one bounded JSON line
# (``STREAM_PROTOCOL``/``STREAM_VERSION``, kind, name, bytes, sha256), then
# exactly ``bytes`` raw octets from the same verified read. A typed error
# before the header is the ordinary ``{"error": ...}`` line with exit 2, so
# the panel never has to guess whether the first binary chunk is JSON.
STREAM_PROTOCOL = "metal-calc-document-stream"
STREAM_VERSION = 1


def register(subparsers):
    for command in ("document-job-start", "document-jobs-list"):
        subparsers.add_parser(command).add_argument("--order-id", required=True)
    for command in ("document-job-status", "document-job-cancel", "document-job-retry"):
        parser = subparsers.add_parser(command)
        parser.add_argument("--job-id", required=True)
        if command == "document-job-status":
            parser.add_argument("--source-limit", type=int, default=200)
            parser.add_argument("--source-offset", type=int, default=0)
        elif command == "document-job-retry":
            # Optional: retry exactly one eligible failed source of this job.
            parser.add_argument("--source-id", default=None)
    for command in ("document-source-result", "document-source-info", "document-source-read",
                    "document-source-stream", "document-image-info", "document-image-read",
                    "document-image-stream"):
        parser = subparsers.add_parser(command)
        parser.add_argument("--job-id", required=True)
        parser.add_argument("--source-id", required=True)


def stream_header(kind, result, data):
    # ``result`` is the kernel's metadata for the very same ``data`` object;
    # DocumentJobs already rejected any size/SHA mismatch before returning it.
    return {"protocol": STREAM_PROTOCOL, "version": STREAM_VERSION, "kind": kind,
            "name": result["name"], "bytes": len(data), "sha256": result["sha256"]}


def run(args, read_json, emit):
    try:
        if os.environ.get("METAL_CALC_ROLE") != "front":
            raise OrderScopeDenied("Документы доступны в кабинете приёма заказов")
        jobs = DocumentJobs(Settings.from_env().orders_root)
        command = args.command
        if command == "document-job-start":
            body = read_json()
            if set(body) - {"command", "options", "source_id"}:
                raise InvalidState("Неизвестные параметры документного задания")
            reader = load_reader()
            action = body.get("command", "inspect")
            options = reader.canonical_options(action, body.get("options"))
            result = jobs.start(args.order_id, command=action, options=options,
                                source_id=body.get("source_id"),
                                reader_version=reader.reader_fingerprint(action, options))
        elif command == "document-jobs-list":
            result = jobs.list(args.order_id)
        elif command == "document-job-status":
            result = jobs.get(args.job_id, source_limit=args.source_limit, source_offset=args.source_offset)
        elif command == "document-job-cancel":
            result = jobs.cancel(args.job_id)
        elif command == "document-job-retry":
            # No --source-id keeps the historical whole-job call; an explicit
            # source is delegated to the kernel, which owns eligibility guards.
            if args.source_id is None:
                result = jobs.retry(args.job_id)
            else:
                result = jobs.retry(args.job_id, source_id=args.source_id)
        elif command == "document-source-result":
            result = jobs.result(args.job_id, args.source_id)
        else:
            kind = "image" if command.startswith("document-image-") else "source"
            if kind == "image":
                data, result = jobs.image(args.job_id, args.source_id)
                result = {**result, "name": "document.png"}
            else:
                data, result = jobs.read_source(args.job_id, args.source_id)
                result = {**result, "name": result["relative_path"].rsplit("/", 1)[-1]}
            if command.endswith("-read"):
                sys.stdout.buffer.write(data)
                return
            if command.endswith("-stream"):
                header = json.dumps(stream_header(kind, result, data), ensure_ascii=False, sort_keys=True)
                # One read, one frame: nothing else touches the source between
                # the metadata line and the octets it describes.
                sys.stdout.buffer.write(header.encode("utf-8") + b"\n")
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
                return
        emit(result)
    except MetalCalcError as exc:
        emit({"error": {"code": exc.code, "message": exc.public_message}})
        raise SystemExit(2) from exc
    except ValueError as exc:
        emit({"error": {"code": "InvalidState", "message": "Некорректные параметры чтения документа"}})
        raise SystemExit(2) from exc
    except (OSError, sqlite3.Error) as exc:
        emit({"error": {"code": "StorageUnavailable", "message": "Хранилище документов недоступно"}})
        raise SystemExit(2) from exc
