"""``korra backup --to-drive``: a copy of the installation on the owner's Google Drive.

The archive is produced as a stream and uploaded with the Drive v3 resumable
protocol while it is being written, so no ZIP is ever stored on the server's disk
(only temporary SQLite snapshots, as in a normal backup).
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from korra_constants import display_hermes_home, get_default_hermes_root


API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

FOLDER_NAME = "Korra — резервные копии"
FOLDER_MIME = "application/vnd.google-apps.folder"
ZIP_MIME = "application/zip"
PROP_BACKUP = "korra_backup"
PROP_FOLDER = "korra_backup_folder"
PROP_INSTALL = "korra_install_id"

_GRANULARITY = 256 * 1024
UPLOAD_CHUNK = 32 * _GRANULARITY  # 8 MiB
_FILE_FIELDS = "id,name,size,md5Checksum,webViewLink,createdTime,appProperties"
_RETRY_PAUSES = (1, 2, 4, 8, 16, 32, 60, 60)
_DEFAULT_KEEP = 4
_TRANSIENT_STATUSES = (408, 429, 500, 502, 503, 504)

TokenProvider = Callable[[bool], str]


class DriveBackupError(RuntimeError):
    """A problem the owner can understand; the message is shown as is."""




class DriveClient:
    """Small Drive v3 client: folders, listing, deletion, quota, resumable upload."""

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        api_base: str = API_BASE,
        upload_base: str = UPLOAD_BASE,
        http: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
        pauses: tuple[float, ...] = _RETRY_PAUSES,
    ) -> None:
        self._token_provider = token_provider
        self.api_base = api_base.rstrip("/")
        self.upload_base = upload_base.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0))
        self._sleep = sleep
        self._pauses = pauses

    def close(self) -> None:
        self._http.close()

    def _headers(self, force_token: bool = False, extra: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token_provider(force_token)}"}
        if extra:
            headers.update(extra)
        return headers

    def request(
        self, method: str, url: str, *, headers: Optional[dict] = None, retry: bool = True, **kwargs
    ) -> httpx.Response:
        """One call: a single token refresh on 401; with *retry*, pauses on 429/5xx/network errors."""
        force_token = False
        for attempt in range(len(self._pauses) + 1):
            try:
                response = self._http.request(
                    method, url, headers=self._headers(force_token, headers), **kwargs
                )
            except httpx.TransportError as exc:
                if not retry:
                    raise
                if attempt >= len(self._pauses):
                    raise DriveBackupError(f"нет связи с Google Диском ({exc.__class__.__name__})") from exc
                self._sleep(self._pauses[attempt])
                continue
            if response.status_code == 401 and not force_token:
                force_token = True
                continue
            if retry and response.status_code in _TRANSIENT_STATUSES and attempt < len(self._pauses):
                self._sleep(self._pauses[attempt])
                continue
            return response
        raise DriveBackupError("Google Диск не отвечает")

    def _json(self, response: httpx.Response, what: str) -> dict:
        if response.status_code == 403 and "storageQuotaExceeded" in response.text:
            raise DriveBackupError("на Google Диске закончилось место")
        if response.status_code in (401, 403):
            raise DriveBackupError(
                f"Google Диск отказал в доступе ({what}). Подключите Google заново в «Ключи и доступы» "
                "и разрешите доступ к Диску"
            )
        if response.status_code >= 300:
            raise DriveBackupError(f"Google Диск вернул ошибку {response.status_code} ({what})")
        try:
            return response.json()
        except ValueError as exc:
            raise DriveBackupError(f"Google Диск вернул непонятный ответ ({what})") from exc

    def free_space(self) -> Optional[int]:
        """Free bytes in the account, or ``None`` when the quota is unlimited."""
        data = self._json(
            self.request("GET", f"{self.api_base}/about", params={"fields": "storageQuota"}),
            "проверка места",
        )
        quota = data.get("storageQuota") or {}
        limit = quota.get("limit")
        if not limit:
            return None
        return max(0, int(limit) - int(quota.get("usage") or 0))

    def _list(self, query: str, fields: str = _FILE_FIELDS) -> list[dict]:
        found: list[dict] = []
        token = None
        while True:
            params: dict[str, Any] = {
                "q": query,
                "fields": f"nextPageToken,files({fields})",
                "pageSize": 100,
                "spaces": "drive",
            }
            if token:
                params["pageToken"] = token
            data = self._json(self.request("GET", f"{self.api_base}/files", params=params), "список файлов")
            found.extend(data.get("files") or [])
            token = data.get("nextPageToken")
            if not token:
                return found

    def ensure_folder(self) -> str:
        query = (
            f"mimeType='{FOLDER_MIME}' and trashed=false and 'root' in parents and "
            f"appProperties has {{ key='{PROP_FOLDER}' and value='1' }}"
        )
        existing = self._list(query, "id,name")
        if existing:
            return existing[0]["id"]
        created = self._json(
            self.request(
                "POST",
                f"{self.api_base}/files",
                params={"fields": "id"},
                json={
                    "name": FOLDER_NAME,
                    "mimeType": FOLDER_MIME,
                    "parents": ["root"],
                    "appProperties": {PROP_FOLDER: "1"},
                },
            ),
            "создание папки",
        )
        return created["id"]

    def list_backups(self, folder_id: str, install_id: str) -> list[dict]:
        query = (
            f"'{folder_id}' in parents and trashed=false and "
            f"appProperties has {{ key='{PROP_BACKUP}' and value='1' }} and "
            f"appProperties has {{ key='{PROP_INSTALL}' and value='{install_id}' }}"
        )
        return self._list(query)

    def delete(self, file_id: str) -> None:
        response = self.request("DELETE", f"{self.api_base}/files/{file_id}")
        if response.status_code not in (200, 204, 404):
            raise DriveBackupError(f"не удалось удалить старую копию (ошибка {response.status_code})")

    def get_file(self, file_id: str) -> dict:
        return self._json(
            self.request("GET", f"{self.api_base}/files/{file_id}", params={"fields": _FILE_FIELDS}),
            "проверка файла",
        )

    def start_upload(self, name: str, folder_id: str, install_id: str) -> "UploadSession":
        response = self.request(
            "POST",
            f"{self.upload_base}/files",
            params={"uploadType": "resumable", "fields": _FILE_FIELDS},
            headers={"X-Upload-Content-Type": ZIP_MIME},
            json={
                "name": name,
                "mimeType": ZIP_MIME,
                "parents": [folder_id],
                "appProperties": {PROP_BACKUP: "1", PROP_INSTALL: install_id},
            },
        )
        self._json_ok(response, "начало загрузки")
        location = response.headers.get("Location")
        if not location:
            raise DriveBackupError("Google Диск не дал адрес для загрузки")
        return UploadSession(self, location)

    def _json_ok(self, response: httpx.Response, what: str) -> None:
        if response.status_code >= 300:
            self._json(response, what)


class UploadSession:
    """One resumable upload: chunks are confirmed by the server and re-sent on failure."""

    def __init__(self, client: DriveClient, url: str) -> None:
        self._client = client
        self._url = url

    def confirmed_bytes(self) -> tuple[int, Optional[dict]]:
        """Ask the server how much it has: ``(bytes, finished_resource_or_None)``."""
        response = self._client.request(
            "PUT", self._url, headers={"Content-Range": "bytes */*"}, content=b""
        )
        return self._interpret(response)

    def _interpret(self, response: httpx.Response) -> tuple[int, Optional[dict]]:
        if response.status_code in (200, 201):
            try:
                return -1, response.json()
            except ValueError:
                return -1, {}
        if response.status_code == 308:
            received = response.headers.get("Range")
            match = re.fullmatch(r"bytes=0-(\d+)", (received or "").strip())
            return (int(match.group(1)) + 1 if match else 0), None
        if response.status_code in (404, 410):
            raise DriveBackupError("Google Диск закрыл сессию загрузки; повторите команду позже")
        if response.status_code == 403 and "storageQuotaExceeded" in response.text:
            raise DriveBackupError("на Google Диске закончилось место")
        if response.status_code in (401, 403):
            raise DriveBackupError(
                "Google Диск отказал в доступе. Подключите Google заново в «Ключи и доступы»"
            )
        raise DriveBackupError(f"Google Диск вернул ошибку {response.status_code} при загрузке")

    def send(self, offset: int, data: bytes, total: Optional[int]) -> tuple[int, Optional[dict]]:
        """Send *data* located at *offset*; returns ``(confirmed_end, resource_if_finished)``.

        *total* is the final length when this is the last chunk, else ``None``.
        After a failure the server is asked how much it has and only the rest is
        re-sent; the upload never restarts from the beginning.
        """
        end = offset + len(data)
        position = offset
        trouble = 0
        pauses = self._client._pauses
        while True:
            chunk = data[position - offset:]
            if chunk:
                content_range = f"bytes {position}-{end - 1}/{total if total is not None else '*'}"
            else:
                content_range = f"bytes */{total}"
            try:
                response = self._client.request(
                    "PUT", self._url, headers={"Content-Range": content_range},
                    content=chunk, retry=False,
                )
                if response.status_code in _TRANSIENT_STATUSES:
                    raise httpx.TransportError(f"HTTP {response.status_code}")
                confirmed, resource = self._interpret(response)
            except httpx.TransportError:
                trouble += 1
                if trouble > len(pauses):
                    raise DriveBackupError("нет связи с Google Диском: загрузка прервана")
                self._client._sleep(pauses[trouble - 1])
                confirmed, resource = self.confirmed_bytes()
            if resource is not None:
                return end, resource
            if confirmed < position:
                raise DriveBackupError("Google Диск потерял часть загруженных данных; повторите команду")
            if total is None and confirmed >= end:
                return end, None
            if confirmed == position and chunk:
                trouble += 1
            elif not chunk:
                trouble += 1
            if trouble > len(pauses):
                raise DriveBackupError("Google Диск не принимает данные: загрузка прервана")
            position = confirmed

    def cancel(self) -> None:
        try:
            self._client._http.request(
                "DELETE", self._url, headers=self._client._headers(), timeout=20.0
            )
        except Exception:
            pass


class ResumableSink:
    """Write-only, non-seekable file for ``zipfile`` that uploads what is written."""

    def __init__(
        self,
        session: UploadSession,
        *,
        chunk_size: Optional[int] = None,
        progress: Optional[Callable[[int], None]] = None,
    ) -> None:
        chunk_size = chunk_size or UPLOAD_CHUNK
        if chunk_size % _GRANULARITY:
            raise ValueError("chunk_size must be a multiple of 256 KiB")
        self._session = session
        self._chunk_size = chunk_size
        self._progress = progress
        self._buffer = bytearray()
        self._sent = 0
        self._position = 0
        self._md5 = hashlib.md5()
        self._closed = False
        self.resource: Optional[dict] = None

    @property
    def md5_hex(self) -> str:
        return self._md5.hexdigest()

    @property
    def size(self) -> int:
        return self._position

    @property
    def closed(self) -> bool:
        return self._closed

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def write(self, data) -> int:
        if self._closed:
            raise ValueError("write to closed ResumableSink")
        view = memoryview(data).cast("B")
        self._md5.update(view)
        self._buffer += view
        self._position += len(view)
        # Keep at least one byte back so close() always sends a closing chunk.
        while len(self._buffer) > self._chunk_size:
            chunk = bytes(self._buffer[: self._chunk_size])
            del self._buffer[: self._chunk_size]
            self._sent, _ = self._session.send(self._sent, chunk, None)
            if self._progress:
                self._progress(self._sent)
        return len(view)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        total = self._position
        _, self.resource = self._session.send(self._sent, bytes(self._buffer), total)
        self._sent = total
        self._buffer.clear()
        if self._progress:
            self._progress(total)
        if self.resource is None:
            raise DriveBackupError("Google Диск не подтвердил окончание загрузки")


def installation_label(config: dict, install_id: str) -> str:
    raw = str((config.get("backup") or {}).get("installation_name") or "").strip()
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "-", raw).strip("-.")[:40]
    return cleaned or install_id[:8]


def backup_file_name(label: str, moment: datetime) -> str:
    return f"korra-{label}-{moment:%Y-%m-%d-%H%M}.zip"


def _drive_keep(config: dict) -> int:
    try:
        return max(1, int((config.get("backup") or {}).get("drive_keep", _DEFAULT_KEEP)))
    except (TypeError, ValueError):
        return _DEFAULT_KEEP


def _recipient_key(config: dict):
    from korra_cli.backup_crypto import (
        DEFAULT_OPERATOR_PUBLIC_KEY,
        SecretsCryptoError,
        parse_public_key,
    )

    value = str((config.get("backup") or {}).get("operator_public_key") or "").strip()
    try:
        return parse_public_key(value or DEFAULT_OPERATOR_PUBLIC_KEY)
    except SecretsCryptoError as exc:
        raise DriveBackupError(f"{exc} (backup.operator_public_key в config.yaml)") from exc


def google_token_provider() -> TokenProvider:
    """Token of the owner's Google connection (the root grant of the google-workspace skill)."""
    from korra_cli import google_workspace as gw

    root = get_default_hermes_root()
    state: dict[str, Any] = {"token": None, "at": 0.0}

    def provider(force: bool = False) -> str:
        fresh = state["token"] is not None and time.monotonic() - state["at"] < 1500
        if fresh and not force:
            return state["token"]
        try:
            creds = gw._credentials(root, required_service="drive")
            if force and creds.refresh_token:
                from google.auth.transport.requests import Request

                creds.refresh(Request())
        except gw.GoogleWorkspaceError as exc:
            raise DriveBackupError(_google_error_text(exc)) from exc
        except ImportError as exc:
            raise DriveBackupError(
                "в этой установке нет библиотек Google; подключите Google в «Ключи и доступы»"
            ) from exc
        state["token"], state["at"] = creds.token, time.monotonic()
        return state["token"]

    return provider


