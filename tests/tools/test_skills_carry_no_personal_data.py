"""Бандл-скиллы не должны нести персональные данные и следы нашей инфраструктуры.

Скиллы из `skills/` попадают в образ и вместе с ним — на сервер клиента. Между
тем рядом, в личных контурах владельца, живут скиллы, написанные под конкретных
людей: договор с реквизитами самозанятой, ростер клиентов, разбор аварии с
именем клиента и адресом его сервера. Перенос скиллов между контурами делается
руками, и один неверный `cp -r` отправляет чужие паспортные данные незнакомому
человеку — необратимо, потому что образ уже у него.

Здесь проверяется класс, а не конкретные имена: список фамилий сам по себе
персональные данные, в репозитории ему не место. Он живёт в контуре
(`denylist.local.txt`) и проверяется отдельно, в смоуке образа перед
публикацией — см. `projects/Korra 21/scripts/smoke-candidate.sh`.
"""

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[2] / "skills"

#: Реквизиты рядом со своим названием. Голое число не годится: версии, даты и
#: номера портов дают ложные срабатывания пачками.
_REQUISITES = re.compile(
    r"(ИНН|ОГРНИП|ОГРН|БИК|КПП|Р/с|К/с|расч[её]тный\s+сч[её]т)\s*[:\-№]?\s*\d{9,20}",
    re.IGNORECASE,
)

#: Налоговый статус физлица — слово специфичное, в обучающем тексте не встречается.
_SELF_EMPLOYED = re.compile(r"самозанят", re.IGNORECASE)

#: Имена нашей инфраструктуры: хост сборки, приватный образ флота, контуры
#: клиентов. Публичные адреса исключены намеренно — организация на GitHub
#: (`ceremoneymeister-bit`) и домен сайтов (`ceremoneymeister.xyz`) обязаны
#: быть в образе: по ним работают самообновление и происхождение сборки.
_OUR_INFRA = re.compile(
    r"\bceremoneymeister(?!-bit|\.xyz)\b|\bkorra-agent-2\b"
    r"|\bkorra-victoria\b|\bkorra-vladislav\b",
    re.IGNORECASE,
)

#: Российский паспорт и СНИЛС в тексте.
_ID_DOCS = re.compile(r"(паспорт\s*(серия|№|:)|СНИЛС\s*[:\-]?\s*\d)", re.IGNORECASE)


def _skill_files() -> list[Path]:
    files: list[Path] = []
    for path in SKILLS.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip"}:
            continue
        files.append(path)
    return files


@pytest.fixture(scope="module")
def texts() -> list[tuple[Path, str]]:
    out = []
    for path in _skill_files():
        try:
            out.append((path, path.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue
    assert out, "дерево скиллов пусто — проверка ничего не проверяет"
    return out


def _hits(texts, pattern) -> list[str]:
    found = []
    for path, body in texts:
        match = pattern.search(body)
        if match:
            rel = path.relative_to(SKILLS.parent)
            # В сообщение об ошибке попадает путь и вид совпадения, но не сами
            # данные: отчёт о падении теста тоже кто-то прочитает.
            found.append(f"{rel} (совпадение вида «{match.group(1) if match.groups() else match.group(0)[:12]}»)")
    return found


class TestBundledSkillsAreImpersonal:
    def test_no_payment_requisites(self, texts):
        assert not _hits(texts, _REQUISITES)

    def test_no_self_employment_paperwork(self, texts):
        assert not _hits(texts, _SELF_EMPLOYED)

    def test_no_identity_documents(self, texts):
        assert not _hits(texts, _ID_DOCS)

    def test_no_internal_host_names(self, texts):
        """Имена наших серверов и приватных образов рассказывают клиенту то,
        чего ему знать незачем, и помогают целиться в наш флот."""
        assert not _hits(texts, _OUR_INFRA)

    def test_no_client_roster_file(self):
        """Ростер клиентов из контура не должен оказаться в поставке.

        Файл существует в личных контурах и сам себя описывает как «в коробку
        НЕ едет» — но лежит в дереве скиллов, а перенос делается руками.
        """
        assert not list(SKILLS.rglob("denylist.local.txt"))
