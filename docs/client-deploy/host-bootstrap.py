#!/usr/bin/env python3
"""Operator Docker/OpenSSH/systemd bootstrap. --plan is read-only; no image build."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import shlex
import struct
import socket
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
IDENTITY = re.compile(r"[0-9a-f]{32}")
IMAGE = re.compile(r"(?:ghcr\.io/ceremoneymeister-bit/korra\.twenty\.one@)?sha256:[0-9a-f]{64}")
ID_CODE = """from korra_cli.install_identity import get_install_id
value=get_install_id()
if not value: raise RuntimeError('Installation identity unavailable')
print(value)
"""


class HostError(RuntimeError):
    pass


def command(args, *, timeout=120, check=True, env=None):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True,
                            timeout=timeout, env=env)
    if check and result.returncode:
        raise HostError(f"Operator command failed: {Path(args[0]).name}; no credentials logged")
    return result


def trusted(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise HostError("Control path must be root-owned, non-symlink and not writable by others")


def mkdir(path, mode=0o700):
    path.mkdir(parents=True, exist_ok=True)
    trusted(path)
    path.chmod(mode)


def publish(path, value, mode=0o600):
    trusted(path.parent)
    if path.is_symlink():
        raise HostError("Refusing control-file symlink")
    data = value.encode() if isinstance(value, str) else value
    if path.exists():
        trusted(path)
        if path.read_bytes() == data and stat.S_IMODE(path.stat().st_mode) == mode:
            return False
    fd, temp = tempfile.mkstemp(prefix=".korra-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        Path(temp).unlink(missing_ok=True)
    return True


@contextlib.contextmanager
def data_ssh(data, uid, gid):
    # Hold directory descriptors: agent-controlled .ssh symlink replacement
    # must never redirect host-root publication outside the selected DATA.
    rootfd = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir(".ssh", 0o700, dir_fd=rootfd)
        except FileExistsError:
            pass
        fd = os.open(".ssh", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
        try:
            os.fchmod(fd, 0o700)
            os.fchown(fd, uid, gid)
            yield fd
        finally:
            os.close(fd)
    finally:
        os.close(rootfd)


def read_at(fd, name):
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(child, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise HostError("Unsafe SSH file")
        return stream.read()


def write_at(fd, name, value, uid, gid, mode=0o600):
    data = value.encode() if isinstance(value, str) else value
    if read_at(fd, name) == data:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode):
            return
    temp = ".korra-host-" + os.urandom(12).hex()
    child = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=fd)
    try:
        with os.fdopen(child, "wb") as stream:
            os.fchown(stream.fileno(), uid, gid)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=fd)


def require_empty_nft_tables():
    """Presence-only kernel witness when nft is absent; never writes rules."""
    try:
        with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 12) as channel:
            channel.settimeout(2)
            channel.bind((0, 0))
            sequence = 1
            # NETLINK_NETFILTER / NFT_MSG_GETTABLE, REQUEST|ROOT|MATCH.
            request = struct.pack("=IHHII", 20, (10 << 8) | 1, 0x301, sequence, 0) + bytes(4)
            channel.sendto(request, (0, 0))
            for _ in range(64):
                data, _, flags, peer = channel.recvmsg(65536)
                if flags & socket.MSG_TRUNC or not data or peer[0] != 0:
                    raise HostError("Uncertain firewall inventory")
                offset = 0
                while offset < len(data):
                    if len(data) - offset < 16:
                        raise HostError("Malformed firewall inventory")
                    length, kind, message_flags, seq, _pid = struct.unpack_from("=IHHII", data, offset)
                    if length < 16 or offset + length > len(data) or seq != sequence or message_flags & 0x10:
                        raise HostError("Incomplete firewall inventory")
                    payload = data[offset + 16:offset + length]
                    if kind == 3:  # NLMSG_DONE: only a successful empty dump.
                        if (payload and (len(payload) < 4 or struct.unpack_from("=i", payload)[0] != 0)
                                or offset + ((length + 3) & ~3) != len(data)):
                            raise HostError("Incomplete firewall inventory")
                        return
                    if kind != 2 or len(payload) < 4 or struct.unpack_from("=i", payload)[0] != 0:
                        raise HostError("Existing/unknown nft firewall requires nft inspection")
                    offset += (length + 3) & ~3
            raise HostError("Firewall inventory exceeded its bound")
    except (OSError, ValueError, struct.error) as exc:
        raise HostError("Cannot verify existing nft firewall without nft") from exc


def iptables_inventory(text, active, ssh_port):
    """Recognize only existing UFW, fail2ban SSH, Docker forwarding/NAT."""
    tables, chains, counts = set(), {}, {}
    table = None
    linked_ufw = all(re.search(r"(?m)^-A " + chain + r" (?:.* )?-j ufw-", text)
                     for chain in ("INPUT", "OUTPUT"))
    for line in text.splitlines():
        if not line or line.startswith("#") or line == "COMMIT":
            continue
        if line.startswith("*"):
            table = line[1:]
            if table not in {"filter", "nat", "mangle", "raw", "security"}:
                raise HostError("Unknown firewall table")
            tables.add(table)
            continue
        if line.startswith(":"):
            parts = line[1:].split()
            if not table or len(parts) != 3:
                raise HostError("Malformed firewall chain inventory")
            chain, policy = parts[:2]
            chains[(table, chain)] = policy
            allowed_policies = {"ACCEPT"}
            if active and linked_ufw and table == "filter" and chain == "INPUT":
                allowed_policies.add("DROP")
            if table == "filter" and chain == "FORWARD" and (active or
                    re.search(r"(?m)^-A FORWARD -j DOCKER-(?:USER|FORWARD|INGRESS)(?:\s|$)", text)):
                allowed_policies.add("DROP")
            if chain in {"INPUT", "OUTPUT", "FORWARD", "PREROUTING", "POSTROUTING"} and policy not in allowed_policies:
                raise HostError("Existing firewall kernel policy conflicts with UFW/host defaults")
            continue
        words = shlex.split(line)
        if not table or len(words) < 4 or words[0] != "-A":
            raise HostError("Unrepresentable firewall rule")
        chain = words[1]
        counts[(table, chain)] = counts.get((table, chain), 0) + 1
        target = words[words.index("-j") + 1] if "-j" in words else ""
        allowed = active and table == "filter" and (
            chain.startswith("ufw-") or chain in {"INPUT", "OUTPUT", "FORWARD"} and target.startswith("ufw-"))
        if table == "filter":
            if chain.startswith("f2b-") and target in {"RETURN", "REJECT", "DROP"}:
                allowed = True
            if chain == "INPUT" and target == "f2b-sshd":
                port_arg = next((key for key in ("--dport", "--dports") if key in words), None)
                allowed = bool(port_arg and str(ssh_port) in words[words.index(port_arg) + 1].split(","))
            if chain == "FORWARD" and target in {"DOCKER-USER", "DOCKER-FORWARD", "DOCKER-INGRESS"}:
                allowed = True
            if chain.startswith("DOCKER") and (target in {"ACCEPT", "DROP", "RETURN"} or target.startswith("DOCKER")):
                allowed = True
        elif table == "nat":
            if chain in {"PREROUTING", "OUTPUT"} and target == "DOCKER" and "--dst-type" in words:
                allowed = words[words.index("--dst-type") + 1] == "LOCAL"
            if chain == "DOCKER" and target in {"DNAT", "RETURN"}:
                allowed = True
            if chain == "POSTROUTING" and target == "MASQUERADE" and "-s" in words and "-o" in words:
                index = words.index("-o")
                bridge = words[index + 1]
                allowed = words[index - 1] == "!" and (bridge == "docker0" or bridge.startswith("br-"))
        if not allowed:
            raise HostError("Existing firewall rule requires operator integration")
    return tables, chains, counts


class HostBootstrap:
    def __init__(self, options, *, system_root=Path("/"), run=command):
        self.o, self.root, self.run = options, Path(system_root), run
        self.home, self.data = Path(options.home), Path(options.data)
        self.registry = self.root / "etc/korra-host-admin"

    def preflight(self):
        o = self.o
        if not o.plan and os.geteuid() != 0:
            raise HostError("Run the operator CLI as host root")
        if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
            raise HostError("Supported host: Linux x86_64")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", o.name):
            raise HostError("Invalid contour name")
        for path in (self.home, self.data):
            if not path.is_absolute() or len(path.parts) < 3:
                raise HostError("Use dedicated absolute control/DATA paths")
        if not o.plan:
            trusted(self.home)
        if o.action == "revoke":
            # Root inventory must allow revocation even after DATA or the
            # container disappears/is compromised. No image/agent dependency.
            return {"name": o.name, "action": "revoke", "data": str(self.data)}
        values = {}
        for line in (self.root / "etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value.strip('"')
        if values.get("ID") not in {"ubuntu", "debian"}:
            raise HostError("Supported host: Ubuntu or Debian with systemd")
        if not o.plan and o.admin:
            # UsePAM=no keeps every session in the per-contour systemd cgroup.
            # Never silently change the host's global root account policy.
            account = self.run(["passwd", "-S", "root"]).stdout.split()
            if len(account) < 2 or account[1] not in {"P", "NP"}:
                raise HostError("Host root account is locked/unknown; configure it through the operator console before granting SSH")
        if not IMAGE.fullmatch(o.image or ""):
            raise HostError("Require immutable Korra image digest")
        ports = (o.panel_port, o.api_port, o.admin_port, o.ssh_port)
        if any(not 1 <= port <= 65535 for port in ports) or len(set(ports)) != len(ports):
            raise HostError("Panel/API/admin/operator SSH ports must be distinct and valid")
        if min(o.panel_port, o.api_port, o.admin_port) < 1024 or min(o.uid, o.gid) < 1:
            raise HostError("Invalid engine UID/GID or service ports")
        if any(path.resolve() != path for path in (self.home, self.data)):
            raise HostError("Refusing symlinked control/DATA paths")
        if self.home == self.data or self.home.is_relative_to(self.data):
            raise HostError("Control plane must be outside DATA")
        parent = self.data.parent
        while not parent.exists():
            parent = parent.parent
        if not o.plan:
            trusted(parent)
            if self.data.exists():
                info = self.data.lstat()
                if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (o.uid, o.gid):
                    raise HostError("Existing DATA has unexpected ownership")
        if shutil.disk_usage(parent).free < 24 * 1024**3:
            raise HostError("At least 24 GiB free is required before host provisioning")
        memory = self.memory_kib()
        if (os.cpu_count() or 0) < 4 or memory < 7 * 1024**2:
            raise HostError("Require at least 4 CPUs and an 8 GiB-class host (7 GiB reported RAM)")
        if not o.plan and o.action == "bootstrap":
            self.firewall_preflight()
        return {"name": o.name, "data": str(self.data), "image": o.image, "admin": o.admin,
                "panel_port": o.panel_port, "api_port": o.api_port, "admin_port": o.admin_port,
                "host_components": ["Docker", "swap", "UFW", "fail2ban", "OpenSSH"],
                "image_build": False}

    def checked_container(self):
        """Reuse the native updater's exact target/mount/command boundary."""
        spec = importlib.util.spec_from_file_location("korra_host_updater", HERE / "updater.py")
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        before = os.environ.copy()
        try:
            os.environ.update(NAME=self.o.name, DATA=str(self.data),
                              PANEL_PORT=str(self.o.panel_port), API_PORT=str(self.o.api_port))
            updater = native.Updater(self.home)
            updater.command = lambda args, **kw: self.run(args, **kw).stdout.strip()
            info = updater.inspect_target()
        finally:
            os.environ.clear()
            os.environ.update(before)
        runtime = native.runtime_env_from_info(info)
        if runtime["AGENT_SUDO"] != ("1" if self.o.admin else "0"):
            raise HostError("Existing container admin mode differs; use controlled recreation")
        if (runtime["ENGINE_UID"], runtime["ENGINE_GID"]) != (str(self.o.uid), str(self.o.gid)):
            raise HostError("Container runtime UID/GID differs from DATA ownership")
        image = json.loads(self.run(["docker", "image", "inspect", self.o.image]).stdout)[0]
        if image.get("Id") != info["Image"]:
            raise HostError("Running image differs from the requested immutable image")
        return info

    def identity(self):
        self.checked_container()
        value = self.run(["docker", "exec", "-u", f"{self.o.uid}:{self.o.gid}", self.o.name,
                          "/opt/hermes/.venv/bin/python", "-c", ID_CODE]).stdout.strip()
        if not IDENTITY.fullmatch(value):
            raise HostError("Native installation identity unavailable")
        return value

    def memory_kib(self):
        fields = dict(line.split(":", 1) for line in
                      (self.root / "proc/meminfo").read_text(encoding="ascii").splitlines() if ":" in line)
        return int(fields.get("MemTotal", "0").split()[0])

    def firewall_preflight(self):
        """Read-only native policy inventory. Unknown policies require an operator."""
        for manager in ("nftables", "firewalld"):
            result = self.run(["systemctl", "is-active", manager], check=False)
            if result.stdout.strip() == "active":
                raise HostError("Existing firewall manager requires operator integration")
            if result.returncode not in {3, 4}:
                raise HostError("Cannot verify existing firewall manager")
        active = False
        locale = {**os.environ, "LC_ALL": "C"}
        if shutil.which("ufw"):
            status = self.run(["ufw", "status", "verbose"], env=locale).stdout
            if status.startswith("Status: active"):
                active = True
                allow = rf"(?m)^{self.o.ssh_port}(?:/tcp)?\s+ALLOW IN\s+Anywhere\s*$"
                if ("Default: deny (incoming), allow (outgoing)" not in status
                        or not re.search(allow, status)):
                    raise HostError("Existing UFW policy conflicts or operator SSH allowance is unverified")
            elif status.strip() != "Status: inactive":
                raise HostError("Cannot verify existing UFW policy")
            elif any(line.strip().startswith("ufw ") for line in
                     self.run(["ufw", "show", "added"], env=locale).stdout.splitlines()):
                raise HostError("Inactive UFW contains existing firewall rules")
        # A native nft table must be completely representable by iptables.
        # Arbitrary sets/inet tables are deliberately left to the operator.
        nft_tables = {"ip": set(), "ip6": set()}
        nft_chains = {"ip": {}, "ip6": {}}
        nft_counts = {"ip": {}, "ip6": {}}
        hooks = {"INPUT": "input", "OUTPUT": "output", "FORWARD": "forward",
                 "PREROUTING": "prerouting", "POSTROUTING": "postrouting"}
        if shutil.which("nft"):
            dump = json.loads(self.run(["nft", "--json", "list", "ruleset"]).stdout)
            entries = dump.get("nftables") if isinstance(dump, dict) else None
            if not isinstance(entries, list):
                raise HostError("Malformed nft firewall inventory")
            for entry in entries:
                if not isinstance(entry, dict) or len(entry) != 1:
                    raise HostError("Malformed nft firewall entry")
                kind, value = next(iter(entry.items()))
                if not isinstance(value, dict):
                    raise HostError("Malformed nft firewall object")
                if kind == "metainfo":
                    continue
                family = value.get("family")
                table = value.get("name") if kind == "table" else value.get("table")
                if family not in nft_tables or table not in {"filter", "nat", "mangle", "raw", "security"}:
                    raise HostError("Unknown nft firewall table/hook requires operator integration")
                nft_tables[family].add(table)
                if kind == "chain":
                    name = value.get("name", "")
                    if name not in hooks and not name.startswith(("DOCKER", "ufw-", "f2b-")):
                        raise HostError("Unknown nft firewall chain")
                    if value.get("hook") and value["hook"] != hooks.get(name):
                        raise HostError("Unknown nft firewall base hook")
                    nft_chains[family][(table, name)] = value.get("policy", "-").upper()
                elif kind == "rule":
                    key = (table, value.get("chain"))
                    nft_counts[family][key] = nft_counts[family].get(key, 0) + 1
                elif kind != "table":
                    raise HostError("Native nft objects require explicit operator integration")
        else:
            require_empty_nft_tables()
        ufw_kernel_verified = False
        for family, prefix in (("ip", "iptables"), ("ip6", "ip6tables")):
            legacy = self.root / "proc/net" / (family + "_tables_names")
            legacy_binary = prefix + "-legacy-save"
            if legacy.exists() and legacy.read_text(encoding="ascii").strip() and not shutil.which(legacy_binary):
                raise HostError("Legacy firewall tables require their native inspection tool")
            mirrors = []
            for binary in (prefix + "-save", prefix + "-nft-save", legacy_binary):
                if not shutil.which(binary):
                    continue
                result = self.run([binary], env=locale)
                if getattr(result, "stderr", "").strip():
                    raise HostError("Firewall inspection diagnostic requires operator attention")
                inventory = iptables_inventory(result.stdout, active, self.o.ssh_port)
                if binary == prefix + "-nft-save":
                    mirrors.append(inventory)
                if family == "ip" and inventory[1].get(("filter", "INPUT")) == "DROP":
                    ufw_kernel_verified = True
            if nft_tables[family] and not any(
                nft_tables[family] <= tables
                and {key: policy for key, policy in chains.items() if key[0] in nft_tables[family]} == nft_chains[family]
                and all(counts.get(key, 0) == count for key, count in nft_counts[family].items())
                and all(nft_counts[family].get(key, 0) == count for key, count in counts.items() if key[0] in nft_tables[family])
                for tables, chains, counts in mirrors
            ):
                raise HostError("Native nft firewall has no complete iptables inspection mirror")
        if active and not ufw_kernel_verified:
            raise HostError("UFW status has no verified active kernel policy")
        return active

    def prepare(self):
        firewall_ready = self.firewall_preflight()
        packages = ["ca-certificates", "curl", "unzip", "rclone", "openssh-server", "ufw", "fail2ban"]
        if not shutil.which("docker"):
            packages.append("docker.io")
        missing = [name for name in packages if self.run(
            ["dpkg-query", "-W", "-f=" + "$" + "{Status}", name], check=False).stdout.strip() != "install ok installed"]
        if missing:
            self.run(["apt-get", "update"], timeout=600)
            self.run(["apt-get", "install", "-y", *missing], timeout=900)
        self.run(["systemctl", "enable", "--now", "docker"])
        swap_lines = (self.root / "proc/swaps").read_text(encoding="ascii").splitlines()
        if len(swap_lines) < 2 and self.memory_kib() <= 8 * 1024**2:
            directory = self.root / "var/lib/korra-host-admin"
            mkdir(directory)
            swap = directory / "swapfile"
            if swap.exists() or swap.is_symlink():
                raise HostError("Inactive existing managed swap needs operator inspection")
            self.run(["fallocate", "-l", str(4 * 1024**3), str(swap)])
            swap.chmod(0o600)
            self.run(["mkswap", str(swap)])
            self.run(["swapon", str(swap)])
            fstab = self.root / "etc/fstab"
            text = fstab.read_text(encoding="utf-8")
            entry = str(swap) + " none swap sw 0 0"
            if entry not in text.splitlines():
                publish(fstab, text.rstrip() + "\n" + entry + "\n", 0o644)
        if not firewall_ready:
            self.run(["ufw", "allow", f"{self.o.ssh_port}/tcp"])
            self.run(["ufw", "default", "deny", "incoming"])
            self.run(["ufw", "default", "allow", "outgoing"])
            self.run(["ufw", "--force", "enable"])
            if not self.firewall_preflight():
                raise HostError("UFW did not become active")
        jail_dir = self.root / "etc/fail2ban/jail.d"
        mkdir(jail_dir, 0o755)
        changed = publish(jail_dir / "korra-sshd.local",
                          f"[sshd]\nenabled = true\nbackend = systemd\nport = {self.o.ssh_port}\n", 0o644)
        self.run(["systemctl", "enable", "--now", "fail2ban"])
        if changed:
            self.run(["fail2ban-client", "reload"])
        jail = self.run(["fail2ban-client", "status", "sshd"]).stdout
        if "Status for the jail: sshd" not in jail:
            raise HostError("The fail2ban sshd jail could not be verified")
        self.data.mkdir(parents=True, exist_ok=True)
        os.chown(self.data, self.o.uid, self.o.gid, follow_symlinks=False)
        self.data.chmod(0o750)

    def record(self, identity):
        path = self.registry / identity / "inventory.json"
        if not path.exists():
            return None
        trusted(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if (value.get("install_id") != identity or value.get("data") != str(self.data)
                or value.get("name") != self.o.name or value.get("port") != self.o.admin_port):
            raise HostError("Root-managed contour inventory differs")
        return value

    def save_record(self, identity, value):
        publish(self.registry / identity / "inventory.json",
                json.dumps(value, indent=2, sort_keys=True) + "\n")

    def unit(self, identity):
        return "korra-host-admin-" + identity + ".service"

    def stop_access(self, identity, record):
        record["state"] = "revoking"
        self.save_record(identity, record)
        publish(self.registry / identity / "authorized_keys", "")
        mode = self.run(["systemctl", "show", self.unit(identity),
                         "--property=KillMode", "--value"]).stdout.strip()
        if mode != "control-group":
            raise HostError("Revocation incomplete: service KillMode differs")
        self.run(["systemctl", "stop", self.unit(identity)])
        state = self.run(["systemctl", "show", self.unit(identity),
                          "--property=ActiveState", "--value"]).stdout.strip()
        if state not in {"inactive", "failed"}:
            raise HostError("Root access revocation incomplete: service remains active")
        self.run(["systemctl", "disable", self.unit(identity)])
        record["state"] = "revoked"
        self.save_record(identity, record)

    def matching_inventory(self):
        matches = []
        if self.registry.exists():
            trusted(self.registry)
            for path in self.registry.glob("*/inventory.json"):
                trusted(path)
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("name") == self.o.name and value.get("data") == str(self.data):
                    if not IDENTITY.fullmatch(path.parent.name) or value.get("install_id") != path.parent.name:
                        raise HostError("Invalid root inventory identity")
                    matches.append((path.parent.name, value))
        if len(matches) > 1:
            raise HostError("Ambiguous root-managed contour grant")
        return matches[0] if matches else None

    def revoke(self):
        match = self.matching_inventory()
        if match is None:
            raise HostError("No root-managed contour grant")
        identity, value = match
        self.stop_access(identity, value)
        return value

    def access(self, action):
        identity = self.identity()
        mkdir(self.registry)
        directory = self.registry / identity
        mkdir(directory)
        for inventory in self.registry.glob("*/inventory.json"):
            trusted(inventory)
            other = json.loads(inventory.read_text(encoding="utf-8"))
            if other.get("install_id") != identity and (
                other.get("data") == str(self.data) or other.get("name") == self.o.name
                or (other.get("port") == self.o.admin_port and other.get("state") != "revoked")
            ):
                raise HostError("Contour identity/name/port conflicts with root inventory")
        record = self.record(identity)
        if action == "rotate":
            if not record:
                raise HostError("No registered root grant")
            self.stop_access(identity, record)
        elif record and record["state"] != "active":
            raise HostError("Revoked/incomplete grant requires explicit rotate")
        host_key = self.root / "etc/ssh/ssh_host_ed25519_key"
        trusted(host_key)
        host_public = self.run(["ssh-keygen", "-y", "-f", str(host_key)]).stdout.strip()
        if not host_public.startswith("ssh-ed25519 "):
            raise HostError("Host Ed25519 identity unavailable")
        host_hash = hashlib.sha256(host_public.encode()).hexdigest()
        if record and action == "grant" and host_hash != record["host_public_sha256"]:
            raise HostError("Host pin changed; verify locally and rotate explicitly")
        if record and action == "grant":
            for path, expected in (
                (directory / "sshd_config", self.server_config(identity, host_key)),
                (self.root / "etc/systemd/system" / self.unit(identity), self.unit_config(identity)),
            ):
                trusted(path)
                if path.read_text(encoding="utf-8") != expected:
                    raise HostError("Registered service configuration changed; rotate explicitly")
        with tempfile.TemporaryDirectory(prefix=".key-", dir=directory) as stage:
            key = Path(stage) / "id_ed25519"
            with data_ssh(self.data, self.o.uid, self.o.gid) as fd:
                current = read_at(fd, "id_ed25519_host")
                if record and action == "grant":
                    if current is None:
                        raise HostError("Registered private key missing; rotate explicitly")
                    key.write_bytes(current)
                    key.chmod(0o600)
                else:
                    if current is not None and action != "rotate":
                        raise HostError("Unregistered/copied private key; refusing silent adoption")
                    self.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                              "korra-host-admin:" + identity, "-f", str(key)])
                public = self.run(["ssh-keygen", "-y", "-f", str(key)]).stdout.strip()
                fingerprint = hashlib.sha256(public.encode()).hexdigest()
                if record and action == "grant" and fingerprint != record["public_sha256"]:
                    raise HostError("Private key differs from root inventory")
                authorized = f'from="127.0.0.1",no-agent-forwarding,no-X11-forwarding {public} korra-host-admin:{identity}\n'
                if record and action == "grant":
                    trusted(directory / "authorized_keys")
                    if (directory / "authorized_keys").read_text(encoding="utf-8") != authorized:
                        raise HostError("Registered authorization changed; rotate explicitly")
                existing = read_at(fd, "config") or b""
                include = b"Include /opt/data/.ssh/korra-host.conf\n"
                if include not in existing.splitlines(keepends=True):
                    write_at(fd, "config", include + existing, self.o.uid, self.o.gid)
                write_at(fd, "id_ed25519_host", key.read_bytes(), self.o.uid, self.o.gid)
                write_at(fd, "id_ed25519_host.pub", public + "\n", self.o.uid, self.o.gid, 0o644)
                write_at(fd, "korra-host.conf", self.client_config(), self.o.uid, self.o.gid)
                write_at(fd, "korra-host-known_hosts",
                         f"[127.0.0.1]:{self.o.admin_port} {host_public}\n", self.o.uid, self.o.gid)
        publish(directory / "authorized_keys", authorized)
        publish(directory / "sshd_config", self.server_config(identity, host_key))
        mkdir(self.root / "run/sshd", 0o755)
        self.run(["/usr/sbin/sshd", "-t", "-f", str(directory / "sshd_config")])
        units = self.root / "etc/systemd/system"
        mkdir(units, 0o755)
        publish(units / self.unit(identity), self.unit_config(identity), 0o644)
        value = {"install_id": identity, "name": self.o.name, "data": str(self.data),
                 "port": self.o.admin_port, "public_sha256": fingerprint,
                 "host_public_sha256": host_hash, "state": "activating"}
        self.save_record(identity, value)
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "enable", "--now", self.unit(identity)])
        self.verify()
        value["state"] = "active"
        self.save_record(identity, value)
        return value

    def unit_config(self, identity):
        directory = self.registry / identity
        return ("[Unit]\nDescription=Korra per-contour host root\nAfter=network.target\n"
                "[Service]\nType=simple\n"
                f"ExecStart=/usr/sbin/sshd -D -e -f {directory}/sshd_config\n"
                "KillMode=control-group\nTimeoutStopSec=10\nSendSIGKILL=yes\n"
                "Restart=on-failure\nRestartSec=2\n[Install]\nWantedBy=multi-user.target\n")

    def client_config(self):
        return f"""Host host korra-host
    HostName 127.0.0.1
    Port {self.o.admin_port}
    User root
    IdentityFile /opt/data/.ssh/id_ed25519_host
    IdentitiesOnly yes
    IdentityAgent none
    StrictHostKeyChecking yes
    UserKnownHostsFile /opt/data/.ssh/korra-host-known_hosts
    GlobalKnownHostsFile /dev/null
    VerifyHostKeyDNS no
    UpdateHostKeys no
    ProxyCommand none
    ProxyJump none
    BatchMode yes
    ConnectTimeout 10
    ControlMaster auto
    ControlPath /opt/data/.ssh/cm-host-%C
    ControlPersist 300
    ServerAliveInterval 30
"""

    def server_config(self, identity, host_key):
        directory = self.registry / identity
        return f"""ListenAddress 127.0.0.1
Port {self.o.admin_port}
HostKey {host_key}
PidFile {directory}/sshd.pid
AuthorizedKeysFile {directory}/authorized_keys
AllowUsers root
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes yes
AllowAgentForwarding no
X11Forwarding no
PermitUserEnvironment no
PermitTTY yes
"""

    def verify(self):
        self.checked_container()
        for args in (["sudo", "-n", "id", "-u"],
                     ["/usr/bin/ssh", "-F", "/opt/data/.ssh/korra-host.conf", "host", "id", "-u"]):
            value = self.run(["docker", "exec", "-u", f"{self.o.uid}:{self.o.gid}", self.o.name, *args]).stdout.strip()
            if value != "0":
                raise HostError("Admin mode must yield container root and pinned host root")

    def bootstrap(self):
        if not self.o.admin and self.matching_inventory() is not None:
            self.revoke()
        image_file = self.home / "IMAGE"
        if image_file.exists() and image_file.read_text(encoding="utf-8").strip() != self.o.image:
            raise HostError("Existing IMAGE differs; use native update")
        present = self.run(["docker", "ps", "-aq", "--filter", "name=^/" + self.o.name + "$"],
                           check=False) if shutil.which("docker") else None
        if present and present.stdout.strip():
            self.checked_container()
            self.prepare()
        else:
            for port in (self.o.panel_port, self.o.api_port, self.o.admin_port):
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
            self.prepare()
            self.run(["docker", "pull", self.o.image], timeout=900)
            image = json.loads(self.run(["docker", "image", "inspect", self.o.image]).stdout)[0]
            if image.get("Architecture") != "amd64" or image.get("Os") != "linux":
                raise HostError("Image must be native linux/amd64")
            publish(image_file, self.o.image + "\n", 0o644)
            env = {**os.environ, "NAME": self.o.name, "DATA": str(self.data),
                   "PANEL_PORT": str(self.o.panel_port), "API_PORT": str(self.o.api_port),
                   "ENGINE_UID": str(self.o.uid), "ENGINE_GID": str(self.o.gid),
                   "AGENT_SUDO": "1" if self.o.admin else "0"}
            self.run(["bash", str(self.home / "up.sh")], env=env, timeout=600)
        if self.o.admin:
            return self.access("grant")
        return {"name": self.o.name, "admin": False, "host_grant": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["bootstrap", "grant", "rotate", "revoke", "verify"])
    p.add_argument("--home", type=Path, default=HERE)
    p.add_argument("--data", type=Path, default=Path("/opt/korra/data"))
    p.add_argument("--name", default="korra")
    p.add_argument("--image")
    p.add_argument("--panel-port", type=int, default=9119)
    p.add_argument("--api-port", type=int, default=8650)
    p.add_argument("--admin-port", type=int, default=22021)
    p.add_argument("--ssh-port", type=int, default=22)
    p.add_argument("--uid", type=int, default=10000)
    p.add_argument("--gid", type=int, default=10000)
    p.add_argument("--no-admin", dest="admin", action="store_false")
    p.add_argument("--plan", action="store_true")
    o = p.parse_args(argv)
    try:
        host = HostBootstrap(o)
        plan = host.preflight()
        if o.plan:
            print(json.dumps(plan, sort_keys=True))
            return 0
        if o.action != "revoke":
            trusted(HERE / "updater.py")
            trusted(o.home / "up.sh")
        # Cross-contour port/identity registration is serialized on the host.
        fd = os.open("/run/lock/korra-host-admin.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            os.close(fd)
            raise HostError("Unsafe host-wide bootstrap lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if o.action == "bootstrap":
                result = host.bootstrap()
            elif o.action == "verify":
                host.verify()
                result = {"verified": True}
            elif o.action == "revoke":
                result = host.revoke()
            else:
                result = host.access(o.action)
            print(json.dumps(result, sort_keys=True))
        finally:
            os.close(fd)
    except (HostError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Host operation refused or incomplete: " + (
            str(exc) if isinstance(exc, HostError) else type(exc).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
