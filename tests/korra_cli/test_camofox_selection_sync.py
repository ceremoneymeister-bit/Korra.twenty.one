"""K21-336 RF12-01/RF12-02: профильный разбор и согласованная сверка службы."""
import multiprocessing
from pathlib import Path
from unittest.mock import patch

import pytest

from korra_cli import camofox_embedded as emb
from tests.fakes.camofox_embedded import install, data, in_profile, choose_browser  # noqa: F401


@pytest.mark.parametrize('dotenv', [
    'CAMOFOX_URL="" # use the embedded browser\n',
    'CAMOFOX_URL=http://old.invalid\nCAMOFOX_URL=\n',
], ids=['empty-with-comment', 'last-assignment-empty'])
def test_sync_agrees_with_runtime_env_parser(install, data, dotenv):
    from agent.secret_scope import load_env_file, set_secret_scope, reset_secret_scope
    from tools import browser_camofox as bc

    choose_browser('Camofox')
    (data / '.env').write_text(dotenv)
    scope = load_env_file(data / '.env')
    assert scope['CAMOFOX_URL'] == ''
    token = set_secret_scope(scope)
    try:
        assert bc.get_camofox_url() == emb.BASE_URL
        assert bc._using_embedded_camofox()
        with patch.object(emb, '_s6_svc') as signal:
            wanted = emb.sync()
        assert wanted and emb.is_enabled(), (
            f'Runtime chose embedded, sync wanted={wanted}, enabled={emb.is_enabled()}, '
            f's6 calls={signal.call_args_list}'
        )
        signal.assert_not_called()
    finally:
        reset_secret_scope(token)


def test_other_profiles_legacy_env_encoding_cannot_break_config_save(install, data):
    from agent.secret_scope import load_env_file
    from korra_cli.config import save_config, read_user_config_raw

    with in_profile(data, 'a') as a:
        choose_browser('Camofox')
        # Existing loader supports legacy non-UTF8 files, including comments.
        (a / '.env').write_bytes(b'# caf\xe9\nCAMOFOX_URL=\n')
        assert load_env_file(a / '.env')['CAMOFOX_URL'] == ''
    with in_profile(data, 'b') as b:
        save_config({'browser': {'cloud_provider': 'local'}})
        assert read_user_config_raw(b / 'config.yaml')['browser']['cloud_provider'] == 'local'
    assert emb.is_enabled()


def test_hidden_deleted_and_symlink_profiles_do_not_start_service(install, data, tmp_path):
    from korra_constants import mark_named_profile_deleted

    root = data / 'profiles'
    root.mkdir()
    for name in ('.stage', 'deleted'):
        home = root / name
        home.mkdir()
        (home / 'config.yaml').write_text('browser:\n  cloud_provider: camofox\n')
    mark_named_profile_deleted(root / 'deleted')
    outside = tmp_path / 'outside-profile'
    outside.mkdir()
    (outside / 'config.yaml').write_text('browser:\n  cloud_provider: camofox\n')
    (root / 'linked').symlink_to(outside, target_is_directory=True)
    assert not emb.sync()
    assert not emb.is_enabled()


def _write_choices(home, start, final):
    from korra_cli.config import atomic_config_write
    assert start.wait(10)
    for index in range(12):
        atomic_config_write(Path(home) / 'config.yaml', {
            'browser': {'cloud_provider': 'camofox' if index % 2 else 'local'},
        })
    atomic_config_write(Path(home) / 'config.yaml', {'browser': {'cloud_provider': final}})


@pytest.mark.parametrize('final', ['local', 'camofox'])
def test_concurrent_process_saves_converge_to_actual_selection(install, data, final):
    ctx = multiprocessing.get_context('fork')
    start = ctx.Event()
    homes = [data / 'profiles' / name for name in ('a', 'b', 'c', 'd')]
    for home in homes:
        home.mkdir(parents=True)
    procs = [ctx.Process(target=_write_choices, args=(str(home), start, final)) for home in homes]
    try:
        for proc in procs:
            proc.start()
        start.set()
        for proc in procs:
            proc.join(20)
            assert proc.exitcode == 0
        # No extra sync here: check the state left by concurrent writers.
        assert emb.is_enabled() == (final == 'camofox')
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(5)


def test_only_last_consumer_signals_stop_and_startup_does_not(install, data):
    with patch.object(emb, '_s6_svc') as signal:
        with in_profile(data, 'a'):
            choose_browser('Camofox')
        with in_profile(data, 'b'):
            choose_browser('Camofox')
        with in_profile(data, 'a'):
            choose_browser('Local Browser')
        signal.assert_not_called()
        with in_profile(data, 'b'):
            choose_browser('Local Browser')
        signal.assert_called_once_with('-t')
        signal.reset_mock()
        (data / 'camofox' / 'enabled').write_text('1\n')
        assert emb.main(['sync']) == 0
        signal.assert_not_called()
        assert not emb.is_enabled()


