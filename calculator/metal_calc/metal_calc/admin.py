"""Операторский CLI расчётчика.

Здесь живут действия, которые модель делать не вправе: утверждение факта и
публикация данных предприятия. Панель вызывает эти команды подпроцессом —
не потому что так красивее, а потому что движковый venv не импортирует
``metal_calc`` (наследование идёт в обратную сторону), и второй свод правил
валидации в панели неизбежно разошёлся бы с этим.

Вывод всегда JSON: ``{...}`` при успехе, ``{"error": {"code", "message"}}``
и код возврата 2 при отказе. Панель показывает ``message`` человеку дословно.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from .config import Settings
from .errors import MetalCalcError
from .securefs import SecureRoot


def _read_stdin_bytes(limit: int = 4 * 1024 * 1024) -> bytes:
    data = sys.stdin.buffer.read(limit + 1)
    if len(data) > limit:
        raise MetalCalcError("Input is too large")
    return data


def _read_stdin_json(limit: int = 64 * 1024) -> dict[str, object]:
    raw = _read_stdin_bytes(limit)
    try:
        value = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetalCalcError("stdin must contain one JSON object") from exc
    if not isinstance(value, dict):
        raise MetalCalcError("stdin must contain one JSON object")
    return value


def _emit(result: object) -> None:
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="metal-calc-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    from . import document_admin
    document_admin.register(subparsers)
    from . import intake_admin
    intake_admin.register(subparsers)

    subparsers.add_parser("folder-upload-create")
    subparsers.add_parser("folder-list")
    for command in ("folder-upload-status", "folder-upload-complete", "folder-upload-file"):
        folder = subparsers.add_parser(command)
        folder.add_argument("--upload-id", required=True)
        if command == "folder-upload-file":
            folder.add_argument("--index", required=True, type=int)
    for command in ("folder-detail", "folder-file-info", "folder-file-read"):
        folder = subparsers.add_parser(command)
        folder.add_argument("--order-id", required=True)
        if command != "folder-detail":
            folder.add_argument("--index", required=True, type=int)

    approve = subparsers.add_parser("approve-fact")
    approve.add_argument("--order", required=True)
    approve.add_argument("--fact", required=True)
    approve.add_argument("--approved-by", required=True)

    qa = subparsers.add_parser(
        "qa-verdict",
        help="зафиксировать человеческий технологический/коммерческий QA",
    )
    qa.add_argument("--order-id", required=True)
    qa.add_argument("--expected-revision", required=True, type=int)
    qa.add_argument("--gate", required=True, choices=("technological", "commercial"))
    qa.add_argument(
        "--verdict",
        required=True,
        choices=("PASS", "ADJUST", "BLOCK", "NO_EVIDENCE"),
    )
    qa.add_argument(
        "--actor",
        required=True,
        help="аутентифицированный оператор панели; не отображаемое имя",
    )

    quote = subparsers.add_parser(
        "contractor-quote-set",
        help="зафиксировать предложение подрядчика для outsource-операции",
    )
    quote.add_argument("--order-id", required=True)
    quote.add_argument("--expected-revision", required=True, type=int)
    quote.add_argument("--route-seq", required=True, type=int)
    quote.add_argument(
        "--actor",
        required=True,
        help="аутентифицированный оператор панели; не отображаемое имя",
    )

    manual_review = subparsers.add_parser(
        "manual-review-complete",
        help="закрыть ручные пункты точной книги человеческой QA-квитанцией",
    )
    manual_review.add_argument("--order-id", required=True)
    manual_review.add_argument("--expected-revision", required=True, type=int)
    manual_review.add_argument("--expected-book-digest", required=True)
    manual_review.add_argument("--actor", required=True)

    route_return = subparsers.add_parser(
        "route-return",
        help="формально вернуть маршрут технологу от имени QA",
    )
    route_return.add_argument("--order-id", required=True)
    route_return.add_argument("--expected-revision", required=True, type=int)
    route_return.add_argument("--actor", required=True)

    subparsers.add_parser("pack-validate", help="проверить пак со stdin, ничего не записывая")

    subparsers.add_parser(
        "pack-upgrade",
        help="черновик пака v3 из пака v2 со stdin — ничего не записывает",
    )

    publish = subparsers.add_parser("pack-publish", help="записать новую ревизию пака")
    publish.add_argument("--author", required=True, help="идентификатор автора, не имя")
    publish.add_argument("--note", default="", help="зачем менялось")
    publish.add_argument(
        "--no-activate",
        action="store_true",
        help="записать ревизию, но оставить действующей прежнюю",
    )

    activate = subparsers.add_parser("pack-activate", help="сделать ревизию действующей (откат)")
    activate.add_argument("--revision", required=True)
    activate.add_argument("--author", required=True)

    subparsers.add_parser("pack-list", help="история ревизий")
    subparsers.add_parser("pack-verify", help="сверить действующий пак с журналом")
    subparsers.add_parser(
        "pack-active",
        help="действующая ревизия с отпечатком данных — для сверки стадий заказов",
    )

    args = parser.parse_args()

    if args.command.startswith("intake-"):
        intake_admin.run(args, _emit)
        return

    if args.command.startswith("document-"):
        document_admin.run(args, _read_stdin_json, _emit)
        return

    if args.command.startswith("folder-"):
        # Folder bytes are transported by the operator cabinet. No new model
        # tool or role capability is exposed, and no geometry/rates imports are
        # needed for each file in a large folder.
        from .folder_intake import FolderIntake, MAX_MANIFEST_BYTES

        service = None
        try:
            if os.environ.get("METAL_CALC_ROLE", "") != "front":
                raise MetalCalcError("Папки заказов доступны в кабинете приёма заказов")
            service = FolderIntake(Settings.from_env().orders_root)
            if args.command == "folder-upload-create":
                result = service.create(_read_stdin_json(MAX_MANIFEST_BYTES))
            elif args.command == "folder-upload-status":
                result = service.status(args.upload_id)
            elif args.command == "folder-upload-file":
                result = service.upload(args.upload_id, args.index, sys.stdin.buffer)
            elif args.command == "folder-upload-complete":
                result = service.complete(args.upload_id)
            elif args.command == "folder-list":
                result = service.list()
            elif args.command == "folder-detail":
                result = service.detail(args.order_id)
            else:
                fd, info = service.open_file(args.order_id, args.index)
                with os.fdopen(fd, "rb") as handle:
                    if args.command == "folder-file-read":
                        shutil.copyfileobj(handle, sys.stdout.buffer, length=1024 * 1024)
                        return
                    result = info
            _emit(result)
        except MetalCalcError as exc:
            _emit({"error": {"code": exc.code, "message": exc.public_message}})
            raise SystemExit(2) from exc
        except OSError as exc:
            _emit({"error": {"code": "StorageUnavailable", "message": "Хранилище файлов недоступно"}})
            raise SystemExit(2) from exc
        finally:
            if service is not None:
                service.close()
        return

    # Служба заказов нужна только операторским действиям; для паков поднимать
    # её незачем — она открывает реестр и схему заказа. Человеческие роли здесь
    # задаёт доверенный CLI, который панель вызывает после своей авторизации;
    # MCP/модель этих команд не видят.
    if args.command in {
        "approve-fact",
        "qa-verdict",
        "contractor-quote-set",
        "manual-review-complete",
        "route-return",
    }:
        from .packs2 import PipelinePackStore
        from .service import MetalCalcService
        from .service3 import WorkflowService

        service = MetalCalcService(Settings.from_env())
        try:
            if args.command == "approve-fact":
                result = service.approve_fact(args.order, args.fact, args.approved_by)
            elif args.command == "qa-verdict":
                request = _read_stdin_json()
                unknown = set(request) - {"reasons", "adjust_owner"}
                if unknown:
                    raise MetalCalcError(
                        "Unknown qa-verdict fields: " + ", ".join(sorted(unknown))
                    )
                reasons = request.get("reasons", [])
                adjust_owner = request.get("adjust_owner")
                workflow = WorkflowService(
                    service.registry,
                    PipelinePackStore(service.rates_root),
                )
                result = workflow.qa_verdict(
                    args.order_id,
                    args.expected_revision,
                    args.gate,
                    args.verdict,
                    reasons,  # type: ignore[arg-type]
                    args.actor,
                    adjust_owner,  # type: ignore[arg-type]
                    actor_role="qa",
                )
            elif args.command == "contractor-quote-set":
                request = _read_stdin_json()
                required = {
                    "amount_rub",
                    "basis",
                    "vat_included",
                    "quoted_at",
                    "valid_until",
                    "source",
                    "reference",
                }
                missing = required - set(request)
                unknown = set(request) - required
                if missing or unknown:
                    details = []
                    if missing:
                        details.append("missing: " + ", ".join(sorted(missing)))
                    if unknown:
                        details.append("unknown: " + ", ".join(sorted(unknown)))
                    raise MetalCalcError(
                        "Invalid contractor-quote-set fields (" + "; ".join(details) + ")"
                    )
                workflow = WorkflowService(
                    service.registry,
                    PipelinePackStore(service.rates_root),
                )
                result = workflow.contractor_quote_set(
                    args.order_id,
                    args.expected_revision,
                    args.route_seq,
                    request["amount_rub"],
                    request["basis"],
                    request["vat_included"],
                    request["quoted_at"],
                    request["valid_until"],
                    request["source"],
                    request["reference"],
                    args.actor,
                    actor_role="panel",
                )
            elif args.command == "manual-review-complete":
                request = _read_stdin_json()
                if set(request) != {"reviews"}:
                    raise MetalCalcError(
                        "manual-review-complete requires exactly reviews"
                    )
                workflow = WorkflowService(
                    service.registry,
                    PipelinePackStore(service.rates_root),
                )
                result = workflow.manual_review_complete(
                    args.order_id,
                    args.expected_revision,
                    args.expected_book_digest,
                    request["reviews"],  # type: ignore[arg-type]
                    args.actor,
                    actor_role="qa",
                )
            else:
                request = _read_stdin_json()
                if set(request) != {"reason"}:
                    raise MetalCalcError("route-return requires exactly reason")
                workflow = WorkflowService(
                    service.registry,
                    PipelinePackStore(service.rates_root),
                )
                result = workflow.route_return(
                    args.order_id,
                    args.expected_revision,
                    request["reason"],  # type: ignore[arg-type]
                    args.actor,
                    actor_role="qa",
                )
            _emit({"ok": True, **result})
        except MetalCalcError as exc:
            _emit({"error": {"code": exc.code, "message": exc.public_message}})
            raise SystemExit(2) from exc
        finally:
            service.close()
        return

    settings = Settings.from_env()
    from .packadmin import PackPublisher, upgrade_pack_v2_to_v3, validate_pack
    from .packs2 import PipelinePackStore

    try:
        if args.command == "pack-validate":
            # Команде не нужен корень на запись: панель зовёт её на каждое
            # «Проверить», и требовать rw было бы враньём про её намерения.
            _emit(validate_pack(_read_stdin_bytes()))
            return

        if args.command == "pack-upgrade":
            # Тоже чтение: результат — черновик для человека, не публикация.
            _emit(upgrade_pack_v2_to_v3(_read_stdin_bytes()))
            return

        if args.command == "pack-active":
            # Тоже чтение. Отпечаток данных (без имени ревизии) — то, чем
            # стадии заказов сверяются с действующими ставками; вычислять его
            # в панели значило бы завести вторую правду о том, что считается
            # «теми же данными».
            from .securefs import SecureRoot as _RoRoot

            store = PipelinePackStore(_RoRoot(settings.rates_root, writable=False))
            try:
                pack = store.load_active()
            except MetalCalcError as exc:
                _emit({"active": None, "reason": exc.public_message})
                return
            _emit(
                {
                    "active": pack.revision,
                    "sha256": pack.sha256,
                    "fingerprint": pack.fingerprint,
                    "status": pack.status,
                }
            )
            return

        with SecureRoot(settings.rates_root, writable=True) as root:
            publisher = PackPublisher(root)
            if args.command == "pack-publish":
                _emit(
                    publisher.publish(
                        _read_stdin_bytes(),
                        author=args.author,
                        note=args.note,
                        activate=not args.no_activate,
                    )
                )
            elif args.command == "pack-activate":
                _emit(publisher.activate(args.revision, author=args.author))
            elif args.command == "pack-list":
                _emit(publisher.revisions())
            elif args.command == "pack-verify":
                result = publisher.verify_active()
                _emit(result)
                if not result.get("ok"):
                    raise SystemExit(3)
    except MetalCalcError as exc:
        _emit({"error": {"code": exc.code, "message": exc.public_message}})
        raise SystemExit(2) from exc
    except OSError as exc:
        _emit({"error": {"code": "StorageUnavailable", "message": str(exc)}})
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
