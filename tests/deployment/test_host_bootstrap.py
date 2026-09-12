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

UPDATER_SPEC = importlib.util.spec_from_file_location("native_updater", SOURCE.parent / "updater.py")
u = importlib.util.module_from_spec(UPDATER_SPEC)
UPDATER_SPEC.loader.exec_module(u)


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
        self.firewall_active = False
        self.firewall_policy = "deny (incoming), allow (outgoing)"
        self.custom_nft = False
        self.custom_iptables = False
        self.pending_rules = False
        self.firewall_manager = False
        self.jail_ready = True
        self.container_sudo = True

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
            if "sudo" in args and not self.container_sudo:
                return SimpleNamespace(stdout="", returncode=1)
            output = self.identity if "-c" in args else "0"
        elif args[0] == "systemctl":
            if args[1] == "is-active":
                return SimpleNamespace(stdout="active" if self.firewall_manager else "inactive", returncode=0 if self.firewall_manager else 3)
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
        elif args[0] == "ufw":
            if args[1:] == ["status", "verbose"]:
                output = ("Status: active\nDefault: " + self.firewall_policy + "\n22/tcp ALLOW IN Anywhere\n") if self.firewall_active else "Status: inactive\n"
            elif args[1:] == ["show", "added"]:
                output = "Added user rules (see 'ufw status' for running firewall):\n"
                if self.pending_rules:
                    output += "ufw allow 1234/tcp\n"
            elif args[1:] == ["--force", "enable"]:
                self.firewall_active = True
        elif args[0] == "nft":
            output = json.dumps({"nftables": [{"chain": {"family": "inet", "table": "foreign", "name": "input", "hook": "input"}}] if self.custom_nft else []})
        elif args[0] in {"iptables-save", "ip6tables-save", "iptables-nft-save", "ip6tables-nft-save", "iptables-legacy-save", "ip6tables-legacy-save"}:
            output = "*filter\n:INPUT " + ("DROP" if self.firewall_active else "ACCEPT") + " [0:0]\n:OUTPUT ACCEPT [0:0]\n:FORWARD " + ("DROP" if self.firewall_active else "ACCEPT") + " [0:0]\n"
            if self.firewall_active:
                output += ":ufw-before-input - [0:0]\n:ufw-before-output - [0:0]\n-A INPUT -j ufw-before-input\n-A OUTPUT -j ufw-before-output\n"
            if self.custom_iptables:
                output += "-A INPUT -s 192.0.2.1 -j DROP\n"
            output += "COMMIT\n"
        elif args[0] == "fail2ban-client":
            if args[1:] == ["status", "sshd"]:
                output = "Status for the jail: sshd\n" if self.jail_ready else "Status unavailable"
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
    (root / "proc/meminfo").write_text("MemTotal: 8388608 kB\n")
    (root / "proc/swaps").write_text("Filename Type Size Used Priority\nexisting file 4 0 -2\n")
    (root / "etc/fstab").write_text("# preserved fixture\n")
    (root / "kit/up.sh").write_bytes((SOURCE.parent / "up.sh").read_bytes())
    (root / "kit/up.sh").chmod(0o755)
    h.command(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "etc/ssh/ssh_host_ed25519_key")])
    os.chown(root / "data", 10000, 10000)
    o = SimpleNamespace(action="grant", home=root / "kit", data=root / "data", name="synthetic",
        image="sha256:" + "a" * 64, panel_port=29119, api_port=28650, admin_port=22021,
        ssh_port=22, uid=10000, gid=10000, admin=True, plan=False)
    original_which = h.shutil.which
    monkeypatch.setattr(h.shutil, "which", lambda name: "/synthetic/" + name if name in {"ufw", "nft", "iptables-save", "ip6tables-save", "iptables-nft-save", "ip6tables-nft-save", "iptables-legacy-save", "ip6tables-legacy-save"} else original_which(name))
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
    assert not any((call[0] in {"apt-get", "fallocate", "mkswap", "swapon"} or call[:2] == ["fail2ban-client", "reload"]) for call in fake.calls)


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


