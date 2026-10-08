"""Doubles of the Bitrix24 and amoCRM HTTP APIs for tests (no network)."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib import parse

from korra_cli import crm_readers as cr

WEBHOOK = "https://acme.bitrix24.ru/rest/17/abcdef1234567890/"
AMO_TOKEN = "eyJhbGciOiJSUzI1NiJ9." + "eyJzdWIiOjcsImF1ZCI6IngifQ" + ".sig-secret-xyz"


def _nested(pairs: list[tuple[str, str]]) -> dict:
    """Undo the ``a[b][0]=c`` form encoding."""
    out: dict = {}
    for key, value in pairs:
        parts = [p for p in key.replace("]", "").split("[")]
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


class FakeBitrix:
    """``handlers[method](params) -> payload dict`` (or ``cr.Response``/Exception)."""

    def __init__(self, handlers: dict[str, Callable[[dict], Any]] | None = None) -> None:
        self.handlers = handlers or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, url, headers, body):
        name = url.rsplit("/", 1)[1].removesuffix(".json")
        params = _nested(parse.parse_qsl((body or b"").decode(), keep_blank_values=True))
        self.calls.append((name, params))
        handler = self.handlers.get(name)
        if handler is None:
            return cr.Response(200, {}, json.dumps({"error": "METHOD_NOT_FOUND"}).encode())
        answer = handler(params)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, cr.Response):
            return answer
        return cr.Response(200, {}, json.dumps(answer).encode())

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeAmo:
    """``handlers[path](query) -> payload`` where ``query`` is ``parse_qs`` of the URL."""

    def __init__(self, handlers: dict[str, Callable[[dict], Any]] | None = None) -> None:
        self.handlers = handlers or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, url, headers, body):
        assert method == "GET" and body is None
        parts = parse.urlsplit(url)
        path = parts.path.removeprefix("/api/v4/")
        query = parse.parse_qs(parts.query)
        self.calls.append((path, query))
        handler = self.handlers.get(path)
        if handler is None:
            return cr.Response(404, {}, b"{}")
        answer = handler(query)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, cr.Response):
            return answer
        if answer is None:
            return cr.Response(204, {}, b"")
        return cr.Response(200, {}, json.dumps(answer).encode())


def bitrix_probe_handlers(**over) -> dict[str, Callable[[dict], Any]]:
    base = {
        "profile": lambda p: {"result": {"NAME": "Анна", "LAST_NAME": "Миронова"}},
        "crm.deal.list": lambda p: {"result": [{"ID": "1"}], "total": 46},
        "crm.category.list": lambda p: {
            "result": {"categories": [{"id": 0, "name": "Продажи"}, {"id": 4, "name": "Партнёры"}]}
        },
        "user.get": lambda p: {"result": [{"ID": "1"}], "total": 8},
        "tasks.task.list": lambda p: {"result": {"tasks": []}, "total": 0},
    }
    base.update(over)
    return base


def amo_probe_handlers(**over) -> dict[str, Callable[[dict], Any]]:
    pipelines = {
        "_embedded": {
            "pipelines": [
                {
                    "id": 900,
                    "name": "Продажи",
                    "is_main": True,
                    "sort": 1,
                    "_embedded": {
                        "statuses": [
                            {"id": 1, "name": "Неразобранное", "type": 1},
                            {"id": 11, "name": "Заявка", "sort": 10},
                            {"id": 12, "name": "КП", "sort": 20},
                            {"id": 142, "name": "Успешно", "sort": 10000},
                            {"id": 143, "name": "Закрыто", "sort": 11000},
                        ]
                    },
                }
            ]
        }
    }
    base = {
        "account": lambda q: {"id": 1, "name": "ООО Ромашка", "subdomain": "acme"},
        "leads/pipelines": lambda q: pipelines,
        "users": lambda q: {"_embedded": {"users": [{"id": 7, "name": "Ирина"}, {"id": 8, "name": "Олег"}]}},
        "leads": lambda q: {"_embedded": {"leads": [{"id": i} for i in range(5)]}},
    }
    base.update(over)
    return base
