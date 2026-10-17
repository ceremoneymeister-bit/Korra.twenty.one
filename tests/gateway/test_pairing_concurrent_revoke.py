"""An approval in another store must never resurrect a revoked sender."""

import json
import threading

import pytest

from gateway import pairing


@pytest.mark.linux_only
def test_concurrent_approval_does_not_restore_revoked_sender(tmp_path, monkeypatch):
    import korra_cli.auth as auth

    monkeypatch.setattr(pairing, "PAIRING_DIR", tmp_path)
    monkeypatch.setattr(pairing, "_sync_allowlist_add", lambda *args: None)
    monkeypatch.setattr(pairing, "_sync_allowlist_remove", lambda *args: None)
    seed = pairing.PairingStore()
    seed._approve_user("telegram", "revoked")
    code = seed.generate_code("telegram", "new-user", "New user")
    dashboard, cli = pairing.PairingStore(), pairing.PairingStore()
    approved_path = dashboard._approved_path("telegram")
    observed = threading.Event()
    revoke_done = threading.Event()
    errors, results, threads = [], [], []
    real_flock, real_load = auth.fcntl.flock, dashboard._load_json

    def flock(fd, operation):
        try:
            return real_flock(fd, operation)
        except BlockingIOError:
            observed.set()
            raise

    monkeypatch.setattr(auth.fcntl, "flock", flock)

    def revoke():
        try:
            results.append(cli.revoke("telegram", "revoked"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            observed.set()
            revoke_done.set()

    def load_then_revoke(path):
        data = real_load(path)
        if path == approved_path and not threads:
            worker = threading.Thread(target=revoke, daemon=True)
            threads.append(worker)
            worker.start()
            assert observed.wait(5), "revoke neither completed nor reached the file lock"
        return data

    monkeypatch.setattr(dashboard, "_load_json", load_then_revoke)
    assert dashboard.approve_code("telegram", code)["user_id"] == "new-user"
    assert revoke_done.wait(5)
    for worker in threads:
        worker.join(5)
        assert not worker.is_alive()
    assert not errors
    assert results == [True]
    approved = json.loads(approved_path.read_text())
    assert "new-user" in approved
    assert "revoked" not in approved
