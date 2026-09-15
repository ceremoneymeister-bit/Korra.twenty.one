"""Real HTTP paths: files, chat attachment admission, and download policy."""
from pathlib import Path
from urllib.parse import quote

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient
from korra_cli import web_server as server


@pytest.fixture
def files(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    root.mkdir()
    monkeypatch.setenv('KORRA_HOME', str(root))
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setenv('KORRA_DASHBOARD_FILES_ROOT', str(root))
    monkeypatch.setenv('KORRA_UI_MODE', 'fleet')
    monkeypatch.setattr(server.app.state, 'auth_required', False, raising=False)
    monkeypatch.setattr(server.app.state, 'bound_host', None, raising=False)
    client = TestClient(server.app)
    try:
        client.headers[server._SESSION_HEADER_NAME] = server._SESSION_TOKEN
        workspace = Path(client.get('/api/files').json()['path'])
        assert workspace == root / 'workspace'
        yield client, root, workspace
    finally:
        client.close()


@pytest.mark.parametrize('style', ['MEDIA', 'path', 'sandbox', 'file'])
def test_delivery_reference_downloads_exact_bytes(files, style):
    client, root, workspace = files
    path = workspace / 'Отчёт (сентябрь).xlsx'
    path.write_bytes(b'PK\x03\x04 test excel bytes')
    ref = {'MEDIA': f'MEDIA:"{path}"', 'path': str(path),
           'sandbox': 'sandbox:' + quote(str(path)), 'file': path.as_uri()}[style]
    info = client.get('/api/files/attachment', params={'path': ref})
    assert info.status_code == 200, info.text
    assert info.json()['size'] == path.stat().st_size
    response = client.get('/api/files/download', params={'path': ref, 'chat': 1})
    assert response.content == path.read_bytes()
    assert response.headers['content-disposition'].startswith('attachment;')


def test_profile_private_paths_traversal_and_symlinks_are_denied(files):
    client, root, workspace = files
    private = root / 'profiles' / 'lawyer' / 'memo.xlsx'
    private.parent.mkdir(parents=True)
    private.write_bytes(b'private')
    (workspace / 'link.xlsx').symlink_to(private)
    (workspace / '.env').write_text('secret')
    for path in [str(private), str(workspace / 'link.xlsx'), str(workspace / '.env'),
                 str(workspace / '..' / 'profiles' / 'lawyer' / 'memo.xlsx'),
                 'file://remote/etc/secret.xlsx', 'sandbox:/etc/passwd']:
        for endpoint in ['/api/files/attachment', '/api/files/download']:
            response = client.get(endpoint, params={'path': path, 'chat': 1})
            assert response.status_code in (400, 403), (path, response.text)
    client.headers.clear()
    assert client.get('/api/files/attachment', params={'path': str(private)}).status_code == 401


def test_upload_visible_downloadable_and_reusable_without_copy(files):
    client, root, workspace = files
    data = 'Проверка: сумма 2145'.encode()
    uploaded = client.post('/api/chat/upload', files={'file': ('Данные.txt', data)})
    assert uploaded.status_code == 200, uploaded.text
    descriptor = uploaded.json()
    target = Path(descriptor['path'])
    assert target.is_relative_to(workspace)
    listing = client.get('/api/files', params={'path': str(target.parent)}).json()
    assert [entry['path'] for entry in listing['entries']] == [str(target)]
    assert client.get('/api/files/download', params={'path': str(target)}).content == data
    request = Request({'type': 'http', 'app': server.app, 'path': '/', 'headers': [], 'server': ('testserver', 80)})
    body = {'messages': [{'role': 'user', 'content': 'Прочти'}], 'attachments': [descriptor, descriptor]}
    server._apply_chat_attachments(body, request=request)
    assert body['messages'][0]['content'].count(str(target)) == 1
    assert len(list(workspace.rglob('*.txt'))) == 1
    # A document made/uploaded in Files is admitted through the same path.
    existing = workspace / 'existing.txt'
    existing.write_bytes(data)
    body = {'messages': [{'role': 'user', 'content': ''}], 'attachments': [{'path': str(existing)}]}
    server._apply_chat_attachments(body, request=request)
    assert str(existing) in body['messages'][0]['content']


def test_empty_oversized_and_symlink_upload_leave_no_partial_files(files, monkeypatch):
    client, root, workspace = files
    monkeypatch.setattr(server, '_CHAT_MAX_UPLOAD_BYTES', 8)
    for data, status in [(b'', 400), (b'123456789', 413)]:
        assert client.post('/api/chat/upload', files={'file': ('x.txt', data)}).status_code == status
    assert not list(workspace.rglob('*.upload'))
    assert not list(workspace.rglob('*.txt'))
    assert not list(workspace.rglob('*.part'))
    import shutil
    # Отказавшая загрузка больше не создаёт дерево заранее: папка клиента
    # появляется только в момент публикации (K21-059).
    shutil.rmtree(workspace / 'client', ignore_errors=True)
    (workspace / 'client').symlink_to(root, target_is_directory=True)
    assert client.post('/api/chat/upload', files={'file': ('x.txt', b'ok')}).status_code == 403


def test_fleet_profile_scope_uses_shared_workspace(files, monkeypatch):
    client, root, workspace = files
    profile = root / 'profiles' / 'lawyer'
    profile.mkdir(parents=True)
    monkeypatch.setattr(server, '_resolve_profile_dir', lambda name: profile)
    result = client.post('/api/chat/upload?profile=lawyer', files={'file': ('case.txt', b'case')})
    assert result.status_code == 200, result.text
    assert Path(result.json()['path']).is_relative_to(workspace)
    assert not list(profile.rglob('*.txt'))


def test_telegram_and_dashboard_resolve_the_same_media_file(files):
    from gateway.platforms.base import BasePlatformAdapter
    client, _, workspace = files
    path = workspace / 'Продажи за сентябрь.xlsx'
    path.write_bytes(b'xlsx')
    text = f'Готово.\nMEDIA:"{path}"'
    media, caption = BasePlatformAdapter.extract_media(text)
    assert media == [(str(path), False)]
    info = client.get('/api/files/attachment', params={'path': f'MEDIA:"{path}"'})
    assert info.json()['path'] == media[0][0]
    assert 'MEDIA:' not in caption


def test_large_download_reports_limit_without_reading_file(files):
    client, _, workspace = files
    path = workspace / 'big.zip'
    with path.open('wb') as f:
        f.truncate(2 * 1024 ** 3 + 1)
    for endpoint in ['/api/files/attachment', '/api/files/download']:
        response = client.get(endpoint, params={'path': str(path), 'chat': 1})
        assert response.status_code == 413
        assert '2 ГБ' in response.json()['detail']


def test_folder_attachment_recounts_files_and_skips_private_links(files):
    client, root, workspace = files
    folder = workspace / 'Материалы'
    (folder / 'empty').mkdir(parents=True)
    (folder / 'visible.txt').write_bytes(b'hello')
    (folder / '.env').write_bytes(b'secret')
    (folder / 'link').symlink_to(root, target_is_directory=True)
    descriptor = client.get('/api/files/attachment', params={'path': str(folder)}).json()
    assert descriptor['kind'] == 'folder'
    assert descriptor['file_count'] == 1
    assert descriptor['size'] == 5
    assert descriptor['sample'] == ['visible.txt']
    assert descriptor['truncated'] is False
    request = Request({'type': 'http', 'app': server.app, 'path': '/', 'headers': [], 'server': ('testserver', 80)})
    descriptor.update(file_count=900, size=1000000, sample=['invented'])
    body = {'messages': [{'role': 'user', 'content': 'Выбери нужное'}], 'attachments': [descriptor]}
    server._apply_chat_attachments(body, request=request)
    text = body['messages'][0]['content']
    assert 'файлов: 1' in text and 'visible.txt' in text
    assert 'invented' not in text and 'secret' not in text


def test_thirty_attachments_are_admitted_without_truncation(files):
    client, _, workspace = files
    attachments = []
    for index in range(30):
        path = workspace / f'{index}.txt'
        path.write_text(str(index))
        attachments.append({'path': str(path)})
    request = Request({'type': 'http', 'app': server.app, 'path': '/', 'headers': [], 'server': ('testserver', 80)})
    body = {'messages': [{'role': 'user', 'content': ''}], 'attachments': attachments}
    server._apply_chat_attachments(body, request=request)
    assert all(item['path'] in body['messages'][0]['content'] for item in attachments)


def test_result_outside_workspace_explains_the_reason_and_the_next_step(files):
    """Карточка «Показать в папке» должна объяснить, почему файла нет, а не
    показать общий отказ: результат в профиле агента лежит вне общей рабочей
    папки, и продолжение — попросить агента сохранить его в workspace."""
    client, root, workspace = files
    private = root / 'profiles' / 'designer' / 'листовка.pdf'
    private.parent.mkdir(parents=True)
    private.write_bytes(b'%PDF')
    response = client.get('/api/files/attachment', params={'path': str(private)})
    assert response.status_code == 403
    assert response.json()['detail'] == 'Файл вне рабочей папки. Попросите агента сохранить его в workspace.'
    # Удалённый файл внутри workspace — другая причина и другое продолжение.
    missing = client.get('/api/files/attachment', params={'path': str(workspace / 'нет.pdf')})
    assert missing.status_code == 404
    assert 'удалён или перемещён' in missing.json()['detail']


def test_attachment_revision_changes_for_same_size_edit_and_removal(files):
    client, _, workspace = files
    path = workspace / 'preview.png'
    path.write_bytes(b'old-image')
    def describe():
        return client.get('/api/files/attachment', params={'path': str(path)})
    first = describe().json()['revision']
    assert describe().json()['revision'] == first
    path.write_bytes(b'new-image')
    assert describe().json()['revision'] != first
    path.unlink()
    assert describe().status_code == 404
