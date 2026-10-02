"""K21-267: a keyed "+" create never publishes an agent whose skills or model failed."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_DASHBOARD_SESSION_TOKEN', 'preparation-test-token')
    from korra_cli import web_server
    with TestClient(web_server.app, raise_server_exceptions=False) as http:
        http.headers['Authorization'] = 'Bearer preparation-test-token'
        yield http


def body(**extra):
    return {'name': 'helper-one', 'display_name': 'Помощник', 'soul': 'Помогай.',
            'idempotency_key': 'preparation-key-0000001', **extra}


def assert_nothing_published(name='helper-one'):
    from korra_cli import profiles
    assert not profiles.get_profile_dir(name).exists()
    assert not list(profiles._get_profiles_root().glob('.profile-requests/*/stage'))


def test_failed_skill_seed_publishes_nothing_and_retry_succeeds(client, monkeypatch):
    from korra_cli import profiles
    monkeypatch.setattr(profiles, 'seed_profile_skills', lambda *a, **k: None)
    failed = client.post('/api/profiles', json=body())
    assert failed.status_code == 500
    assert 'навыки' in failed.json()['detail']
    assert_nothing_published()
    assert 'helper-one' not in [p['name'] for p in client.get('/api/profiles').json()['profiles']]

    monkeypatch.setattr(profiles, 'seed_profile_skills', lambda *a, **k: {'copied': []})
    retried = client.post('/api/profiles', json=body())
    assert retried.status_code == 200, retried.text
    assert profiles.get_profile_dir('helper-one').is_dir()


def test_failed_model_write_publishes_nothing_and_retry_succeeds(client, monkeypatch):
    from korra_cli import profiles
    from korra_cli.web_routers import profiles as router
    monkeypatch.setattr(profiles, 'seed_profile_skills', lambda *a, **k: {'copied': []})
    real_write = router._write_profile_model

    def broken(*a, **k):
        raise OSError('disk full')

    monkeypatch.setattr(router, '_write_profile_model', broken)
    pick = {'provider': 'anthropic', 'model': 'claude-test'}
    failed = client.post('/api/profiles', json=body(**pick))
    assert failed.status_code == 500
    assert 'модель' in failed.json()['detail']
    assert_nothing_published()

    monkeypatch.setattr(router, '_write_profile_model', real_write)
    retried = client.post('/api/profiles', json=body(**pick))
    assert retried.status_code == 200, retried.text
    assert retried.json()['model_set'] is True


def test_no_skills_is_still_allowed_and_not_a_failure(client):
    from korra_cli import profiles
    created = client.post('/api/profiles', json=body(no_skills=True))
    assert created.status_code == 200, created.text
    assert profiles.get_profile_dir('helper-one').is_dir()


def test_missing_model_is_reported_as_not_configured(client, monkeypatch):
    from korra_cli import profiles
    monkeypatch.setattr(profiles, 'seed_profile_skills', lambda *a, **k: {'copied': []})
    created = client.post('/api/profiles', json=body())
    assert created.status_code == 200, created.text
    assert created.json()['model_set'] is False


def test_inherited_model_counts_as_configured(client, monkeypatch, tmp_path):
    from korra_cli import profiles
    monkeypatch.setattr(profiles, 'seed_profile_skills', lambda *a, **k: {'copied': []})
    (tmp_path / 'config.yaml').write_text('model:\n  default: claude-test\n  provider: anthropic\n')
    created = client.post('/api/profiles', json=body())
    assert created.status_code == 200, created.text
    assert created.json()['model_set'] is True


def test_legacy_create_without_key_stays_best_effort(client, monkeypatch):
    from korra_cli import profiles
    from korra_cli.web_routers import profiles as router
    monkeypatch.setattr(profiles, 'seed_profile_skills', lambda *a, **k: None)
    monkeypatch.setattr(router, '_write_profile_model',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    legacy = body(provider='anthropic', model='claude-test')
    del legacy['idempotency_key']
    created = client.post('/api/profiles', json=legacy)
    assert created.status_code == 200, created.text
    assert created.json()['model_set'] is False
    assert profiles.get_profile_dir('helper-one').is_dir()
