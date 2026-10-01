"""K21-239: real artifact extraction/store/ledger; only transport/model are doubles."""
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from cron import jobs, executions, scheduler
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(jobs, 'CRON_DIR', tmp_path / 'cron')
    monkeypatch.setattr(jobs, 'JOBS_FILE', tmp_path / 'cron/jobs.json')
    monkeypatch.setattr(jobs, 'OUTPUT_DIR', tmp_path / 'cron/output')
    monkeypatch.setattr(executions, 'EXECUTIONS_FILE', tmp_path / 'cron/executions.db')
    (tmp_path / 'config.yaml').write_text('cron: {wrap_response: false}\n')
    return tmp_path


@pytest.mark.parametrize('prompt', [
    'Создай HTML-отчёт и отправь его вложением.',
    'Подготовь краткую сводку по данным CSV и пришли текстом, без файлов',
    'Если появились новые сделки, создай HTML-отчёт. Если изменений нет, верни [SILENT]',
    'Напиши краткий отчёт',
])
def test_prompt_wording_never_creates_a_file_contract(prompt):
    # Review R1/R2: guessing from wording failed text-only and [SILENT] jobs.
    assert jobs.required_result_extensions({'prompt': prompt}) is None


def test_explicit_contract_survives_create_update_and_old_json_shape(store):
    job = jobs.create_job('Do it', 'every 1h', required_file=True)
    assert jobs.get_job(job['id'])['required_file'] is True
    assert jobs.required_result_extensions(jobs.get_job(job['id'])) == ()
    jobs.update_job(job['id'], {'required_file': False})
    assert jobs.required_result_extensions(jobs.get_job(job['id'])) is None
    with pytest.raises(ValueError):
        jobs.update_job(job['id'], {'required_file': 'true'})
    assert jobs.get_job(job['id'])['required_file'] is False
    jobs.update_job(job['id'], {'required_file': None, 'prompt': 'Создай HTML-отчёт'})
    assert jobs.required_result_extensions(jobs.get_job(job['id'])) is None


def _job():
    return {'id': 'report', 'name': 'Вечерний отчёт', 'prompt': 'Создай HTML-отчёт и отправь вложением',
        'deliver': 'telegram:123', 'created_by_owner': True, 'required_file': True,
        '_result_started_at': time.time() - 2}


@pytest.mark.parametrize('style', ['sandbox', 'backticks', 'media', 'both'])
def test_existing_file_delivered_once(store, style):
    file = store / 'reports/report.html'
    file.parent.mkdir()
    file.write_text('<html>готово</html>')
    link = f'[Отчёт](sandbox:{file})'
    content = {'sandbox': link, 'backticks': f'Готово: `{file}`',
        'media': f'MEDIA:{file}', 'both': f'{link}\n`{file}`\nMEDIA:{file}'}[style]
    # Default parser still requires directives; cron opts into result references.
    if style in ('sandbox', 'backticks'):
        assert BasePlatformAdapter.extract_media(content)[0] == []
    cfg = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token='fake')})
    send = AsyncMock(return_value={'success': True, 'message_id': '1'})
    with patch('gateway.config.load_gateway_config', return_value=cfg), \
         patch('tools.send_message_tool._send_to_platform', send):
        error = scheduler._deliver_result(_job(), content)
    assert error is None
    send.assert_awaited_once()
    assert send.call_args.kwargs['media_files'] == [(str(file.resolve()), False)]


@pytest.mark.parametrize('case', ['missing', 'old', 'sibling', 'symlink', 'quoted_example', 'json_example'])
def test_no_arbitrary_or_unavailable_fallback_file(store, case):
    file = store / 'reports/report.html'
    file.parent.mkdir()
    if case != 'missing':
        file.write_text('result')
    if case == 'old':
        os.utime(file, (0, 0))
    elif case in ('sibling', 'symlink'):
        foreign = store / 'profiles/other/report.html'
        foreign.parent.mkdir(parents=True)
        foreign.write_text('another profile')
        if case == 'symlink':
            file.unlink()
            file.symlink_to(foreign)
        else:
            file = foreign
    content = f'[Отчёт](sandbox:{file})'
    if case == 'quoted_example':
        content = f'```markdown\n{content}\n```'
    if case == 'json_example':
        import json
        content = json.dumps({'stored_result': content})
    result, error = scheduler._prepare_required_file_result(_job(), content)
    assert error and 'Задание требует файл результата' in error
    assert result == content


def test_failed_attachment_never_books_delivered(store):
    file = store / 'report.html'
    file.write_text('result')
    cfg = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token='fake')})
    with patch('gateway.config.load_gateway_config', return_value=cfg), \
         patch('tools.send_message_tool._send_to_platform', AsyncMock(return_value={'success': False, 'error': 'upload rejected'})):
        error = scheduler._deliver_result(_job(), f'MEDIA:{file}')
    assert error and 'upload rejected' in error


