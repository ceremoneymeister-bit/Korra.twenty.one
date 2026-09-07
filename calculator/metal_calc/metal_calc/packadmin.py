"""Публикация паков данных предприятия — единственный путь записи ставок.

Отдельный модуль, а не метод хранилища, и это несущее решение. Хранилище
:class:`metal_calc.packs2.PipelinePackStore` живёт внутри MCP-сервера, до
которого дотягивается модель; здесь — код, который вызывает только CLI под
человеком. У MCP нет не только прав на запись, но и самого кода записи.

Дисциплина публикации:

* **ревизии иммутабельны.** Пишем через ``SecureRoot.atomic_write``, а он
  линкует и падает на занятом имени. Затереть вчерашние цены нельзя даже
  ошибкой в коде — только завести новые;
* **валидируем перед записью и ещё раз перед активацией.** Второе не
  паранойя: активируют и старую ревизию (откат), а её файл мог быть записан
  прежней версией правил;
* **журнал append-only.** Каталог ставок теперь доступен на запись изнутри
  контейнера, и это осознанная уступка ради того, чтобы методолог вводил
  данные сам. Журнал — то, чем мы за неё платим: расхождение sha256 активной
  ревизии с последней записью означает запись мимо этого модуля.

Автор ревизии — идентификатор (``tg758050420``), никогда не имя человека:
``image/privacy_scan.py`` не пускает имена в исходники и артефакты.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from .errors import Conflict, InvalidIdentifier, InvalidRatePack, NotFound
from .packs2 import (
    ACTIVE_POINTER,
    _validate_pack2,
    implausible_values,
    norm_coverage,
)
from .securefs import SecureRoot
from .util import sha256_bytes, utcnow, validate_revision

JOURNAL = "_journal.jsonl"
DRAFT = "_draft.json"

#: Служебные имена: их нельзя запросить как ревизию, потому что
#: ``util.REVISION_RE`` требует первым символом буквоцифру.
RESERVED = frozenset({ACTIVE_POINTER, JOURNAL, DRAFT})

#: Автор — короткий машинный идентификатор, не имя.
AUTHOR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")

MAX_PACK_BYTES = 1024 * 1024
MAX_NOTE_CHARS = 500

#: Сколько секунд подряд пробовать занятое имя ревизии.
MAX_REVISION_ATTEMPTS = 30

#: Мягкий потолок: 3,7 КБ на ревизию, поэтому места хватит надолго. Смысл
#: предупреждения — заметить не разросшийся диск, а зациклившегося клиента
#: панели, который публикует по ревизии в секунду.
REVISION_COUNT_WARNING = 2000


def validate_author(value: str) -> str:
    if not isinstance(value, str) or not AUTHOR_RE.fullmatch(value):
        raise InvalidIdentifier("Invalid author id")
    return value


def parse_pack_bytes(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_PACK_BYTES:
        raise InvalidRatePack("Pack is too large")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRatePack(f"Pack is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidRatePack("Pipeline pack must be an object")
    return data


def _canonical_bytes(data: dict[str, Any]) -> bytes:
    """Байты, которые лягут на диск.

    Ровно они же хешируются: ``pack_sha256`` в стадии заказа считается от
    содержимого файла, поэтому расхождение «что проверили» и «что записали»
    сделало бы провенанс ложным.
    """
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")


def validate_pack(raw: bytes, *, revision: str | None = None) -> dict[str, Any]:
    """Проверить кандидата дословными правилами движка. Ничего не пишет.

    ``revision`` внутри пака валидатор сверяет с запрошенной, а на проверке
    имени будущей ревизии ещё нет — поэтому сверяем с тем, что в самом паке.
    """
    data = parse_pack_bytes(raw)
    declared = data.get("revision")
    if revision is None:
        if not isinstance(declared, str):
            raise InvalidRatePack("Pack revision is required")
        revision = validate_revision(declared)
    payload = _canonical_bytes(data)
    pack = _validate_pack2(data, sha256_bytes(payload), revision)
    # Пробелы в покрытии норм не делают пак невалидным — предприятие вправе
    # не обрабатывать нержавейку на эрозии. Но узнать о них человек должен
    # здесь, при проверке, а не на третьей стадии заказа, когда заготовка и
    # маршрут уже утверждены и переделывать придётся обе.
    return {
        "ok": True,
        "revision": revision,
        "sha256": sha256_bytes(payload),
        "norms_missing": norm_coverage(pack),
        "implausible": implausible_values(pack),
    }


#: Автоперенос v2→v3: какие коды каталога получают операции заготовки v2.
#: pipe_cut двусмыслен (механический труборез или лазерный) — если BLANK.CUTOFF
#: уже занят лентопилом, pipe_cut уезжает в CUT.LASER.TUBE с предупреждением.
_BLANK_TO_PARK = {
    "laser": "CUT.LASER.SHEET",
    "plasma": "CUT.PLASMA",
    "waterjet": "CUT.WATERJET",
    "bench": "FINISH.FITTER",
}

#: Семейство формул v2 → представитель в каталоге. Выбор ЧПУ-кода — заглушка
#: автопереноса, а не знание о предприятии: черновик обязателен к правке.
_MACHINE_TO_PARK = {
    "turning": "MACHINING.TURN.CNC",
    "milling": "MACHINING.MILL.CNC",
    "grinding": "MACHINING.GRIND.GENERAL",
    "edm": "MACHINING.EDM.GENERAL",
}


def upgrade_pack_v2_to_v3(raw: bytes) -> dict[str, Any]:
    """Черновик пака v3 из пака v2. Ничего не пишет и не публикует.

    Автоперенос честен ровно настолько, насколько честны его допущения,
    поэтому каждое допущение возвращается предупреждением, а каждая
    перенесённая запись парка помечена «проверьте». Публикует человек —
    и валидатор v3 всё равно проверит результат дословно.
    """
    data = parse_pack_bytes(raw)
    if data.get("schema_version") in {3, 4}:
        # «Уже enterprise» — не значит «валидный»: ok без проверки прятал бы
        # битую сетку или пустой парк до самой публикации (Codex M11).
        declared_v3 = data.get("revision")
        revision_v3 = (
            validate_revision(declared_v3) if isinstance(declared_v3, str) else "draft-upgrade"
        )
        data.setdefault("revision", revision_v3)
        _validate_pack2(data, sha256_bytes(_canonical_bytes(data)), data["revision"])
        return {"ok": True, "already_v3": True, "pack": data, "warnings": []}
    declared = data.get("revision")
    revision = validate_revision(declared) if isinstance(declared, str) else "draft-upgrade"
    data.setdefault("revision", revision)
    _validate_pack2(data, sha256_bytes(_canonical_bytes(data)), data["revision"])

    warnings: list[str] = []
    park: dict[str, Any] = {}
    blank_ops = data.get("blank_ops") or {}
    if "bandsaw" in blank_ops:
        park["BLANK.CUTOFF"] = {
            "method_code": "BAND_SAW",
            "rate_id": "blank:bandsaw",
            "note": "автоперенос из v2 — проверьте",
        }
        if "pipe_cut" in blank_ops:
            park["CUT.LASER.TUBE"] = {
                "rate_id": "blank:pipe_cut",
                "note": "автоперенос из v2 — проверьте",
            }
            warnings.append(
                "pipe_cut перенесён как CUT.LASER.TUBE, потому что BLANK.CUTOFF "
                "уже занят лентопилом; если труборез механический — поправьте "
                "парк руками"
            )
    elif "pipe_cut" in blank_ops:
        park["BLANK.CUTOFF"] = {
            "method_code": "MECHANICAL_PIPE_CUTTER",
            "rate_id": "blank:pipe_cut",
            "note": "автоперенос из v2 — проверьте",
        }
    for op_code, park_code in _BLANK_TO_PARK.items():
        if op_code in blank_ops:
            park[park_code] = {
                "rate_id": f"blank:{op_code}",
                "note": "автоперенос из v2 — проверьте",
            }
    machines = data.get("machines") or {}
    for op_code, park_code in _MACHINE_TO_PARK.items():
        if op_code in machines:
            park[park_code] = {
                "rate_id": f"machine:{op_code}",
                "note": "автоперенос из v2 — уточните конкретный станок",
            }
            warnings.append(
                f"«{op_code}» перенесён обобщённым кодом {park_code} — если "
                f"парк предприятия различает станки этой группы, разведите их"
            )
    if "drilling" in machines:
        warnings.append(
            "Для сверления в каталоге операций нет отдельного кода: переходы "
            "сверления живут внутри токарных/фрезерных операций, ставка "
            "machine:drilling остаётся доступной по адресу"
        )

    candidate = dict(data)
    candidate["schema_version"] = 3
    candidate["process_park"] = park
    candidate["rate_registry"] = {}
    _validate_pack2(
        candidate, sha256_bytes(_canonical_bytes(candidate)), candidate["revision"]
    )
    return {"ok": True, "already_v3": False, "pack": candidate, "warnings": warnings}


def make_revision(author: str, *, now: str | None = None) -> str:
    """``r20260827-091500-tg758050420`` — сортируется как хронология."""
    stamp = (now or utcnow()).replace("-", "").replace(":", "").replace("Z", "")
    day, _, clock = stamp.partition("T")
    return validate_revision(f"r{day}-{clock}-{author}")


def _shift_seconds(moment: str, seconds: int) -> str:
    """Сдвинуть отметку времени, сохранив её формат."""
    parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    shifted = parsed + timedelta(seconds=seconds)
    return shifted.isoformat(timespec="seconds").replace("+00:00", "Z")


class PackPublisher:
    def __init__(self, root: SecureRoot) -> None:
        if not root.writable:
            raise InvalidRatePack("Rates root is read-only")
        self.root = root

    # -- журнал ---------------------------------------------------------

    def _append_journal(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        self.root.append_bytes(JOURNAL, line.encode("utf-8"), mode=0o640)

    def journal(self) -> list[dict[str, Any]]:
        try:
            raw = self.root.read_bytes(JOURNAL, limit=8 * 1024 * 1024)
        except (NotFound, ValueError):
            return []
        entries: list[dict[str, Any]] = []
        for line in raw.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Битую строку пропускаем молча: журнал — свидетельство, а не
                # источник правды, и одна повреждённая запись не должна делать
                # недоступной всю историю.
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    # -- указатель ------------------------------------------------------

    def active_revision(self) -> str | None:
        try:
            raw = self.root.read_bytes(ACTIVE_POINTER, limit=64 * 1024)
        except (NotFound, ValueError):
            return None
        try:
            pointer = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(pointer, dict):
            return None
        revision = pointer.get("revision")
        return revision if isinstance(revision, str) else None

    def _set_active(self, revision: str, sha256: str, author: str) -> None:
        pointer = {
            "revision": revision,
            "sha256": sha256,
            "activated_at": utcnow(),
            "activated_by": author,
        }
        payload = json.dumps(pointer, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        self.root.atomic_replace(ACTIVE_POINTER, payload.encode("utf-8"), mode=0o640)

    # -- операции -------------------------------------------------------

    def publish(
        self,
        raw: bytes,
        *,
        author: str,
        note: str = "",
        activate: bool = True,
        now: str | None = None,
    ) -> dict[str, Any]:
        author = validate_author(author)
        note = (note or "").strip()[:MAX_NOTE_CHARS]
        data = parse_pack_bytes(raw)

        # Имя ревизии проставляем мы, а не автор пака: иначе панель могла бы
        # опубликовать содержимое под чужим именем и разойтись с журналом.
        #
        # Отметка времени секундная, а два сохранения подряд — обычное дело
        # (исправила опечатку, сохранила снова). Занятое имя не ошибка
        # человека, поэтому не показываем её человеку: сдвигаем секунду и
        # пробуем дальше. Иммутабельность истории при этом сохраняется —
        # именно `atomic_write` и отказывается перезаписывать.
        moment = now or utcnow()
        for attempt in range(MAX_REVISION_ATTEMPTS):
            revision = make_revision(author, now=_shift_seconds(moment, attempt))
            data["revision"] = revision
            payload = _canonical_bytes(data)
            digest = sha256_bytes(payload)
            pack = _validate_pack2(data, digest, revision)
            try:
                self.root.atomic_write(f"{revision}.json", payload, mode=0o640)
                break
            except Conflict:
                continue
        else:
            raise Conflict("Не удалось подобрать имя ревизии — попробуйте ещё раз")
        previous = self.active_revision()
        self._append_journal(
            {
                "at": utcnow(),
                "action": "publish",
                "revision": revision,
                "sha256": digest,
                "author": author,
                "note": note,
                "prev_active": previous,
            }
        )
        if activate:
            self.activate(revision, author=author, _validated=(digest, payload, pack.status))
        # Предупреждения — в ответе публикации, а не только у «Проверить»:
        # человек, жмущий сразу «Опубликовать», прежде не видел ни опечатку в
        # разряде ставки, ни пробелы покрытия норм — пак уходил в бой молча.
        return {
            "ok": True,
            "revision": revision,
            "sha256": digest,
            "active": activate,
            "prev_active": previous,
            "implausible": implausible_values(pack),
            "norms_missing": norm_coverage(pack),
        }

    def activate(
        self,
        revision: str,
        *,
        author: str,
        _validated: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any]:
        author = validate_author(author)
        revision = validate_revision(revision)
        if _validated is None:
            # Откат на прежнюю ревизию перечитывает и перевалидирует файл:
            # он мог быть записан правилами прошлой версии кода, и молча
            # вернуть в бой то, что сегодня невалидно, — худший сценарий.
            try:
                raw = self.root.read_bytes(f"{revision}.json", limit=MAX_PACK_BYTES)
            except (NotFound, ValueError) as exc:
                raise InvalidRatePack(f"Ревизия {revision} не найдена") from exc
            data = parse_pack_bytes(raw)
            digest = sha256_bytes(raw)
            status = _validate_pack2(data, digest, revision).status
        else:
            digest, _, status = _validated
        if status != "active":
            # Действующий образец останавливает все расчёты сразу: каждый
            # денежный инструмент откажет, а справочник выглядит настроенным.
            raise InvalidRatePack(
                "Ревизия со статусом «template» — образец, его нельзя делать "
                "действующим. Заполните ставки и опубликуйте пак со статусом "
                "active."
            )

        previous = self.active_revision()
        self._set_active(revision, digest, author)
        self._append_journal(
            {
                "at": utcnow(),
                "action": "activate",
                "revision": revision,
                "sha256": digest,
                "author": author,
                "note": "",
                "prev_active": previous,
            }
        )
        return {"ok": True, "revision": revision, "sha256": digest, "prev_active": previous}

    def revisions(self) -> dict[str, Any]:
        """История: файлы на диске, обогащённые журналом.

        Основа — диск, а не журнал: файл ревизии есть даже тогда, когда
        журнальная запись потерялась, и показать его важнее, чем сохранить
        стройность списка.
        """
        active = self.active_revision()
        published: dict[str, dict[str, Any]] = {}
        for entry in self.journal():
            if entry.get("action") != "publish":
                continue
            revision = entry.get("revision")
            if isinstance(revision, str):
                published[revision] = entry

        items: list[dict[str, Any]] = []
        for name in self.root.list_names():
            if name in RESERVED or not name.endswith(".json") or name.startswith("_"):
                continue
            revision = name[: -len(".json")]
            entry = published.get(revision, {})
            items.append(
                {
                    "revision": revision,
                    "at": entry.get("at"),
                    "author": entry.get("author"),
                    "note": entry.get("note", ""),
                    "sha256": entry.get("sha256"),
                    "active": revision == active,
                    "journaled": bool(entry),
                }
            )
        items.sort(key=lambda item: item["revision"], reverse=True)
        warning = (
            f"Ревизий больше {REVISION_COUNT_WARNING} — проверьте, не публикует ли что-то само"
            if len(items) > REVISION_COUNT_WARNING
            else None
        )
        return {"active": active, "revisions": items, "warning": warning}

    def verify_active(self) -> dict[str, Any]:
        """Сверить действующий файл с тем, что записано в журнале.

        Это компенсация за то, что каталог ставок стал доступен на запись:
        расхождение sha256 означает, что кто-то изменил цены мимо публикации.
        """
        active = self.active_revision()
        if active is None:
            return {"ok": True, "active": None, "reason": "no active pack"}
        try:
            raw = self.root.read_bytes(f"{active}.json", limit=MAX_PACK_BYTES)
        except (NotFound, ValueError):
            return {"ok": False, "active": active, "reason": "active revision file is missing"}
        on_disk = sha256_bytes(raw)
        journaled = next(
            (
                entry.get("sha256")
                for entry in reversed(self.journal())
                if entry.get("revision") == active and entry.get("sha256")
            ),
            None,
        )
        if journaled is None:
            return {"ok": False, "active": active, "reason": "no journal entry for active revision"}
        if journaled != on_disk:
            return {
                "ok": False,
                "active": active,
                "reason": "active pack content differs from the journal",
                "journal_sha256": journaled,
                "disk_sha256": on_disk,
            }
        return {"ok": True, "active": active, "sha256": on_disk}


__all__ = [
    "ACTIVE_POINTER",
    "Conflict",
    "DRAFT",
    "JOURNAL",
    "PackPublisher",
    "make_revision",
    "upgrade_pack_v2_to_v3",
    "validate_author",
    "validate_pack",
]
