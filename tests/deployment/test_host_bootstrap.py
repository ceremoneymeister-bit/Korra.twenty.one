"""Operator CLI contract with synthetic homes and no live host mutations."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/client-deploy/host-bootstrap.py"


def test_operator_cli_plan_is_available_without_creating_data(tmp_path):
    target = tmp_path / "new-data"
    result = subprocess.run([sys.executable, str(SOURCE), "bootstrap", "--plan",
        "--home", str(tmp_path), "--data", str(target), "--name", "synthetic-k21-plan",
        "--image", "ghcr.io/ceremoneymeister-bit/korra.twenty.one@sha256:" + "a" * 64],
        env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert not target.exists()
    assert '"admin": true' in result.stdout

import importlib.util
import json
import hashlib
from types import SimpleNamespace
import pytest

SPEC = importlib.util.spec_from_file_location("host_bootstrap", SOURCE)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


class HostFixture:
    """Real keys/files; every host provisioning/service command stays synthetic."""
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.active = {}
        self.kill_mode = "control-group"
        self.identity = "a" * 32
        self.failed_stop = False
        self.container_present = False
        self.arch = "amd64"
        self.launch_env = None
        self.launch_output = ""
        self.missing = set()

    def __call__(self, args, **kwargs):
        args = list(map(str, args))
        self.calls.append(args)
        output = ""
        if args[0] == "ssh-keygen":
            return h.command(args, **kwargs)
        if args[:3] == ["passwd", "-S", "root"]:
            output = "root P synthetic"
        elif args[0] == "dpkg-query":
            output = "" if args[-1] in self.missing else "install ok installed"
        elif args[0] == "apt-get":
            if args[1] == "install":
                self.missing.difference_update(args[3:])
        elif args[:2] == ["docker", "ps"]:
            output = "synthetic-id" if self.container_present else ""
        elif args[:2] == ["docker", "pull"]:
            pass
        elif args[:3] == ["docker", "image", "inspect"]:
            output = json.dumps([{"Id": "sha256:" + "a" * 64, "Architecture": self.arch, "Os": "linux"}])
        elif args[0] == "bash":
            self.launch_env = kwargs["env"]
            result = h.command([*args, "--dry-run"], **kwargs)
            self.launch_output = result.stdout
            self.container_present = True
            return result
        elif args[:2] == ["docker", "exec"]:
            output = self.identity if "-c" in args else "0"
        elif args[0] == "systemctl":
            if args[1] == "stop":
                if self.failed_stop:
                    raise h.HostError("synthetic stop failure")
                self.active[args[2]] = False
            elif args[1:3] == ["enable", "--now"]:
                self.active[args[3]] = True
            elif args[1] == "show":
                output = self.kill_mode if "--property=KillMode" in args else (
                    "active" if self.active.get(args[2]) else "inactive")
            elif args[1] not in {"disable", "daemon-reload"}:
                raise AssertionError(args)
        elif args[0] == "/usr/sbin/sshd":
            assert args[1] == "-t"
            return h.command(args, **kwargs)
        elif args[0] in {"ufw", "fail2ban-client"}:
            pass
        elif args[0] == "fallocate":
            Path(args[-1]).write_bytes(b"synthetic swap")
        elif args[0] == "mkswap":
            pass
        elif args[0] == "swapon":
            (self.root / "proc/swaps").write_text("Filename Type Size Used Priority\n" + args[1] + " file 4 0 -2\n")
        else:
            raise AssertionError(f"Unexpected host command: {args[0]}")
        return SimpleNamespace(stdout=output, returncode=0)


@pytest.fixture
def host(tmp_path, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip("host ownership and root SSH acceptance requires isolated Linux root fixture")
    root = tmp_path / "system"
    for name in ("etc/ssh", "etc/systemd/system", "proc", "run", "var/lib", "kit", "data"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "etc/os-release").write_text('ID=ubuntu\n')
    (root / "proc/swaps").write_text("Filename Type Size Used Priority\nexisting file 4 0 -2\n")
    (root / "etc/fstab").write_text("# preserved fixture\n")
    (root / "kit/up.sh").write_bytes((SOURCE.parent / "up.sh").read_bytes())
    (root / "kit/up.sh").chmod(0o755)
    h.command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "etc/ssh/ssh_host_ed25519_key")])
    os.chown(root / "data", 10000, 10000)
    o = SimpleNamespace(action="grant", home=root / "kit", data=root / "data", name="synthetic",
        image="sha256:" + "a" * 64, panel_port=29119, api_port=28650, admin_port=22021,
        ssh_port=22, uid=10000, gid=10000, admin=True, plan=False)
    fake = HostFixture(root)
    item = h.HostBootstrap(o, system_root=root, run=fake)
    monkeypatch.setattr(item, "checked_container", lambda: {})
    return item, fake


def files(root):
    return {str(p.relative_to(root)): (p.stat().st_ino, p.stat().st_mode, p.stat().st_uid,
             hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob("*") if p.is_file()}


def test_grant_is_idempotent_and_preserves_unrelated_ssh_config(host):
    item, fake = host
    ssh = item.data / ".ssh"
    ssh.mkdir()
    original = b"Host unrelated\n  HostName example.invalid\n"
    (ssh / "config").write_bytes(original)
    first = item.access("grant")
    key = (ssh / "id_ed25519_host").read_bytes()
    second = item.access("grant")
    assert first == second
    assert hashlib.sha256((ssh / "id_ed25519_host").read_bytes()).digest() == hashlib.sha256(key).digest()
    assert (ssh / "config").read_bytes().endswith(original)
    assert (ssh / "config").read_bytes().count(b"Include ") == 1
    assert ssh.stat().st_mode & 0o777 == 0o700
    for name in ("id_ed25519_host", "config", "korra-host.conf", "korra-host-known_hosts"):
        info = (ssh / name).stat()
        assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (10000, 10000, 0o600)
    assert first["state"] == "active"
    assert sum(call[:2] == ["ssh-keygen", "-q"] for call in fake.calls) == 1
    assert not any(call[:2] == ["systemctl", "stop"] for call in fake.calls)


def test_distinct_contours_have_distinct_native_identity_and_root_keys(host, monkeypatch):
    item, fake = host
    first = item.access("grant")
    second_data = item.data.with_name("second-data")
    second_data.mkdir()
    os.chown(second_data, 10000, 10000)
    o = SimpleNamespace(**{**vars(item.o), "name": "second", "data": second_data, "admin_port": 22022})
    second = h.HostBootstrap(o, system_root=item.root, run=fake)
    monkeypatch.setattr(second, "checked_container", lambda: {})
    fake.identity = "b" * 32
    next_record = second.access("grant")
    assert first["install_id"] != next_record["install_id"]
    assert first["public_sha256"] != next_record["public_sha256"]


def test_copied_unregistered_private_key_is_not_adopted(host):
    item, fake = host
    ssh = item.data / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519_host").write_bytes(b"inert copied fixture")
    with pytest.raises(h.HostError, match="Unregistered/copied"):
        item.access("grant")
    assert not any(call[:3] == ["systemctl", "enable", "--now"] for call in fake.calls)
    assert not list(item.registry.glob("*/authorized_keys"))


@pytest.mark.parametrize("damage", ["host_pin", "private_key", "port", "identity"])
def test_registered_trust_drift_refuses_without_new_authorization(host, damage):
    item, fake = host
    record = item.access("grant")
    authorized = item.registry / record["install_id"] / "authorized_keys"
    before = authorized.read_bytes()
    if damage == "host_pin":
        key = item.root / "etc/ssh/ssh_host_ed25519_key"
        key.unlink()
        key.with_suffix(".pub").unlink()
        h.command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    elif damage == "private_key":
        key = item.data / ".ssh/id_ed25519_host"
        key.unlink()
        h.command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    elif damage == "port":
        item.o.admin_port += 1
    else:
        fake.identity = "b" * 32
    with pytest.raises(h.HostError):
        item.access("grant")
    assert authorized.read_bytes() == before


def test_rotate_revokes_old_key_before_minting_new_one(host):
    item, fake = host
    first = item.access("grant")
    fake.calls.clear()
    second = item.access("rotate")
    stop = next(i for i, call in enumerate(fake.calls) if call[:2] == ["systemctl", "stop"])
    mint = next(i for i, call in enumerate(fake.calls) if call[:2] == ["ssh-keygen", "-q"])
    assert stop < mint
    assert first["public_sha256"] != second["public_sha256"]
    assert second["state"] == "active"


def test_revoke_uses_root_inventory_when_container_and_data_are_unavailable(host, monkeypatch):
    item, fake = host
    record = item.access("grant")
    monkeypatch.setattr(item, "identity", lambda: pytest.fail("revocation depends on the agent"))
    item.data.rename(item.data.with_name("offline-data"))
    result = item.revoke()
    assert result["state"] == "revoked"
    assert not fake.active[item.unit(record["install_id"])]
    assert (item.registry / record["install_id"] / "authorized_keys").read_bytes() == b""
    assert item.revoke()["state"] == "revoked"


@pytest.mark.parametrize("failure", ["stop", "kill_mode"])
def test_incomplete_revoke_never_records_success(host, failure):
    item, fake = host
    record = item.access("grant")
    fake.failed_stop = failure == "stop"
    fake.kill_mode = "process" if failure == "kill_mode" else "control-group"
    with pytest.raises(h.HostError):
        item.revoke()
    saved = item.record(record["install_id"])
    assert saved["state"] == "revoking"
    assert (item.registry / record["install_id"] / "authorized_keys").read_bytes() == b""


def test_data_ssh_symlink_cannot_redirect_host_root_writes(host, tmp_path):
    item, fake = host
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "config").write_text("preserve")
    before = files(victim)
    (item.data / ".ssh").symlink_to(victim, target_is_directory=True)
    with pytest.raises((h.HostError, OSError)):
        item.access("grant")
    assert files(victim) == before
    assert not any(call[:3] == ["systemctl", "enable", "--now"] for call in fake.calls)


def test_prepare_reuses_existing_docker_swap_and_is_idempotent(host):
    item, fake = host
    item.prepare()
    before = files(item.root)
    fake.calls.clear()
    item.prepare()
    assert files(item.root) == before
    assert not any(call[0] in {"apt-get", "fallocate", "mkswap", "swapon", "fail2ban-client"} for call in fake.calls)


def test_prepare_managed_swap_persists_once(host):
    item, fake = host
    (item.root / "proc/swaps").write_text("Filename Type Size Used Priority\n")
    item.prepare()
    item.prepare()
    assert sum(call[0] == "mkswap" for call in fake.calls) == 1
    assert (item.root / "etc/fstab").read_text().count("swapfile none swap sw 0 0") == 1


@pytest.mark.parametrize("field,value", [("image", "latest"), ("api_port", 29119), ("uid", 0), ("name", "../bad")])
def test_bad_operator_plan_fails_before_any_write(host, field, value):
    item, fake = host
    setattr(item.o, field, value)
    before = files(item.root)
    with pytest.raises(h.HostError):
        item.preflight()
    assert files(item.root) == before
    assert not any(call[0] not in {"passwd"} for call in fake.calls)


def test_real_sshd_root_rotation_and_controlmaster_revocation(host, tmp_path):
    """Real OpenSSH; systemd boundary kills only this fixture's process tree.

    The production unit's cgroup kill policy is validated separately. No real
    system service, production authorized_keys or production inventory changes.
    """
    import contextlib
    import socket
    import time
    import psutil

    item, fake = host
    account = subprocess.check_output(["passwd", "-S", "root"], text=True).split()
    if account[1] not in {"P", "NP"}:
        pytest.skip("dedicated UsePAM=no fixture requires unlocked host root")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        item.o.admin_port = listener.getsockname()[1]
    processes = {}
    clients = []
    client_config = tmp_path / "client.conf"
    log = (tmp_path / "sshd-fixture.log").open("ab")

    def stop(unit):
        process = processes.pop(unit, None)
        if not process:
            return
        descendants = psutil.Process(process.pid).children(recursive=True)
        for child in reversed(descendants):
            with contextlib.suppress(psutil.NoSuchProcess):
                child.kill()
        process.terminate()
        process.wait(timeout=5)
        psutil.wait_procs(descendants, timeout=5)

    def ssh(*args, check=True):
        return subprocess.run(["/usr/bin/ssh", "-F", str(client_config), *args],
                              cwd=tmp_path, capture_output=True, text=True, timeout=10, check=check)

    def actual(args, **kw):
        args = list(map(str, args))
        if args[:3] == ["systemctl", "enable", "--now"] and args[3].startswith("korra-host-admin-"):
            unit = args[3]
            if unit not in processes:
                config = item.registry / fake.identity / "sshd_config"
                process = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
                                           stdout=log, stderr=log)
                processes[unit] = process
                deadline = time.monotonic() + 5
                while True:
                    assert process.poll() is None, "fixture sshd stopped"
                    try:
                        with socket.create_connection(("127.0.0.1", item.o.admin_port), timeout=0.1):
                            break
                    except OSError:
                        assert time.monotonic() < deadline
                        time.sleep(0.02)
        elif args[:2] == ["systemctl", "stop"]:
            stop(args[2])
        elif args[:2] == ["docker", "exec"] and "/usr/bin/ssh" in args:
            # Translate only the container's DATA path for the isolated host
            # client; relative ControlPath avoids AF_UNIX path-length limits.
            text = item.client_config().replace("/opt/data", str(item.data))
            text = text.replace(f"{item.data}/.ssh/cm-host-%C", "cm-%C")
            client_config.write_text(text)
            return ssh("-o", "ControlPath=none", "host", "id", "-u")
        return fake(args, **kw)

    item.run = actual
    try:
        first = item.access("grant")
        assert ssh("-o", "ControlPath=none", "host", "id", "-u").stdout.strip() == "0"
        old_key = tmp_path / "old-key"
        old_key.write_bytes((item.data / ".ssh/id_ed25519_host").read_bytes())
        old_key.chmod(0o600)
        master = subprocess.Popen(["/usr/bin/ssh", "-F", str(client_config),
            "-M", "-N", "-o", "ControlPersist=no", "host"], cwd=tmp_path, stdout=log, stderr=log)
        clients.append(master)
        deadline = time.monotonic() + 5
        while ssh("-O", "check", "host", check=False).returncode:
            assert master.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        authorized = item.registry / first["install_id"] / "authorized_keys"
        original = authorized.read_bytes()
        # Exact old runbook behavior: remove authorization but keep sshd alive.
        authorized.write_bytes(b"")
        assert ssh("host", "id", "-u").stdout.strip() == "0", "old ControlMaster should demonstrate the gap"
        authorized.write_bytes(original)
        active = subprocess.Popen(["/usr/bin/ssh", "-F", str(client_config),
            "-o", "ControlPath=none", "host", "printf ready; exec sleep 60"],
            cwd=tmp_path, stdout=subprocess.PIPE, stderr=log)
        clients.append(active)
        assert active.stdout.read(5) == b"ready"
        second = item.access("rotate")
        master.wait(timeout=5)
        active.wait(timeout=5)
        assert first["public_sha256"] != second["public_sha256"]
        assert ssh("-O", "check", "host", check=False).returncode != 0
        # A standalone client with only the old key cannot authenticate.
        denied = subprocess.run(["/usr/bin/ssh", "-F", "/dev/null", "-i", str(old_key),
            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "UserKnownHostsFile=" + str(item.data / ".ssh/korra-host-known_hosts"),
            "-p", str(item.o.admin_port), "root@127.0.0.1", "id", "-u"],
            capture_output=True, text=True, timeout=10)
        assert denied.returncode != 0 and not denied.stdout
        assert ssh("-o", "ControlPath=none", "host", "id", "-u").stdout.strip() == "0"
        item.revoke()
        assert ssh("-o", "ControlPath=none", "host", "id", "-u", check=False).returncode != 0
    finally:
        for unit in list(processes):
            stop(unit)
        for process in clients:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        log.close()


@pytest.mark.parametrize("file_name", ["authorized_keys", "sshd_config"])
def test_registered_operator_edits_require_explicit_rotation(host, file_name):
    item, fake = host
    value = item.access("grant")
    target = item.registry / value["install_id"] / file_name
    target.write_text("")
    with pytest.raises(h.HostError):
        item.access("grant")
    assert target.read_bytes() == b"", "bootstrap silently undid an operator trust edit"


@pytest.mark.parametrize("admin", [False, True])
def test_bootstrap_passes_explicit_admin_mode_to_actual_native_launcher(host, admin):
    item, fake = host
    item.o.admin = admin
    result = item.bootstrap()
    assert fake.launch_env["AGENT_SUDO"] == ("1" if admin else "0")
    assert "KORRA_AGENT_SUDO=" + ("1" if admin else "0") in fake.launch_output
    assert "docker.sock" not in fake.launch_output
    assert "--privileged" not in fake.launch_output
    if admin:
        assert result["state"] == "active"
    else:
        assert result["host_grant"] is False
        assert not (item.data / ".ssh").exists()


def test_no_admin_bootstrap_revokes_an_existing_managed_host_grant(host):
    item, fake = host
    first = item.access("grant")
    item.o.admin = False
    fake.container_present = True
    result = item.bootstrap()
    assert result["host_grant"] is False
    assert item.record(first["install_id"])["state"] == "revoked"


def test_wrong_image_arch_never_launches_native_container(host):
    item, fake = host
    fake.arch = "arm64"
    with pytest.raises(h.HostError, match="linux/amd64"):
        item.bootstrap()
    assert fake.launch_env is None
    assert not item.registry.exists()
    assert not (item.home / "IMAGE").exists()


def test_host_package_installation_is_idempotent_and_off_image(host, monkeypatch):
    item, fake = host
    original = h.shutil.which
    monkeypatch.setattr(h.shutil, "which", lambda name: None if name == "docker" else original(name))
    fake.missing = {"docker.io", "fail2ban"}
    item.prepare()
    item.prepare()
    assert [call for call in fake.calls if call[:2] == ["apt-get", "install"]] == [
        ["apt-get", "install", "-y", "fail2ban", "docker.io"]]
    assert not any(call[:2] in (["docker", "build"], ["docker", "exec"]) for call in fake.calls)
    allow = next(i for i, call in enumerate(fake.calls) if call[:2] == ["ufw", "allow"])
    deny = next(i for i, call in enumerate(fake.calls) if call[:3] == ["ufw", "default", "deny"])
    assert allow < deny


def test_plan_accepts_documented_control_home_without_writes(tmp_path):
    target = tmp_path / "uncreated-data"
    result = subprocess.run([sys.executable, str(SOURCE), "bootstrap", "--plan",
        "--home", "/opt/korra", "--data", str(target), "--name", "synthetic-plan",
        "--image", "sha256:" + "a" * 64], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not target.exists()