def test_missing_file_is_failed_in_real_job_and_execution_store(store):
    job = jobs.create_job('Создай HTML-отчёт и отправь вложением', 'every 1h',
        name='Вечерний отчёт', deliver='telegram:123', created_by_owner=True, required_file=True,
        origin={'platform': 'telegram', 'chat_id': '123', 'user_id': '123'})
    cfg = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token='fake')})
    send = AsyncMock(return_value={'success': True, 'message_id': '1'})
    with patch('cron.scheduler.run_job', return_value=(True, '# done', 'Отчёт готов.', None)), \
         patch('gateway.config.load_gateway_config', return_value=cfg), \
         patch('tools.send_message_tool._send_to_platform', send):
        assert scheduler.run_one_job(job)
    row = executions.latest_execution(job['id'])
    assert row['status'] == 'failed'
    assert row['delivery_outcome'] == 'failed'
    assert 'Задание требует файл результата' in row['delivery_error']
    saved = jobs.get_job(job['id'])
    assert saved['last_status'] == 'error'
    assert 'Задание требует файл результата' in saved['last_error']
    assert any('Задание требует файл результата' in str(call) for call in send.call_args_list)


def test_prompt_tells_model_how_to_attach_result():
    assert 'MEDIA:/полный/путь/к/файлу' in scheduler._build_job_prompt(_job())


@pytest.mark.parametrize('upload_ok', [True, False])
def test_file_result_books_real_delivery_outcome(store, upload_ok):
    job = jobs.create_job('Создай HTML-отчёт', 'every 1h', deliver='telegram:123',
        created_by_owner=True, origin={'platform': 'telegram', 'chat_id': '123', 'user_id': '123'})
    file = store / 'report.html'
    def generate(*args, **kwargs):
        file.write_text('<html>report</html>')
        return True, 'done', f'[Отчёт](sandbox:{file})', None
    cfg = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token='fake')})
    send = AsyncMock(return_value=({'success': True, 'message_id': '1'} if upload_ok
        else {'success': False, 'error': 'upload rejected'}))
    with patch('cron.scheduler.run_job', side_effect=generate), \
         patch('gateway.config.load_gateway_config', return_value=cfg), \
         patch('tools.send_message_tool._send_to_platform', send):
        assert scheduler.run_one_job(job)
    row = executions.latest_execution(job['id'])
    assert row['delivery_outcome'] == ('delivered' if upload_ok else 'failed')
    if not upload_ok:
        assert 'upload rejected' in row['delivery_error']
    send.assert_awaited_once()
    assert send.call_args.kwargs['media_files'] == [(str(file.resolve()), False)]


def test_active_profile_does_not_attach_sibling_even_in_shared_workdir(store, monkeypatch):
    active = store / 'profiles/one'
    other = store / 'profiles/two'
    active.mkdir(parents=True)
    other.mkdir(parents=True)
    foreign = other / 'report.html'
    foreign.write_text('sibling output')
    monkeypatch.setenv('HERMES_HOME', str(active))
    job = {**_job(), 'workdir': str(store)}
    _, error = scheduler._prepare_required_file_result(job, f'[Отчёт](sandbox:{foreign})')
    assert error and 'Задание требует файл результата' in error
    own = active / 'report.html'
    own.write_text('own output')
    content, error = scheduler._prepare_required_file_result(job, f'[Отчёт](sandbox:{own})')
    assert error is None
    assert f'MEDIA:{own}' in content


def test_job_without_contract_attaches_sandbox_link_but_not_mentions(store):
    """K21-239 cause: the file existed, the model linked it as sandbox:."""
    file = store / 'report.html'
    file.write_text('<html>report</html>')
    job = {**_job(), 'required_file': None}
    content, error = scheduler._prepare_required_file_result(job, f'[Отчёт](sandbox:{file})')
    assert error is None and f'MEDIA:{file}' in content
    text = f'Обновил `{file}`, сводка ниже.'
    assert scheduler._prepare_required_file_result(job, text) == (text, None)


def test_text_only_and_silent_jobs_are_not_failed(store):
    # Review R1: text answer for a prompt that mentions CSV.
    text_job = {**_job(), 'required_file': None,
        'prompt': 'Подготовь краткую сводку по данным CSV и пришли текстом, без файлов'}
    assert scheduler._prepare_required_file_result(text_job, 'Итог: 3 сделки.') == ('Итог: 3 сделки.', None)
    # Review R2: allowed silence is never a missing file, even with a contract.
    assert scheduler._prepare_required_file_result(_job(), '[SILENT]') == ('[SILENT]', None)
