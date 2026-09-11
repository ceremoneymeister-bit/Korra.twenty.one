"""Образ обязан честно называть свою версию.

`korra --version` и панель печатают `korra_cli.__version__`; `uv sync --frozen`
и метаданные пакета берут версию из `pyproject.toml`, а она же попадает в
`uv.lock`. Это три независимые записи одного числа, и до 0.21.2 их ничто не
сверяло: выпуск 0.21.2 собрался и раскатился, называя себя 0.21.1, потому что
подняли только `pyproject.toml`. Версия — часть идентичности неизменяемого
артефакта, и расхождение в ней делает приёмку и разбор инцидента гаданием.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _lock_version() -> str:
    data = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    for package in data.get("package", []):
        if package.get("name") == "hermes-agent":
            return str(package["version"])
    raise AssertionError("uv.lock не описывает сам проект")


def test_cli_version_matches_packaging_metadata():
    from korra_cli import __version__

    assert __version__ == _pyproject_version(), (
        "korra_cli.__version__ и pyproject.toml называют разные версии: "
        f"{__version__} против {_pyproject_version()}"
    )


def test_lockfile_version_matches_packaging_metadata():
    assert _lock_version() == _pyproject_version(), (
        "uv.lock отстал от pyproject.toml — запустите `uv lock` и закоммитьте: "
        f"{_lock_version()} против {_pyproject_version()}"
    )