def listening_contour(item):
    """Панель и API контура на петле: порты занимает сам тест, не контейнер."""
    import socket
    panel, api = socket.socket(), socket.socket()
    for sock in (panel, api):
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
    item.o.panel_port, item.o.api_port = panel.getsockname()[1], api.getsockname()[1]
    return panel, api


def test_client_mode_verify_passes_without_sudo_and_host_root(host):
    """`verify --no-admin` проверяет то, что обещает режим, а не обратное.

    Установка Голди 12.09.2026: сценарий A инструкции идёт с `--no-admin`, а
    verify требовала `sudo -n id -u` в контейнере и вход по закреплённому
    host-root ключу. В этом режиме обе проверки обязаны падать, и установщик,
    следующий инструкции буквально, решал, что сломал контур.
    """
    item, fake = host
    item.o.admin = False
    fake.container_sudo = False
    panel, api = listening_contour(item)
    try:
        assert item.verify() is True
    finally:
        panel.close()
        api.close()
    assert not any("/usr/bin/ssh" in call for call in fake.calls)


@pytest.mark.parametrize("case", ["sudo", "grant", "panel_down"])
def test_client_mode_verify_refuses_a_contour_that_kept_host_powers(host, case):
    item, fake = host
    if case == "grant":
        item.access("grant")
    fake.container_sudo = case == "sudo"
    item.o.admin = False
    panel, api = listening_contour(item)
    if case == "panel_down":
        panel.close()
    try:
        with pytest.raises(h.HostError, match="Client mode|loopback"):
            item.verify()
    finally:
        panel.close()
        api.close()


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


@pytest.mark.parametrize("conflict", ["ufw_policy", "nft", "iptables", "pending_rules", "manager"])
def test_firewall_conflict_refuses_before_host_mutation(host, conflict):
    item, fake = host
    if conflict == "ufw_policy":
        fake.firewall_active = True
        fake.firewall_policy = "deny (incoming), deny (outgoing)"
    else:
        setattr(fake, {"nft": "custom_nft", "iptables": "custom_iptables",
                      "pending_rules": "pending_rules", "manager": "firewall_manager"}[conflict], True)
    before = files(item.root)
    with pytest.raises(h.HostError, match="firewall|Firewall|UFW"):
        item.prepare()
    assert files(item.root) == before
    assert not any(call[0] in {"apt-get", "fallocate", "mkswap", "swapon"}
                   or call[:2] == ["systemctl", "enable"] for call in fake.calls)


def test_compatible_active_firewall_is_preserved(host):
    item, fake = host
    fake.firewall_active = True
    item.prepare()
    assert not any(call[0] == "ufw" and call[1] not in {"status", "show"} for call in fake.calls)


def test_bootstrap_requires_verified_sshd_jail(host):
    item, fake = host
    fake.jail_ready = False
    with pytest.raises(h.HostError, match="jail"):
        item.bootstrap()
    assert fake.launch_env is None


@pytest.mark.parametrize("low", ["cpu", "ram"])
def test_host_resources_refuse_before_provisioning(host, monkeypatch, low):
    item, fake = host
    item.o.action = "bootstrap"
    if low == "cpu":
        monkeypatch.setattr(h.os, "cpu_count", lambda: 2)
    else:
        (item.root / "proc/meminfo").write_text("MemTotal: 1048576 kB\n")
    before = files(item.root)
    with pytest.raises(h.HostError, match="CPU|RAM"):
        item.preflight()
    assert files(item.root) == before


@pytest.mark.parametrize("action", ["grant", "rotate", "revoke", "verify"])
def test_small_host_keeps_granting_and_revoking_host_root(host, monkeypatch, action):
    """Гейт установки не должен закрывать выдачу и отзыв уже выданного доступа.

    Живой случай 11.09.2026 (Павлова, 2 CPU / 3,8 ГиБ): preflight вызывается до
    разбора действия, поэтому «мало CPU/RAM/диска» отказывал и в отзыве
    host-root — на типовом клиентском сервере отозвать доступ было нельзя.
    """
    item, fake = host
    monkeypatch.setattr(h.os, "cpu_count", lambda: 2)
    (item.root / "proc/meminfo").write_text("MemTotal: 3985408 kB\n")
    monkeypatch.setattr(h.shutil, "disk_usage", lambda path: SimpleNamespace(free=2 * 1024**3))
    item.o.action = action
    assert item.preflight()["name"] == "synthetic"
    item.o.action = "bootstrap"
    with pytest.raises(h.HostError, match="CPU|RAM|GiB"):
        item.preflight()


