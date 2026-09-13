"""Проверка индексов FTS5 в state.db: повреждение индекса от потери данных.

Повреждённый обратный индекс FTS5 ничем себя не выдаёт. Беседы читаются,
сообщения пишутся, `korra status` отвечает `ok` — и так продолжается до
следующей миграции, где импорт первым же `PRAGMA quick_check` отказывается
принимать архив. Ровно так это прожило в работающем контуре Виктории до
12.09.2026 (K21-055) и в архивных копиях профилей Павловой (K21-039).

Разница между двумя бедами существенна:

* повреждён только индекс. Сообщения лежат в ``messages`` и целы; индекс
  пересобирается из них штатной стратегией ``rebuild_fts``
  (:func:`korra_state.repair_state_db_schema`), ничего не теряя;
* повреждены сами страницы базы. Пересборка индекса здесь не поможет и не
  должна запускаться: нужно восстановление из копии.

Классификация опирается на **пробную пересборку**, а не на текст сообщения
`quick_check`. Первая версия этого модуля разбирала строку
``malformed inverted index for FTS5 table <схема>.<таблица>`` регулярным
выражением — и разошлась с реальностью ровно на той сборке, ради которой
писалась. Одно и то же повреждение называется по-разному:

* SQLite 3.45–3.50: ``malformed inverted index for FTS5 table main.<таблица>``;
* SQLite 3.53: ``fts5: corruption found reading blob <id> from table "<таблица>"``.

Образ везёт 3.53, поэтому версия на регулярном выражении объявляла бы «данные
повреждены, нужно восстановление из копии» на каждом ребилдабельном индексе,
то есть не работала бы именно в продакшене. Поймал это CI, где интерпретатор
собран с 3.53.1, а не хостовой 3.45.

Проба отвечает на тот самый вопрос, который и задаёт классификация: «станет ли
база целой, если пересобрать индекс». Порядок такой:

1. `quick_check` на копии, снятой ``sqlite3.Connection.backup()``. Боевой файл
   при этом не открывается на запись и не меняется вовсе;
2. каждой таблице FTS5 из схемы задаётся её собственный вопрос — команда
   ``INSERT INTO t(t) VALUES('integrity-check')``. Она отвечает одинаково на
   всех версиях и называет ровно те таблицы, чей индекс не сходится с
   содержимым. Читающее соединение её не выполнит («attempt to write a readonly
   database»), поэтому проба и работает на копии;
3. найденные таблицы пересобираются в копии, и `quick_check` повторяется.
   Чисто — повреждён только индекс; не чисто — повреждены данные.

Проба запускается лишь тогда, когда `quick_check` уже нашёл беду, то есть на
здоровом контуре не стоит ничего. `quick_check`, а не `integrity_check`: обе
видят повреждение индекса FTS5 (SQLite 3.45+ спрашивает виртуальные таблицы
через ``xIntegrity``), но первая не перечитывает содержимое обычных индексов и
на большой базе стоит в разы дешевле.

Оговорка про версии остаётся в силе для самого приговора: проверять базу
контура нужно тем же runtime, который её пишет — изнутри образа. Хостовый 3.45
умеет ложно браковать trigram-индекс, записанный 3.53 (журнал за 12.09.2026).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
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

# Виртуальная таблица FTS5 лежит в схеме как обычная строка с типом `table`;
# отличает её только текст CREATE. Тот же запрос использует rebuild_fts_indexes.
_FTS5_TABLES_SQL = (
    "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%USING fts5%'"
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


def _fts5_tables(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Имена таблиц FTS5 по схеме базы, а не по тексту ошибки."""
    return tuple(str(row[0]) for row in conn.execute(_FTS5_TABLES_SQL))


