"""Заметки к выпуску Korra 21 — единственный источник «что нового».

Зачем модуль. Клиент видит в панели, что у него стоит и что доступно, а
оператор — то же самое в кабинете. Оба должны читать ОДИН текст, написанный
человеческими словами, иначе «что нового» превращается в `ghcr.io/…@sha256:…`
и предприниматель не понимает, за что нажимает кнопку.

Источник — `RELEASE_NOTES.md` в корне репозитория. Он едет внутри образа
(в `.dockerignore` для него сделано исключение из общего `*.md`), поэтому
работающая установка всегда может рассказать о себе сама, без обращения
наружу: верхняя запись файла — это и есть выпуск, из которого собран образ.

Формат заметок — обычный markdown, который правит человек:

    ## K21-2026.09.08 · Русский интерфейс

    - Дата: 08.09.2026
    - Ревизия: 20cf8035d9
    - Пауза: около минуты

    Одно предложение о том, что изменилось.

    ### Что нового

    - **Короткий заголовок.** Пояснение обычными словами.

Разбор намеренно снисходительный: пропущенная метадата или лишний раздел не
должны ломать экран обновления — человек увидит меньше подробностей, но не
пустую страницу с ошибкой.

CLI-режим (`python -m korra_cli.release_notes`) умеет два дела: показать
заметки в JSON и собрать ЧЕРНОВИК новой записи из коммитов между ревизиями и
отчётов `docs/qa/`. Черновик — заготовка для человека, а не готовый текст:
машина не знает, что из сделанного важно предпринимателю.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

#: Корень репозитория/образа: `korra_cli/` лежит рядом с `RELEASE_NOTES.md`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_NOTES_PATH = PROJECT_ROOT / "RELEASE_NOTES.md"

#: `## K21-2026.09.08 · Заголовок` — идентификатор и название выпуска.
_RELEASE_HEADING = re.compile(
    r"^##\s+(?P<id>[A-Za-z0-9][A-Za-z0-9._-]{0,63})\s*(?:[·:—-]\s*(?P<title>.+?))?\s*$"
)
_SECTION_HEADING = re.compile(r"^###\s+(?P<heading>.+?)\s*$")
_META_ITEM = re.compile(r"^[-*]\s+(?P<key>[^:]{1,40}):\s*(?P<value>.+?)\s*$")
_LIST_ITEM = re.compile(r"^[-*]\s+(?P<body>.+?)\s*$")

#: Ключи метаданных пишутся по-русски; здесь их переводим в поля записи.
_META_KEYS = {
    "дата": "published_at",
    "ревизия": "revision",
    "пауза": "pause",
    "образ": "image",
}

#: Размер, за которым файл заметок перестаёт быть заметками. Панель читает его
#: на каждый запрос состояния, поэтому чужой гигабайт сюда попасть не должен.
_MAX_NOTES_BYTES = 512 * 1024


@dataclass
class NoteItem:
    """Один пункт «что нового»: жирный заголовок и пояснение к нему."""

    title: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class NoteSection:
    """Раздел выпуска — «Что нового», «Что починили», «Что важно знать»."""

    heading: str
    items: list[NoteItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"heading": self.heading, "items": [item.to_dict() for item in self.items]}


@dataclass
class ReleaseNote:
    """Заметки к одному выпуску."""

    release_id: str
    title: str = ""
    published_at: str = ""
    revision: str = ""
    pause: str = ""
    image: str = ""
    summary: str = ""
    sections: list[NoteSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sections"] = [section.to_dict() for section in self.sections]
        return data

    def highlights(self, limit: int = 4) -> list[dict[str, str]]:
        """Короткая выжимка для карточки: первые пункты первого раздела.

        Карточка выпуска в панели и в кабинете показывает не весь текст, а
        несколько строк — остальное человек раскрывает сам.
        """
        found: list[dict[str, str]] = []
        for section in self.sections:
            for item in section.items:
                found.append(item.to_dict())
                if len(found) >= limit:
                    return found
        return found


def _strip_markdown(text: str) -> str:
    """Убрать разметку из строки, оставив то, что читает человек."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.strip()


def _split_item(body: str) -> NoteItem:
    """Разложить пункт списка на заголовок и пояснение.

    Соглашение простое и не требует от автора заметок ничего лишнего:
    `**Заголовок.** Пояснение` даёт две части, обычная строка — одну.
    """
    bold = re.match(r"^\*\*(?P<title>.+?)\*\*[.:]?\s*(?P<detail>.*)$", body.strip())
    if bold:
        title = _strip_markdown(bold.group("title")).rstrip(".")
        return NoteItem(title=title, detail=_strip_markdown(bold.group("detail")))
    plain = _strip_markdown(body)
    # Без выделения делим по первой точке: первая фраза работает заголовком.
    head, sep, tail = plain.partition(". ")
    if sep and len(head) <= 80:
        return NoteItem(title=head, detail=tail.strip())
    return NoteItem(title=plain)