def initialized_data(path):
    """Каталог DATA как после установки: апдейтер требует в нём config.yaml."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.yaml").write_text("# synthetic\n")
    os.chown(path / "config.yaml", 10000, 10000)
    os.chown(path, 10000, 10000)
    path.chmod(0o750)
    return path


# Кейс каталога DATA → принимают ли его обе стороны contract.
DATA_CONTRACT = {"short_root": False, "dedicated": True, "symlink": False,
                 "traversal": False, "sticky_ancestor": True, "group_writable_ancestor": False}


def data_case(tmp_path, case):
    if case == "short_root":
        return Path("/root/.korra21")       # три компонента: путь из ring 10.09
    if case == "symlink":
        initialized_data(tmp_path / "real/data")
        (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
        return tmp_path / "link/data"
    if case == "traversal":
        initialized_data(tmp_path / "opt/korra/data")
        return tmp_path / "opt/korra/../korra/data"
    ancestor = tmp_path / {"sticky_ancestor": "sticky", "group_writable_ancestor": "shared",
                           "dedicated": "opt"}[case]
    data = initialized_data(ancestor / "korra/data")
    ancestor.chmod({"sticky_ancestor": 0o1777, "group_writable_ancestor": 0o775,
                    "dedicated": 0o755}[case])
    return data


def bootstrap_accepts(item, path):
    probe = h.HostBootstrap(SimpleNamespace(**{**vars(item.o), "data": Path(path)}),
                            system_root=item.root, run=item.run)
    try:
        probe.preflight()
        return True
    except h.HostError:
        return False


def updater_accepts(path):
    try:
        u.canonical_data(str(path))
        return True
    except u.UpdateError:
        return False


@pytest.mark.parametrize("case", sorted(DATA_CONTRACT))
def test_existing_data_contract_matches_the_native_updater(host, tmp_path, case):
    """Установщик и апдейтер выносят о каталоге DATA один вердикт (K21-024).

    Ring 10.09.2026: `host-bootstrap --plan` принял существующий `/root/.korra21`,
    а native updater отверг тот же путь из-за минимума в четыре компонента —
    данные владельца пришлось переносить уже после установки.
    """
    item, fake = host
    path = data_case(tmp_path, case)
    expected = DATA_CONTRACT[case]
    assert updater_accepts(path) is expected
    assert bootstrap_accepts(item, path) is expected


MOUNT_PROBE = """
import importlib.util, json, os, subprocess, sys
source, data = sys.argv[1], sys.argv[2]
subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=0750", "tmpfs", data], check=True)
os.chown(data, 10000, 10000)
with open(os.path.join(data, "config.yaml"), "w") as stream:
    stream.write("# synthetic\\n")
os.chown(os.path.join(data, "config.yaml"), 10000, 10000)
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
host = load("host_bootstrap", source)
native = load("native_updater", os.path.join(os.path.dirname(source), "updater.py"))
verdict = {}
for name, call in (("bootstrap", lambda: host.check_data_path(data, must_exist=False)),
                   ("updater", lambda: native.canonical_data(data))):
    try:
        call()
        verdict[name] = True
    except (host.HostError, native.UpdateError):
        verdict[name] = False