def _quick_check_problems(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Строки `quick_check`, кроме `ok`."""
    rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
    return tuple(row for row in rows if row.lower() != OK)


def _failing_fts5_tables(
    conn: sqlite3.Connection, tables: tuple[str, ...]
) -> tuple[str, ...]:
    """Таблицы, чей индекс не сходится с содержимым.

    ``integrity-check`` — штатная команда самой FTS5: она отвечает исключением
    независимо от того, какими словами конкретная версия SQLite описывает ту же
    беду в `quick_check`. Соединение должно быть пишущим (команда оформлена как
    INSERT), поэтому вызывается только на копии.
    """
    failing = []
    for table in tables:
        try:
            conn.execute(f'INSERT INTO "{table}"("{table}") VALUES (\'integrity-check\')')
        except sqlite3.DatabaseError:
            failing.append(table)
    return tuple(failing)


def _probe_rebuild_on_copy(
    path: Path, tables: tuple[str, ...], deadline: float
) -> tuple[str, tuple[str, ...], str]:
    """Ответить, лечится ли повреждение пересборкой индекса, не трогая ``path``.

    Работает на копии, снятой ``backup()``: боевая база не открывается на
    запись. Возвращает ``(статус, таблицы, пояснение)``.
    """
    # Копия ложится рядом с базой — тот же том, та же оценка свободного места.
    # На read-only монтировании падаем в системный temp, а не в отказ.
    try:
        handle, copy_name = tempfile.mkstemp(prefix=".korra-fts-probe-", dir=str(path.parent))
    except OSError:
        handle, copy_name = tempfile.mkstemp(prefix=".korra-fts-probe-")
    os.close(handle)
    copy_path = Path(copy_name)
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
        try:
            probe = sqlite3.connect(str(copy_path), timeout=10.0)
            try:
                probe.set_progress_handler(
                    lambda: int(time.monotonic() > deadline), 10_000
                )
                source.backup(probe)
                failing = _failing_fts5_tables(probe, tables)
                if not failing:
                    return DATA_DAMAGED, (), "ни один индекс FTS5 не назвал себя виновным"
                for table in failing:
                    probe.execute(f'INSERT INTO "{table}"("{table}") VALUES (\'rebuild\')')
                probe.commit()
                if _quick_check_problems(probe):
                    return DATA_DAMAGED, failing, "пересборка индекса не вылечила копию"
                return INDEX_DAMAGED, failing, "пересборка индекса вылечила копию"
            finally:
                probe.close()
        finally:
            source.close()
    except sqlite3.DatabaseError as exc:
        # Копия не снялась или пересборка упала — это уже не «только индекс».
        return DATA_DAMAGED, (), f"проба пересборки не прошла: {exc}"
    finally:
        copy_path.unlink(missing_ok=True)


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
        problems = _quick_check_problems(conn)
        schema_tables = _fts5_tables(conn)
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

    if not problems:
        return FtsIntegrityReport(path, OK, "quick_check пройден")

    def data_damaged(why: str) -> FtsIntegrityReport:
        return FtsIntegrityReport(
            path, DATA_DAMAGED,
            f"повреждены сами данные ({why}): {problems[0]}. Пересборка индекса "
            "здесь не поможет — нужно восстановление из резервной копии",
            problems,
        )

    if not schema_tables:
        return data_damaged("таблиц FTS5 в схеме нет")

    status, tables, why = _probe_rebuild_on_copy(path, schema_tables, deadline)
    if status != INDEX_DAMAGED:
        return data_damaged(why)
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


@dataclass(frozen=True)
class SnapshotRepair:
    """Что стало со снимком state.db перед упаковкой в архив."""

    status: str
    rebuilt: tuple[str, ...] = ()
    problem: str | None = None


def repair_snapshot_fts(
    path: Path, *, timeout_seconds: float = 120.0
) -> SnapshotRepair:
    """Привести снимок state.db в состояние, которое примет `korra import`.

    Вызывается на стороне `korra backup` для копии, только что снятой
    ``sqlite3.Connection.backup()``: копия ещё никому не принадлежит, поэтому
    индекс можно пересобрать прямо в ней, не трогая боевую базу. Импорт
    отбивает архив по собственному `quick_check`, поэтому такую копию нельзя
    ни класть в архив как есть, ни молча выбрасывать.

    Здесь проба идёт **в самом файле**, без второй копии: снимок для того и
    существует. Классификация от этого не меняется — вопрос тот же, что и в
    :func:`check_state_db_fts`: чинит ли пересборка индекса.
    """
    path = Path(path)
    deadline = time.monotonic() + timeout_seconds
    try:
        conn = sqlite3.connect(str(path), timeout=10.0)
    except sqlite3.DatabaseError as exc:
        return SnapshotRepair(UNREADABLE, problem=f"база не открывается: {exc}")
    try:
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        if not _quick_check_problems(conn):
            return SnapshotRepair(OK)
        failing = _failing_fts5_tables(conn, _fts5_tables(conn))
        if not failing:
            return SnapshotRepair(
                DATA_DAMAGED,
                problem="повреждены сами данные: ни один индекс FTS5 не назвал "
                        "себя виновным, пересборка не поможет",
            )
        for table in failing:
            conn.execute(f'INSERT INTO "{table}"("{table}") VALUES (\'rebuild\')')
        conn.commit()
        if _quick_check_problems(conn):
            return SnapshotRepair(
                DATA_DAMAGED, rebuilt=failing,
                problem="повреждены сами данные: пересборка индекса ("
                        + ", ".join(failing) + ") не вылечила снимок",
            )
        return SnapshotRepair(OK, rebuilt=failing)
    except sqlite3.DatabaseError as exc:
        return SnapshotRepair(UNREADABLE, problem=f"проверка не прошла: {exc}")
    finally:
        conn.close()


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