def test_external_url_of_one_profile_does_not_disable_another(install, data):
    from korra_cli.config import save_env_value
    with in_profile(data, 'a'):
        choose_browser('Camofox')
    with in_profile(data, 'b'):
        save_env_value('CAMOFOX_URL', 'http://external.invalid', update_process=False)
        choose_browser('Camofox')
    assert emb.is_enabled()
    with in_profile(data, 'a'):
        choose_browser('Local Browser')
    assert not emb.is_enabled()


@pytest.mark.parametrize("selection", ["config", "env"])
def test_scan_must_not_stop_a_continuously_used_service(install, data, selection):
    """B starts using Camofox BEFORE A stops; there is always a consumer."""
    import threading
    import fcntl
    from concurrent.futures import ThreadPoolExecutor
    from korra_cli.config import atomic_config_write, read_user_config_raw, save_env_value, remove_env_value

    # Assign roles from the actual filesystem enumeration order.
    b = data / 'profiles' / 'b'
    a = data / 'profiles' / 'a'
    b.mkdir(parents=True)
    a.mkdir()
    b, a = list((data / 'profiles').iterdir())
    if selection == "env":
        (b / '.env').write_text('CAMOFOX_URL=http://external.invalid\n')
    atomic_config_write(b / 'config.yaml', {'browser': {'cloud_provider': 'local' if selection == "config" else 'camofox'}})
    atomic_config_write(a / 'config.yaml', {'browser': {'cloud_provider': 'camofox'}})
    assert emb.is_enabled()

    b_read = threading.Event()
    resume_scan = threading.Event()
    original_read = emb._profile_setting

    def controlled_read(home, env_name, keys):
        result = original_read(home, env_name, keys)
        pause_keys = ('browser', 'cloud_provider') if selection == "config" else ()
        if home == b and keys == pause_keys and not b_read.is_set():
            assert result == ('local' if selection == "config" else 'http://external.invalid')
            b_read.set()
            assert resume_scan.wait(10)
        return result

    writer = threading.local()
    original_flock = fcntl.flock

    def observed_flock(lock, operation):
        if getattr(writer, 'attempt', None) is not None:
            writer.attempt.set()
        return original_flock(lock, operation)

    def write(home, value, attempt):
        writer.attempt = attempt
        if selection == "config":
            atomic_config_write(home / 'config.yaml', {'browser': {'cloud_provider': value}})
        else:
            with in_profile(data, home.name):
                if value == 'camofox':
                    remove_env_value('CAMOFOX_URL', update_process=False)
                else:
                    save_env_value('CAMOFOX_URL', 'http://external.invalid', update_process=False)

    def b_is_published():
        if selection == "config":
            return read_user_config_raw(b / 'config.yaml')['browser']['cloud_provider'] == 'camofox'
        return 'CAMOFOX_URL' not in (b / '.env').read_text()

    with patch.object(emb, '_profile_setting', side_effect=controlled_read), patch.object(emb, '_s6_svc') as signal, patch.object(fcntl, 'flock', side_effect=observed_flock):
        with ThreadPoolExecutor(max_workers=3) as pool:
            scan = pool.submit(emb.sync)
            try:
                assert b_read.wait(10)
                b_attempt = threading.Event()
                writer_b = pool.submit(write, b, 'camofox', b_attempt)
                assert b_attempt.wait(10)
                # Before the fix, the writer published BEFORE trying the lock.
                # Preserve the reviewer's bad interleaving on that implementation.
                # With serialized publication, let the scan finish before B writes.
                if not b_is_published():
                    resume_scan.set()
                    writer_b.result(timeout=10)
                a_attempt = threading.Event()
                writer_a = pool.submit(write, a, 'local', a_attempt)
                assert a_attempt.wait(10)
            finally:
                resume_scan.set()
            scan.result(timeout=10)
            writer_b.result(timeout=10)
            writer_a.result(timeout=10)
        assert emb.is_enabled()  # Eventual consistency does work.
        assert signal.call_args_list == [], (
            f'Camofox had a consumer throughout, but received {signal.call_args_list}'
        )


def test_unreadable_profile_env_is_logged_and_cannot_break_another_save(install, data, caplog, monkeypatch):
    from korra_cli.config import save_config, read_user_config_raw

    with in_profile(data, 'a') as a:
        choose_browser('Camofox')
        (a / '.env').mkdir()  # A real read failure, even when tests run as root.
    monkeypatch.setenv('CAMOFOX_URL', 'http://another-profile.invalid')
    with in_profile(data, 'b') as b:
        save_config({'browser': {'cloud_provider': 'local'}})
        assert read_user_config_raw(b / 'config.yaml')['browser']['cloud_provider'] == 'local'
    assert emb.is_enabled()
    assert str(a / '.env') in caplog.text
    assert 'IsADirectoryError' in caplog.text