def parse_release_notes(text: str) -> list[ReleaseNote]:
    """Разобрать текст `RELEASE_NOTES.md`. Порядок записей сохраняется."""
    releases: list[ReleaseNote] = []
    current: Optional[ReleaseNote] = None
    section: Optional[NoteSection] = None
    in_meta = False

    for raw in text.splitlines():
        line = raw.rstrip()
        heading = _RELEASE_HEADING.match(line)
        if heading:
            current = ReleaseNote(
                release_id=heading.group("id"),
                title=_strip_markdown(heading.group("title") or ""),
            )
            releases.append(current)
            section = None
            in_meta = True
            continue
        if current is None:
            continue

        sub = _SECTION_HEADING.match(line)
        if sub:
            section = NoteSection(heading=_strip_markdown(sub.group("heading")))
            current.sections.append(section)
            in_meta = False
            continue

        if not line.strip():
            continue
        if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", line.strip()):
            # Разделитель между записями: сам по себе ничего не значит и в
            # текст пункта попасть не должен.
            continue

        if section is None and in_meta:
            meta = _META_ITEM.match(line)
            if meta:
                key = _META_KEYS.get(meta.group("key").strip().lower())
                if key:
                    setattr(current, key, _strip_markdown(meta.group("value")))
                continue
            in_meta = False

        item = _LIST_ITEM.match(line)
        if item:
            if section is None:
                # Список до первого `###` — тоже пункты выпуска; заводим им
                # безымянный раздел, чтобы текст не потерялся.
                section = NoteSection(heading="")
                current.sections.append(section)
            section.items.append(_split_item(item.group("body")))
            continue

        if section is None:
            # Абзац до первого `###` — краткое описание выпуска; перенос
            # строки в markdown не делит абзац, поэтому склеиваем.
            current.summary = (current.summary + " " + _strip_markdown(line)).strip()
        elif section.items:
            # Продолжение последнего пункта на следующей строке.
            last = section.items[-1]
            last.detail = (last.detail + " " + _strip_markdown(line)).strip()

    return releases


