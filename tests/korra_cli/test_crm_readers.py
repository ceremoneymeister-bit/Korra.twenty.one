"""Bitrix24 / amoCRM readers: parsing, limits, read-only allowlist, errors, socket route."""

from __future__ import annotations

import json
import socket
import ssl
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib import parse

import pytest

from korra_cli import crm_readers as cr

WEBHOOK = "https://acme.bitrix24.ru/rest/17/abcdef1234567890/"


@pytest.fixture(autouse=True)
def _isolated(no_real_network):
    cr.reset_pace()
    yield
    cr.reset_pace()


class Script:
    """Transport double: plays answers in order and records the requests."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def ok(payload, status=200, headers=None):
    return cr.Response(status, headers or {}, json.dumps(payload).encode())


def bitrix(*answers, **kwargs):
    script = Script(*answers)
    reader = cr.Bitrix24Reader(WEBHOOK, transport=script, sleep=lambda s: None, **kwargs)
    return reader, script


def amo(*answers, **kwargs):
    script = Script(*answers)
    reader = cr.AmoReader("acme.amocrm.ru", "tok-secret-1", transport=script, sleep=lambda s: None, **kwargs)
    return reader, script


# ------------------------------------------------------------ addresses


@pytest.mark.parametrize(
    "value",
    [
        "http://acme.bitrix24.ru/rest/17/abcdef1234567890/",
        "https://127.0.0.1/rest/17/abcdef1234567890/",
        "https://user:pw@acme.bitrix24.ru/rest/17/abcdef1234567890/",
        "https://acme.bitrix24.ru:8443/rest/17/abcdef1234567890/",
        "https://acme.bitrix24.ru/rest/17/abcdef1234567890/?x=1",
        "https://acme.bitrix24.ru/rest/17/",
        "https://acme.bitrix24.ru/",
        "",
        "not a url",
    ],
)
def test_bitrix_rejects_foreign_or_malformed_addresses(value):
    with pytest.raises(cr.CrmError) as caught:
        cr.parse_bitrix_webhook(value)
    assert caught.value.code == "bad_url"


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example.com/rest/17/abcdef1234567890/",
        "https://acme.bitrix24.ru.evil.com/rest/17/abcdef1234567890/",
    ],
)
def test_bitrix_foreign_domain_is_reported_as_unsupported(value):
    with pytest.raises(cr.CrmError) as caught:
        cr.parse_bitrix_webhook(value)
    assert caught.value.code == "self_hosted"


def test_bitrix_accepts_vendor_domains():
    assert cr.parse_bitrix_webhook(WEBHOOK) == ("acme.bitrix24.ru", WEBHOOK)
    host, base = cr.parse_bitrix_webhook("https://x-1.bitrix24.com.br/rest/1/abcdefgh12345678")
    assert host == "x-1.bitrix24.com.br" and base.endswith("/rest/1/abcdefgh12345678/")


@pytest.mark.parametrize("value", ["http://acme.amocrm.ru", "evil.com", "acme.amocrm.ru.evil.com", "10.0.0.1", ""])
def test_amo_rejects_foreign_domains(value):
    with pytest.raises(cr.CrmError) as caught:
        cr.parse_amo_domain(value)
    assert caught.value.code == "bad_url"


def test_amo_accepts_url_or_host():
    assert cr.parse_amo_domain("https://Acme.amoCRM.ru/") == "acme.amocrm.ru"
    assert cr.parse_amo_domain("acme.kommo.com") == "acme.kommo.com"


# ------------------------------------------------------------ Bitrix24


def test_bitrix_call_posts_form_to_method_url():
    reader, script = bitrix(ok({"result": {"NAME": "Анна"}}))
    assert reader.call("profile") == {"result": {"NAME": "Анна"}}
    method, url, headers, body = script.requests[0]
    assert (method, url) == ("POST", WEBHOOK + "profile.json")
    assert "abcdef" not in (body or b"").decode()


def test_bitrix_flattens_nested_params():
    reader, script = bitrix(ok({"result": []}))
    reader.call("crm.deal.list", {"filter": {"STAGE_SEMANTIC_ID": "P", ">=X": "1"}, "select": ["ID", "TITLE"]})
    body = parse.parse_qs(script.requests[0][3].decode())
    assert body["filter[STAGE_SEMANTIC_ID]"] == ["P"]
    assert body["select[1]"] == ["TITLE"]


@pytest.mark.parametrize(
    "method",
    ["crm.deal.add", "crm.deal.update", "crm.deal.delete", "tasks.task.add", "batch", "user.update", "crm.lead.add", ""],
)
def test_bitrix_write_or_unknown_methods_are_not_sent(method):
    reader, script = bitrix(ok({"result": []}))
    with pytest.raises(cr.CrmError) as caught:
        reader.call(method)
    assert caught.value.code == "not_allowed"
    assert script.requests == []


@pytest.mark.parametrize("key", ["auth", "cmd", "method", "url", "access_token", "AUTH"])
def test_bitrix_forbidden_param_names(key):
    reader, script = bitrix(ok({"result": []}))
    with pytest.raises(cr.CrmError):
        reader.call("crm.deal.list", {key: "x"})
    assert script.requests == []


def test_bitrix_collect_follows_next_and_reports_total():
    reader, script = bitrix(
        ok({"result": [{"ID": "1"}, {"ID": "2"}], "next": 2, "total": 3}),
        ok({"result": [{"ID": "3"}], "total": 3}),
    )
    out = reader.collect("crm.deal.list", {"select": ["ID"]})
    assert [d["ID"] for d in out["items"]] == ["1", "2", "3"]
    assert out == {"items": out["items"], "total": 3, "truncated": False}
    assert parse.parse_qs(script.requests[1][3].decode())["start"] == ["2"]


def test_bitrix_collect_stops_at_page_cap_and_says_so():
    reader, script = bitrix(
        ok({"result": [{"ID": "1"}], "next": 50, "total": 500}),
        ok({"result": [{"ID": "2"}], "next": 100, "total": 500}),
        ok({"result": [{"ID": "3"}], "next": 150, "total": 500}),
    )
    out = reader.collect("crm.deal.list", max_pages=3)
    assert out["truncated"] is True and out["total"] == 500 and len(script.requests) == 3
    assert len(out["items"]) == 3


def test_bitrix_collect_unwraps_nested_task_lists():
    reader, _ = bitrix(ok({"result": {"tasks": [{"id": "5"}]}, "total": 1}))
    assert reader.collect("tasks.task.list")["items"] == [{"id": "5"}]


def test_bitrix_collect_detects_cursor_loop():
    reader, _ = bitrix(ok({"result": [{"ID": "1"}], "next": 0}))
    with pytest.raises(cr.CrmError) as caught:
        reader.collect("crm.deal.list")
    assert caught.value.code == "protocol"


def test_bitrix_request_budget():
    reader, script = bitrix(ok({"result": []}), max_requests=2)
    reader.call("profile")
    reader.call("profile")
    with pytest.raises(cr.CrmError) as caught:
        reader.call("profile")
    assert caught.value.code == "budget" and len(script.requests) == 2


def test_bitrix_redirect_is_not_followed():
    reader, script = bitrix(cr.Response(302, {"location": "https://evil.example/"}, b""))
    with pytest.raises(cr.CrmError) as caught:
        reader.call("profile")
    assert caught.value.code == "redirect" and len(script.requests) == 1


def test_bitrix_oversized_answer():
    reader, _ = bitrix(cr.Response(200, {}, b"x" * (cr.MAX_BYTES + 1)))
    with pytest.raises(cr.CrmError) as caught:
        reader.call("profile")
    assert caught.value.code == "limit"


@pytest.mark.parametrize(
    "answer, code",
    [
        (ok({"error": "INVALID_CREDENTIALS", "error_description": "x"}, 401), "bad_key"),
        (ok({"error": "NO_AUTH_FOUND"}, 401), "bad_key"),
        (ok({"error": "INSUFFICIENT_SCOPE", "error_description": "scope"}, 403), "forbidden"),
        (ok({"error": "ACCESS_DENIED"}, 200), "forbidden"),
        (ok({"error": "ACCESS_DENIED", "error_description": "REST is available only on commercial plans"}, 403), "plan_closed"),
        (ok({"error": "x"}, 402), "plan_closed"),
        (cr.Response(200, {}, b"<html>not json</html>"), "protocol"),
    ],
)
def test_bitrix_error_codes(answer, code):
    reader, _ = bitrix(answer)
    with pytest.raises(cr.CrmError) as caught:
        reader.call("profile")
    assert caught.value.code == code


def test_bitrix_rate_limit_retries_once_then_pauses():
    reader, script = bitrix(ok({"error": "QUERY_LIMIT_EXCEEDED"}, 503))
    with pytest.raises(cr.CrmError) as caught:
        reader.call("profile")
    assert caught.value.code == "rate_limited" and len(script.requests) == 2
    # the pause is shared: a new reader for the same portal does not hammer it
    other, other_script = bitrix(ok({"result": 1}))
    with pytest.raises(cr.CrmError) as again:
        other.call("profile")
    assert again.value.code == "rate_limited" and other_script.requests == []


def test_bitrix_rate_limit_recovers_on_retry():
    reader, script = bitrix(ok({"error": "QUERY_LIMIT_EXCEEDED"}, 503), ok({"result": 7}))
    assert reader.call("profile")["result"] == 7 and len(script.requests) == 2


def test_bitrix_network_retries_once_then_network():
    reader, script = bitrix(OSError("boom"))
    with pytest.raises(cr.CrmError) as caught:
        reader.call("profile")
    assert caught.value.code == "network" and len(script.requests) == 2
    assert "acme" not in str(caught.value) and "abcdef" not in str(caught.value)


def test_bitrix_server_error_then_ok():
    reader, _ = bitrix(cr.Response(502, {}, b"bad gateway"), ok({"result": 1}))
    assert reader.call("profile")["result"] == 1


def test_bitrix_answers_are_scrubbed():
    payload = {
        "result": [
            {
                "TITLE": "см. https://acme.bitrix24.ru/rest/17/abcdef1234567890/crm.deal.get",
                "download_url": "https://x/y",
                "NOTE": "https://files.example/a?auth=SECRET&x=1",
            }
        ]
    }
    reader, _ = bitrix(ok(payload))
    text = json.dumps(reader.call("crm.deal.list"))
    assert "abcdef1234567890" not in text and "SECRET" not in text and "https://x/y" not in text


def test_deal_url_points_into_portal():
    reader, _ = bitrix(ok({}))
    assert reader.deal_url(42) == "https://acme.bitrix24.ru/crm/deal/details/42/"


# ------------------------------------------------------------ amoCRM


def test_amo_get_sends_bearer_and_only_reads():
    reader, script = amo(ok({"id": 1, "name": "acme"}))
    assert reader.get("account", {"with": "users"})["name"] == "acme"
    method, url, headers, body = script.requests[0]
    assert method == "GET" and body is None
    assert url == "https://acme.amocrm.ru/api/v4/account?with=users"
    assert headers["Authorization"] == "Bearer tok-secret-1"


@pytest.mark.parametrize(
    "path",
    ["leads/1/link", "contacts", "companies", "leads/pipelines/1/statuses", "../oauth2/access_token", "webhooks", "calls", "leads/abc"],
)
def test_amo_paths_outside_allowlist_are_not_sent(path):
    reader, script = amo(ok({}))
    with pytest.raises(cr.CrmError) as caught:
        reader.get(path)
    assert caught.value.code == "not_allowed" and script.requests == []


def test_amo_has_no_write_verb():
    reader, script = amo(ok({}))
    assert not hasattr(reader, "post") and not hasattr(reader, "patch")
    reader.get("leads")
    assert {r[0] for r in script.requests} == {"GET"}


def test_amo_paged_stops_on_short_page():
    first = ok({"_embedded": {"leads": [{"id": i} for i in range(3)]}})
    reader, script = amo(first)
    out = reader.paged("leads", {"filter[statuses][0][pipeline_id]": 5}, "leads", limit=100, page_size=3)
    # full page -> second page requested; the same answer again would loop to the cap
    assert len(script.requests) >= 2
    short = ok({"_embedded": {"leads": [{"id": 9}]}})
    reader, script = amo(first, short)
    out = reader.paged("leads", None, "leads", limit=100, page_size=3)
    assert [d["id"] for d in out["items"]] == [0, 1, 2, 9] and out["truncated"] is False
    assert "page=2" in script.requests[1][1] and "limit=3" in script.requests[1][1]


def test_amo_paged_truncates_at_limit():
    full = ok({"_embedded": {"leads": [{"id": i} for i in range(5)]}})
    reader, script = amo(full)
    out = reader.paged("leads", None, "leads", limit=7, page_size=5)
    assert len(out["items"]) == 7 and out["truncated"] is True and len(script.requests) == 2


def test_amo_paged_empty_204():
    reader, _ = amo(cr.Response(204, {}, b""))
    assert reader.paged("leads", None, "leads", limit=10) == {"items": [], "truncated": False}


@pytest.mark.parametrize(
    "status, code",
    [(401, "bad_key"), (403, "forbidden"), (402, "plan_closed"), (429, "rate_limited"), (301, "redirect")],
)
def test_amo_status_codes(status, code):
    reader, _ = amo(cr.Response(status, {"location": "https://evil/"}, b"{}"))
    with pytest.raises(cr.CrmError) as caught:
        reader.get("account")
    assert caught.value.code == code


def test_amo_429_pauses_the_portal():
    reader, _ = amo(cr.Response(429, {"retry-after": "30"}, b"{}"))
    with pytest.raises(cr.CrmError):
        reader.get("account")
    other, other_script = amo(ok({}))
    with pytest.raises(cr.CrmError) as caught:
        other.get("account")
    assert caught.value.code == "rate_limited" and other_script.requests == []


def test_amo_server_error_retries_once_then_network():
    reader, script = amo(cr.Response(500, {}, b""), cr.Response(503, {}, b""))
    with pytest.raises(cr.CrmError) as caught:
        reader.get("account")
    assert caught.value.code == "network" and len(script.requests) == 2


def test_amo_network_error_hides_details():
    reader, _ = amo(OSError("connect to 1.2.3.4 failed"))
    with pytest.raises(cr.CrmError) as caught:
        reader.get("account")
    assert caught.value.code == "network" and "1.2.3.4" not in str(caught.value)
    assert "tok-secret-1" not in repr(caught.value.__dict__)


def test_amo_bad_json_is_protocol():
    reader, _ = amo(cr.Response(200, {}, b"<html>"))
    with pytest.raises(cr.CrmError) as caught:
        reader.get("account")
    assert caught.value.code == "protocol"


def test_amo_budget():
    reader, script = amo(ok({}), max_requests=1)
    reader.get("account")
    with pytest.raises(cr.CrmError) as caught:
        reader.get("account")
    assert caught.value.code == "budget"


def test_amo_token_must_look_like_a_token():
    with pytest.raises(cr.CrmError) as caught:
        cr.AmoReader("acme.amocrm.ru", "has space", transport=Script(ok({})))
    assert caught.value.code == "bad_key"
    with pytest.raises(cr.CrmError):
        cr.AmoReader("acme.amocrm.ru", "", transport=Script(ok({})))


def test_amo_deal_url():
    reader, _ = amo(ok({}))
    assert reader.deal_url(7) == "https://acme.amocrm.ru/leads/detail/7"


# ------------------------------------------------------------ amoCRM over a local socket


class _FakeSSL(SimpleNamespace):
    pass


def _short_dir():
    return tempfile.TemporaryDirectory(prefix="k21")


def test_amo_socket_route_connects_to_socket_with_account_sni(monkeypatch):
    seen = {}

    class Context:
        verify_mode = ssl.CERT_REQUIRED
        check_hostname = True

        def wrap_socket(self, sock, server_hostname=None):
            seen["sni"] = server_hostname
            return sock

    monkeypatch.setattr(
        cr, "ssl", _FakeSSL(create_default_context=lambda: Context(), SSLError=ssl.SSLError)
    )
    with _short_dir() as tmp:
        path = str(Path(tmp) / "amo.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        seen["requests"] = []

        def serve():
            conn, _ = server.accept()
            with conn:
                data = b""
                while b"\r\n\r\n" not in data:
                    data += conn.recv(4096)
                seen["requests"].append(data.decode())
                body = b'{"id": 5, "name": "acme"}'
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        reader = cr.AmoReader("acme.amocrm.ru", "tok-secret-1", unix_socket=path)
        try:
            assert reader.get("account")["name"] == "acme"
        finally:
            reader.close()
            thread.join(2)
            server.close()
    assert seen["sni"] == "acme.amocrm.ru"
    assert seen["requests"][0].startswith("GET /api/v4/account HTTP/1.1")
    assert "Host: acme.amocrm.ru" in seen["requests"][0]


def test_amo_socket_must_be_absolute():
    with pytest.raises(cr.CrmError) as caught:
        cr.AmoReader("acme.amocrm.ru", "tok", unix_socket="relative.sock")
    assert caught.value.code == "bad_url"


def test_amo_missing_socket_is_a_network_error_not_a_dns_lookup():
    reader = cr.AmoReader("acme.amocrm.ru", "tok", unix_socket="/nonexistent/amo.sock", sleep=lambda s: None)
    with pytest.raises(cr.CrmError) as caught:
        reader.get("account")
    assert caught.value.code == "network"


def test_amo_direct_route_tries_next_address(monkeypatch):
    attempts = []

    def getaddrinfo(host, port, **kw):
        return [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("8.8.4.4", 443))]

    class Failing(cr.AmoTransport):
        def _connect(self, address):
            attempts.append(address)
            raise OSError("down")

    monkeypatch.setattr(cr.socket, "getaddrinfo", getaddrinfo)
    transport = Failing("acme.amocrm.ru")
    with pytest.raises(OSError):
        transport("GET", "https://acme.amocrm.ru/api/v4/account", {}, None)
    assert attempts[:2] == ["8.8.8.8", "8.8.4.4"]
    assert len(attempts) == 2 * cr.HANDSHAKE_ATTEMPTS


# ------------------------------------------------------------ messages


def test_described_errors_are_russian_and_carry_no_secret():
    for code in sorted(cr.ERROR_CODES):
        info = cr.describe_error(code, cr.AMOCRM, "acme.amocrm.ru")
        assert info["title"] and info["message"]
        assert "{" not in info["title"] + info["message"]
        assert any("а" <= ch.lower() <= "я" for ch in info["message"])


def test_plan_closed_for_bitrix_mentions_marketplace():
    assert "Маркетплейс" in cr.describe_error("plan_closed", cr.BITRIX)["message"]


def test_network_is_retryable_and_names_the_portal():
    info = cr.describe_error("network", cr.AMOCRM, "acme.amocrm.ru")
    assert info["retry"] is True and "acme.amocrm.ru" in info["message"]
    assert cr.describe_error("bad_key", cr.BITRIX)["retry"] is False
