"""Guard external frontend imports in the filtered Nix web source."""

from __future__ import annotations

import re
from pathlib import Path

from tests.tools.test_dockerfile_frontend_build_context import (
    _cross_package_imports,
    _is_covered,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_NIX = REPO_ROOT / "nix/web.nix"
NIX_WEB_ORIGIN_PREFIXES = ("web/", "apps/shared/")


def _nix_web_sources(text: str) -> list[str]:
    uncommented = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    uncommented = re.sub(r"^\s*#.*$", "", uncommented, flags=re.M)
    match = re.search(r"\bdirs\s*=\s*\[(.*?)\];", uncommented, flags=re.S)
    assert match is not None, "список dirs не найден в nix/web.nix"
    return re.findall(r'"([^"\\]+)"', match.group(1))


def _missing_cross_package_imports(text: str) -> set[str]:
    sources = _nix_web_sources(text)
    return {
        path
        for origin, _, path in _cross_package_imports()
        if origin.startswith(NIX_WEB_ORIGIN_PREFIXES)
        if not _is_covered(path, sources)
    }


def test_nix_web_source_has_every_cross_package_import() -> None:
    missing = _missing_cross_package_imports(WEB_NIX.read_text(encoding="utf-8"))
    assert not missing, "Nix web source не содержит: " + ", ".join(sorted(missing))


def test_gate_rejects_the_pre_fix_nix_source_boundary() -> None:
    text = WEB_NIX.read_text(encoding="utf-8")
    pre_fix = re.sub(
        r'^\s*"korra_cli/data/dashboard-themes\.json"\s*\n',
        "",
        text,
        flags=re.M,
    )
    assert pre_fix != text, "явный Nix source path темы не найден"
    assert "korra_cli/data/dashboard-themes.json" in _missing_cross_package_imports(
        pre_fix
    )


def test_commented_source_path_does_not_satisfy_the_gate() -> None:
    text = WEB_NIX.read_text(encoding="utf-8")
    commented = re.sub(
        r'^(\s*)"korra_cli/data/dashboard-themes\.json"\s*$',
        r'\1/* "korra_cli/data/dashboard-themes.json" */',
        text,
        flags=re.M,
    )
    assert commented != text
    assert "korra_cli/data/dashboard-themes.json" in _missing_cross_package_imports(
        commented
    )