def _google_error_text(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    connect = "Подключите Google в «Ключи и доступы» и разрешите доступ к Google Диску"
    if code == "not_authenticated":
        return f"Google не подключён. {connect}."
    if code in {"service_not_selected", "scope_contract_invalid", "scope_mismatch"}:
        return f"Текущее подключение Google не даёт доступа к Диску. {connect}."
    if code == "token_refresh_failed":
        return "Доступ Google истёк или был отозван. Подключите Google заново в «Ключи и доступы»."
    return f"Не удалось получить доступ к Google ({exc}). {connect}."


def _estimate_bytes(files, externals) -> int:
    total = 0
    for abs_path, _ in [*files, *externals]:
        try:
            total += abs_path.stat().st_size
        except OSError:
            continue
    return total


def run_backup_to_drive(
    hermes_root: Path,
    *,
    client: Optional[DriveClient] = None,
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> None:
    """Make the copy; the caller holds ``_backup_operation_lock``. Exits with 1 on failure."""
    from korra_cli import backup as backup_mod
    from korra_cli.install_identity import read_or_create_install_id

    try:
        _run(hermes_root, backup_mod, read_or_create_install_id, client, config, now)
    except DriveBackupError as exc:
        print(f"Ошибка: {str(exc).rstrip('.')}.")
        raise SystemExit(1) from exc


def _run(hermes_root, backup_mod, read_install_id, client, config, now) -> None:
    if config is None:
        from korra_cli.config import load_config_readonly

        config = load_config_readonly()
    install_id = read_install_id(hermes_root)
    if not install_id:
        raise DriveBackupError("не удалось определить идентификатор установки (файл install_id)")
    recipient = _recipient_key(config)
    own_client = client is None
    if client is None:
        client = DriveClient(google_token_provider())
    try:
        _upload(hermes_root, backup_mod, install_id, recipient, client, config, now)
    finally:
        if own_client:
            client.close()


def _upload(hermes_root, backup_mod, install_id, recipient, client, config, now) -> None:
    import tempfile

    print(f"Проверяю файлы в {display_hermes_home()}…")
    files, skipped_dirs = backup_mod._scan_backup_files(hermes_root)
    externals, skipped_external = backup_mod._scan_external_files()
    if not files and not externals:
        print("Нет файлов для резервного копирования.")
        return

    need = _estimate_bytes(files, externals)
    free = client.free_space()
    if free is not None and need > free:
        mb = backup_mod._format_size
        raise DriveBackupError(
            f"на вашем Google Диске мало места: для копии нужно до {mb(need)}, свободно {mb(free)}. "
            "Освободите место на Диске и повторите команду. Копия не начата"
        )

    moment = now or datetime.now()
    label = installation_label(config, install_id)
    name = backup_file_name(label, moment)
    folder_id = client.ensure_folder()
    session = client.start_upload(name, folder_id, install_id)
    print(f"Загружаю копию на Google Диск: {name} (файлов: {len(files) + len(externals)})…")

    last_report = [0]

    def report(sent: int) -> None:
        if sent - last_report[0] >= 64 * 1024 * 1024:
            last_report[0] = sent
            print(f"  Загружено: {backup_mod._format_size(sent)}")

    sink = ResumableSink(session, progress=report)
    started = time.monotonic()
    snapshot_dir = Path(tempfile.mkdtemp(prefix="korra-backup-"))
    try:
        stats = backup_mod._write_split_archive(
            sink,
            files,
            externals,
            recipient,
            snapshot_dir,
            progress=lambda done, total: print(f"  Файлов: {done}/{total}…"),
        )
        sink.close()
    except BaseException:
        session.cancel()
        raise
    finally:
        import shutil

        shutil.rmtree(snapshot_dir, ignore_errors=True)

    resource = sink.resource or {}
    file_id = resource.get("id")
    if not file_id:
        raise DriveBackupError("Google Диск не вернул данные загруженного файла")
    meta = _checked_resource(client, resource, file_id, sink.md5_hex)

    removed = 0
    kept_note = ""
    if stats.errors:
        kept_note = "Старые копии не удалялись: новая копия неполная."
    else:
        removed = _rotate(client, folder_id, install_id, file_id, _drive_keep(config))

    elapsed = time.monotonic() - started
    mb = backup_mod._format_size
    print()
    print("Копия на Google Диске готова." if not stats.errors else "Копия на Google Диске создана не полностью.")
    print(f"  Файл:     {meta.get('name', name)}")
    print(f"  Дата:     {moment:%d.%m.%Y %H:%M}")
    print(f"  Размер:   {mb(sink.size)}")
    print(f"  Файлов:   {stats.files}")
    print(f"  Секретов зашифровано: {stats.secret_files}")
    if meta.get("webViewLink"):
        print(f"  Ссылка:   {meta['webViewLink']}")
    print(f"  Время:    {elapsed:.1f} с")
    if removed:
        print(f"  Удалено старых копий: {removed} (хранится последних: {_drive_keep(config)})")
    if kept_note:
        print(f"  {kept_note}")
    if stats.secret_files:
        print(
            "\nТокены, ключи и доступы лежат в копии в зашифрованном виде. Расшифровать их и "
            "перенести установку на новый сервер может только команда Korra."
        )
    if stats.fts_notes:
        print("\n  Поисковый индекс FTS5:")
        for note in stats.fts_notes:
            print(f"  {note}")
    if skipped_external:
        print(f"\n  Пропущено внешних путей провайдера памяти: {len(skipped_external)} (вне домашней папки).")
    if stats.errors:
        print(f"\n  Не удалось добавить файлов: {len(stats.errors)}:")
        for line in stats.errors[:10]:
            print(line)
        if len(stats.errors) > 10:
            print(f"  … и ещё {len(stats.errors) - 10}")


def _checked_resource(client: DriveClient, resource: dict, file_id: str, local_md5: str) -> dict:
    meta = resource
    for attempt in range(4):
        if meta.get("md5Checksum") and meta.get("webViewLink"):
            break
        client._sleep(2 * attempt)
        meta = client.get_file(file_id)
    remote = (meta.get("md5Checksum") or "").lower()
    if not remote:
        raise DriveBackupError(
            "Google Диск не вернул контрольную сумму загруженной копии; проверить её не удалось. "
            "Старые копии не тронуты"
        )
    if remote != local_md5.lower():
        try:
            client.delete(file_id)
        except DriveBackupError:
            pass
        raise DriveBackupError(
            "контрольная сумма копии на Google Диске не совпала с отправленной; повреждённый "
            "файл удалён, старые копии не тронуты. Повторите команду"
        )
    return meta


def _rotate(client: DriveClient, folder_id: str, install_id: str, new_id: str, keep: int) -> int:
    """Delete this installation's own older backups beyond the last *keep*."""
    own = []
    for item in client.list_backups(folder_id, install_id):
        props = item.get("appProperties") or {}
        if props.get(PROP_BACKUP) == "1" and props.get(PROP_INSTALL) == install_id:
            own.append(item)
    own.sort(key=lambda item: item.get("createdTime") or "", reverse=True)
    survivors = [item for item in own if item["id"] == new_id]
    survivors += [item for item in own if item["id"] != new_id]
    removed = 0
    for item in survivors[keep:]:
        if item["id"] == new_id:
            continue
        client.delete(item["id"])
        removed += 1
    return removed
