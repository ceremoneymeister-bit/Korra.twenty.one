"""Real sockets and TLS, real discovery/config/base dispatch; no model or MAX API."""
import asyncio
import json
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest


def certificates(root):
    ca, cert, key = (root / name for name in ("ca.pem", "server.pem", "server.key"))
    ca_key, csr, ext = (root / name for name in ("ca.key", "server.csr", "extensions.cnf"))
    ext.write_text("subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n")
    commands = [
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=MAX test CA",
         "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", str(ca_key), "-out", str(ca)],
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(csr)],
        ["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial",
         "-days", "1", "-extfile", str(ext), "-out", str(cert)],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True)
    return ca, cert, key


@pytest.mark.asyncio
async def test_poll_dispatch_reply_and_reject_bad_tls(tmp_path, monkeypatch):
    from gateway.config import Platform, load_gateway_config
    from gateway.platform_registry import platform_registry
    from korra_cli.plugins import discover_plugins
    from korra_constants import get_hermes_home

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text("platforms:\n  max:\n    enabled: true\n    extra:\n      auto_reply: false\n      listen_host: ignored\n      ca_root: /missing/legacy.pem\n      expected_bot_id: 999\n")
    monkeypatch.setenv("MAX_BOT_TOKEN", "fixture-token")
    monkeypatch.setenv("MAX_ALLOWED_USERS", "12")
    monkeypatch.setenv("KORRA_GATEWAY_LOCK_DIR", str(tmp_path / "locks"))
    discover_plugins()
    config = load_gateway_config()
    entry = platform_registry.get("max")
    module = sys.modules[entry.adapter_factory.__module__]
    api = sys.modules[module.MaxClient.__module__]
    ca, cert, key = certificates(tmp_path)
    requests, posts, seen = [], [], []
    delivered = asyncio.Event()
    loop = asyncio.get_running_loop()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/me":
                self.reply({"user_id": 99, "is_bot": True, "first_name": "Тестовый бот"})
            else:
                assert urlsplit(self.path).path == "/updates"
                query = parse_qs(urlsplit(self.path).query)
                updates = []
                if query.get("marker") == ["100"]:
                    event = {"update_type": "message_created", "timestamp": 1700000000000,
                             "message": {"sender": {"user_id": 12, "first_name": "Марина"},
                                         "recipient": {"chat_id": -77, "chat_type": "dialog"},
                                         "body": {"mid": "incoming", "text": "Привет"}}}
                    updates = [event, event]  # replay in a batch must not run twice
                self.reply({"updates": updates, "marker": 101})

        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization")))
            posts.append((parse_qs(urlsplit(self.path).query), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.reply({"message": {"body": {"mid": "answer"}}})
            loop.call_soon_threadsafe(delivered.set)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(api, "API_URL", f"https://localhost:{server.server_port}")
    monkeypatch.setattr(api, "ssl_context", lambda host: ssl.create_default_context(cafile=ca))
    checkpoint = home / "gateway" / "max-99-marker.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text('{"marker": 100}')
    adapter = platform_registry.create_adapter("max", config.platforms[Platform("max")])

    async def agent_handler(event):
        # This is the agent boundary; no model runs in a transport test.
        seen.append(event)
        return "Ответ агентa"

    adapter.set_message_handler(agent_handler)
    adapter.set_authorization_check(lambda uid, *args: uid == "12")
    try:
        assert await adapter.connect(), adapter.fatal_error_message
        await asyncio.wait_for(delivered.wait(), timeout=15)
        assert len(seen) == 1 and seen[0].text == "Привет"
        assert seen[0].source.chat_type == "dm"
        assert posts == [({"chat_id": ["-77"]}, {"text": "Ответ агентa", "link": {"type": "reply", "mid": "incoming"}})]
        assert all(token == "fixture-token" for _, token in requests)
        assert any("marker=100" in path and "timeout=90" in path for path, _ in requests)
        assert json.loads(checkpoint.read_text())["marker"] == 101
        await adapter.disconnect()
        assert adapter._poll_task is None and adapter._client is None

        # A trusted root must not disable hostname checking; system trust alone
        # must not accept our private test issuer. No insecure TLS switch exists.
        for host, trusted in [("127.0.0.1", True), ("localhost", False)]:
            monkeypatch.setattr(api, "API_URL", f"https://{host}:{server.server_port}")
            monkeypatch.setattr(api, "ssl_context", lambda h: ssl.create_default_context(cafile=ca) if trusted else ssl.create_default_context())
            bad = api.MaxClient("fixture-token")
            try:
                with pytest.raises(api.MaxError):
                    await bad.me()
            finally:
                await bad.close()
    finally:
        await adapter.disconnect()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
