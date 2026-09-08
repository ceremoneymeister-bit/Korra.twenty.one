"""Operator-only handoff admission and dispatch lifecycle commands."""
import os
import sqlite3
import sys

from .config import Settings
from .errors import MetalCalcError, OrderScopeDenied
from .intake_handoffs import IntakeHandoffs


def register(subparsers):
    subparsers.add_parser("intake-prepare").add_argument("--order-id", required=True)
    for command in ("get", "claim", "session-created", "dispatched", "error", "received", "finished"):
        parser = subparsers.add_parser("intake-" + command)
        parser.add_argument("--handoff-id", required=True)
        if command == "dispatched":
            parser.add_argument("--run-id", required=True)
        elif command == "error":
            parser.add_argument("--error-code", required=True)
    for command in ("preparation", "answers-save"):
        parser = subparsers.add_parser("intake-" + command)
        parser.add_argument("--handoff-id", required=True)


def run(args, emit):
    try:
        if os.environ.get("METAL_CALC_ROLE") != "front":
            raise OrderScopeDenied("Передача доступна в кабинете приёма заказов")
        store = IntakeHandoffs(Settings.from_env().orders_root)
        if args.command in {"intake-preparation", "intake-answers-save"}:
            from .intake_preparation import IntakePreparation
            from .admin import _read_stdin_json
            from .errors import InvalidState
            preparation = IntakePreparation(store)
            if args.command == "intake-answers-save":
                body = _read_stdin_json(limit=24 * 1024)
                if set(body) != {"snapshot_id", "expected_revision", "request_id", "answers"}:
                    raise InvalidState("Некорректный запрос сохранения начальных ответов")
                preparation.save(args.handoff_id, **body)
            emit(preparation.view(args.handoff_id, classify=True))
            return
        method = {"intake-prepare": "prepare", "intake-get": "get", "intake-claim": "claim",
                  "intake-session-created": "mark_session_created", "intake-dispatched": "mark_dispatched",
                  "intake-error": "mark_error", "intake-received": "mark_received",
                  "intake-finished": "mark_finished"}[args.command]
        positional = [args.order_id if args.command == "intake-prepare" else args.handoff_id]
        if args.command == "intake-dispatched":
            positional.append(args.run_id)
        elif args.command == "intake-error":
            positional.append(args.error_code)
        emit(getattr(store, method)(*positional))
    except MetalCalcError as exc:
        emit({"error": {"code": exc.code, "message": exc.public_message}})
        sys.exit(2)
    except (sqlite3.Error, OSError):
        emit({"error": {"code": "StorageUnavailable", "message": "Хранилище передачи временно недоступно"}})
        sys.exit(2)