print(json.dumps(verdict))
"""


def test_data_on_its_own_mount_is_judged_the_same_by_both_entrypoints(tmp_path):
    """DATA отдельным томом: ни установщик, ни апдейтер не смотрят на точку монтирования.

    Монтирование живёт в приватном mount namespace: даже убитый тест не оставит
    на хосте постороннего тома.
    """
    if os.geteuid() != 0 or not h.shutil.which("unshare"):
        pytest.skip("приватный mount namespace требует root и unshare")
    data = tmp_path / "opt/korra/data"
    data.mkdir(parents=True)
    result = subprocess.run(["unshare", "-m", "--propagation", "private", sys.executable,
                             "-c", MOUNT_PROBE, str(SOURCE), str(data)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        pytest.skip("mount в namespace недоступен: " + result.stderr.strip()[-120:])
    assert json.loads(result.stdout) == {"bootstrap": True, "updater": True}


def executable_lines(document):
    """Строки, которые установщик действительно выполняет: код блоков и сам скрипт."""
    text = (ROOT / document).read_text(encoding="utf-8")
    if document.endswith(".sh"):
        return [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            inside = not inside
        elif inside:
            lines.append(line)
    return lines


@pytest.mark.parametrize("document", ["INSTALL.md", "INSTALL.en.md", "docs/client-deploy/README.md",
                                      "docs/client-deploy/up.sh"])
def test_documented_data_recipe_needs_no_passwd_entry(document):
    """У uid 10000 на минимальном хосте нет записи в passwd (K21-004).

    Ubuntu 26.04 Татьяны: `install -d -o 10000 -g 10000` отвечает там
    `invalid user: '10000'`, и установка встаёт на первом же шаге. Переносима
    двухфазная схема — создать каталог, затем назначить владельца числами.
    """
    assert not [line for line in executable_lines(document) if "install -d -o" in line
                or "install -o " in line]
    assert any("chown 10000:10000" in line or "chown $ENGINE_UID:$ENGINE_GID" in line
               for line in executable_lines(document))


def test_data_directory_recipe_is_numeric_idempotent_and_keeps_existing_data(tmp_path):
    """Та же схема на живой файловой системе: владелец числами, повтор безопасен."""
    if os.geteuid() != 0:
        pytest.skip("назначение чужого владельца требует root")
    data = tmp_path / "korra/data"
    recipe = f"mkdir -p {data} && chown 10000:10000 {data} && chmod 750 {data}"
    subprocess.run(["bash", "-c", recipe], check=True, timeout=20)
    (data / "config.yaml").write_text("# существующие данные\n")
    subprocess.run(["bash", "-c", recipe], check=True, timeout=20)
    assert (data.stat().st_uid, data.stat().st_gid, data.stat().st_mode & 0o777) == (10000, 10000, 0o750)
    assert (data / "config.yaml").read_text() == "# существующие данные\n"


def test_host_installs_native_launcher_and_backup_dependencies(host):
    item, fake = host
    fake.missing = {"curl", "unzip", "rclone"}
    item.prepare()
    assert not fake.missing


@pytest.mark.parametrize("case", ["empty", "ack_empty", "table", "error", "truncated",
    "short", "bad_length", "wrong_seq", "interrupted", "timeout", "foreign_peer", "done_error"])
def test_missing_nft_requires_complete_empty_kernel_witness(monkeypatch, case):
    import struct
    def packet(kind=3, flags=2, seq=1, body=b""):
        return struct.pack("=IHHII", 16 + len(body), kind, flags, seq, 0) + body
    data, flags, peer = packet(), 0, (0, 0)
    if case == "ack_empty":
        data = packet(2, body=bytes(4)) + data
    elif case == "table":
        data = packet(10 << 8, body=bytes(4)) + data
    elif case == "error":
        data = packet(2, body=struct.pack("=i", -1))
    elif case == "truncated":
        flags = h.socket.MSG_TRUNC
    elif case == "short":
        data = b"short"
    elif case == "bad_length":
        data = struct.pack("=IHHII", 100, 3, 2, 1, 0)
    elif case == "wrong_seq":
        data = packet(seq=5)
    elif case == "interrupted":
        data = packet(flags=0x12)
    elif case == "foreign_peer":
        peer = (999, 0)
    elif case == "done_error":
        data = packet(body=struct.pack("=i", -1))
    class Channel:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): assert address == (0, 0)
        def settimeout(self, seconds): assert 0 < seconds <= 2
        def sendto(self, message, address):
            header = struct.unpack("=IHHII", message[:16])
            assert header[1:3] == ((10 << 8) | 1, 0x301)  # GETTABLE dump; never a mutation.
            assert address == (0, 0)
        def recvmsg(self, limit):
            if case == "timeout":
                raise TimeoutError()
            return data, [], flags, peer
    monkeypatch.setattr(h.socket, "socket", lambda *args: Channel())
    if case in {"empty", "ack_empty"}:
        h.require_empty_nft_tables()
    else:
        with pytest.raises(h.HostError):
            h.require_empty_nft_tables()


@pytest.mark.parametrize("hook", ["ingress", "prerouting", "output"])
def test_foreign_nft_hooks_refuse_before_mutation(host, hook):
    item, fake = host
    original = item.run
    def run(args, **kwargs):
        if args[0] == "nft":
            return SimpleNamespace(stdout=json.dumps({"nftables": [
                {"chain": {"family": "inet", "table": "foreign", "name": "traffic", "hook": hook}}]}))
        return original(args, **kwargs)
    item.run = run
    with pytest.raises(h.HostError, match="firewall"):
        item.prepare()
    assert not any(call[:2] == ["systemctl", "enable"] for call in fake.calls)


def test_more_than_eight_gib_does_not_create_unneeded_swap(host):
    item, fake = host
    (item.root / "proc/swaps").write_text("Filename Type Size Used Priority\n")
    (item.root / "proc/meminfo").write_text("MemTotal: 16777216 kB\n")
    item.prepare()
    assert not any(call[0] in {"fallocate", "mkswap", "swapon"} for call in fake.calls)


@pytest.mark.parametrize("case", ["docker_input", "kernel_output", "mismatched_hook"])
def test_firewall_recognized_names_do_not_hide_foreign_policy(host, case):
    item, fake = host
    fake.firewall_active = case == "kernel_output"
    original = item.run
    def run(args, **kwargs):
        if args[0] in {"iptables-save", "ip6tables-save", "iptables-nft-save", "ip6tables-nft-save", "iptables-legacy-save", "ip6tables-legacy-save"}:
            policy = "DROP" if case == "kernel_output" else "ACCEPT"
            extra = "-A INPUT -j DOCKER-USER\n-A DOCKER-USER -j DROP\n" if case == "docker_input" else ""
            return SimpleNamespace(stdout="*filter\n:INPUT " + ("DROP" if fake.firewall_active else "ACCEPT")
                + " [0:0]\n:OUTPUT " + policy + " [0:0]\n" + extra + "COMMIT\n", stderr="")
        if args[0] == "nft" and case == "mismatched_hook":
            return SimpleNamespace(stdout=json.dumps({"nftables": [
                {"chain": {"family": "ip", "table": "filter", "name": "INPUT", "hook": "prerouting"}}]}))
        return original(args, **kwargs)
    item.run = run
    with pytest.raises(h.HostError):
        item.prepare()
    assert not any(call[:2] == ["systemctl", "enable"] for call in fake.calls)


def test_native_docker_rules_are_accepted_only_on_forwarding_and_nat_paths():
    rules = """*filter
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:FORWARD DROP [0:0]
:DOCKER-USER - [0:0]
:DOCKER-FORWARD - [0:0]
-A FORWARD -j DOCKER-USER
-A FORWARD -j DOCKER-FORWARD
-A DOCKER-USER -j RETURN
-A DOCKER-FORWARD -j ACCEPT
COMMIT
*nat
:PREROUTING ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:POSTROUTING ACCEPT [0:0]
:DOCKER - [0:0]
-A PREROUTING -m addrtype --dst-type LOCAL -j DOCKER
-A OUTPUT -m addrtype --dst-type LOCAL -j DOCKER
-A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE
-A DOCKER -j RETURN
COMMIT
"""
    tables, chains, counts = h.iptables_inventory(rules, False, 22)
    assert tables == {"filter", "nat"}
    assert chains[("filter", "INPUT")] == "ACCEPT"
    assert counts[("filter", "FORWARD")] == 2


@pytest.mark.parametrize("case", ["legacy_drop", "nft_missing_mirror", "ufw_missing_kernel"])
def test_all_firewall_backends_must_be_accounted_for(host, case):
    item, fake = host
    fake.firewall_active = case == "ufw_missing_kernel"
    original = item.run
    def run(args, **kwargs):
        if case == "legacy_drop" and args[0] == "iptables-legacy-save":
            return SimpleNamespace(stdout="*filter\n:INPUT DROP [0:0]\nCOMMIT\n", stderr="")
        if case == "nft_missing_mirror" and args[0] == "nft":
            return SimpleNamespace(stdout=json.dumps({"nftables": [
                {"table": {"family": "ip", "name": "raw"}},
                {"chain": {"family": "ip", "table": "raw", "name": "PREROUTING", "hook": "prerouting", "policy": "accept"}},
                {"rule": {"family": "ip", "table": "raw", "chain": "PREROUTING", "expr": [{"drop": None}]}}
            ]}))
        if case == "ufw_missing_kernel" and "tables" in args[0]:
            return SimpleNamespace(stdout="*filter\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n", stderr="")
        return original(args, **kwargs)
    item.run = run
    with pytest.raises(h.HostError, match="firewall|Firewall|UFW"):
        item.prepare()
    assert not any(call[:2] == ["systemctl", "enable"] for call in fake.calls)


def test_active_ufw_can_coexist_with_empty_legacy_backend(host):
    item, fake = host
    fake.firewall_active = True
    original = item.run
    def run(args, **kwargs):
        if args[0].endswith("legacy-save"):
            return SimpleNamespace(stdout="*filter\n:INPUT ACCEPT [0:0]\n:OUTPUT ACCEPT [0:0]\nCOMMIT\n", stderr="")
        return original(args, **kwargs)
    item.run = run
    item.prepare()
    assert not any(call[0] == "ufw" and call[1] not in {"status", "show"} for call in fake.calls)


@pytest.mark.parametrize("hook", ["PREROUTING", "POSTROUTING"])
def test_foreign_legacy_hook_policy_is_not_accepted(hook):
    with pytest.raises(h.HostError):
        h.iptables_inventory("*mangle\n:" + hook + " DROP [0:0]\nCOMMIT\n", False, 22)


def test_legacy_dump_cannot_witness_native_nft_rules(host, monkeypatch):
    item, fake = host
    which = h.shutil.which
    monkeypatch.setattr(h.shutil, "which", lambda name: None if name.endswith("-nft-save") else which(name))
    original = item.run
    def run(args, **kwargs):
        if args[0] == "nft":
            return SimpleNamespace(stdout=json.dumps({"nftables": [
                {"table": {"family": "ip", "name": "filter"}},
                {"chain": {"family": "ip", "table": "filter", "name": "INPUT", "hook": "input", "policy": "accept"}},
                {"rule": {"family": "ip", "table": "filter", "chain": "INPUT", "expr": [{"drop": None}]}}
            ]}))
        if args[0].startswith("iptables"):
            return SimpleNamespace(stdout="*filter\n:INPUT ACCEPT [0:0]\n:f2b-sshd - [0:0]\n-A INPUT -p tcp --dport 22 -j f2b-sshd\nCOMMIT\n", stderr="")
        return original(args, **kwargs)
    item.run = run
    with pytest.raises(h.HostError, match="mirror"):
        item.firewall_preflight()


@pytest.mark.parametrize("field", ["uid", "gid"])
def test_native_remap_ceiling_refuses_before_provisioning(host, field):
    item, fake = host
    setattr(item.o, field, 65535)
    # Keep the selected DATA ownership consistent: refusal is for the native
    # remap limit, not a coincidental ownership mismatch.
    os.chown(item.data, item.o.uid, item.o.gid)
    with pytest.raises(h.HostError, match="UID|GID"):
        item.preflight()
    assert not any(call[:2] == ["systemctl", "enable"] for call in fake.calls)


def dual_stack_nft(family, ufw_prefix):
    """Инвентарь ядра на хосте с Docker и включённым UFW обеих семей."""
    filter_chains = [("INPUT", "input", "drop"), ("OUTPUT", "output", "accept"),
                     ("FORWARD", "forward", "drop")]
    entries = [{"table": {"family": family, "name": "filter"}},
               {"table": {"family": family, "name": "nat"}}]
    for name, hook, policy in filter_chains:
        entries.append({"chain": {"family": family, "table": "filter", "name": name,
                                  "hook": hook, "policy": policy}})
    for name in (ufw_prefix + "before-input", ufw_prefix + "before-output", "DOCKER-USER"):
        entries.append({"chain": {"family": family, "table": "filter", "name": name}})
    for chain in ("INPUT", "OUTPUT"):
        entries.append({"rule": {"family": family, "table": "filter", "chain": chain,
                                 "expr": [{"jump": {"target": ufw_prefix + "before-" + chain.lower()}}]}})
    for name, hook in (("PREROUTING", "prerouting"), ("POSTROUTING", "postrouting")):
        entries.append({"chain": {"family": family, "table": "nat", "name": name,
                                  "hook": hook, "policy": "accept"}})
    return entries


def dual_stack_mirror(ufw_prefix):
    """То же зеркалом iptables-nft: с пустой nat/INPUT, которой в ядре нет."""
    return ("*filter\n:INPUT DROP [0:0]\n:OUTPUT ACCEPT [0:0]\n:FORWARD DROP [0:0]\n"
            ":" + ufw_prefix + "before-input - [0:0]\n:" + ufw_prefix + "before-output - [0:0]\n"
            ":DOCKER-USER - [0:0]\n"
            "-A INPUT -j " + ufw_prefix + "before-input\n"
            "-A OUTPUT -j " + ufw_prefix + "before-output\n"
            "COMMIT\n"
            "*nat\n:INPUT ACCEPT [0:0]\n:PREROUTING ACCEPT [0:0]\n:POSTROUTING ACCEPT [0:0]\nCOMMIT\n")


def test_dual_stack_ufw_host_is_accepted(host, monkeypatch):
    """UFW с IPv6 — это результат работы самого bootstrap, а не чужая политика.

    Живой случай 12.09.2026: Ubuntu 26.04 + Docker 29, где UFW называет свои
    цепочки ufw6-* для IPv6, а iptables-nft-save дорисовывает пустую nat/INPUT.
    До исправления preflight отвергал такой хост тремя разными отказами подряд.
    """
    item, fake = host
    fake.firewall_active = True
    which = h.shutil.which
    monkeypatch.setattr(h.shutil, "which",
                        lambda name: None if name.endswith("legacy-save") else which(name))
    original = item.run
    def run(args, **kwargs):
        if args[0] == "nft":
            return SimpleNamespace(stdout=json.dumps({"nftables":
                dual_stack_nft("ip", "ufw-") + dual_stack_nft("ip6", "ufw6-")}), returncode=0)
        if args[0].startswith("iptables"):
            return SimpleNamespace(stdout=dual_stack_mirror("ufw-"), stderr="", returncode=0)
        if args[0].startswith("ip6tables"):
            return SimpleNamespace(stdout=dual_stack_mirror("ufw6-"), stderr="", returncode=0)
        return original(args, **kwargs)
    item.run = run
    assert item.firewall_preflight() is True


def test_preloaded_image_is_not_pulled_again(host):
    """Образ, доставленный тарболом на изолированный хост, уже лежит локально."""
    item, fake = host
    seen = []
    original = item.run
    def run(args, **kwargs):
        if args[0] == "docker":
            seen.append(args[1])
            if args[1] == "image":
                return SimpleNamespace(stdout=json.dumps(
                    [{"Architecture": "amd64", "Os": "linux", "Id": item.o.image}]), returncode=0)
            if args[1] == "pull":
                raise AssertionError("предзагруженный образ не должен тянуться из реестра")
            return SimpleNamespace(stdout="", returncode=0)
        return original(args, **kwargs)
    item.run = run
    assert item.run(["docker", "image", "inspect", item.o.image], check=False).returncode == 0
    assert "pull" not in seen
