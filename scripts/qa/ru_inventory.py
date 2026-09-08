#!/usr/bin/env python3
"""Инвентарь видимых строк: AST-кандидаты, поиск употреблений и таблица переводов.

Это инструмент аудита, а не фильтр вывода. Кандидаты требуют проверки читателя:
строки для модели, ключи и диагностические сообщения нельзя переводить автоматически.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ("gateway", "korra_cli", "plugins/platforms", "tools", "agent", "tui_gateway",
         "cli.py", "run_agent.py")
SINKS = {"print", "cprint", "prompt", "input", "confirm", "send", "send_message",
         "reply", "reply_text", "respond", "send_text", "send_notification",
         "add_argument", "add_parser", "ArgumentParser", "CommandDef", "Panel",
         "add_row", "add_column", "set_footer", "set_author", "Embed",
         "InlineKeyboardButton", "Button", "Text", "choose", "choice"}


def source_files(revision: str | None):
    if revision:
        names = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", revision, "--", *SCOPE], cwd=ROOT,
            text=True).splitlines()
        for name in names:
            if name.endswith(".py"):
                yield name, subprocess.check_output(
                    ["git", "show", f"{revision}:{name}"], cwd=ROOT, text=True)
    else:
        for scope in SCOPE:
            path = ROOT / scope
            for item in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
                yield str(item.relative_to(ROOT)), item.read_text(encoding="utf-8")


def candidates(revision: str | None):
    for file, source in source_files(revision):
        tree = ast.parse(source)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Constant, ast.JoinedStr)):
                continue
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.JoinedStr):
                continue  # Сохраняем целую f-строку, включая поля форматирования.
            if isinstance(parent, ast.Expr) and isinstance(parents.get(parent),
                    (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # docstring
            value = node.value if isinstance(node, ast.Constant) else ast.get_source_segment(source, node)
            if not re.search(r"[A-Za-z]{2}", value):
                continue
            chain = []
            cursor = parent
            while cursor is not None:
                chain.append(cursor)
                cursor = parents.get(cursor)
            calls = [item for item in chain if isinstance(item, ast.Call)]
            call_names = [ast.unparse(call.func) for call in calls]
            if any(re.search(r"(?:logger|logging|_log|log)\.(?:debug|info|warning|error|exception|critical)$", name)
                   for name in call_names):
                continue
            function = next((n.name for n in chain if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), "")
            marker = any(isinstance(item, ast.Compare) for item in chain[:3])
            marker |= any(name.rsplit(".", 1)[-1] in {"startswith", "endswith", "match", "fullmatch", "compile", "getenv"}
                          for name in call_names[:1])
            sink = any(name.rsplit(".", 1)[-1] in SINKS for name in call_names[:2])
            sink |= any(isinstance(item, ast.keyword) and item.arg in {"help", "description", "epilog", "label", "title"}
                        for item in chain[:2])
            prose = bool(re.search(r"[A-Za-z]{2}[ ,:!].*[A-Za-z]{2}", value))
            if not (sink or marker or prose):
                continue
            yield {"file": file, "line": node.lineno, "before": value,
                   "function": function,
                   "classification": "marker-review" if marker else "user-sink" if sink else "review",
                   "source": ast.get_source_segment(source, node)}


def usages(rows):
    """rg проверяет каждый переводимый литерал; содержимое секретных файлов не читается."""
    for row in rows:
        before = row["before"]
        # Многострочные литералы проверяем по содержательной строке; никакого shell.
        needle = max(before.splitlines(), key=len, default=before)
        result = subprocess.run(["rg", "-n", "-F", "--glob", "*.py", "--", needle, *SCOPE, "tests"],
                                cwd=ROOT, text=True, capture_output=True)
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr)
        row["rg_usages"] = result.stdout.splitlines()
    return rows


def markdown(rows):
    counts = collections.Counter(row.get("layer", "прочее") for row in rows)
    out = ["# Русификация Korra: инвентарь", "",
           "Одна строка таблицы — один переводимый литерал или шаблон. Имена API, команды,",
           "маркеры, логи и инструкции модели сохраняются. JSON рядом содержит данные для проверки.", "",
           "По слоям: " + "; ".join(f"{k}: {v}" for k, v in sorted(counts.items())), "",
           "| Файл:строка | Было | Станет | Основание |", "|---|---|---|---|"]
    def cell(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", "<br>")
    for row in sorted(rows, key=lambda r: (r["file"], r["line"])):
        out.append("| " + " | ".join(cell(v) for v in (
            f'{row["file"]}:{row["line"]}', row["before"], row["after"], row.get("reason", "Пользовательский текст"))) + " |")
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="Базовый коммит для инвентаризации до правок")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plans", type=Path, nargs="*", help="Объединить проверенные планы переводов")
    parser.add_argument("--usages", action="store_true", help="Найти rg все употребления переводимых строк")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.plans:
        rows = []
        for plan in args.plans:
            layer = plan.stem.removeprefix("ru-inventory-")
            for row in json.loads(plan.read_text(encoding="utf-8")):
                rows.append({**row, "layer": layer})
        if args.usages:
            usages(rows)
        (args.output / "ru-inventory.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (args.output / "ru-inventory.md").write_text(markdown(rows), encoding="utf-8")
    else:
        rows = list(candidates(args.revision))
        (args.output / "ru-candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"строк": len(rows), "каталог": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
