"""Updater transactions on real filesystem/SQLite with fake Docker boundaries."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import types

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "docs/client-deploy/updater.py"
spec = importlib.util.spec_from_file_location("client_updater", SOURCE)
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)
OLD = "sha256:" + "1" * 64
NEW = "sha256:" + "2" * 64


class FakeDockerUpdater(u.Updater):
    def __init__(self, home):
        super().__init__(home)
        self.image = OLD
        self.running = True
        self.calls = []
        self.fail_smoke = False
        self.busy = False
        self.is_draining = False
        self.wrong_mount = False
        self.mounts_override = None
        self.extra_env = []
        self.tags = {}
        self.judge_verdict = {"bad": []}
        self.judge_image_missing = False
        # Мультиплексный контур: профили обслуживает корневой шлюз и он же их
        # перечисляет. Без мультиплекса у каждого профиля свой шлюз и своё home,
        # а лежачий профиль в ответе просто отсутствует.
        self.multiplex = True
        self.profile_gateways = ()

    def free_space(self, *args):
        pass

    def capability(self, **kwargs):
        return {"mode": "configured", "provider_hash": "a" * 64}

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "inspect":
            mounts = self.mounts_override or [{
                "Type": "bind",
                "Source": "/wrong" if self.wrong_mount else str(self.data),
                "Destination": "/opt/data",
                "RW": True,
            }]
            return json.dumps([{"Name": "/" + self.name, "Image": self.image,
                "State": {"Running": self.running},
                "Mounts": mounts,
                "HostConfig": {"NetworkMode": "host"},
                "Config": {"Cmd": ["gateway", "run"], "Env": [f"KORRA_DASHBOARD_PORT={self.panel}", f"API_SERVER_PORT={self.api}", *self.extra_env]}}])
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": self.tags.get(args[2], OLD if args[2] == OLD else NEW), "Size": 1}])
        if args[:2] == ("image", "tag"):
            self.tags[args[3]] = args[2]
            return "ok"
        if args[:2] == ("image", "ls"):
            return "\n".join(f"{reference}|{image}" for reference, image in sorted(self.tags.items()))
        if args[:2] == ("image", "rm"):
            if args[2] not in self.tags:
                raise u.UpdateError("docker image rm failed (exit 1)")
            del self.tags[args[2]]
            return "Untagged: " + args[2]
        if args[0] == "stop":
            self.running = False
            self.is_draining = False
            return "ok"
        if args[0] == "start":
            self.running = True
            return "ok"
        if args[0] == "pull":
            return "ok"
        if args[0] == "run":
            # Судья SQLite: одноразовый контейнер старого образа поверх снимка,
            # тем же приёмом, что и schema_rehearsal. Отсутствующий образ docker
            # отвергает кодом возврата, а не пустым ответом.
            if self.judge_image_missing:
                raise u.UpdateError("docker run failed (exit 125)")
            return json.dumps(self.judge_verdict)
        raise AssertionError(args)

    def probe_image(self, image):
        self.calls.append(("probe", image))
        return {"skills": {}}

    def native_states(self, action="status", timeout=None):
        self.calls.append(("native", action))
        # Маркер настоящий: внутри контейнера drain — это файл в DATA, и
        # «не остался ли он после операции» проверяется по файлу, а не по флагу.
        marker = self.data / ".drain_request.json"
        if action == "drain":
            self.is_draining = True
            marker.write_text(json.dumps({"action": "drain", "principal": "host-updater:fixture-job"}))
        if action == "cancel":
            self.is_draining = False
            marker.unlink(missing_ok=True)
        state = {"gateway_state": "draining" if self.is_draining else "running",
                 "active_agents": 1 if self.busy else 0}
        if self.multiplex:
            return [{"home": "/opt/data", "state": {**state, "served_profiles": ["default", "secretary"]}}]
        return [{"home": "/opt/data", "state": dict(state)}] + [
            {"home": "/opt/data/profiles/" + name, "state": dict(state)}
            for name in sorted(self.profile_gateways)]

    def start_image(self, image):
        self.calls.append(("start_image", image))
        self.image = image
        self.running = True
        self.is_draining = False
        (self.home / "IMAGE").write_text(image)

    def smoke(self, image):
        self.inspect_target(image)
        if self.fail_smoke and image == NEW:
            (self.data / "auth.json").write_text('{"refresh_token": "rotated"}')
            (self.data / "revoked.token").unlink()
            (self.data / "new-message.txt").write_text("write during new boot")
            with sqlite3.connect(self.data / "state.db") as database:
                database.execute("INSERT INTO messages VALUES ('new turn')")
            raise u.UpdateError("Injected model smoke failure")

    def preserve_config_credentials(self, latest, stage):
        return []

    def rehearse_schema(self, image):
        self.calls.append(("schema_rehearsal", image))
        return {"scope": "native_session_db", "databases": []}


@pytest.fixture
def updater(tmp_path, monkeypatch):
    # The real Docker integration covers privileged host ownership. Unit fake
    # boundaries must also run under an unprivileged CI account; DATA is this
    # test's freshly created directory and locks remain inside its temp root.
    monkeypatch.setattr(u, "canonical_data", lambda value: Path(value))
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setattr(u, "LOCK_ROOT", lock_root)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("model: fixture\n")
    (data / "auth.json").write_text('{"refresh_token": "old"}')
    (data / "revoked.token").write_text("old-revoked")
    (data / "profiles/secretary").mkdir(parents=True)
    (data / "profiles/secretary/.no-bundled-skills").write_text("preserve")
    with sqlite3.connect(data / "state.db") as database:
        database.execute("CREATE TABLE messages(text)")
        database.execute("INSERT INTO messages VALUES ('before')")
    home = tmp_path / "deploy"
    home.mkdir()
    (home / "IMAGE").write_text(OLD)
    monkeypatch.setenv("DATA", str(data))
    monkeypatch.setenv("NAME", "updater-fixture")
    instance = FakeDockerUpdater(home)
    instance.initialize("fixture-job", "registry.example/korra:latest")
    instance.receipt["baseline_capability"] = instance.capability()
    instance.receipt["old_runtime"] = {"ENGINE_UID": "10000", "ENGINE_GID": "10000", "AGENT_SUDO": "1"}
    return instance


def test_update_fetches_and_probes_before_quiescing(updater):
    updater.update("registry.example/korra:latest")
    assert updater.receipt["status"] == "succeeded"
    assert updater.image == NEW
    assert updater.calls.index(("pull", "registry.example/korra:latest")) < updater.calls.index(("native", "drain"))
    assert updater.calls.index(("probe", NEW)) < updater.calls.index(("native", "drain"))
    assert (updater.job / "before/state.db").exists()
    assert json.loads((updater.job / "status.json").read_text())["target_image_id"] == NEW


def test_wrong_mount_rejected_before_any_mutation(updater):
    updater.wrong_mount = True
    before = u.tree_manifest(updater.data)
    with pytest.raises(u.UpdateError, match="identity/mount"):
        updater.update("registry.example/korra:latest")
    assert u.tree_manifest(updater.data) == before
    assert not any(call[0] in {"stop", "pull", "start_image"} for call in updater.calls)


def _data_mount(updater):
    return {
        "Type": "bind",
        "Source": str(updater.data),
        "Destination": "/opt/data",
        "RW": True,
    }


def _google_mount(updater):
    return {
        "Type": "bind",
        "Source": str(updater.home / "google" / "oauth_client.json"),
        "Destination": "/run/korra-secrets/google-oauth-client.json",
        "RW": False,
    }


def test_exact_optional_google_oauth_mount_is_accepted_in_any_order(updater):
    updater.mounts_override = [_google_mount(updater), _data_mount(updater)]
    updater.validate_google_oauth_source = lambda _gid: None

    assert updater.inspect_target()["Image"] == OLD


def _telegram_mount(updater):
    source = updater.home.parent / "telegram-bot-api"
    source.mkdir(exist_ok=True)
    updater.extra_env = ["KORRA_TELEGRAM_LOCAL_ROOT=/opt/data/telegram-bot-api"]
    return {"Type": "bind", "Source": str(source),
            "Destination": "/opt/data/telegram-bot-api", "RW": False}


def test_existing_telegram_media_mount_survives_update_and_rollback(updater):
    updater.mounts_override = [_telegram_mount(updater), _data_mount(updater)]
    updater.update("registry.example/korra:latest")
    assert updater.receipt["status"] == "succeeded"
    assert updater.receipt["telegram_mount"] == {
        "present": True, "source": updater.mounts_override[0]["Source"],
        "destination": "/opt/data/telegram-bot-api",
    }
    updater.rollback()
    assert updater.image == OLD
    assert updater.inspect_target()["Mounts"] == updater.mounts_override


@pytest.mark.parametrize("change", ["missing", "source", "writeable", "environment", "extra", "symlink"])
def test_telegram_media_contract_cannot_change_after_capture(updater, change):
    mount = _telegram_mount(updater)
    updater.mounts_override = [_data_mount(updater), mount]
    initial = updater.inspect_target()
    updater.receipt["telegram_mount"] = updater.telegram_mount_contract(initial)
    if change == "missing":
        updater.mounts_override.pop()
        updater.extra_env = []
    elif change == "source":
        other = updater.home.parent / "foreign-media"
        other.mkdir()
        mount["Source"] = str(other)
    elif change == "writeable":
        mount["RW"] = True
    elif change == "environment":
        updater.extra_env = []
    elif change == "extra":
        updater.mounts_override.append(dict(mount))
    else:
        source = Path(mount["Source"])
        other = source.with_name("displaced-media")
        source.rename(other)
        source.symlink_to(other, target_is_directory=True)
    with pytest.raises(u.UpdateError):
        updater.inspect_target()


def test_telegram_mount_cannot_appear_after_absent_baseline(updater):
    updater.receipt["telegram_mount"] = updater.telegram_mount_contract(updater.inspect_target())
    updater.mounts_override = [_data_mount(updater), _telegram_mount(updater)]
    with pytest.raises(u.UpdateError):
        updater.inspect_target()


def test_telegram_source_inside_swapped_data_is_refused_before_mutation(updater):
    mount = _telegram_mount(updater)
    source = updater.data / "telegram-bot-api"
    source.mkdir()
    mount["Source"] = str(source)
    updater.mounts_override = [_data_mount(updater), mount]
    before = u.tree_manifest(updater.data)
    with pytest.raises(u.UpdateError):
        updater.update("registry.example/korra:latest")
    assert u.tree_manifest(updater.data) == before
    assert not any(call[0] in {"pull", "native", "stop", "start_image"} for call in updater.calls)


@pytest.mark.parametrize("rollback", [False, True])
def test_launcher_gets_exact_telegram_mount_not_ambient_overrides(updater, monkeypatch, rollback):
    mount = _telegram_mount(updater)
    updater.mounts_override = [_data_mount(updater), mount]
    updater.receipt["telegram_mount"] = updater.telegram_mount_contract(updater.inspect_target())
    updater.receipt["phase"] = "rollback_recreate" if rollback else "recreate"
    updater.receipt["old_resources"] = {"nano_cpus": 0, "memory_bytes": 0}
    monkeypatch.setenv("BOT_API_DIR", "/foreign")
    monkeypatch.setenv("BOT_API_DEST", "/foreign")
    captured = []
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **kw:
                        captured.append(kw["env"]) or types.SimpleNamespace(returncode=0, stdout=""))
    u.Updater.start_image(updater, NEW)
    assert captured[0]["BOT_API_DIR"] == mount["Source"]
    assert captured[0]["BOT_API_DEST"] == mount["Destination"]
    assert captured[0]["BOT_API_AUTODETECT"] == "0"


def test_absent_telegram_contract_disables_launcher_discovery(updater):
    updater.receipt["telegram_mount"] = updater.telegram_mount_contract(updater.inspect_target())
    assert updater.launch_telegram_env() == {
        "BOT_API_DIR": "", "BOT_API_DEST": "", "BOT_API_AUTODETECT": "0",
    }


@pytest.mark.parametrize("present", [False, True])
def test_real_launcher_preserves_captured_media_contract_without_docker_discovery(updater, present):
    if present:
        updater.mounts_override = [_data_mount(updater), _telegram_mount(updater)]
    updater.receipt["telegram_mount"] = updater.telegram_mount_contract(updater.inspect_target())
    u.shutil.copy2(SOURCE.with_name("up.sh"), updater.home / "up.sh")
    (updater.data / "config.yaml").write_text("telegram_base_url: http://127.0.0.1:19999/bot\n")
    binaries = updater.home / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_CALL_LOG"\nexit 77\n')
    docker.chmod(0o755)
    env = {**os.environ, "DATA": str(updater.data), "ENGINE_UID": str(os.getuid()),
           "ENGINE_GID": str(os.getgid()), "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
           "DOCKER_CALL_LOG": str(updater.home / "docker-calls.log"),
           **updater.launch_telegram_env()}
    result = subprocess.run(["bash", str(updater.home / "up.sh"), "--dry-run"],
                            env=env, text=True, capture_output=True, check=True)
    argv = shlex.split(result.stdout.splitlines()[-1])
    assert not (updater.home / "docker-calls.log").exists()
    media = [argv[i+1] for i, value in enumerate(argv[:-1])
             if value == "--mount" and "telegram-bot-api" in argv[i+1]]
    if present:
        contract = updater.receipt["telegram_mount"]
        assert media == [f"type=bind,src={contract['source']},dst={contract['destination']},readonly"]
        assert "KORRA_TELEGRAM_LOCAL_ROOT=" + contract["destination"] in argv
    else:
        assert media == []
        assert not any(value.startswith("KORRA_TELEGRAM_LOCAL_ROOT=") for value in argv)


def test_telegram_and_google_mounts_can_coexist_without_allowing_other_binds(updater):
    updater.mounts_override = [_telegram_mount(updater), _google_mount(updater), _data_mount(updater)]
    updater.validate_google_oauth_source = lambda _gid: None
    assert updater.inspect_target()["Image"] == OLD


@pytest.mark.parametrize("source", ["/", "/root", "/opt/data", "/host/path,readonly=false", "relative/path"])
def test_telegram_contract_rejects_broad_and_malformed_sources(updater, source):
    with pytest.raises(u.UpdateError):
        updater.validate_telegram_contract({"present": True, "source": source,
                                            "destination": "/opt/data/telegram-bot-api"})


def test_google_mount_source_must_exist_and_must_not_be_a_dangling_symlink(updater):
    updater.mounts_override = [_data_mount(updater), _google_mount(updater)]

    with pytest.raises(u.UpdateError, match="identity/mount"):
        updater.inspect_target()

    source = updater.google_oauth_source()
    source.parent.mkdir()
    source.symlink_to(source.parent / "missing.json")
    with pytest.raises(u.UpdateError, match="identity/mount"):
        updater.inspect_target()


def test_recorded_google_mount_cannot_disappear_on_target_or_rollback(updater):
    updater.mounts_override = [_data_mount(updater), _google_mount(updater)]
    updater.validate_google_oauth_source = lambda _gid: None
    initial = updater.inspect_target()
    updater.receipt["google_oauth_mount"] = updater.google_oauth_mount_contract(initial)

    updater.mounts_override = [_data_mount(updater)]
    with pytest.raises(u.UpdateError, match="identity/mount"):
        updater.inspect_target()


def test_update_records_google_mount_contract_before_mutation(updater, monkeypatch):
    updater.mounts_override = [_google_mount(updater), _data_mount(updater)]
    updater.validate_google_oauth_source = lambda _gid: None

    updater.update("registry.example/korra:latest", dry_run=True)

    assert updater.receipt["google_oauth_mount"] == {
        "present": True,
        "source": str(updater.google_oauth_source()),
    }


def test_recreate_requires_recorded_google_source_before_running_up_sh(updater, monkeypatch):
    updater.receipt["phase"] = "recreate"
    updater.receipt["old_resources"] = {"nano_cpus": 0, "memory_bytes": 0}
    updater.receipt["google_oauth_mount"] = {
        "present": True,
        "source": str(updater.google_oauth_source()),
    }
    attempted = []
    monkeypatch.setattr(u.subprocess, "run", lambda *_args, **_kwargs: attempted.append(True))

    with pytest.raises(u.UpdateError, match="credential is missing"):
        u.Updater.start_image(updater, NEW)
    assert attempted == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Source", "/opt/korra/foreign-data"),
        ("Destination", "/opt/foreign-data"),
        ("RW", False),
        ("Type", "volume"),
    ],
)
def test_changed_data_mount_is_rejected(updater, field, value):
    data_mount = _data_mount(updater)
    data_mount[field] = value
    updater.mounts_override = [data_mount]

    with pytest.raises(u.UpdateError, match="identity/mount"):
        updater.inspect_target()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Source", "/opt/korra/google/foreign.json"),
        ("Destination", "/run/korra-secrets/foreign.json"),
        ("RW", True),
        ("Type", "volume"),
    ],
)
def test_changed_google_oauth_mount_is_rejected(updater, field, value):
    google_mount = _google_mount(updater)
    google_mount[field] = value
    updater.mounts_override = [_data_mount(updater), google_mount]

    with pytest.raises(u.UpdateError, match="identity/mount"):
        updater.inspect_target()


def test_any_extra_mount_is_rejected(updater):
    updater.mounts_override = [
        _data_mount(updater),
        _google_mount(updater),
        {
            "Type": "bind",
            "Source": "/host/extra",
            "Destination": "/container/extra",
            "RW": False,
        },
    ]

    with pytest.raises(u.UpdateError, match="identity/mount"):
        updater.inspect_target()


def test_dry_run_does_not_pull_drain_or_write_data(updater):
    before = u.tree_manifest(updater.data)
    updater.update("registry.example/korra:latest", dry_run=True)
    assert updater.receipt["phase"] == "dry_run"
    assert u.tree_manifest(updater.data) == before
    assert not any(call[0] in {"pull", "native", "stop"} for call in updater.calls)


def test_drain_timeout_restores_admission_and_never_stops(updater, monkeypatch):
    updater.busy = True
    monkeypatch.setenv("DRAIN_TIMEOUT", "0")
    with pytest.raises(u.UpdateError, match="normal admission restored"):
        updater.update("registry.example/korra:latest")
    assert updater.running and not updater.is_draining
    assert not any(call[0] == "stop" for call in updater.calls)
    assert not (updater.job / "before").exists()


def test_successful_update_leaves_no_drain_marker_in_data(updater):
    # Маркер, оставшийся после удавшегося обновления, запирал следующее:
    # в снимок он не попадает, поэтому после отката его не было, а после
    # обычного успеха он лежал в DATA и упирал вторую операцию в «чужой drain».
    updater.update("registry.example/korra:latest")
    assert updater.receipt["status"] == "succeeded"
    assert not (updater.data / ".drain_request.json").exists()
    assert updater.calls.index(("native", "cancel")) > updater.calls.index(("start_image", NEW))


def test_second_update_after_a_successful_one_still_drains(updater):
    updater.update("registry.example/korra:latest")
    updater.tags.clear()
    updater.receipt = {}
    updater.initialize("second-job", "registry.example/korra:latest")
    updater.image = OLD
    (updater.home / "IMAGE").write_text(OLD)
    updater.update("registry.example/korra:latest")
    assert updater.receipt["status"] == "succeeded"


def test_drain_cleanup_failure_does_not_undo_a_finished_update(updater, monkeypatch):
    real = updater.native_states

    def flaky(action="status"):
        if action == "cancel":
            raise u.UpdateError("docker exec failed (exit 1)")
        return real(action)

    monkeypatch.setattr(updater, "native_states", flaky)
    updater.update("registry.example/korra:latest")
    assert updater.receipt["status"] == "succeeded" and updater.image == NEW
    assert "Drain marker cleanup skipped" in (updater.home / "updates.log").read_text()


def run_drain_code(monkeypatch, root, action, principal):
    """Выполнить DRAIN_CODE так же, как его выполняет `docker exec` в образе.

    Настоящий `gateway.drain_control` не подменяется: смысл проверки в том,
    что updater и движок одинаково отвечают на вопрос «drain сейчас идёт?».
    """
    import pathlib as real_pathlib

    class Runner:
        def _drain_control_watcher(self):
            return "_persist_active_agents"

        def _active_api_run_count(self):
            return "active_agent_work_count"

    control = types.ModuleType("gateway.control_socket")
    control.query_gateway_control = lambda home, what: {"gateway_state": "running", "active_agents": 0}
    runner = types.ModuleType("gateway.run")
    runner.GatewayRunner = Runner
    api_server = types.ModuleType("gateway.platforms.api_server")
    api_server.APIServerAdapter = object
    stub_pathlib = types.ModuleType("pathlib")
    stub_pathlib.Path = lambda value: root if str(value) == "/opt/data" else real_pathlib.Path(value)
    for name, module in (("gateway.control_socket", control), ("gateway.run", runner),
                         ("gateway.platforms.api_server", api_server), ("pathlib", stub_pathlib)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "argv", ["-c", action, principal])
    exec(compile(u.DRAIN_CODE, "DRAIN_CODE", "exec"), {"__name__": "__main__"})


@pytest.fixture
def drain_root(tmp_path):
    from gateway import drain_control

    root = tmp_path / "opt-data"
    (root / "profiles").mkdir(parents=True)
    drain_control.current_instantiation_epoch.cache_clear()
    yield root
    drain_control.current_instantiation_epoch.cache_clear()


def test_drain_marker_left_by_a_previous_container_does_not_block_a_new_update(monkeypatch, drain_root):
    from gateway import drain_control

    marker = drain_control.drain_request_path(drain_root)
    marker.write_text(json.dumps({"action": "drain", "principal": "host-updater:earlier-job",
                                  "epoch": "boot-of-a-container-that-is-gone:1"}))
    run_drain_code(monkeypatch, drain_root, "drain", "host-updater:new-job")
    assert json.loads(marker.read_text())["principal"] == "host-updater:new-job"


def test_expired_same_epoch_drain_marker_does_not_block_a_new_update(monkeypatch, drain_root):
    from gateway import drain_control

    drain_control.write_drain_request(home=drain_root, principal="host-updater:abandoned-job")
    marker = drain_control.drain_request_path(drain_root)
    body = json.loads(marker.read_text())
    body["requested_at"] = "2026-09-08T00:00:00+00:00"
    marker.write_text(json.dumps(body))
    monkeypatch.setattr(drain_control, "_marker_is_expired", lambda body: True)
    run_drain_code(monkeypatch, drain_root, "drain", "host-updater:new-job")
    assert json.loads(marker.read_text())["principal"] == "host-updater:new-job"


def test_drain_of_another_live_operation_is_still_refused(monkeypatch, drain_root):
    from gateway import drain_control

    drain_control.write_drain_request(home=drain_root, principal="host-updater:running-job")
    with pytest.raises(RuntimeError, match="owned by another operation"):
        run_drain_code(monkeypatch, drain_root, "drain", "host-updater:new-job")
    marker = drain_control.drain_request_path(drain_root)
    assert json.loads(marker.read_text())["principal"] == "host-updater:running-job"


def test_cancel_keeps_a_foreign_marker_and_survives_a_corrupt_one(monkeypatch, drain_root):
    from gateway import drain_control

    marker = drain_control.drain_request_path(drain_root)
    drain_control.write_drain_request(home=drain_root, principal="host-updater:other-job")
    run_drain_code(monkeypatch, drain_root, "cancel", "host-updater:new-job")
    assert json.loads(marker.read_text())["principal"] == "host-updater:other-job"
    marker.write_text("{половина файла")
    run_drain_code(monkeypatch, drain_root, "cancel", "host-updater:new-job")
    assert marker.exists()


def test_cancel_removes_our_own_marker(monkeypatch, drain_root):
    from gateway import drain_control

    drain_control.write_drain_request(home=drain_root, principal="host-updater:new-job")
    run_drain_code(monkeypatch, drain_root, "cancel", "host-updater:new-job")
    assert not drain_control.drain_request_path(drain_root).exists()


def test_failed_update_rolls_back_data_preserving_new_auth_and_export(updater):
    updater.fail_smoke = True
    with pytest.raises(u.UpdateError, match="Injected model"):
        updater.update("registry.example/korra:latest")
    assert updater.receipt["status"] == "rolled_back"
    assert updater.image == OLD and updater.running
    assert json.loads((updater.data / "auth.json").read_text())["refresh_token"] == "rotated"
    assert not (updater.data / "revoked.token").exists()
    assert not (updater.data / "new-message.txt").exists()
    assert (updater.job / "after/new-message.txt").read_text() == "write during new boot"
    assert (updater.data / "profiles/secretary/.no-bundled-skills").exists()
    with sqlite3.connect(updater.data / "state.db") as database:
        assert database.execute("SELECT * FROM messages").fetchall() == [("before",)]
    with sqlite3.connect(updater.job / "after/state.db") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (2,)
    changes = json.loads((updater.job / "post-update-changes.json").read_text())
    assert {"auth.json", "state.db", "new-message.txt", "revoked.token"} <= set(changes)


def _profile_with_gateway_state(updater, name, desired_state):
    home = updater.data / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "SOUL.md").write_text("профиль " + name)
    (home / "gateway_state.json").write_text(json.dumps({
        "gateway_state": "running", "desired_state": desired_state,
        # Идентичность умершего контейнера: возвращать её обратно нельзя.
        "pid": 4242, "argv": ["korra", "gateway", "run", "-p", name],
        "start_time": "8140012", "code_sha": "9876d4a2"}))
    return home


def _boot_actions(updater):
    """Что сделает с восстановленным DATA сам движок на старте контейнера."""
    from korra_cli.container_boot import reconcile_profile_gateways

    actions = reconcile_profile_gateways(
        hermes_home=updater.data, scandir=updater.data.parent / "scandir", dry_run=True,
        container_argv=("/init", "main-wrapper.sh", "gateway", "run"))
    return {action.profile: action.action for action in actions}


def test_rollback_returns_profile_gateways_to_their_recorded_intent(updater):
    (updater.data / "gateway_state.json").write_text(json.dumps(
        {"gateway_state": "running", "desired_state": "running", "pid": 41}))
    _profile_with_gateway_state(updater, "secretary", "running")
    _profile_with_gateway_state(updater, "figma-storybook", "running")
    _profile_with_gateway_state(updater, "archive", "stopped")
    updater.fail_smoke = True

    with pytest.raises(u.UpdateError, match="Injected model"):
        updater.update("registry.example/korra:latest")

    assert updater.receipt["status"] == "rolled_back"
    actions = _boot_actions(updater)
    assert actions["secretary"] == "started" and actions["figma-storybook"] == "started"
    # Осознанно остановленный профиль откат не поднимает.
    assert actions["archive"] == "registered"
    body = json.loads((updater.data / "profiles/secretary/gateway_state.json").read_text())
    assert body == {"desired_state": "running", "gateway_state": "running"}
    # Отдельный `--rollback` читает намерение из квитанции, а не из памяти.
    receipt = json.loads((updater.job / "status.json").read_text())
    assert receipt["gateway_intent"]["profiles/archive/gateway_state.json"]["desired_state"] == "stopped"
    assert json.loads((updater.job / "restored-gateway-intent.json").read_text()) == [
        "gateway_state.json", "profiles/archive/gateway_state.json",
        "profiles/figma-storybook/gateway_state.json", "profiles/secretary/gateway_state.json"]


def test_backup_corruption_prevents_restore(updater):
    updater.update("registry.example/korra:latest")
    (updater.job / "before/config.yaml").write_text("tampered")
    before = u.tree_manifest(updater.data)
    with pytest.raises(u.UpdateError, match="checksum"):
        updater.rollback()
    assert u.tree_manifest(updater.data) == before
    assert updater.running


def test_snapshot_keeps_auth_and_sqlite_with_non_db_extension(tmp_path):
    source, target = tmp_path / "source", tmp_path / "snapshot"
    source.mkdir()
    (source / "browser-profiles").mkdir()
    with sqlite3.connect(source / "browser-profiles/Cookies") as database:
        database.execute("CREATE TABLE cookie(value)")
        database.execute("INSERT INTO cookie VALUES ('private')")
    (source / "gateway_state.json").write_text("stale")
    u.snapshot(source, target)
    assert not (target / "gateway_state.json").exists()
    with sqlite3.connect(target / "browser-profiles/Cookies") as database:
        assert database.execute("SELECT * FROM cookie").fetchall() == [("private",)]


def test_snapshot_sqlite_wal_cached_directory_entries(tmp_path):
    source, target = tmp_path / "source", tmp_path / "snapshot"
    source.mkdir()
    database = sqlite3.connect(source / "state.db")
    try:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE messages(text)")
        database.execute("INSERT INTO messages VALUES ('committed WAL row')")
        database.commit()
        assert (source / "state.db-wal").exists()
        u.snapshot(source, target)
        assert not (target / "state.db-wal").exists()
        assert not (target / "state.db-shm").exists()
        with sqlite3.connect(target / "state.db") as snapshot:
            assert snapshot.execute("SELECT * FROM messages").fetchall() == [("committed WAL row",)]
    finally:
        database.close()


def test_snapshot_of_snapshot_never_changes_source_wal_manifest(tmp_path):
    source, before, restored = (tmp_path / name for name in ("source", "before", "restored"))
    source.mkdir()
    database = sqlite3.connect(source / "state.db")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE messages(text)")
    database.execute("INSERT INTO messages VALUES ('preserved')")
    database.commit()
    database.close()
    original = u.tree_manifest(source)
    manifest = u.snapshot(source, before)
    assert u.tree_manifest(source) == original
    u.snapshot(before, restored)
    assert u.tree_manifest(before) == manifest
    assert not list(before.glob("*-wal")) and not list(before.glob("*-shm"))
    with sqlite3.connect(restored / "state.db") as connection:
        assert connection.execute("SELECT * FROM messages").fetchall() == [("preserved",)]


def test_snapshot_never_follows_external_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (source / "token-link").symlink_to(outside)
    target = tmp_path / "snapshot"
    u.snapshot(source, target)
    assert (target / "token-link").is_symlink()
    assert outside.read_text() == "untouched"


def test_credential_overlay_rejects_symlink_ancestor(tmp_path):
    latest, restored, outside = (tmp_path / n for n in ("latest", "restored", "outside"))
    latest.mkdir()
    restored.mkdir()
    outside.mkdir()
    (latest / "profiles/a").mkdir(parents=True)
    (latest / "profiles/a/auth.json").write_text("new")
    (restored / "profiles").symlink_to(outside, target_is_directory=True)
    with pytest.raises(u.UpdateError, match="symlink ancestor"):
        u.overlay_credentials(latest, restored)
    assert list(outside.iterdir()) == []


def test_prune_only_unchanged_managed_skills(updater):
    base = updater.data / "skills"
    base.mkdir()
    old = {"skills": {}}
    lines = []
    for name in ("unchanged", "customized", "unmanaged", "pinned"):
        path = base / name
        path.mkdir()
        (path / "SKILL.md").write_text("original")
        old["skills"][name] = {"path": name, "native_hash": "native", "files": u.tree_manifest(path)}
        if name != "unmanaged":
            lines.append(name + ":native")
    (base / ".bundled_manifest").write_text("\n".join(lines))
    (base / "customized/SKILL.md").write_text("customer additions")
    (base / "pinned/.pin").touch()
    removed = updater.prune_skills(old, {"skills": {}})
    assert removed == ["skills/unchanged"]
    assert all((base / n).exists() for n in ("customized", "unmanaged", "pinned"))


@pytest.mark.parametrize("value", ["../other", "/other", "name;rm", "name\nother", "-flag"])
def test_identifiers_reject_traversal_and_command_injection(value):
    with pytest.raises(u.UpdateError):
        u.checked_identifier(value)


def test_cli_rejects_conflicting_modes():
    with pytest.raises(SystemExit):
        u.parse_args(["--update", "image", "--rollback", "job"])


def test_already_current_does_not_recreate(updater):
    updater.update(OLD)
    assert updater.receipt["phase"] == "already_current"
    assert not any(call[0] in {"stop", "start_image"} for call in updater.calls)


def test_rollback_refuses_a_replaced_container(updater):
    updater.update("registry.example/korra:latest")
    updater.image = "sha256:" + "3" * 64
    before = u.tree_manifest(updater.data)
    calls = len(updater.calls)
    with pytest.raises(u.UpdateError, match="changed by another"):
        updater.rollback()
    assert u.tree_manifest(updater.data) == before
    assert updater.running
    assert not any(call[0] == "stop" for call in updater.calls[calls:])


def test_profile_opt_out_retains_even_unchanged_bundled_orphan(updater):
    path = updater.data / "profiles/secretary/skills/orphan"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("unchanged original")
    (path.parent / ".bundled_manifest").write_text("orphan:native\n")
    old = {"skills": {"orphan": {"path": "orphan", "native_hash": "native", "files": u.tree_manifest(path)}}}
    assert updater.prune_skills(old, {"skills": {}}) == []
    assert path.is_dir()


def test_prune_never_reads_or_rewrites_marker_through_profiles_symlink(updater, tmp_path, monkeypatch):
    root = updater.data / "skills/orphan"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("unchanged")
    (root.parent / ".bundled_manifest").write_text("orphan:native\n")
    old = {"skills": {"orphan": {"path": "orphan", "native_hash": "native", "files": u.tree_manifest(root)}}}
    outside = tmp_path / "outside"
    marker = outside / "foreign/skills/.bundled_manifest"
    marker.parent.mkdir(parents=True)
    marker.write_text("untouched-external:hash\n")
    u.shutil.rmtree(updater.data / "profiles")
    (updater.data / "profiles").symlink_to(outside, target_is_directory=True)
    read_text = Path.read_text
    def guarded(path, *args, **kwargs):
        if path.resolve() == marker:
            raise AssertionError("Updater read a marker outside DATA")
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    assert updater.prune_skills(old, {"skills": {}}) == ["skills/orphan"]
    assert marker.read_bytes() == b"untouched-external:hash\n"


def test_capabilities_is_unprivileged_without_data_or_docker(monkeypatch, capsys):
    monkeypatch.setenv("DATA", "/does/not/exist")
    monkeypatch.setattr(u.os, "geteuid", lambda: 1000)
    def forbidden(*args, **kwargs):
        raise AssertionError("Capabilities accessed subprocess/filesystem configuration")
    monkeypatch.setattr(u.subprocess, "run", forbidden)
    monkeypatch.setattr(u, "trusted_control", forbidden)
    assert u.main(["--capabilities"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["protocol"] == 1 and output["rollback_detach"] is True
    # Кампания должна отличать хост, который сам держит договор о timezone,
    # от хоста со старым kit, где эту проверку по-прежнему ведёт оператор.
    assert output["runtime_timezone"] is True


def test_job_cannot_be_loaded_for_different_data(updater):
    updater.receipt["data"] = "/other/client/data"
    u.atomic_json(updater.job / "status.json", updater.receipt)
    # Trust-path checks have separate coverage; temp pytest dirs are in /tmp.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(u, "trusted_control", lambda path: None)
        with pytest.raises(u.UpdateError, match="another deployment"):
            u.load_job(updater, "fixture-job")


def test_control_files_reject_engine_owner(tmp_path, monkeypatch):
    file = tmp_path / "updater.py"
    file.write_text("executable")
    file.chmod(0o666)
    with pytest.raises(u.UpdateError, match="root-owned"):
        u.trusted_control(file)


def test_insufficient_disk_never_drains(updater, monkeypatch):
    from collections import namedtuple
    Disk = namedtuple("Disk", "total used free")
    updater.free_space = u.Updater.free_space.__get__(updater)
    monkeypatch.setattr(u.shutil, "disk_usage", lambda path: Disk(1000, 999, 1))
    with pytest.raises(u.UpdateError, match="Insufficient space"):
        updater.update("registry.example/korra:latest")
    assert not any(call[0] in {"native", "stop", "pull"} for call in updater.calls)


@pytest.mark.parametrize("before,now,expected", [
    ({"telegram": {"bot_token": "revoked", "enabled": True}}, {}, {"telegram": {"enabled": True}}),
    ({"custom_providers": [{"name": "demo", "api_key": "revoked", "model": "old"}]},
     {"custom_providers": []}, {"custom_providers": [{"name": "demo", "model": "old"}]}),
    ({"providers": {"a": {"api_key": "old", "model": "before"}}},
     {"providers": {"a": {"api_key": "new", "model": "after"}}},
     {"providers": {"a": {"api_key": "new", "model": "before"}}}),
    ({"custom_providers": [{"name": "a", "api_key": "a-old"}, {"name": "b", "api_key": "b-old"}]},
     {"custom_providers": [{"name": "b", "api_key": "b-new"}, {"name": "a", "api_key": "a-new"}]},
     {"custom_providers": [{"name": "a", "api_key": "a-new"}, {"name": "b", "api_key": "b-new"}]}),
])
def test_config_auth_rotation_and_whole_parent_revocation(before, now, expected):
    u.merge_config_credentials(before, now)
    assert before == expected


@pytest.mark.parametrize("relative", [
    "mcp-tokens/server.json", "profiles/secretary/mcp-tokens/server.client.json",
    "pairing/telegram.json", "profiles/secretary/platforms/pairing/discord.json",
    "cache/bws_cache.enc.json", "profiles/secretary/cache/bws_cache.json",
    "webhook_subscriptions.json", "profiles/secretary/webhook_subscriptions.json",
    ".secrets/yandex.env", "profiles/secretary/.secrets/providers/service.env",
])
def test_canonical_credential_stores_preserve_updates_and_revocation(tmp_path, relative):
    latest, restored = tmp_path / "latest", tmp_path / "restored"
    latest.mkdir()
    restored.mkdir()
    new, old = latest / relative, restored / relative
    new.parent.mkdir(parents=True, exist_ok=True)
    old.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("rotated")
    old.write_text("old")
    u.overlay_credentials(latest, restored)
    assert old.read_text() == "rotated"
    new.unlink()
    u.overlay_credentials(latest, restored)
    assert not old.exists()


@pytest.mark.parametrize("relative", [
    "lazy-packages/packaging/_tokenizer.py", "lazy-packages/openpyxl/formula/tokenizer.py",
    "lazy-packages/aiohttp/cookiejar.py", "profiles/secretary/lazy-packages/token.json",
    ".cache/uv/archive-v0/pkg/auth.json", "home/.cache/uv/pkg/token.json",
    ".venv/lib/python3.13/site-packages/credentials.json",
    "profiles/secretary/.venv/lib/python3.13/site-packages/oauth.json",
    "node_modules/pkg/token.json", "skills/custom/scripts/oauth.py",
])
def test_credential_overlay_never_creates_partial_library_or_cache_tree(tmp_path, relative):
    latest, restored = tmp_path / "latest", tmp_path / "restored"
    latest.mkdir()
    restored.mkdir()
    payload = latest / relative
    payload.parent.mkdir(parents=True)
    payload.write_text("library payload, not a credential")
    assert u.overlay_credentials(latest, restored) == []
    assert list(restored.iterdir()) == []


def test_new_credential_ancestors_preserve_latest_owner_mode_under_private_umask(tmp_path):
    latest, restored = tmp_path / "latest", tmp_path / "restored"
    latest.mkdir()
    restored.mkdir()
    source = latest / "profiles/new-agent/auth/provider/token.json"
    source.parent.mkdir(parents=True)
    source.write_text("rotated token")
    for parent in [source.parent, *list(source.parent.parents)[:3]]:
        parent.chmod(0o750)
    before_umask = os.umask(0o077)
    try:
        u.overlay_credentials(latest, restored)
    finally:
        os.umask(before_umask)
    target = restored / source.relative_to(latest)
    assert target.read_text() == "rotated token"
    parent = target.parent
    while parent != restored:
        expected = latest / parent.relative_to(restored)
        assert (parent.stat().st_uid, parent.stat().st_gid, parent.stat().st_mode & 0o777) == (
            expected.stat().st_uid, expected.stat().st_gid, expected.stat().st_mode & 0o777)
        parent = parent.parent


def test_rollback_resumes_verified_export_after_two_credential_failures(updater):
    updater.update("registry.example/korra:latest")
    (updater.data / "auth.json").write_text('{"refresh_token": "latest"}')
    (updater.data / "new-message.txt").write_text("retain this")
    failures = [2]
    def transient(latest, stage):
        if failures[0]:
            failures[0] -= 1
            # A partial transform must not be trusted on retry.
            (stage / "config.yaml").write_text("half-applied\n")
            raise u.UpdateError("Injected credential parser outage")
        return []
    updater.preserve_config_credentials = transient
    for _ in range(2):
        with pytest.raises(u.UpdateError, match="parser outage"):
            updater.rollback()
        assert not updater.running
        assert (updater.job / "after/new-message.txt").read_text() == "retain this"
    updater.rollback()
    assert updater.receipt["status"] == "rolled_back" and updater.running
    assert (updater.data / "config.yaml").read_text() == "model: fixture\n"
    assert json.loads((updater.data / "auth.json").read_text())["refresh_token"] == "latest"
    assert len(list(updater.data.parent.glob("data.restore-fixture-job.attempt-*"))) == 2
    assert (updater.job / "after/new-message.txt").read_text() == "retain this"


def test_rollback_retry_exports_writes_after_container_restarts(updater):
    updater.update("registry.example/korra:latest")
    updater.preserve_config_credentials = lambda *args: (_ for _ in ()).throw(u.UpdateError("once"))
    with pytest.raises(u.UpdateError, match="once"):
        updater.rollback()
    (updater.data / "late-message.txt").write_text("later write")
    updater.running = True
    updater.preserve_config_credentials = lambda *args: []
    updater.rollback()
    assert updater.receipt["status"] == "rolled_back"
    assert len(updater.receipt["post_update_exports"]) == 2
    assert Path(updater.receipt["post_update_export"]).joinpath("late-message.txt").read_text() == "later write"


def test_cli_dry_run_job_id_cannot_claim_real_update(updater, monkeypatch, capsys):
    monkeypatch.setattr(u.os, "geteuid", lambda: 0)
    monkeypatch.setattr(u, "HERE", updater.home)
    monkeypatch.setattr(u, "Updater", lambda: updater)
    monkeypatch.setattr(u, "trusted_control", lambda path: None)
    reference = "registry.example/korra:latest"
    assert u.main(["--update", reference, "--job-id", "cli-dry", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "dry_run"
    with pytest.raises(u.UpdateError, match="dry-run/execution mode"):
        u.main(["--update", reference, "--job-id", "cli-dry"])
    assert updater.image == OLD and updater.running


def _stale_job(updater, job_id, candidate, rollback):
    job = updater.jobs / job_id
    job.mkdir()
    u.atomic_json(job / "status.json", {"job_id": job_id, "name": updater.name, "data": str(updater.data),
                                        "candidate_image_ref": candidate, "rollback_image_ref": rollback})


def test_gc_keeps_current_and_previous_and_drops_this_installations_stale_tags(updater):
    updater.update("registry.example/korra:latest")
    current = {updater.receipt["candidate_image_ref"], updater.receipt["rollback_image_ref"]}
    _stale_job(updater, "old-job", "korra-local-candidate:old", "korra-local-rollback:old")
    updater.tags.update({
        "korra-local-candidate:old": "sha256:" + "5" * 64,
        "korra-local-rollback:old": "sha256:" + "6" * 64,
        # Соседняя установка на том же хосте: её теги трогать нельзя.
        "korra-local-candidate:neighbour": "sha256:" + "7" * 64,
        "ghcr.io/example/korra.twenty.one:0.21.4": NEW,
    })

    result = updater.gc()

    assert result["removed"] == ["korra-local-candidate:old", "korra-local-rollback:old"]
    assert set(result["kept"]) == current
    assert result["skipped"] == ["korra-local-candidate:neighbour"]
    assert set(updater.tags) == current | {"korra-local-candidate:neighbour",
                                           "ghcr.io/example/korra.twenty.one:0.21.4"}


def test_gc_keeps_the_rollback_tag_of_the_previous_image(updater):
    updater.update("registry.example/korra:latest")
    rollback = updater.receipt["rollback_image_ref"]
    (updater.home / "IMAGE.prev").unlink()

    assert rollback in updater.gc()["removed"]
    assert (updater.home / "IMAGE.prev").exists() is False

    # Пока IMAGE.prev на месте, откат по нему остаётся возможен.
    (updater.home / "IMAGE.prev").write_text(OLD + "\n")
    updater.tags[rollback] = OLD
    assert rollback in updater.gc()["kept"]


def test_gc_cli_refuses_while_another_operation_holds_the_target_lock(updater, monkeypatch, capsys):
    monkeypatch.setattr(u.os, "geteuid", lambda: 0)
    monkeypatch.setattr(u, "HERE", updater.home)
    monkeypatch.setattr(u, "Updater", lambda: updater)
    monkeypatch.setattr(u, "trusted_control", lambda path: None)
    assert u.main(["--gc"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "gc"

    held = u.target_lock_path(updater.data).open("a")
    u.fcntl.flock(held, u.fcntl.LOCK_EX | u.fcntl.LOCK_NB)
    try:
        with pytest.raises(u.UpdateError, match="target lock"):
            u.main(["--gc"])
    finally:
        held.close()


def test_rollback_retry_after_old_image_start_failure_preserves_all_exports(updater):
    updater.update("registry.example/korra:latest")
    (updater.data / "new-message.txt").write_text("new image business write")
    start_image = updater.start_image
    attempts = [1]
    def fails_once(image):
        if attempts[0]:
            attempts[0] -= 1
            raise u.UpdateError("Injected old image start failure")
        start_image(image)
    updater.start_image = fails_once
    with pytest.raises(u.UpdateError, match="old image start failure"):
        updater.rollback()
    assert (updater.job / "after/new-message.txt").read_text() == "new image business write"
    assert not (updater.data / "new-message.txt").exists()
    updater.rollback()
    assert updater.receipt["status"] == "rolled_back" and updater.running
    assert updater.image == OLD
    assert len(updater.receipt["displaced_data_history"]) == 2
    assert (updater.job / "after/new-message.txt").read_text() == "new image business write"


def saved_image_archive(path, *, oci=True, corrupt=False):
    files = {}
    def blob(value):
        data = json.dumps(value, sort_keys=True).encode()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        files["blobs/sha256/" + digest.split(":")[1]] = data
        return {"digest": digest, "size": len(data)}
    config = blob({"architecture": "amd64", "os": "linux"})
    image = blob({"schemaVersion": 2, "config": config, "layers": []})
    index = blob({"schemaVersion": 2, "manifests": [image]})
    files["manifest.json"] = json.dumps([{"Config": "blobs/sha256/" + config["digest"].split(":")[1], "Layers": []}]).encode()
    if oci:
        files["index.json"] = json.dumps({"schemaVersion": 2, "manifests": [index]}).encode()
    if corrupt:
        files["blobs/sha256/" + index["digest"].split(":")[1]] += b" "
    with tarfile.open(path, "w") as archive:
        for name, payload in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))
    return [index["digest"], image["digest"], config["digest"]]


@pytest.mark.parametrize("store", ["containerd", "classic"])
def test_tar_resolver_uses_verified_oci_or_classic_docker_identity(updater, tmp_path, store):
    archive = tmp_path / "image.tar"
    ids = saved_image_archive(archive)
    expected = ids[0] if store == "containerd" else ids[-1]
    seen = []
    def docker(*args, **kwargs):
        seen.append(args)
        if args[0] == "load":
            return "loaded"
        if args[:2] == ("image", "inspect") and args[2] == expected:
            return json.dumps([{"Id": expected, "Size": 1}])
        raise u.UpdateError("No such image in this Docker store")
    updater.docker = docker
    assert updater.resolve_image(str(archive)) == expected
    assert updater.receipt["archive_image_ids"] == ids
    assert len(updater.receipt["archive_sha256"]) == 64
    assert seen[0][0] == "load"


def test_classic_docker_tar_without_oci_index(tmp_path):
    path = tmp_path / "classic.tar"
    ids = saved_image_archive(path, oci=False)
    assert u.archive_image_ids(path) == [ids[-1]]


def test_corrupt_oci_descriptor_rejected_before_docker_load(updater, tmp_path):
    path = tmp_path / "corrupt.tar"
    saved_image_archive(path, corrupt=True)
    calls = len(updater.calls)
    with pytest.raises(u.UpdateError, match="checksum/size"):
        updater.resolve_image(str(path))
    assert not updater.calls[calls:]


def test_old_image_reference_is_protected_before_candidate_tag_churn(updater):
    docker = updater.docker
    def churn(*args, **kwargs):
        if args[0] == "pull":
            assert OLD in updater.tags.values(), "Old OCI identity was unreferenced during pull"
        return docker(*args, **kwargs)
    updater.docker = churn
    updater.update("registry.example/korra:latest")
    assert updater.tags[updater.receipt["rollback_image_ref"]] == OLD
    assert updater.tags[updater.receipt["candidate_image_ref"]] == NEW


def test_expected_current_mismatch_fails_before_image_or_data_mutation(updater):
    updater.receipt["expected_current"] = NEW
    before = u.tree_manifest(updater.data)
    with pytest.raises(u.UpdateError, match="Expected current image"):
        updater.update("registry.example/korra:latest")
    assert updater.receipt["error_code"] == "expected_current_mismatch"
    assert updater.receipt["status"] == "failed"
    assert updater.receipt["old_image_id"] == OLD
    assert u.tree_manifest(updater.data) == before
    assert updater.running and updater.image == OLD
    assert not any(call[0] in {"pull", "load", "stop", "native", "start_image"}
                   or call[:2] == ("image", "tag") for call in updater.calls)


def test_expected_current_matches_and_is_immutable_job_identity(updater, monkeypatch, capsys):
    monkeypatch.setattr(u.os, "geteuid", lambda: 0)
    monkeypatch.setattr(u, "HERE", updater.home)
    monkeypatch.setattr(u, "Updater", lambda: updater)
    monkeypatch.setattr(u, "trusted_control", lambda path: None)
    args = ["--update", "registry.example/korra:latest", "--job-id", "cas-job", "--dry-run", "--expected-current", OLD]
    assert u.main(args) == 0
    assert json.loads(capsys.readouterr().out)["expected_current"] == OLD
    with pytest.raises(u.UpdateError, match="different expected-current"):
        u.main([*args[:-1], NEW])
    with pytest.raises(u.UpdateError, match="different expected-current"):
        u.main(args[:-2])
    assert updater.image == OLD


def test_expected_current_cli_rejects_mutable_tag():
    with pytest.raises(SystemExit):
        u.parse_args(["--update", NEW, "--expected-current", "latest"])


def test_expected_archive_checksum_mismatch_rejected_before_load(updater, tmp_path):
    path = tmp_path / "image.tar"
    saved_image_archive(path)
    updater.receipt["expected_archive_sha256"] = "0" * 64
    before = len(updater.calls)
    with pytest.raises(u.UpdateError, match="approved artifact"):
        updater.resolve_image(str(path))
    assert updater.receipt["error_code"] == "archive_checksum_mismatch"
    assert updater.calls[before:] == []


def test_expected_archive_sha_requires_tar_before_registry_pull(updater):
    updater.receipt["expected_archive_sha256"] = "0" * 64
    before = len(updater.calls)
    with pytest.raises(u.UpdateError, match="existing tar"):
        updater.resolve_image("registry.example/korra:latest")
    assert updater.receipt["error_code"] == "archive_required"
    assert updater.calls[before:] == []


def test_archive_mutation_during_load_fails_before_any_image_use(updater, tmp_path):
    path = tmp_path / "image.tar"
    saved_image_archive(path)
    updater.receipt["expected_archive_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    seen = []
    def docker(*args, **kwargs):
        seen.append(args)
        assert args[0] == "load"
        with path.open("ab") as stream:
            stream.write(b"mutated while Docker read it")
        return "loaded"
    updater.docker = docker
    with pytest.raises(u.UpdateError, match="changed during load"):
        updater.resolve_image(str(path))
    assert updater.receipt["error_code"] == "archive_changed"
    assert len(seen) == 1


def test_expected_target_image_mismatch_never_drains(updater):
    updater.receipt["expected_target"] = OLD
    with pytest.raises(u.UpdateError, match="approved target image"):
        updater.update("registry.example/korra:latest")
    assert updater.receipt["error_code"] == "expected_target_mismatch"
    assert updater.receipt["target_image_id"] == NEW
    assert updater.image == OLD and updater.running
    assert not any(call[0] in {"native", "stop", "start_image"} for call in updater.calls)


def test_expected_target_and_artifact_sha_are_immutable_job_identity(updater, monkeypatch, capsys):
    monkeypatch.setattr(u.os, "geteuid", lambda: 0)
    monkeypatch.setattr(u, "HERE", updater.home)
    monkeypatch.setattr(u, "Updater", lambda: updater)
    monkeypatch.setattr(u, "trusted_control", lambda path: None)
    args = ["--update", "/unused/fixture.tar", "--job-id", "artifact-job", "--dry-run",
            "--expected-current", OLD, "--expected-target", NEW, "--expected-archive-sha256", "a" * 64]
    assert u.main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["expected_target"] == NEW and receipt["expected_archive_sha256"] == "a" * 64
    with pytest.raises(u.UpdateError, match="different artifact"):
        u.main([*args[:-1], "b" * 64])
    changed_target = list(args)
    changed_target[changed_target.index("--expected-target") + 1] = OLD
    with pytest.raises(u.UpdateError, match="different artifact"):
        u.main(changed_target)
    assert updater.image == OLD and updater.running


@pytest.mark.parametrize("nanos,memory,expected", [
    (0, 0, {"CONTAINER_CPUS": "", "CONTAINER_MEMORY": ""}),
    (5_125_000_000, 8 * 1024**3, {"CONTAINER_CPUS": "5.125", "CONTAINER_MEMORY": str(8 * 1024**3)}),
])
def test_resources_preserve_original_limits_without_override(updater, monkeypatch, nanos, memory, expected):
    monkeypatch.delenv("CONTAINER_CPUS", raising=False)
    monkeypatch.delenv("CONTAINER_MEMORY", raising=False)
    updater.receipt["old_resources"] = {"nano_cpus": nanos, "memory_bytes": memory}
    assert updater.launch_resource_env() == expected
    assert updater.launch_resource_env(rollback=True) == expected


def test_resource_override_only_applies_to_update_rollback_restores_unlimited(updater, monkeypatch):
    updater.receipt["old_resources"] = {"nano_cpus": 0, "memory_bytes": 0}
    monkeypatch.setenv("CONTAINER_CPUS", "4")
    monkeypatch.setenv("CONTAINER_MEMORY", "4g")
    assert updater.launch_resource_env() == {"CONTAINER_CPUS": "4", "CONTAINER_MEMORY": "4g"}
    assert updater.launch_resource_env(rollback=True) == {"CONTAINER_CPUS": "", "CONTAINER_MEMORY": ""}


def test_preflight_records_original_resources(updater):
    docker = updater.docker
    def inspect_limits(*args, **kwargs):
        value = docker(*args, **kwargs)
        if args[0] == "inspect":
            info = json.loads(value)
            info[0]["HostConfig"].update(NanoCpus=6_000_000_000, Memory=12 * 1024**3)
            return json.dumps(info)
        return value
    updater.docker = inspect_limits
    updater.update("registry.example/korra:latest", dry_run=True)
    assert updater.receipt["old_resources"] == {"nano_cpus": 6_000_000_000, "memory_bytes": 12 * 1024**3, "memory_swap_bytes": 0}


@pytest.mark.parametrize("explicit", [False, True])
def test_up_launcher_resource_flags_are_optional(tmp_path, explicit):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    u.shutil.copy2(SOURCE.with_name("up.sh"), deploy / "up.sh")
    (deploy / "IMAGE").write_text(NEW)
    env = {**os.environ, "DATA": str(data), "ENGINE_UID": str(os.getuid()), "ENGINE_GID": str(os.getgid())}
    env.pop("CONTAINER_CPUS", None)
    env.pop("CONTAINER_MEMORY", None)
    if explicit:
        env.update(CONTAINER_CPUS="4", CONTAINER_MEMORY="4g")
    result = subprocess.run(["bash", str(deploy / "up.sh"), "--dry-run"], env=env, text=True,
                            capture_output=True, check=True)
    assert ("--cpus 4" in result.stdout) is explicit
    assert ("--memory 4g" in result.stdout) is explicit


def test_detached_rollback_returns_pending_then_worker_completes(updater, monkeypatch, capsys):
    updater.update("registry.example/korra:latest")
    backup = updater.receipt["backup_path"]
    monkeypatch.setattr(u.os, "geteuid", lambda: 0)
    monkeypatch.setattr(u, "HERE", updater.home)
    monkeypatch.setattr(u, "Updater", lambda: updater)
    monkeypatch.setattr(u, "trusted_control", lambda path: None)
    pending = {}
    def spawn(command, **kwargs):
        # Simulate the actual inherited file description: it holds the same
        # flock after the caller returns, until our worker turn consumes it.
        pending["fd"] = os.dup(kwargs["pass_fds"][0])
        pending["command"] = command
        return type("Process", (), {"pid": 123456})()
    monkeypatch.setattr(u.subprocess, "Popen", spawn)
    assert u.main(["--rollback", "fixture-job", "--detach"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "pending" and response["phase"] == "rollback_pending"
    assert response["action"] == "rollback" and response["backup_path"] == backup
    assert updater.running and updater.image == NEW
    persisted = json.loads((updater.job / "status.json").read_text())
    assert persisted["status"] == "pending" and persisted["phase"] == "rollback_pending"
    # A duplicate request observes the pending receipt under the held lock.
    assert u.main(["--rollback", "fixture-job", "--detach"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pending"
    assert u.main(["--worker", "fixture-job", "--lock-fd", str(pending["fd"]), "--rollback-worker"]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "rolled_back" and completed["action"] == "rollback"
    assert updater.image == OLD and updater.running


def test_detached_rollback_spawn_failure_preserves_recovery_metadata(updater, monkeypatch, capsys):
    updater.update("registry.example/korra:latest")
    backup = updater.receipt["backup_path"]
    monkeypatch.setattr(u.os, "geteuid", lambda: 0)
    monkeypatch.setattr(u, "HERE", updater.home)
    monkeypatch.setattr(u, "Updater", lambda: updater)
    monkeypatch.setattr(u, "trusted_control", lambda path: None)
    def spawn(*args, **kwargs):
        raise OSError("Injected process spawn failure")
    monkeypatch.setattr(u.subprocess, "Popen", spawn)
    assert u.main(["--rollback", "fixture-job", "--detach"]) == 1
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "rollback_failed" and response["phase"] == "spawn_failed"
    assert response["error_code"] == "worker_spawn_failed"
    assert response["backup_path"] == backup
    assert response["old_image_id"] == OLD and response["target_image_id"] == NEW
    assert updater.image == NEW and updater.running
    assert json.loads((updater.job / "status.json").read_text())["status"] == "rollback_failed"


def test_native_schema_rehearsal_runs_before_cleanup_and_recreate(updater):
    updater.update("registry.example/korra:latest")
    assert updater.calls.index(("schema_rehearsal", NEW)) < updater.calls.index(("start_image", NEW))


def test_failed_schema_rehearsal_restores_old_state_before_candidate_boot(updater):
    before = u.tree_manifest(updater.data)
    def fail(image):
        raise u.UpdateError("Injected incompatible native schema")
    updater.rehearse_schema = fail
    with pytest.raises(u.UpdateError, match="incompatible native schema"):
        updater.update("registry.example/korra:latest")
    assert updater.receipt["status"] == "rolled_back"
    assert updater.image == OLD and updater.running
    assert ("start_image", NEW) not in updater.calls
    restored = u.tree_manifest(updater.data)
    restored.pop("state.db")
    before.pop("state.db")
    assert restored == before
    with sqlite3.connect(updater.data / "state.db") as connection:
        assert connection.execute("SELECT * FROM messages").fetchall() == [("before",)]


def _index_the_host_sqlite_rejects(path):
    """База, которую хостовый SQLite бракует, а движок в образе принимает.

    Живой случай (Виктория и Павлова, 12.09.2026): хост 3.45 и образ 3.53
    расходятся в оценке кириллического trigram-индекса FTS5 на байт-идентичном
    файле. Здесь тот же класс расхождения воспроизводится доступным способом —
    индекс, чьё содержимое не сходится с таблицей: постраничная копия проходит,
    а `PRAGMA quick_check` хоста отвечает `malformed inverted index`.
    """
    with sqlite3.connect(path) as database:
        database.execute("CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(content, tokenize='trigram')")
        database.executemany("INSERT INTO messages_fts_trigram(content) VALUES (?)",
                             [("Привет, как дела",), ("Отчёт по проекту",)])
        database.commit()  # индекс должен лечь на диск до того, как его расшатают
        database.execute("DELETE FROM messages_fts_trigram_data WHERE id > 100")
    with sqlite3.connect(path) as database:
        assert database.execute("PRAGMA quick_check").fetchone() != ("ok",)


def test_index_only_the_host_rejects_no_longer_fails_the_backup_gate(updater):
    _index_the_host_sqlite_rejects(updater.data / "profiles/secretary/state.db")

    updater.update("registry.example/korra:latest")

    assert updater.receipt["status"] == "succeeded"
    assert (updater.job / "before/profiles/secretary/state.db").exists()


def test_snapshot_sqlite_is_judged_by_the_image_that_wrote_it(updater):
    updater.update("registry.example/korra:latest")

    judged = [call for call in updater.calls if call[0] == "run"]
    assert judged, "гейт SQLite судит хостовым sqlite3: docker run старого образа не вызывался"
    call = judged[0]
    assert call[call.index("--network") + 1] == "none"
    assert call[call.index("-v") + 1] == str(updater.job / "before") + ":/opt/data:ro"
    assert call[call.index("-v") + 2] == updater.receipt["rollback_image_ref"]
    assert updater.calls.index(("schema_rehearsal", NEW)) > updater.calls.index(call)


def test_engine_verdict_stops_the_update_before_schema_rehearsal(updater):
    with sqlite3.connect(updater.data / "profiles/secretary/state.db") as database:
        database.execute("CREATE TABLE messages(text)")
    updater.judge_verdict = {"bad": ["profiles/secretary/state.db"]}
    before = u.tree_manifest(updater.data)

    with pytest.raises(u.UpdateError, match="profiles/secretary/state.db"):
        updater.update("registry.example/korra:latest")

    assert not any(call[0] == "schema_rehearsal" for call in updater.calls)
    assert updater.image == OLD and updater.running
    assert u.tree_manifest(updater.data) == before


def test_missing_previous_image_fails_the_gate_and_starts_the_container_again(updater):
    updater.judge_image_missing = True

    with pytest.raises(u.UpdateError, match="SQLite"):
        updater.update("registry.example/korra:latest")

    assert not any(call[0] == "schema_rehearsal" for call in updater.calls)
    assert updater.calls.index(("start", updater.name)) > updater.calls.index(("stop", "--time", "60", updater.name))
    assert updater.image == OLD and updater.running


@pytest.fixture
def real_smoke_context(updater, monkeypatch):
    clock = [0.0]
    models = []
    settings = {"alias": "HERMES", "status": {
        "gateway_running": True, "gateway_state": "running", "overall": "ok",
        "config_version": 37, "latest_config_version": 37,
        "components": {name: {"status": "ok"} for name in ("gateway", "dashboard", "storage", "platforms")},
    }}
    monkeypatch.setenv("WAIT_SECONDS", "5")
    monkeypatch.delenv("CONTAINER_CPUS", raising=False)
    monkeypatch.delenv("CONTAINER_MEMORY", raising=False)
    monkeypatch.setattr(u.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(u.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    updater.receipt.update(phase="smoke", served_profiles=["default", "secretary"],
                           old_resources={"nano_cpus": 0, "memory_bytes": 0})
    def urlopen(request, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        if url.endswith("/"):
            return io.BytesIO(f'window.__{settings["alias"]}_SESSION_TOKEN__="test-token"'.encode())
        if url.endswith("/api/status"):
            return io.BytesIO(json.dumps(settings["status"]).encode())
        return io.BytesIO(b'{"profiles":[{"name":"default"},{"name":"secretary"}]}')
    monkeypatch.setattr(u.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updater, "execute", lambda code, *args, **kwargs: models.append(code))
    return updater, clock, models, settings


def test_preflight_expects_every_profile_that_its_own_gateway_serves(updater):
    # Контур без мультиплекса: свой шлюз у каждого профиля, свой served_profiles
    # он не сообщает — имя профиля видно только по его home.
    updater.multiplex = False
    updater.profile_gateways = ("figma-storybook", "secretary")
    for name in ("figma-storybook", "secretary", "archive"):
        _profile_with_gateway_state(updater, name, "running")

    updater.update("registry.example/korra:latest")

    assert updater.receipt["served_profiles"] == ["default", "figma-storybook", "secretary"]
    # `archive` намерен работать, но лежал ещё до обновления: обновление не
    # обязано его поднять, а откат по такому требованию встал бы в
    # rollback_failed на исправном контуре. Гейтом он не становится, но виден.
    assert updater.receipt["expected_profiles"] == ["default", "figma-storybook", "secretary"]
    assert updater.receipt["profiles_down_before"] == ["archive"]


def test_smoke_is_red_with_the_name_of_a_profile_that_never_came_up(real_smoke_context, monkeypatch):
    updater, clock, models, _ = real_smoke_context
    updater.receipt["expected_profiles"] = ["default", "figma-storybook", "secretary"]
    monkeypatch.setenv("PROFILE_WAIT_SECONDS", "20")
    monkeypatch.setattr(updater, "native_states", lambda **kwargs: [
        {"home": "/opt/data", "state": {"gateway_state": "running"}},
        {"home": "/opt/data/profiles/secretary", "state": {"gateway_state": "running"}}])

    with pytest.raises(u.UpdateError, match="figma-storybook"):
        u.Updater.smoke(updater, OLD)

    assert updater.receipt["error_code"] == "profile_gateway_timeout"
    assert updater.receipt["missing_profiles"] == ["figma-storybook"]
    assert models == []


def test_smoke_waits_out_a_slow_profile_inside_its_own_budget(real_smoke_context, monkeypatch):
    updater, clock, models, _ = real_smoke_context
    updater.receipt["expected_profiles"] = ["default", "secretary"]
    monkeypatch.setenv("PROFILE_WAIT_SECONDS", "60")
    homes = [{"home": "/opt/data", "state": {"gateway_state": "running"}}]

    def native(**kwargs):
        # Шлюз профиля поднимается дольше WAIT_SECONDS: исправный выпуск на
        # таком контуре откатывать нельзя.
        if clock[0] < 12:
            return homes
        return homes + [{"home": "/opt/data/profiles/secretary", "state": {"gateway_state": "running"}}]

    monkeypatch.setattr(updater, "native_states", native)
    u.Updater.smoke(updater, OLD)

    assert updater.receipt["profile_readiness"] == "ok"
    assert len(models) == 1 and clock[0] >= 12


def test_smoke_profile_gate_can_be_stood_down_deliberately(real_smoke_context, monkeypatch):
    updater, clock, models, _ = real_smoke_context
    updater.receipt["expected_profiles"] = ["default", "figma-storybook"]
    monkeypatch.setenv("PROFILE_WAIT_SECONDS", "0")
    monkeypatch.setattr(updater, "native_states", lambda **kwargs: [
        {"home": "/opt/data", "state": {"gateway_state": "running"}}])

    u.Updater.smoke(updater, OLD)

    assert updater.receipt["profile_readiness"] == "skipped"
    assert len(models) == 1


@pytest.mark.parametrize("alias", ["HERMES", "KORRA"])
def test_real_smoke_waits_for_native_control_and_profiles_then_calls_model_once(real_smoke_context, monkeypatch, alias):
    updater, clock, models, settings = real_smoke_context
    settings["alias"] = alias
    calls = [0]
    def native(**kwargs):
        calls[0] += 1
        # Каждый запрос ограничен своей фазой: до готовности корня — его
        # сроком (WAIT_SECONDS=5), после — бюджетом профилей, но не больше 10 с.
        assert 0 < kwargs["timeout"] <= 10
        if calls[0] == 1:
            raise u.UpdateError("Gateway control socket unavailable")
        return [{"home": "/opt/data", "state": {
            "gateway_state": "starting" if calls[0] == 2 else "running",
            "served_profiles": ["default"] if calls[0] == 3 else ["default", "secretary"]}}]
    monkeypatch.setattr(updater, "native_states", native)
    u.Updater.smoke(updater, OLD)
    assert calls[0] == 4 and clock[0] == 3
    assert len(models) == 1 and "KORRA_UPDATE_OK" in models[0]


def test_real_smoke_native_timeout_is_bounded_and_never_calls_model(real_smoke_context, monkeypatch):
    updater, clock, models, _ = real_smoke_context
    monkeypatch.setenv("WAIT_SECONDS", "3")
    def native(**kwargs):
        raise u.UpdateError("Gateway control socket unavailable")
    monkeypatch.setattr(updater, "native_states", native)
    with pytest.raises(u.UpdateError, match="Native gateway readiness timed out after 3s"):
        u.Updater.smoke(updater, OLD)
    assert clock[0] == 3 and models == []
    assert updater.receipt["error_code"] == "native_readiness_timeout"


@pytest.mark.parametrize("mismatch", ["image", "stopped", "resources"])
def test_real_smoke_identity_and_resource_failures_are_not_retried(real_smoke_context, mismatch):
    updater, clock, models, _ = real_smoke_context
    if mismatch == "image": updater.image = NEW
    elif mismatch == "stopped": updater.running = False
    else: updater.receipt["old_resources"]["nano_cpus"] = 4_000_000_000
    with pytest.raises(u.UpdateError):
        u.Updater.smoke(updater, OLD)
    assert clock[0] == 0 and models == []


def test_real_smoke_model_failure_has_safe_specific_reason(real_smoke_context, monkeypatch):
    updater, _, _, _ = real_smoke_context
    monkeypatch.setattr(updater, "native_states", lambda **kwargs: [{"home": "/opt/data", "state": {
        "gateway_state": "running", "served_profiles": ["default", "secretary"]}}])
    def failure(*args, **kwargs):
        raise u.UpdateError("private-upstream-response")
    monkeypatch.setattr(updater, "execute", failure)
    with pytest.raises(u.UpdateError, match="Model smoke failed; see private operation.log") as caught:
        u.Updater.smoke(updater, OLD)
    assert "private-upstream-response" not in str(caught.value)
    assert updater.receipt["error_code"] == "model_smoke_failed"


@pytest.mark.parametrize("damage", ["degraded", "missing", "schema", "malformed", "false_version"])
def test_degraded_health_never_reaches_model(real_smoke_context, monkeypatch, damage):
    updater, clock, models, settings = real_smoke_context
    status = settings["status"]
    if damage == "degraded":
        status["components"]["storage"]["status"] = "degraded"
    elif damage == "missing":
        del status["components"]["dashboard"]
    elif damage == "schema":
        status["config_version"] -= 1
    elif damage == "false_version":
        status["config_version"] = True
    else:
        status["components"] = []
    monkeypatch.setattr(updater, "native_states", lambda **kw: [{
        "home": "/opt/data", "state": {"gateway_state": "running", "served_profiles": ["default", "secretary"]}}])
    with pytest.raises(u.UpdateError):
        u.Updater.smoke(updater, OLD)
    assert models == []


@pytest.mark.parametrize("phase", ["smoke", "rollback_recreate"])
def test_provider_mismatch_rejects_before_model(real_smoke_context, monkeypatch, phase):
    updater, _, models, _ = real_smoke_context
    updater.receipt.update(phase=phase, baseline_capability={"mode": "configured", "provider_hash": "a" * 64})
    monkeypatch.setattr(updater, "capability", lambda **kw: {"mode": "foundation"}, raising=False)
    monkeypatch.setattr(updater, "native_states", lambda **kw: [{
        "home": "/opt/data", "state": {"gateway_state": "running", "served_profiles": ["default", "secretary"]}}])
    with pytest.raises(u.UpdateError, match="capability"):
        u.Updater.smoke(updater, OLD)
    assert models == []


def test_baseline_capability_recorded_before_any_stop(updater, monkeypatch):
    calls = []
    def capture(**kw):
        assert not any(call[0] == "stop" for call in updater.calls)
        calls.append(True)
        return {"mode": "foundation"}
    monkeypatch.setattr(updater, "capability", capture, raising=False)
    updater.update("registry.example/korra:latest")
    assert calls == [True]
    assert updater.receipt["baseline_capability"] == {"mode": "foundation"}


@pytest.mark.parametrize("phase", ["smoke", "rollback_recreate"])
def test_foundation_checks_expected_api_error_without_model_ack(real_smoke_context, monkeypatch, phase):
    updater, _, codes, _ = real_smoke_context
    updater.receipt.update(phase=phase, baseline_capability={"mode": "foundation"})
    monkeypatch.setattr(updater, "capability", lambda **kw: {"mode": "foundation"})
    monkeypatch.setattr(updater, "native_states", lambda **kw: [{
        "home": "/opt/data", "state": {"gateway_state": "running", "served_profiles": ["default", "secretary"]}}])
    u.Updater.smoke(updater, OLD)
    assert len(codes) == 1
    assert "KORRA_UPDATE_OK" not in codes[0]
    assert codes[0] == u.FOUNDATION_SMOKE_CODE
    assert updater.receipt["active_capability"] == {"mode": "foundation"}


def test_legacy_rollback_without_capability_refuses_before_stop(updater):
    del updater.receipt["baseline_capability"]
    with pytest.raises(u.UpdateError, match="capability"):
        updater.rollback()
    assert not updater.calls


@pytest.mark.parametrize("case,configured", [("fresh", False), ("key", True), ("missing_key", None)])
def test_capability_probe_uses_real_native_resolver_in_isolated_home(tmp_path, case, configured):
    home = tmp_path / "user"
    home.mkdir()
    data = home / ".hermes"
    data.mkdir()
    config = "gateway: {}\n" if case == "fresh" else "model:\n  provider: anthropic\n  default: claude-fixture\n"
    (data / "config.yaml").write_text(config)
    (data / ".env").write_text("ANTHROPIC_API_KEY=synthetic-test-key\n" if case == "key" else "")
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "HERMES_HOME": str(data),
           "PYTHONPATH": str(SOURCE.parents[2]), "AWS_EC2_METADATA_DISABLED": "true",
           "HERMES_SKIP_CHMOD": "1", "HERMES_DISABLE_LAZY_INSTALLS": "1"}
    result = subprocess.run([sys.executable, "-c", u.CAPABILITY_CODE], env=env,
                            capture_output=True, text=True, timeout=30)
    if configured is None:
        assert result.returncode != 0
        assert not result.stdout.strip()
    else:
        assert result.returncode == 0, result.stderr
        capability = u.validate_capability(json.loads(result.stdout))
        assert capability["mode"] == ("configured" if configured else "foundation")
        assert "synthetic-test-key" not in result.stdout


@pytest.mark.parametrize("case", ["ok", "no_done", "success", "wrong_error", "malformed"])
def test_foundation_probe_exercises_stateless_sse_contract(monkeypatch, case):
    import urllib.request
    message = "Провайдер ответа не настроен: добавьте ключ в разделе «Ключи»."
    event = {"choices": [{"delta": {}, "finish_reason": "error"}], "error": {"message": message}}
    if case == "success":
        event["choices"][0]["finish_reason"] = "stop"
    elif case == "wrong_error":
        event["error"]["message"] = "Ошибка авторизации. Ключи."
    body = "data: " + json.dumps(event) + "\n\n"
    if case != "no_done":
        body += "data: [DONE]\n\n"
    if case == "malformed":
        body = "data: {broken}\n\n"
    calls = []
    def serve(request, **kw):
        payload = json.loads(request.data)
        assert payload["stream"] is True
        assert "KORRA_UPDATE_OK" not in str(payload)
        assert request.full_url == "http://127.0.0.1:8642/v1/chat/completions"
        assert kw["timeout"] == 90
        calls.append(request)
        return io.BytesIO(body.encode())
    monkeypatch.setenv("API_SERVER_KEY", "synthetic-api-key")
    monkeypatch.setenv("API_SERVER_PORT", "8642")
    monkeypatch.setattr(urllib.request, "urlopen", serve)
    if case == "ok":
        exec(u.FOUNDATION_SMOKE_CODE, {})
    else:
        with pytest.raises((RuntimeError, ValueError)):
            exec(u.FOUNDATION_SMOKE_CODE, {})
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["{}", "null", '{"mode":"configured"}', '{"mode":"foundation","secret":"bad"}'])
def test_capability_probe_malformed_output_is_safe(updater, monkeypatch, value):
    monkeypatch.setattr(updater, "execute", lambda *a, **kw: value)
    with pytest.raises(u.UpdateError, match="capability cannot be verified"):
        u.Updater.capability(updater)


def test_capability_probe_timeout_is_bounded_and_safe(updater, monkeypatch):
    def expired(code, *, timeout):
        assert timeout == 30
        raise subprocess.TimeoutExpired("synthetic probe", timeout)
    monkeypatch.setattr(updater, "execute", expired)
    with pytest.raises(u.UpdateError, match="capability cannot be verified"):
        u.Updater.capability(updater)


@pytest.mark.parametrize("phase,override,expected", [
    ("recreate", None, "1"), ("recreate", "0", "0"), ("rollback_recreate", "0", "1")])
def test_native_launcher_preserves_uid_gid_and_admin_mode(updater, monkeypatch, phase, override, expected):
    updater.receipt.update(phase=phase, old_resources={"nano_cpus": 0, "memory_bytes": 0},
        old_runtime={"ENGINE_UID": "12345", "ENGINE_GID": "12346", "AGENT_SUDO": "1"})
    for key in ("ENGINE_UID", "ENGINE_GID", "AGENT_SUDO"):
        monkeypatch.delenv(key, raising=False)
    if override is not None:
        monkeypatch.setenv("AGENT_SUDO", override)
    launches = []
    def launch(args, **kwargs):
        launches.append(kwargs["env"])
        return types.SimpleNamespace(returncode=0, stdout="synthetic native launcher")
    monkeypatch.setattr(u.subprocess, "run", launch)
    u.Updater.start_image(updater, OLD)
    assert launches[0].get("ENGINE_UID") == "12345"
    assert launches[0].get("ENGINE_GID") == "12346"
    assert launches[0].get("AGENT_SUDO") == expected


def test_update_records_original_runtime_root_contract(updater, monkeypatch):
    native = updater.docker
    def docker(*args, **kwargs):
        result = native(*args, **kwargs)
        if args[0] == "inspect":
            value = json.loads(result)
            value[0]["Config"]["Env"] += ["KORRA_UID=12345", "KORRA_GID=12346", "KORRA_AGENT_SUDO=0"]
            return json.dumps(value)
        return result
    monkeypatch.setattr(updater, "docker", docker)
    updater.update("registry.example/korra:latest")
    # Контур без заданного часового пояса: его и нечего сохранять, поэтому обе
    # timezone остаются пустыми, а не выдумываются из дефолта launcher.
    assert updater.receipt["old_runtime"] == {"ENGINE_UID": "12345", "ENGINE_GID": "12346",
                                              "AGENT_SUDO": "0", "TIMEZONE": "", "OWNER_TIMEZONE": ""}


@pytest.mark.parametrize("key,value", [("ENGINE_UID", "12345"), ("ENGINE_GID", "12346"), ("AGENT_SUDO", "unknown")])
def test_invalid_runtime_override_refuses_before_fetch_or_stop(updater, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    before = u.tree_manifest(updater.data)
    with pytest.raises(u.UpdateError):
        updater.update("registry.example/korra:latest")
    assert u.tree_manifest(updater.data) == before
    assert not any(call[0] in {"pull", "stop", "start_image"} for call in updater.calls)


def test_smoke_refuses_wrong_root_mode_before_model(real_smoke_context):
    updater, clock, models, _ = real_smoke_context
    updater.receipt["old_runtime"]["AGENT_SUDO"] = "0"
    with pytest.raises(u.UpdateError, match="root/ownership"):
        u.Updater.smoke(updater, OLD)
    assert clock[0] == 0 and models == []


@pytest.mark.parametrize("phase", ["preflight", "smoke", "rollback_recreate"])
def test_native_exec_uses_receipt_ownership(updater, monkeypatch, phase):
    updater.receipt.update(phase=phase, old_runtime={
        "ENGINE_UID": "12345", "ENGINE_GID": "12346", "AGENT_SUDO": "0"})
    calls = []
    monkeypatch.setattr(updater, "docker", lambda *a, **kw: calls.append(a) or "ok")
    assert u.Updater.execute(updater, "print('synthetic')") == "ok"
    assert calls[0][:3] == ("exec", "-u", "12345:12346")


def test_credential_overlay_runs_with_original_data_ownership(updater, monkeypatch, tmp_path):
    updater.receipt["old_runtime"] = {
        "ENGINE_UID": "12345", "ENGINE_GID": "12346", "AGENT_SUDO": "0"}
    calls = []
    monkeypatch.setattr(updater, "docker", lambda *a, **kw: calls.append(a) or "[]")
    u.Updater.preserve_config_credentials(updater, tmp_path / "latest", tmp_path / "stage")
    assert calls[0][calls[0].index("--user") + 1] == "12345:12346"


def test_native_exec_can_read_private_data_as_selected_uid(updater, monkeypatch, tmp_path):
    if os.geteuid() != 0:
        pytest.skip("requires isolated root fixture for real setuid file permission proof")
    data = tmp_path / "ownership-proof"
    data.mkdir(mode=0o750)
    secret = data / ".env"
    secret.write_text("synthetic-private-config")
    os.chown(data, 12345, 12346)
    os.chown(secret, 12345, 12346)
    secret.chmod(0o600)
    descriptor = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
    updater.receipt["old_runtime"] = {
        "ENGINE_UID": "12345", "ENGINE_GID": "12346", "AGENT_SUDO": "0"}
    def execute(*args, **kwargs):
        selected = args[args.index("-u") + 1]
        ids = selected.split(":")
        uid, gid = int(ids[0]), int(ids[1]) if len(ids) == 2 else 0
        code = "import os; f=os.open('.env',os.O_RDONLY,dir_fd=" + str(descriptor) + "); assert os.read(f,100)==b'synthetic-private-config'; os.close(f); print(str(os.getuid())+':'+str(os.getgid()))"
        result = subprocess.run(["/usr/bin/python3", "-c", code], user=uid, group=gid,
            extra_groups=[], pass_fds=(descriptor,), cwd="/", capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    monkeypatch.setattr(updater, "docker", execute)
    try:
        assert u.Updater.execute(updater, "synthetic probe") == "12345:12346"
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("environment,expected", [
    (["PUID=12345", "PGID=12346"], ("12345", "12346", "1")),
    (["KORRA_UID=", "HERMES_UID=12345", "PUID=999", "KORRA_GID=", "PGID=12346"], ("12345", "12346", "1")),
    (["KORRA_UID=12345", "HERMES_UID=999", "KORRA_GID=12346", "PGID=999"], ("12345", "12346", "1")),
    (["HERMES_AGENT_SUDO=0"], ("10000", "10000", "1")),
    (["KORRA_AGENT_SUDO="], ("10000", "10000", "1")),
    (["KORRA_AGENT_SUDO=FaLsE"], ("10000", "10000", "1")),
])
def test_runtime_identity_follows_native_stage2_environment(environment, expected):
    result = u.runtime_env_from_info({"Config": {"Env": environment}})
    assert tuple(result[key] for key in ("ENGINE_UID", "ENGINE_GID", "AGENT_SUDO")) == expected


@pytest.mark.parametrize("field", ["ENGINE_UID", "ENGINE_GID"])
def test_recorded_runtime_requires_native_remappable_identity(field):
    value = {"ENGINE_UID": "10000", "ENGINE_GID": "10000", "AGENT_SUDO": "1"}
    value[field] = "65535"
    with pytest.raises(u.UpdateError, match="UID|GID"):
        u.validate_runtime_env(value)


# ─── Оба timezone в договоре native update/rollback (K21-092) ───────────────
#
# 15.09.2026 адресное обновление клиента вернуло `succeeded`, а контур переехал
# из Asia/Novosibirsk в Europe/Moscow: часовой пояс задаёт launcher, а его
# постоянный host default не входил в сохраняемый runtime contract.
NOVOSIBIRSK = "Asia/Novosibirsk"
MOSCOW = "Europe/Moscow"
LEGACY_RUNTIME = {"ENGINE_UID": "10000", "ENGINE_GID": "10000", "AGENT_SUDO": "1"}


def _contour_timezone(updater, zone, owner=None):
    """Пусть фиктивный контейнер сообщает то же, что настоящий docker inspect."""
    updater.extra_env = [f"KORRA_TIMEZONE={zone}", f"KORRA_OWNER_TIMEZONE={owner or zone}"]


def _installed_launcher(home, *, default=None, pinned=None):
    """Launcher установки: настоящий up.sh за постоянными настройками хоста.

    Обходится только проверка владельца DATA — к часовому поясу она отношения
    не имеет, а весь путь timezone проходит поставляемый launcher.
    `default` — постоянный host default установки (его и теряли при adoption);
    `pinned` — launcher, который решает сам и договор игнорирует.
    """
    u.shutil.copy2(SOURCE.with_name("up.sh"), home / "launcher-core.sh")
    lines = ["#!/usr/bin/env bash", "set -euo pipefail",
             "ENGINE_UID=$(id -u); ENGINE_GID=$(id -g); export ENGINE_UID ENGINE_GID"]
    if pinned is not None:
        lines.append(f"TIMEZONE={shlex.quote(pinned)}; OWNER_TIMEZONE={shlex.quote(pinned)}")
        lines.append("export TIMEZONE OWNER_TIMEZONE")
    elif default is not None:
        lines.append(f': "${{TIMEZONE:={default}}}"; export TIMEZONE')
    lines.append(f'exec bash {shlex.quote(str(home / "launcher-core.sh"))} "$@"')
    path = home / "up.sh"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return path


def _launcher_timezone(deploy, data, **passed):
    """Что реальный launcher поставил бы контейнеру, без запуска Docker."""
    env = {**os.environ, "DATA": str(data),
           "ENGINE_UID": str(os.getuid()), "ENGINE_GID": str(os.getgid())}
    # Именно этого не хватало при adoption: значение должно приходить из
    # постоянной настройки установки, а не из оболочки проверяющего.
    for key in ("TIMEZONE", "OWNER_TIMEZONE"):
        env.pop(key, None)
    env.update(passed)
    result = subprocess.run(["bash", str(deploy / "up.sh"), "--dry-run"],
                            env=env, text=True, capture_output=True, check=True)
    return u.timezone_from_launch_argv(shlex.split(result.stdout.splitlines()[-1]))


@pytest.fixture
def launcher_home(tmp_path):
    deploy, data = tmp_path / "deploy", tmp_path / "data"
    deploy.mkdir()
    data.mkdir()
    u.shutil.copy2(SOURCE.with_name("up.sh"), deploy / "up.sh")
    (deploy / "IMAGE").write_text(NEW)
    return deploy, data


@pytest.mark.parametrize("passed", [{}, {"TIMEZONE": "", "OWNER_TIMEZONE": ""}])
def test_launcher_timezone_comes_from_its_own_default_not_from_the_shell(launcher_home, passed):
    deploy, data = launcher_home

    zones = _launcher_timezone(deploy, data, **passed)

    # Контур никогда не поднимается без часового пояса, и оба значения
    # приходят из одного постоянного default этой установки.
    assert zones["TIMEZONE"] and zones["OWNER_TIMEZONE"] == zones["TIMEZONE"]


@pytest.mark.parametrize("owner", [NOVOSIBIRSK, MOSCOW])
def test_launcher_uses_the_exact_pair_the_updater_preserved(launcher_home, owner):
    deploy, data = launcher_home

    zones = _launcher_timezone(deploy, data, TIMEZONE=NOVOSIBIRSK, OWNER_TIMEZONE=owner)

    assert zones == {"TIMEZONE": NOVOSIBIRSK, "OWNER_TIMEZONE": owner}


def test_update_records_both_container_timezones_before_any_mutation(updater):
    _contour_timezone(updater, NOVOSIBIRSK)
    _installed_launcher(updater.home, default=NOVOSIBIRSK)

    updater.update("registry.example/korra:latest", dry_run=True)

    assert updater.receipt["old_runtime"]["TIMEZONE"] == NOVOSIBIRSK
    assert updater.receipt["old_runtime"]["OWNER_TIMEZONE"] == NOVOSIBIRSK
    assert updater.receipt["timezone_plan"] == {
        "TIMEZONE": {"expected": NOVOSIBIRSK, "source": "preserved"},
        "OWNER_TIMEZONE": {"expected": NOVOSIBIRSK, "source": "preserved"}}


def test_update_preserves_a_divergent_owner_timezone(updater):
    _contour_timezone(updater, NOVOSIBIRSK, owner=MOSCOW)
    _installed_launcher(updater.home, default=NOVOSIBIRSK)

    updater.update("registry.example/korra:latest")

    assert updater.receipt["status"] == "succeeded"
    assert updater.receipt["old_runtime"]["TIMEZONE"] == NOVOSIBIRSK
    assert updater.receipt["old_runtime"]["OWNER_TIMEZONE"] == MOSCOW


@pytest.mark.parametrize("dry_run", [False, True])
def test_launcher_that_would_move_the_clock_refuses_before_pull_or_drain(updater, dry_run):
    _contour_timezone(updater, NOVOSIBIRSK)
    _installed_launcher(updater.home, pinned=MOSCOW)
    before = u.tree_manifest(updater.data)

    with pytest.raises(u.UpdateError, match="Europe/Moscow"):
        updater.update("registry.example/korra:latest", dry_run=dry_run)

    assert updater.receipt["error_code"] == "launcher_timezone_mismatch"
    assert updater.receipt["status"] == "failed"
    assert u.tree_manifest(updater.data) == before
    assert not any(call[0] in {"pull", "native", "stop", "start_image"} for call in updater.calls)


@pytest.mark.parametrize("key", ["TIMEZONE", "OWNER_TIMEZONE"])
def test_update_refuses_a_timezone_left_over_in_the_operator_shell(updater, monkeypatch, key):
    _contour_timezone(updater, NOVOSIBIRSK)
    _installed_launcher(updater.home, default=NOVOSIBIRSK)
    monkeypatch.setenv(key, MOSCOW)

    with pytest.raises(u.UpdateError, match="launcher setting"):
        updater.update("registry.example/korra:latest")

    assert not any(call[0] in {"pull", "native", "stop", "start_image"} for call in updater.calls)


def test_update_accepts_a_shell_that_agrees_with_the_contour(updater, monkeypatch):
    _contour_timezone(updater, NOVOSIBIRSK)
    _installed_launcher(updater.home, default=NOVOSIBIRSK)
    monkeypatch.setenv("TIMEZONE", NOVOSIBIRSK)

    updater.update("registry.example/korra:latest")

    assert updater.receipt["status"] == "succeeded"


@pytest.mark.parametrize("phase,rollback", [("recreate", False), ("rollback_recreate", True)])
def test_native_launcher_receives_the_preserved_timezone_pair(updater, monkeypatch, phase, rollback):
    updater.receipt.update(phase=phase, old_resources={"nano_cpus": 0, "memory_bytes": 0},
                           old_runtime={**LEGACY_RUNTIME, "TIMEZONE": NOVOSIBIRSK,
                                        "OWNER_TIMEZONE": MOSCOW})
    if rollback:
        # Откат возвращает зафиксированное, чем бы ни была занята оболочка.
        monkeypatch.setenv("TIMEZONE", "Europe/Kaliningrad")
    launches = []
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **kw:
                        launches.append(kw["env"]) or types.SimpleNamespace(returncode=0, stdout=""))

    u.Updater.start_image(updater, OLD)

    assert launches[0]["TIMEZONE"] == NOVOSIBIRSK
    assert launches[0]["OWNER_TIMEZONE"] == MOSCOW


def test_receipt_without_timezone_hands_the_launcher_its_own_default(updater, monkeypatch):
    updater.receipt.update(phase="rollback_recreate", old_runtime=dict(LEGACY_RUNTIME),
                           old_resources={"nano_cpus": 0, "memory_bytes": 0})
    monkeypatch.setenv("TIMEZONE", MOSCOW)
    launches = []
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **kw:
                        launches.append(kw["env"]) or types.SimpleNamespace(returncode=0, stdout=""))

    u.Updater.start_image(updater, OLD)

    # Старая квитанция ничего не обещает: решает постоянный default launcher,
    # а не переменная, случайно оставшаяся в окружении оператора.
    assert launches[0]["TIMEZONE"] == "" and launches[0]["OWNER_TIMEZONE"] == ""
    assert updater.timezone_plan(rollback=True) == {
        "TIMEZONE": {"expected": "", "source": "launcher_default"},
        "OWNER_TIMEZONE": {"expected": "", "source": "launcher_default"}}


def test_rollback_of_a_receipt_without_timezone_still_completes(updater):
    updater.update("registry.example/korra:latest")
    for key in ("TIMEZONE", "OWNER_TIMEZONE"):
        updater.receipt["old_runtime"].pop(key)

    updater.rollback()

    assert updater.receipt["status"] == "rolled_back"
    assert updater.image == OLD and updater.running


def test_rollback_refuses_a_launcher_that_drifted_after_the_update(updater):
    _contour_timezone(updater, NOVOSIBIRSK)
    _installed_launcher(updater.home, default=NOVOSIBIRSK)
    updater.update("registry.example/korra:latest")
    _installed_launcher(updater.home, pinned=MOSCOW)

    with pytest.raises(u.UpdateError, match="Europe/Moscow"):
        updater.rollback()

    # Исправный новый контур не остановлен ради отката, который вернул бы
    # старый образ с чужими часами.
    assert updater.receipt["error_code"] == "launcher_timezone_mismatch"
    assert updater.receipt["status"] != "rolled_back"
    assert updater.image == NEW and updater.running


def test_smoke_refuses_a_contour_that_came_up_in_another_timezone(real_smoke_context):
    updater, clock, models, _ = real_smoke_context
    updater.receipt["old_runtime"] = {**LEGACY_RUNTIME, "TIMEZONE": NOVOSIBIRSK,
                                      "OWNER_TIMEZONE": NOVOSIBIRSK}
    _contour_timezone(updater, MOSCOW)

    with pytest.raises(u.UpdateError, match="timezone differs"):
        u.Updater.smoke(updater, OLD)

    assert updater.receipt["error_code"] == "runtime_timezone_drift"
    assert updater.receipt["active_timezone"] == {"TIMEZONE": MOSCOW, "OWNER_TIMEZONE": MOSCOW}
    assert clock[0] == 0 and models == []


def test_smoke_records_the_timezone_the_contour_actually_runs_on(real_smoke_context):
    updater, _, models, _ = real_smoke_context
    updater.receipt["old_runtime"] = {**LEGACY_RUNTIME, "TIMEZONE": NOVOSIBIRSK,
                                      "OWNER_TIMEZONE": NOVOSIBIRSK}
    _contour_timezone(updater, NOVOSIBIRSK)

    u.Updater.smoke(updater, OLD)

    assert updater.receipt["active_timezone"] == {"TIMEZONE": NOVOSIBIRSK,
                                                 "OWNER_TIMEZONE": NOVOSIBIRSK}
    assert models


@pytest.mark.parametrize("environment,expected", [
    ([], ("", "")),
    (["KORRA_TIMEZONE=Asia/Novosibirsk"], ("Asia/Novosibirsk", "")),
    (["HERMES_TIMEZONE=Asia/Novosibirsk"], ("Asia/Novosibirsk", "")),
    (["KORRA_TIMEZONE=", "HERMES_TIMEZONE=Asia/Novosibirsk"], ("Asia/Novosibirsk", "")),
    (["KORRA_TIMEZONE= Asia/Novosibirsk "], ("Asia/Novosibirsk", "")),
    (["KORRA_TIMEZONE=Asia/Novosibirsk", "KORRA_OWNER_TIMEZONE=Europe/Moscow"],
     ("Asia/Novosibirsk", "Europe/Moscow")),
])
def test_recorded_timezone_follows_the_environment_the_engine_reads(environment, expected):
    result = u.runtime_env_from_info({"Config": {"Env": environment}})
    assert (result["TIMEZONE"], result["OWNER_TIMEZONE"]) == expected


@pytest.mark.parametrize("value", ["Europe/Moscow extra", "Europe/Moscow\nKORRA_UID=0",
                                   "../../etc/localtime", "A" * 65])
def test_unusable_container_timezone_is_refused_rather_than_guessed(value):
    with pytest.raises(u.UpdateError, match="timezone"):
        u.validate_runtime_env({**LEGACY_RUNTIME, "TIMEZONE": value, "OWNER_TIMEZONE": ""})


@pytest.mark.parametrize("swap", [-1, 0, 4 * 1024**3])
def test_memory_swap_is_preserved_on_update_and_rollback(updater, monkeypatch, swap):
    updater.receipt["old_resources"] = {"nano_cpus": 0, "memory_bytes": 3 * 1024**3,
                                        "memory_swap_bytes": swap}
    monkeypatch.delenv("CONTAINER_MEMORY_SWAP", raising=False)
    assert updater.launch_resource_env()["CONTAINER_MEMORY_SWAP"] == str(swap)
    monkeypatch.setenv("CONTAINER_MEMORY_SWAP", str(5 * 1024**3))
    assert updater.launch_resource_env()["CONTAINER_MEMORY_SWAP"] == str(5 * 1024**3)
    assert updater.launch_resource_env(rollback=True)["CONTAINER_MEMORY_SWAP"] == str(swap)


def test_launcher_passes_explicit_swap_budget(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    u.shutil.copy2(SOURCE.with_name("up.sh"), deploy / "up.sh")
    (deploy / "IMAGE").write_text(NEW)
    env = {**os.environ, "DATA": str(data), "ENGINE_UID": str(os.getuid()),
           "ENGINE_GID": str(os.getgid()), "CONTAINER_MEMORY": "3g",
           "CONTAINER_MEMORY_SWAP": "3584m"}
    result = subprocess.run(["bash", str(deploy / "up.sh"), "--dry-run"], env=env,
                            text=True, capture_output=True, check=True)
    args = shlex.split(next(line for line in result.stdout.splitlines() if line.startswith("docker run ")))
    assert args[args.index("--memory") + 1] == "3g"
    assert args[args.index("--memory-swap") + 1] == "3584m"


def test_new_ram_budget_does_not_inherit_incompatible_old_swap(updater, monkeypatch):
    updater.receipt["old_resources"] = {"nano_cpus": 0, "memory_bytes": 1024**3,
                                        "memory_swap_bytes": 2 * 1024**3}
    monkeypatch.setenv("CONTAINER_MEMORY", "4g")
    monkeypatch.delenv("CONTAINER_MEMORY_SWAP", raising=False)
    assert updater.launch_resource_env()["CONTAINER_MEMORY_SWAP"] == "0"
    assert updater.launch_resource_env(rollback=True)["CONTAINER_MEMORY_SWAP"] == str(2 * 1024**3)
