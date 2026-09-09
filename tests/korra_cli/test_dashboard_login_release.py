from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from korra_cli import web_server as ws
from korra_cli.dashboard_auth import login_page


class Forms(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.forms = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.forms.append(dict(attrs))


def test_password_form_never_uses_url_credentials(monkeypatch):
    monkeypatch.setattr(login_page, 'list_session_providers', lambda: [
        SimpleNamespace(name='synthetic', supports_password=True)])
    monkeypatch.setattr(login_page, 'current_greeting', lambda: 'Добрый вечер')
    form, = Forms(login_page.render_login_html(base_path='/c/test')).forms
    assert form.get('method', 'get').lower() == 'post'
    assert form['action'] == '/c/test/auth/password-login'


@pytest.fixture
def brand_client(tmp_path, monkeypatch):
    dist = tmp_path / 'web_dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'brand').mkdir()
    (dist / 'index.html').write_text('<html><head></head><body>SPA</body></html>')
    (dist / 'brand/korra-wordmark.png').write_bytes(b'synthetic-png')
    (dist / 'brand/private.txt').write_text('not a public brand asset')
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'outside')
    (dist / 'brand/korra-21.png').symlink_to(outside)
    monkeypatch.setattr(ws, 'WEB_DIST', dist)
    monkeypatch.setattr(ws, 'load_config', lambda: {})
    app = FastAPI()
    ws.mount_spa(app)
    return TestClient(app)


@pytest.mark.parametrize('path', [
    '/brand/missing.png', '/brand/private.txt', '/brand/',
    '/brand/nested/korra-wordmark.png', '/brand/korra-21.png'])
def test_only_existing_allowlisted_brand_files_are_served(brand_client, path):
    response = brand_client.get(path)
    assert response.status_code == 404
    assert 'SPA' not in response.text


def test_brand_asset_remains_public_and_prefix_independent(brand_client):
    response = brand_client.get('/brand/korra-wordmark.png',
        headers={'X-Forwarded-Prefix': '/c/test'})
    assert response.status_code == 200
    assert response.content == b'synthetic-png'
    assert response.headers['content-type'] == 'image/png'
