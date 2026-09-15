"""Phase 4: lifecycle guard + per-profile observability."""
import pytest

from gateway.config import GatewayConfig
from gateway.restart import GATEWAY_FATAL_CONFIG_EXIT_CODE


class TestServedProfilesStatus:
    def test_write_and_read_served_profiles(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import importlib
        import gateway.status as status
        importlib.reload(status)
        try:
            status.write_runtime_status(
                gateway_state="running", served_profiles=["default", "coder"]
            )
            rec = status.read_runtime_status()
            assert rec.get("served_profiles") == ["default", "coder"]
        finally:
            importlib.reload(status)


def test_cron_profile_homes_follow_allowlist(tmp_path, monkeypatch):
    """The helper wired into in-process cron returns only selected profiles."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    for name in ("worker", "guest"):
        (default_home / "profiles" / name).mkdir(parents=True)

    import gateway.run as gateway_run

    homes = gateway_run._multiplex_profile_homes(
        GatewayConfig(
            multiplex_profiles=True,
            multiplex_profile_allowlist=["worker"],
        )
    )

    assert [name for name, _home in homes] == ["default", "worker"]


class TestNamedProfileMultiplexerGuard:
    """_guard_named_profile_under_multiplexer is inert unless all conditions hold."""


    def test_force_bypasses(self, monkeypatch):
        from korra_cli import gateway as gw
        # Even if it looks like a named profile, force returns immediately.
        monkeypatch.setattr(gw, "_profile_suffix", lambda: "coder")
        gw._guard_named_profile_under_multiplexer(force=True)

    def test_inert_when_no_default_gateway_running(self, monkeypatch, tmp_path):
        from korra_cli import gateway as gw
        monkeypatch.setattr(gw, "_profile_suffix", lambda: "coder")
        monkeypatch.setattr(
            "korra_constants.get_default_hermes_root", lambda: tmp_path
        )
        # No gateway.pid in tmp_path => no running default gateway => no raise.
        gw._guard_named_profile_under_multiplexer(force=False)

    def _fake_running_default_gateway(self, monkeypatch, tmp_path):
        """Make the guard believe a live default gateway exists at tmp_path.

        Liveness is a VERIFIED identity, not bare PID existence (K21-088): a
        stale record whose PID the OS recycled onto an unrelated process must
        not keep lending its served set. So this pytest process stands in for
        the gateway by wearing a gateway command line and a runtime record
        that matches its real start time — the same shape a live gateway
        writes — instead of stubbing ``_pid_exists``.
        """
        import json
        import os

        from korra_cli import gateway as gw
        import gateway.status as status

        monkeypatch.setattr(gw, "_profile_suffix", lambda: "coder")
        monkeypatch.setattr(
            "korra_constants.get_default_hermes_root", lambda: tmp_path
        )
        (tmp_path / "gateway_state.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "hermes_home": str(tmp_path),
                    "gateway_state": "running",
                    "start_time": status._get_process_start_time(os.getpid()),
                }
            ),
            encoding="utf-8",
        )
        real_cmdline = status._read_process_cmdline
        monkeypatch.setattr(
            status,
            "_read_process_cmdline",
            lambda pid: (
                "python -m korra_cli.main gateway run"
                if pid == os.getpid()
                else real_cmdline(pid)
            ),
        )
        status._clear_running_pid_cache()

    def test_unset_allowlist_preserves_historical_guard(self, monkeypatch, tmp_path):
        self._fake_running_default_gateway(monkeypatch, tmp_path)
        (tmp_path / "config.yaml").write_text(
            "gateway:\n  multiplex_profiles: true\n",
            encoding="utf-8",
        )

        from korra_cli import gateway as gw

        with pytest.raises(SystemExit) as excinfo:
            gw._guard_named_profile_under_multiplexer(force=False)
        assert excinfo.value.code == GATEWAY_FATAL_CONFIG_EXIT_CODE

    def test_served_profile_is_still_guarded(self, monkeypatch, tmp_path):
        self._fake_running_default_gateway(monkeypatch, tmp_path)
        (tmp_path / "config.yaml").write_text(
            "gateway:\n"
            "  multiplex_profiles: true\n"
            "  multiplex_profile_allowlist:\n"
            "    - Coder\n",
            encoding="utf-8",
        )

        from korra_cli import gateway as gw

        with pytest.raises(SystemExit) as excinfo:
            gw._guard_named_profile_under_multiplexer(force=False)
        assert excinfo.value.code == GATEWAY_FATAL_CONFIG_EXIT_CODE

    @pytest.mark.parametrize(
        "allowlist_yaml",
        ["[]", "[worker]", "coder"],
    )
    def test_unserved_profile_may_run_standalone(
        self, monkeypatch, tmp_path, allowlist_yaml
    ):
        self._fake_running_default_gateway(monkeypatch, tmp_path)
        (tmp_path / "config.yaml").write_text(
            "gateway:\n"
            "  multiplex_profiles: true\n"
            f"  multiplex_profile_allowlist: {allowlist_yaml}\n",
            encoding="utf-8",
        )

        from korra_cli import gateway as gw

        gw._guard_named_profile_under_multiplexer(force=False)

    def test_named_profile_served_probe_matches_the_start_guard(self, monkeypatch, tmp_path):
        self._fake_running_default_gateway(monkeypatch, tmp_path)
        (tmp_path / "config.yaml").write_text(
            "gateway:\n  multiplex_profiles: true\n",
            encoding="utf-8",
        )

        from korra_cli import gateway as gw

        assert gw.named_profile_served_by_running_multiplexer() is True

        monkeypatch.setattr(gw, "_profile_suffix", lambda: "")
        assert gw.named_profile_served_by_running_multiplexer() is False


