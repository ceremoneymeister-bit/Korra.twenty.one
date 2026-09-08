"""Заметки к выпуску: разбор человеческого markdown и черновик из истории.

Файл `RELEASE_NOTES.md` — единственный источник «что нового» и для панели
владельца, и для операторского кабинета. Разбор обязан быть снисходительным:
кривая запись должна стоить одного пропущенного поля, а не пустого экрана
обновления у клиента.
"""

from __future__ import annotations

import subprocess

import pytest

from korra_cli import release_notes


SAMPLE = """# Что нового

Вводный текст файла, к выпускам не относится.

## K21-2026.09.08 · Обновление одной кнопкой

- Дата: 08.09.2026
- Ревизия: 20cf8035d9
- Пауза: около минуты

Одно предложение сути,
продолженное на второй строке.

### Что нового

- **Раздел «Обновления».** Видно, что стоит и что доступно.
- Пункт без выделения. Пояснение к нему.

### Что починили

- **Отказ модели объясняется словами.**

---

## K21-2026.09.07 · Файлы в чате

- Дата: 07.09.2026

### Что нового

- **Документы прикрепляются.** Из рабочей папки.
"""


def test_parses_releases_in_file_order():
    notes = release_notes.parse_release_notes(SAMPLE)
    assert [note.release_id for note in notes] == ["K21-2026.09.08", "K21-2026.09.07"]
    assert notes[0].title == "Обновление одной кнопкой"
    assert notes[0].published_at == "08.09.2026"
    assert notes[0].revision == "20cf8035d9"
    assert notes[0].pause == "около минуты"


def test_summary_joins_wrapped_paragraph_and_drops_separator():
    note = release_notes.parse_release_notes(SAMPLE)[0]
    assert note.summary == "Одно предложение сути, продолженное на второй строке."
    # Горизонтальная черта между записями не должна прилипать к последнему пункту.
    assert all("---" not in item.detail for section in note.sections for item in section.items)


def test_items_split_into_title_and_detail():
    note = release_notes.parse_release_notes(SAMPLE)[0]
    first = note.sections[0]
    assert first.heading == "Что нового"
    assert first.items[0].title == "Раздел «Обновления»"
    assert first.items[0].detail == "Видно, что стоит и что доступно."
    # Без выделения делим по первой точке — первая фраза работает заголовком.
    assert first.items[1].title == "Пункт без выделения"
    assert first.items[1].detail == "Пояснение к нему."
    assert note.sections[1].heading == "Что починили"


def test_highlights_take_first_items_across_sections():
    note = release_notes.parse_release_notes(SAMPLE)[0]
    assert [item["title"] for item in note.highlights(limit=3)] == [
        "Раздел «Обновления»",
        "Пункт без выделения",
        "Отказ модели объясняется словами",
    ]


def test_missing_metadata_does_not_break_parsing():
    note = release_notes.parse_release_notes("## K21-X\n\n### Что нового\n\n- Просто пункт\n")[0]
    assert note.release_id == "K21-X"
    assert note.title == "" and note.published_at == ""
    assert note.sections[0].items[0].title == "Просто пункт"


def test_read_release_notes_never_raises(tmp_path):
    assert release_notes.read_release_notes(tmp_path / "нет-такого.md") == []
    broken = tmp_path / "RELEASE_NOTES.md"
    broken.write_bytes(b"\xff\xfe not utf8 at all \xff")
    assert release_notes.read_release_notes(broken) == []


def test_oversized_notes_are_ignored(tmp_path):
    huge = tmp_path / "RELEASE_NOTES.md"
    huge.write_text("## K21-X\n" + "- пункт\n" * 200000, encoding="utf-8")
    assert release_notes.read_release_notes(huge) == []


def test_current_release_is_the_top_entry(tmp_path):
    path = tmp_path / "RELEASE_NOTES.md"
    path.write_text(SAMPLE, encoding="utf-8")
    assert release_notes.current_release(path).release_id == "K21-2026.09.08"
    assert release_notes.find_release("K21-2026.09.07", path).title == "Файлы в чате"
    assert release_notes.find_release("нет-такого", path) is None


def test_shipped_notes_parse_and_describe_this_build():
    """Файл в корне репозитория обязан читаться: по нему панель показывает выпуск."""
    note = release_notes.current_release()
    assert note is not None, "RELEASE_NOTES.md в корне не разобрался"
    assert note.release_id.startswith("K21-")
    assert note.summary and note.sections
    # Ревизия верхней записи — факт сборки, а не текст: вне образа её нет.
    assert note.revision == release_notes.build_revision()


STAMPED = """## K21-2026.09.09 · Новый выпуск

- Ревизия: @build-revision@

### Что нового

- **Пункт нового выпуска.**

## K21-2026.09.08 · Прежний выпуск

- Ревизия: 20cf8035d9

### Что нового

- **Пункт прежнего выпуска.**
"""


