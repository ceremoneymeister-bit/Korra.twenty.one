"""Authenticated operator commands; no model-facing writer is registered."""
import os
import sqlite3
import sys

from .config import Settings
from .document_jobs import DocumentJobs
from .document_runtime import load_reader
from .errors import InvalidState, MetalCalcError, OrderScopeDenied


def register(subparsers):
    for command in ("document-job-start", "document-jobs-list"):
        subparsers.add_parser(command).add_argument("--order-id", required=True)
    for command in ("document-job-status", "document-job-cancel", "document-job-retry"):
        parser = subparsers.add_parser(command)
        parser.add_argument("--job-id", required=True)
        if command == "document-job-status":
            parser.add_argument("--source-limit", type=int, default=200)
            parser.add_argument("--source-offset", type=int, default=0)
    for command in ("document-source-result", "document-source-info", "document-source-read",
                    "document-image-info", "document-image-read"):
        parser = subparsers.add_parser(command)
        parser.add_argument("--job-id", required=True)
        parser.add_argument("--source-id", required=True)


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
            result = jobs.retry(args.job_id)
        elif command == "document-source-result":
            result = jobs.result(args.job_id, args.source_id)
        else:
            if command.startswith("document-image-"):
                data, result = jobs.image(args.job_id, args.source_id)
                result = {**result, "name": "document.png"}
            else:
                data, result = jobs.read_source(args.job_id, args.source_id)
                result = {**result, "name": result["relative_path"].rsplit("/", 1)[-1]}
            if command.endswith("-read"):
                sys.stdout.buffer.write(data)
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
