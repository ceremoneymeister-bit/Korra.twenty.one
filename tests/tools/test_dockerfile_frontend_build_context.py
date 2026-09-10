"""Гейт границы Docker context для frontend build (K21-021).

`npm run build` внутри образа видит не рабочее дерево, а только то, что
скопировано в него ПРЕДЫДУЩИМИ инструкциями `COPY`. Полный исходник приходит
позже, в `COPY . .`, поэтому импорт панели за пределы своего пакета собирается
локально и падает в образе: `tsc -b` даёт `TS2307`, и сборка не доходит до
image. Обычный CI такой разрыв не ловит — он собирает web из полного checkout.

Здесь проверяется ровно эта граница: каждый файл, который frontend-исходники
импортируют за пределами своего пакета, обязан попасть в контекст ДО шага
сборки. Проверка статическая и не требует Docker.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"

# Деревья, которые компилирует шаг frontend build, и корень пакета для каждого.
# web/tsconfig.app.json включает `src` вместе с тестами, а tsconfig.node.json —
# `vite.config.ts`, поэтому в обход идут все они.
FRONTEND_TREES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("web", ("web/src", "web/vite.config.ts", "web/vitest.config.ts")),
    ("ui-tui", ("ui-tui/src",)),
    ("apps/shared", ("apps/shared/src",)),
)

SOURCE_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})

# `import x from "y"`, `import "y"`, `export ... from "y"`, `require("y")`.
_SPEC_RE = re.compile(r"""(?:\bfrom|\bimport|\brequire\()\s*["']([^"']+)["']""")


def _instructions(text: str) -> list[str]:
    """Инструкции Dockerfile со склеенными продолжениями строк."""
    out: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not buffer and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        buffer += line
        out.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return out


def _frontend_build_index(instructions: list[str]) -> int:
    for index, instruction in enumerate(instructions):
        if instruction.upper().startswith("RUN ") and re.search(
            r"cd\s+web\s*&&\s*npm run build", instruction
        ):
            return index
    raise AssertionError("шаг `cd web && npm run build` не найден в Dockerfile")


def _context_sources(instructions: list[str], before: int) -> list[str]:
    """Пути из build context, скопированные до указанной инструкции."""
    sources: list[str] = []
    for instruction in instructions[:before]:
        if not instruction.upper().startswith("COPY "):
            continue
        operands = instruction.split()[1:]
        if any(operand.startswith("--from=") for operand in operands):
            continue  # копия из другой стадии, не из build context
        paths = [operand for operand in operands if not operand.startswith("--")]
        sources.extend(paths[:-1])  # последний операнд — назначение
    return sources


def _is_covered(path: str, sources: list[str]) -> bool:
    target = PurePosixPath(path)
    for source in sources:
        normalized = source.removeprefix("./").rstrip("/")
        if normalized in {"", "."}:
            return True
        if path == normalized or target.is_relative_to(PurePosixPath(normalized)):
            return True
    return False


def _resolve(source_file: Path, spec: str) -> Path | None:
    """Файл, в который упирается относительный импорт (bundler-разрешение)."""
    base = (source_file.parent / spec).resolve()
    candidates = [base]
    if base.suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        # NodeNext-стиль: в исходнике `.js`, на диске `.ts`.
        candidates += [base.with_suffix(suffix) for suffix in (".ts", ".tsx")]
    candidates += [
        base.with_name(base.name + suffix)
        for suffix in (".ts", ".tsx", ".js", ".jsx", ".json", ".mjs")
    ]
    candidates += [base / f"index{suffix}" for suffix in (".ts", ".tsx", ".js", ".jsx")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _tree_files(tree: str) -> list[Path]:
    path = REPO_ROOT / tree
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return [
        item
        for item in sorted(path.rglob("*"))
        if item.is_file()
        and item.suffix in SOURCE_SUFFIXES
        and "node_modules" not in item.parts
    ]


def _cross_package_imports() -> list[tuple[str, str, str]]:
    """(файл-исходник, спецификатор, путь в репозитории) для импортов наружу."""
    found: list[tuple[str, str, str]] = []
    for package_root, trees in FRONTEND_TREES:
        prefix = f"{package_root}/"
        for tree in trees:
            for source_file in _tree_files(tree):
                text = source_file.read_text(encoding="utf-8", errors="ignore")
                for spec in _SPEC_RE.findall(text):
                    if not spec.startswith("."):
                        continue  # пакет из node_modules или alias, не путь
                    resolved = _resolve(source_file, spec)
                    origin = source_file.relative_to(REPO_ROOT).as_posix()
                    assert resolved is not None, (
                        f"{origin}: импорт {spec!r} не разрешается ни в один файл "
                        "репозитория"
                    )
                    relative = resolved.relative_to(REPO_ROOT).as_posix()
                    if relative.startswith(prefix):
                        continue  # внутри своего пакета — граница не задета
                    found.append((origin, spec, relative))
    return found


def _missing_from_build_context(dockerfile_text: str) -> list[tuple[str, str, str]]:
    instructions = _instructions(dockerfile_text)
    sources = _context_sources(instructions, _frontend_build_index(instructions))
    return [
        entry
        for entry in _cross_package_imports()
        if not _is_covered(entry[2], sources)
    ]


def test_frontend_build_context_has_every_cross_package_import() -> None:
    missing = _missing_from_build_context(DOCKERFILE.read_text(encoding="utf-8"))
    assert not missing, (
        "frontend build в образе не увидит эти файлы — добавьте COPY до "
        "`npm run build`: "
        + "; ".join(f"{origin} → {spec} ({path})" for origin, spec, path in missing)
    )


def test_dashboard_theme_data_is_a_cross_package_import() -> None:
    """Тема панели действительно берётся из данных бэкенда.

    Если этот источник когда-нибудь переедет внутрь web/, гейт выше перестанет
    что-либо охранять молча. Здесь зафиксировано, что охранять пока есть что.
    """
    paths = {path for _, _, path in _cross_package_imports()}
    assert "korra_cli/data/dashboard-themes.json" in paths


def test_gate_fails_on_the_pre_fix_dockerfile_order() -> None:
    """Гейт кусается ровно на прежнем порядке инструкций (K21-021).

    До исправления `korra_cli/` целиком приходил только с поздним `COPY . .`,
    и сборка образа падала на `TS2307`. Убираем раннюю копию темы из текста —
    проверка обязана это увидеть.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    pre_fix = re.sub(
        r"^COPY korra_cli/data/dashboard-themes\.json .*\n", "", text, flags=re.M
    )
    assert pre_fix != text, "ранний COPY темы не найден в Dockerfile"

    missing = {path for _, _, path in _missing_from_build_context(pre_fix)}
    assert "korra_cli/data/dashboard-themes.json" in missing


def test_theme_data_copy_precedes_the_frontend_build() -> None:
    instructions = _instructions(DOCKERFILE.read_text(encoding="utf-8"))
    sources = _context_sources(instructions, _frontend_build_index(instructions))
    assert "korra_cli/data/dashboard-themes.json" in sources, (
        "тема должна копироваться отдельной ранней инструкцией, а не только "
        "поздним `COPY . .`"
    )