def read_release_notes(path: Optional[Path] = None) -> list[ReleaseNote]:
    """Прочитать заметки из файла. Никогда не бросает: нет файла — нет заметок.

    Экран обновления не должен падать из-за отсутствующего или битого файла:
    в самом плохом случае клиент увидит версию без рассказа о ней.
    """
    target = Path(path) if path is not None else RELEASE_NOTES_PATH
    try:
        if target.stat().st_size > _MAX_NOTES_BYTES:
            return []
        return parse_release_notes(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []


def current_release(path: Optional[Path] = None) -> Optional[ReleaseNote]:
    """Выпуск, из которого собрана эта установка — верхняя запись файла.

    Правило держится на порядке работы: заметки пишутся ДО сборки образа, и
    новая запись всегда добавляется сверху. Поэтому верхняя запись в файле
    внутри образа — это ровно тот выпуск, который у клиента стоит.
    """
    notes = read_release_notes(path)
    return notes[0] if notes else None


def find_release(release_id: str, path: Optional[Path] = None) -> Optional[ReleaseNote]:
    for note in read_release_notes(path):
        if note.release_id == release_id:
            return note
    return None


# ─── Черновик заметок из истории репозитория ────────────────────────────────

#: Коммиты, которые предпринимателю ничего не говорят.
_NOISE = re.compile(
    r"^(?:merge|revert|lint|chore|ci|test|typo|wip|bump|style|refactor)\b[:( ]",
    re.IGNORECASE,
)
#: Русские слова починки — по ним черновик раскладывает коммиты на два раздела.
_FIX_WORDS = re.compile(
    r"(почин|исправ|не терял|перестал|fix|ошибк|падал|ломал)", re.IGNORECASE
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git завершился ошибкой")
    return result.stdout


def commits_between(repo: Path, rev_range: str) -> list[tuple[str, str]]:
    """`[(короткий хэш, заголовок)]` для диапазона ревизий, без шума."""
    output = _git(repo, "log", "--no-merges", "--format=%h%x00%s", rev_range)
    found: list[tuple[str, str]] = []
    for line in output.splitlines():
        short, _, subject = line.partition("\0")
        subject = subject.strip()
        if not subject or _NOISE.match(subject):
            continue
        found.append((short.strip(), subject))
    return found


def qa_reports_between(repo: Path, rev_range: str) -> list[tuple[str, str]]:
    """Отчёты `docs/qa/*.md`, появившиеся в диапазоне: `(путь, заголовок)`.

    Отчёт приёмки — самый честный источник «что изменилось»: его писали, уже
    проверив результат, и в нём есть человеческий заголовок.
    """
    try:
        changed = _git(repo, "diff", "--name-only", "--diff-filter=AM", rev_range, "--", "docs/qa")
    except RuntimeError:
        return []
    reports: list[tuple[str, str]] = []
    for name in sorted(set(changed.split())):
        if not name.endswith(".md"):
            continue
        path = repo / name
        title = ""
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = _strip_markdown(line[2:])
                    break
        except OSError:
            continue
        reports.append((name, title or name))
    return reports


def draft_release_note(
    repo: Path,
    rev_range: str,
    release_id: str,
    title: str = "",
) -> str:
    """Собрать ЧЕРНОВИК записи выпуска. Итоговый текст пишет человек.

    Машина умеет только собрать материал: заголовки коммитов и отчёты приёмки
    за диапазон. Превратить их в «что это даёт владельцу бизнеса» — работа
    автора заметок, поэтому черновик честно помечен как черновик.
    """
    commits = commits_between(repo, rev_range)
    reports = qa_reports_between(repo, rev_range)
    head = ""
    try:
        head = _git(repo, "rev-parse", "--short=10", rev_range.split("..")[-1]).strip()
    except RuntimeError:
        pass

    changes = [(short, subject) for short, subject in commits if not _FIX_WORDS.search(subject)]
    fixes = [(short, subject) for short, subject in commits if _FIX_WORDS.search(subject)]

    lines = [
        f"## {release_id}" + (f" · {title}" if title else " · ЗАГОЛОВОК ВЫПУСКА"),
        "",
        "- Дата: ДД.ММ.ГГГГ",
        f"- Ревизия: {head or 'ХХХХХХ'}",
        "- Пауза: около минуты",
        "",
        "ЧЕРНОВИК. Одно предложение о том, что изменилось для владельца.",
        "",
        "### Что нового",
        "",
    ]
    lines += [f"- **{subject}** ({short})" for short, subject in changes] or ["- (нечего показать)"]
    if fixes:
        lines += ["", "### Что починили", ""]
        lines += [f"- **{subject}** ({short})" for short, subject in fixes]
    if reports:
        lines += ["", "<!-- Отчёты приёмки за этот диапазон:"]
        lines += [f"     {name} — {report_title}" for name, report_title in reports]
        lines += ["-->"]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m korra_cli.release_notes",
        description="Заметки к выпуску Korra 21: показать разобранные или собрать черновик.",
    )
    parser.add_argument("--file", type=Path, default=None, help="путь к RELEASE_NOTES.md")
    parser.add_argument("--json", action="store_true", help="выдать разобранные заметки в JSON")
    parser.add_argument("--release", default="", help="показать один выпуск по идентификатору")
    parser.add_argument("--draft", metavar="REV..REV", default="", help="собрать черновик по диапазону ревизий")
    parser.add_argument("--release-id", default="", help="идентификатор нового выпуска для черновика")
    parser.add_argument("--title", default="", help="заголовок нового выпуска для черновика")
    parser.add_argument("--repo", type=Path, default=PROJECT_ROOT, help="каталог репозитория")
    args = parser.parse_args(argv)

    if args.draft:
        release_id = args.release_id or "K21-ГГГГ.ММ.ДД"
        try:
            sys.stdout.write(draft_release_note(args.repo, args.draft, release_id, args.title))
        except RuntimeError as exc:
            sys.stderr.write(f"Не удалось прочитать историю: {exc}\n")
            return 1
        return 0

    notes = read_release_notes(args.file)
    if args.release:
        notes = [note for note in notes if note.release_id == args.release]
        if not notes:
            sys.stderr.write(f"Выпуск {args.release} в заметках не найден\n")
            return 1
    if args.json:
        print(json.dumps([note.to_dict() for note in notes], ensure_ascii=False, indent=2))
        return 0
    for note in notes:
        print(f"{note.release_id} · {note.title} ({note.published_at})")
        if note.summary:
            print(f"  {note.summary}")
        for section in note.sections:
            if section.heading:
                print(f"  {section.heading}:")
            for item in section.items:
                print(f"    — {item.title}" + (f" {item.detail}" if item.detail else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
