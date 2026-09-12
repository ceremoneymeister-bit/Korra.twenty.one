"""Проверка индексов FTS5 в state.db: повреждение индекса от потери данных.

Повреждённый обратный индекс FTS5 ничем себя не выдаёт. Беседы читаются,
сообщения пишутся, `korra status` отвечает `ok` — и так продолжается до
следующей миграции, где импорт первым же `PRAGMA quick_check` отказывается
принимать архив. Ровно так это прожило в работающем контуре Виктории до
12.09.2026 (K21-055) и в архивных копиях профилей Павловой (K21-039).

Разница между двумя бедами существенна и видна в том же `quick_check`:

* строка ``malformed inverted index for FTS5 table <схема>.<таблица>`` —
  повреждён только индекс. Сообщения лежат в ``messages`` и целы; индекс
  пересобирается из них штатной стратегией ``rebuild_fts``
  (:func:`korra_state.repair_state_db_schema`), ничего не теряя;
* любая другая строка — повреждены сами страницы базы. Пересборка индекса
  здесь не поможет и не должна запускаться: нужно восстановление из копии.

Проверка сознательно делает `quick_check`, а не `integrity_check`: обе видят
повреждение индекса FTS5 (SQLite 3.45+ спрашивает виртуальные таблицы через
``xIntegrity``), но первая не перечитывает содержимое обычных индексов и на
большой базе стоит в разы дешевле.

Оговорка про версии SQLite: приговор выносит та сборка SQLite, которая
выполняет проверку. Хостовый 3.45 умеет ложно браковать trigram-индекс,
записанный 3.53 (обход в журнале за 12.09.2026), поэтому проверять базу
контура нужно тем же runtime, который её пишет — изнутри образа.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# Выше этого размера boot-проверка не запускается: `quick_check` листает файл
# целиком, и на многогигабайтной базе это минуты молчаливой загрузки CPU на
# старте. Тот же потолок, что у `verify_sqlite_integrity` в korra_cli.backup.
DEFAULT_MAX_BYTES = 2 << 30
# Потолок по времени на случай медленного диска: прогресс-обработчик прерывает
# запрос, и это докладывается как «не проверено», а не как поломка.
DEFAULT_TIMEOUT_SECONDS = 30.0

_FTS_PROBLEM = re.compile(
    r"^malformed inverted index for FTS5 table (?:(?P<schema>\w+)\.)?(?P<table>\w+)$"
)

OK = "ok"
INDEX_DAMAGED = "index_damaged"
DATA_DAMAGED = "data_damaged"
UNREADABLE = "unreadable"
SKIPPED = "skipped"
ABSENT = "absent"


@dataclass(frozen=True)
class FtsIntegrityReport:
    """Итог одной проверки: что именно сломано и что с этим делать."""

    path: Path
    status: str
    detail: str
    problems: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.status in (OK, ABSENT)

    @property
    def rebuildable(self) -> bool:
        """True, когда повреждён только индекс и сообщения целы."""
        return self.status == INDEX_DAMAGED

    def line(self) -> str:
        """Одна строка для cont-init / doctor / лога."""
        return f"fts: {self.path} {self.status} — {self.detail}"


def check_state_db_fts(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> FtsIntegrityReport:
    """Проверить ``path`` и назвать класс повреждения, не меняя файл."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return FtsIntegrityReport(path, ABSENT, "файла нет")
    except OSError as exc:
        return FtsIntegrityReport(path, UNREADABLE, f"нет доступа: {exc}")

    if max_bytes and size > max_bytes:
        return FtsIntegrityReport(
            path, SKIPPED,
            f"пропущено: база {size:,} Б больше потолка {max_bytes:,} Б; "
            "проверьте отдельно (korra doctor)",
        )

    deadline = time.monotonic() + timeout_seconds
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    except sqlite3.DatabaseError as exc:
        return FtsIntegrityReport(path, UNREADABLE, f"база не открывается: {exc}")
    try:
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
    except sqlite3.DatabaseError as exc:
        if time.monotonic() > deadline:
            return FtsIntegrityReport(
                path, SKIPPED,
                f"не проверено за {timeout_seconds:g} с; проверьте отдельно "
                "(korra doctor)",
            )
        return FtsIntegrityReport(path, UNREADABLE, f"проверка не прошла: {exc}")
    finally:
        conn.close()

    problems = tuple(row for row in rows if row.lower() != OK)
    if not problems:
        return FtsIntegrityReport(path, OK, "quick_check пройден")

    damaged_tables = []
    for problem in problems:
        match = _FTS_PROBLEM.match(problem)
        if match is None:
            return FtsIntegrityReport(
                path, DATA_DAMAGED,
                "повреждены сами данные: "
                f"{problems[0]}. Пересборка индекса здесь не поможет — нужно "
                "восстановление из резервной копии",
                problems,
            )
        damaged_tables.append(match.group("table"))

    tables = tuple(dict.fromkeys(damaged_tables))
    return FtsIntegrityReport(
        path, INDEX_DAMAGED,
        "повреждён поисковый индекс FTS5 (" + ", ".join(tables) + "); "
        "сообщения целы, индекс пересобирается из них: korra doctor --fix",
        problems, tables,
    )


def rebuild_fts_indexes(path: Path, tables: tuple[str, ...]) -> tuple[bool, str]:
    """Пересобрать перечисленные индексы FTS5 прямо в файле ``path``.

    Предназначено для файла, которым никто не владеет: снимок, staged-копия,
    распакованный из архива state.db. Живой state.db контура чинится штатным
    :func:`korra_state.repair_state_db_schema` — он берёт межпроцессную
    блокировку, снимает криминалистическую копию и работает на изолированном
    снимке, а не на боевом файле.

    Возвращает ``(успех, что произошло)``.
    """
    path = Path(path)
    try:
        conn = sqlite3.connect(str(path), timeout=10.0)
    except sqlite3.DatabaseError as exc:
        return False, f"база не открывается: {exc}"
    try:
        known = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND sql LIKE '%USING fts5%'"
            )
        }
        for table in tables:
            # Имя пришло из сообщения SQLite, но в SQL оно подставляется
            # строкой — сверяем его со схемой, а не доверяем тексту ошибки.
            if table not in known:
                return False, f"в схеме нет таблицы FTS5 {table}"
            conn.execute(f'INSERT INTO "{table}"("{table}") VALUES (\'rebuild\')')
        conn.commit()
    except sqlite3.DatabaseError as exc:
        return False, f"пересборка не удалась: {exc}"
    finally:
        conn.close()
    return True, "индекс пересобран: " + ", ".join(tables)


def iter_state_databases(hermes_home: Path) -> list[Path]:
    """``state.db`` корневого профиля и всех профилей контура."""
    hermes_home = Path(hermes_home)
    databases = []
    root_db = hermes_home / "state.db"
    if root_db.exists():
        databases.append(root_db)
    profiles = hermes_home / "profiles"
    if profiles.is_dir():
        for profile in sorted(profiles.iterdir()):
            candidate = profile / "state.db"
            if candidate.is_file():
                databases.append(candidate)
    return databases


def check_contour_state_databases(hermes_home: Path, **kwargs) -> list[FtsIntegrityReport]:
    """Проверить все state.db контура. Не чинит и не бросает исключений."""
    return [check_state_db_fts(db, **kwargs) for db in iter_state_databases(hermes_home)]