def test_top_revision_is_stamped_by_the_build_not_written_by_hand(tmp_path, monkeypatch):
    """Точный хэш дерева, в которое входит сам файл, в нём не записать: коммит
    с этой записью его и меняет. Ровно так в выпуск K21-2026.09.08 уехала
    ревизия предыдущего дерева (найдено ревью 08.09.2026)."""
    path = tmp_path / "RELEASE_NOTES.md"
    path.write_text(STAMPED, encoding="utf-8")
    monkeypatch.setattr(release_notes, "build_revision", lambda: "f" * 40)
    notes = release_notes.read_release_notes(path)
    assert notes[0].revision == "f" * 40
    # Прошлые выпуски знают свою ревизию точно — их не трогаем.
    assert notes[1].revision == "20cf8035d9"


def test_marker_never_reaches_the_screen_outside_the_image(tmp_path, monkeypatch):
    """Вне образа ревизии просто нет. Пустая строка честнее метки: карточка в
    панели и в кабинете такую строку не показывает вовсе."""
    path = tmp_path / "RELEASE_NOTES.md"
    path.write_text(STAMPED, encoding="utf-8")
    monkeypatch.setattr(release_notes, "build_revision", lambda: "")
    assert release_notes.read_release_notes(path)[0].revision == ""


def test_shipped_top_entry_leaves_the_revision_to_the_build():
    text = release_notes.RELEASE_NOTES_PATH.read_text(encoding="utf-8")
    top = text.split("\n## ", 1)[1].split("\n## ", 1)[0]
    assert f"- Ревизия: {release_notes.BUILD_REVISION_MARK}" in top


def test_build_revision_comes_from_the_image_provenance(monkeypatch):
    from korra_cli import image_provenance

    def provenance(marker_path=None):
        return image_provenance.ImageProvenance(
            schema=1, deployment_kind="image", manager="docker", image="ghcr.io/x/y",
            version="0.21.0", revision="a" * 40, marker_path="/etc/hermes/image-provenance.json")

    monkeypatch.setattr(image_provenance, "read_image_provenance", provenance)
    assert release_notes.build_revision() == "a" * 40

    monkeypatch.setattr(image_provenance, "read_image_provenance", lambda marker_path=None: None)
    assert release_notes.build_revision() == ""

    # Испорченный провенанс не даёт ревизии: подставлять нечего.
    monkeypatch.setattr(image_provenance, "read_image_provenance",
                        lambda marker_path=None: image_provenance.ImageProvenance(
                            schema=1, deployment_kind="image", manager="unknown", image=None,
                            version=None, revision=None, marker_path="/x", valid=False, error="broken"))
    assert release_notes.build_revision() == ""


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True, text=True)

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    git("config", "user.email", "qa@example.invalid")
    git("config", "user.name", "QA")
    (tmp_path / "README.md").write_text("основа\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-qm", "Первый коммит")
    (tmp_path / "one.txt").write_text("1\n", encoding="utf-8")
    git("add", "one.txt")
    git("commit", "-qm", "Показывать доступный выпуск владельцу")
    (tmp_path / "two.txt").write_text("2\n", encoding="utf-8")
    git("add", "two.txt")
    git("commit", "-qm", "lint: пробелы")
    (tmp_path / "three.txt").write_text("3\n", encoding="utf-8")
    (tmp_path / "docs" / "qa").mkdir(parents=True)
    (tmp_path / "docs" / "qa" / "отчёт.md").write_text("# Отчёт приёмки\n", encoding="utf-8")
    git("add", "three.txt", "docs")
    git("commit", "-qm", "Починить возврат в чат после файла")
    return tmp_path


def test_draft_skips_noise_and_splits_fixes(repo):
    draft = release_notes.draft_release_note(repo, "HEAD~3..HEAD", "K21-2026.09.09")
    assert "## K21-2026.09.09" in draft
    assert "Показывать доступный выпуск владельцу" in draft
    assert "lint: пробелы" not in draft
    # «Починить» уводит коммит в отдельный раздел.
    fixes = draft.split("### Что починили", 1)
    assert len(fixes) == 2 and "Починить возврат в чат после файла" in fixes[1]


def test_draft_lists_qa_reports_from_the_range(repo):
    draft = release_notes.draft_release_note(repo, "HEAD~3..HEAD", "K21-2026.09.09")
    assert "docs/qa/отчёт.md — Отчёт приёмки" in draft


def test_draft_leaves_the_revision_to_the_build(repo):
    """Черновик не подсовывает автору хэш, который к моменту сборки устареет."""
    draft = release_notes.draft_release_note(repo, "HEAD~3..HEAD", "K21-2026.09.09")
    assert f"- Ревизия: {release_notes.BUILD_REVISION_MARK}" in draft
