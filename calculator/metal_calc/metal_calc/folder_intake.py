"""Durable folder intake, independent of model inference and calculation.

Files are immutable numbered blobs inside the orders root. Relative user paths
are metadata only. The canonical Registry.create commit publishes a whole draft;
an unfinished upload never appears in the orders list. Its UUID makes all upload
retries safe, including a lost response after that commit.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID

from .errors import Conflict, FileTooLarge, InvalidIdentifier, InvalidState, NotFound
from .registry import Registry
from .securefs import SecureRoot
from .util import canonical_json, utcnow, validate_id

MAX_FILES = 10_000
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_FOLDER_BYTES = 20 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
LIMITS = {"max_files": MAX_FILES, "max_file_bytes": MAX_FILE_BYTES,
          "max_folder_bytes": MAX_FOLDER_BYTES}


def validate_upload_id(value: Any) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidIdentifier("Некорректный идентификатор загрузки") from exc
    if str(parsed) != value:
        raise InvalidIdentifier("Некорректный идентификатор загрузки")
    return value


def _name(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or value in {".", ".."}
            or len(value.encode("utf-8")) > 255
            or any(c in value for c in "/\\∕⁄⧸")
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise InvalidIdentifier("Недопустимое имя файла или папки")
    return unicodedata.normalize("NFC", value)


def _manifest(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {"upload_id", "folder_name", "files"}:
        raise InvalidState("Ожидались upload_id, folder_name и список files")
    upload_id = validate_upload_id(body["upload_id"])
    folder_name = _name(body["folder_name"])
    files = body["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise InvalidState(f"В папке должно быть от 1 до {MAX_FILES} файлов")
    entries, seen, total = [], set(), 0
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size"}:
            raise InvalidState("Для каждого файла нужны path и size")
        path, size = entry["path"], entry["size"]
        if not isinstance(path, str) or not 1 <= len(path.encode("utf-8")) <= 2048:
            raise InvalidIdentifier("Недопустимый путь файла")
        parts = path.split("/")
        if len(parts) > 16:
            raise InvalidIdentifier("Слишком много вложенных папок")
        normalized = "/".join(_name(part) for part in parts)
        key = normalized.casefold()
        if key in seen:
            raise Conflict(f"Путь файла указан дважды: {normalized}")
        seen.add(key)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise InvalidState(f"Размер файла не указан: {normalized}")
        if size > MAX_FILE_BYTES:
            raise FileTooLarge("Размер одного файла превышает 100 МиБ")
        total += size
        entries.append({"path": normalized, "size": size})
    if total > MAX_FOLDER_BYTES:
        raise FileTooLarge("Размер папки превышает 20 ГиБ")
    # A file cannot simultaneously be a parent directory of another file.
    for key in seen:
        parts = key.split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise Conflict("Один путь используется и как файл, и как папка")
    return {"upload_id": upload_id, "folder_name": folder_name, "files": entries,
            "total_bytes": total, "version": 1}


class _BoundedInput:
    def __init__(self, source: BinaryIO, expected: int) -> None:
        self.source, self.expected, self.total = source, expected, 0
        self.digest = hashlib.sha256()

    def read(self, size: int) -> bytes:
        chunk = self.source.read(min(size, self.expected - self.total + 1))
        self.total += len(chunk)
        if self.total > self.expected:
            raise FileTooLarge("Размер файла больше указанного в списке загрузки")
        if not chunk and self.total != self.expected:
            raise InvalidState("Файл передан не полностью; повторите загрузку")
        self.digest.update(chunk)
        return chunk


class FolderIntake:
    def __init__(self, orders_root: Path) -> None:
        # The root comes exclusively from the trusted deployment config.
        self.root = SecureRoot(orders_root, writable=True)
        self.orders_root = orders_root
        self._ensure_dir("folders")

    def close(self) -> None:
        self.root.close()

    def _registry(self) -> Registry:
        return Registry(self.orders_root / "registry.db")

    def _ensure_dir(self, relative: str) -> None:
        try:
            self.root.ensure_dir(relative)
        except FileExistsError:
            # Concurrent first uploads can both observe a missing directory.
            # Reopening with SecureRoot also rejects a symlink substituted here.
            os.close(self.root._open_dir(relative))
        parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
        fd = self.root._open_dir(parent)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def _lock(self, directory: str, *, shared: bool = False, name: str = ".lock"):
        parent = self.root._open_dir(directory)
        fd = -1
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise InvalidState("Служебный файл загрузки недоступен")
            fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            yield
        finally:
            if fd >= 0:
                os.close(fd)
            os.close(parent)

    def _read_json(self, path: str) -> dict[str, Any]:
        try:
            value = json.loads(self.root.read_bytes(path, limit=MAX_MANIFEST_BYTES))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidState("Список файлов загрузки повреждён") from exc
        if not isinstance(value, dict):
            raise InvalidState("Список файлов загрузки повреждён")
        return value

    def _session(self, upload_id: str) -> tuple[str, dict[str, Any]]:
        directory = f"folders/{validate_upload_id(upload_id)}"
        return directory, self._read_json(f"{directory}/manifest.json")

    @staticmethod
    def _order_id(manifest: dict[str, Any]) -> str:
        identity = manifest["folder_name"].casefold().encode("utf-8")
        return "folder_" + hashlib.sha256(identity).hexdigest()[:32]

    def _existing(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        try:
            _, state = self._registry().get(self._order_id(manifest))
        except NotFound:
            return None
        intake = state.get("folder_intake", {})
        if intake.get("upload_id") != manifest["upload_id"]:
            raise Conflict(f"Папка «{manifest['folder_name']}» уже загружена. "
                           "Найдите её в списке заказов")
        return state

    def create(self, body: Any) -> dict[str, Any]:
        manifest = _manifest(body)
        directory = f"folders/{manifest['upload_id']}"
        self._ensure_dir(directory)
        with self._lock(directory):
            self._existing(manifest)
            try:
                saved = self._read_json(f"{directory}/manifest.json")
            except NotFound:
                self._ensure_dir(f"{directory}/files")
                self._ensure_dir(f"{directory}/receipts")
                manifest["created_at"] = utcnow()
                self.root.atomic_write(f"{directory}/manifest.json", canonical_json(manifest))
            else:
                if {k: v for k, v in saved.items() if k != "created_at"} != manifest:
                    raise Conflict("Эта загрузка уже имеет другой список файлов")
        return self.status(manifest["upload_id"])

    def status(self, upload_id: str) -> dict[str, Any]:
        directory, manifest = self._session(upload_id)
        existing = self._existing(manifest)
        received = [i for i in range(len(manifest["files"]))
                    if self.root.exists(f"{directory}/receipts/{i}.json")]
        return {"upload_id": upload_id, "received": received, "limits": LIMITS,
                **({"order_id": existing["order_id"]} if existing else {})}

    def upload(self, upload_id: str, index: int, source: BinaryIO) -> dict[str, Any]:
        directory, manifest = self._session(upload_id)
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(manifest["files"]):
            raise InvalidIdentifier("Файл отсутствует в списке загрузки")
        entry = manifest["files"][index]
        # Shared session lock permits concurrent different files; finalize takes
        # its exclusive counterpart. A per-index lock serializes duplicate PUTs.
        with self._lock(directory, shared=True), self._lock(f"{directory}/files", name=f".{index}.lock"):
            bounded = _BoundedInput(source, entry["size"])
            relative = f"{directory}/files/{index}"
            try:
                total, digest = self.root.atomic_copy(relative, bounded)
            except Conflict:
                total, digest = bounded.total, bounded.digest.hexdigest()
                fd = self.root.open_read_fd(relative)
                try:
                    with os.fdopen(fd, "rb", closefd=False) as handle:
                        actual = hashlib.file_digest(handle, "sha256").hexdigest()
                    if os.fstat(fd).st_size != total or actual != digest:
                        raise Conflict("Повторная загрузка содержит другой файл")
                finally:
                    os.close(fd)
            record = {"name": entry["path"].rsplit("/", 1)[-1], "relative_path": entry["path"],
                      "bytes": total, "sha256": digest, "received_at": utcnow(), "index": index}
            receipt = f"{directory}/receipts/{index}.json"
            try:
                self.root.atomic_write(receipt, canonical_json(record))
            except Conflict:
                saved = self._read_json(receipt)
                if any(saved.get(k) != record[k] for k in ("sha256", "bytes", "relative_path")):
                    raise Conflict("Сохранённый файл отличается от повторной загрузки")
            return {"ok": True, "index": index}

    def complete(self, upload_id: str) -> dict[str, Any]:
        directory, manifest = self._session(upload_id)
        with self._lock(directory):
            existing = self._existing(manifest)
            if existing:
                return {"order_id": existing["order_id"]}
            files = []
            for i, entry in enumerate(manifest["files"]):
                try:
                    record = self._read_json(f"{directory}/receipts/{i}.json")
                    fd = self.root.open_read_fd(f"{directory}/files/{i}")
                except NotFound as exc:
                    raise Conflict("Загрузите все файлы папки перед созданием заказа") from exc
                try:
                    if record.get("bytes") != entry["size"] or os.fstat(fd).st_size != entry["size"]:
                        raise InvalidState("Размер сохранённого файла изменился; повторите загрузку")
                finally:
                    os.close(fd)
                files.append(record)
            state = {"customer": {}, "status": "draft", "source_files": [], "warnings": [],
                     "timestamps": {}, "provenance": {"created_by": "panel_folder_intake"},
                     "folder_intake": {"version": 1, "upload_id": upload_id,
                                       "folder_name": manifest["folder_name"], "files": files,
                                       "total_bytes": manifest["total_bytes"]}}
            try:
                _, saved = self._registry().create(self._order_id(manifest), state)
            except Conflict:
                saved = self._existing(manifest)
                if saved is None:
                    raise
            return {"order_id": saved["order_id"]}

    @staticmethod
    def summary(state: dict[str, Any]) -> dict[str, Any]:
        intake = state["folder_intake"]
        return {"order_id": state["order_id"], "folder_name": intake["folder_name"],
                "file_count": len(intake["files"]), "total_bytes": intake["total_bytes"],
                "created_at": state["timestamps"]["created_at"], "status": state["status"]}

    def list(self) -> dict[str, Any]:
        orders = [self.summary(state) for state in self._registry().all_states()
                  if isinstance(state.get("folder_intake"), dict)]
        orders.sort(key=lambda order: order["created_at"], reverse=True)
        return {"orders": orders, "limits": LIMITS}

    def detail(self, order_id: str) -> dict[str, Any]:
        validate_id(order_id, field="order_id")
        _, state = self._registry().get(order_id)
        if not isinstance(state.get("folder_intake"), dict):
            raise NotFound("Папка заказа не найдена")
        return {**self.summary(state), "source_files": state["folder_intake"]["files"], "result_files": []}

    def open_file(self, order_id: str, index: int) -> tuple[int, dict[str, Any]]:
        validate_id(order_id, field="order_id")
        _, state = self._registry().get(order_id)
        intake = state.get("folder_intake")
        if not isinstance(intake, dict) or index < 0 or index >= len(intake["files"]):
            raise NotFound("Файл заказа не найден")
        upload_id = validate_upload_id(intake["upload_id"])
        fd = self.root.open_read_fd(f"folders/{upload_id}/files/{index}")
        info = intake["files"][index]
        if os.fstat(fd).st_size != info["bytes"]:
            os.close(fd)
            raise InvalidState("Размер сохранённого файла изменился")
        return fd, info
