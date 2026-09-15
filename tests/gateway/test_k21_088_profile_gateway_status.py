"""K21-088 — «обслуживается мультиплексором» отличается от «остановлен».

Контур Зариповой 14.09.2026: после `korra profile create` мультиплексор
`default` уже вёл чат и API новых профилей, а `korra profile list` и
`GET /api/profiles` показывали `stopped` / `gateway_running: false`.
Профиль, который обслуживает общий шлюз, не пишет ни своего `gateway.pid`,
ни своего `gateway_state.json`, поэтому любая проверка по файлам профиля
честно отвечала «нет процесса» — и владелец читал это как «агент выключен».

Здесь закреплена матрица состояний одного профиля:

* ``running``  — у профиля есть собственный живой шлюз;
* ``served``   — живой мультиплексор основного профиля действительно ведёт
  этот профиль (запись `served_profiles`, а при её отсутствии — конфиг с
  ``multiplex_profile_allowlist``);
* ``stopped``  — ни своего шлюза, ни обслуживания;
* потерянный/протухший PID-файл ``running`` не даёт: живость подтверждается
  проверенной личностью процесса, а не существованием PID.

И адресность управления: у обслуживаемого профиля нечего останавливать и
перезапускать по отдельности, а профиль со своим шлюзом управляется как
прежде.
"""

from __future__ import annotations

import contextlib
import io
import json
import os

import pytest


GATEWAY_CMDLINE = "python -m korra_cli.main gateway run"


def _write_state(path, **fields):
    path.write_text(json.dumps(fields), encoding="utf-8")


@pytest.fixture
def contour(tmp_path, monkeypatch):
    """Контур с живым мультиплексором `default`, профилями alpha и beta.

    Живость шлюза подтверждается проверенной личностью процесса, поэтому
    этот pytest-процесс «притворяется» шлюзом только тем, что его командная
    строка читается как `gateway run`; остальные PID сохраняют настоящую.
    """
    import gateway.status as status
    import korra_constants

    root = tmp_path / "data"
    (root / "profiles" / "alpha").mkdir(parents=True)
    (root / "profiles" / "beta").mkdir(parents=True)
    (root / "config.yaml").write_text("gateway:\n  multiplex_profiles: true\n", encoding="utf-8")
    _write_state(
        root / "gateway_state.json",
        pid=os.getpid(),
        hermes_home=str(root),
        gateway_state="running",
        start_time=status._get_process_start_time(os.getpid()),
        served_profiles=["default", "alpha"],
        platforms={"api_server": {"state": "connected"}, "alpha:telegram": {"state": "connected"}},
    )
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.delenv("KORRA_HOME", raising=False)
    monkeypatch.delenv("GATEWAY_MULTIPLEX_PROFILES", raising=False)
    monkeypatch.delenv("KORRA_MULTIPLEX_PROFILES", raising=False)
    monkeypatch.setattr(korra_constants, "_default_hermes_root_memo", None)

    real_cmdline = status._read_process_cmdline
    monkeypatch.setattr(
        status,
        "_read_process_cmdline",
        lambda pid: GATEWAY_CMDLINE if pid == os.getpid() else real_cmdline(pid),
    )
    status._clear_running_pid_cache()
    yield root
    status._clear_running_pid_cache()


def _status(profile_dir):
    from korra_cli.profiles import resolve_profile_gateway_status

    return resolve_profile_gateway_status(profile_dir)


class TestStatusMatrix:
    def test_served_profile_is_not_stopped(self, contour):
        assert _status(contour / "profiles" / "alpha") == "served"

    def test_unserved_profile_is_stopped(self, contour):
        assert _status(contour / "profiles" / "beta") == "stopped"

    def test_default_profile_with_its_own_gateway_is_running(self, contour):
        assert _status(contour) == "running"

    def test_own_gateway_wins_over_multiplexer(self, contour, monkeypatch):
        """Профиль, поднятый через --force, управляется своим шлюзом."""
        import gateway.status as status

        beta = contour / "profiles" / "beta"
        _write_state(
            beta / "gateway_state.json",
            pid=os.getpid(),
            hermes_home=str(beta),
            gateway_state="running",
            start_time=status._get_process_start_time(os.getpid()),
        )
        real = status._read_process_cmdline
        monkeypatch.setattr(
            status,
            "_read_process_cmdline",
            lambda pid: (
                f"python -m korra_cli.main -p beta gateway run"
                if pid == os.getpid()
                else real(pid)
            ),
        )
        assert _status(beta) == "running"

    def test_lost_pid_file_keeps_the_served_profile_served(self, contour):
        """Мультиплексор под супервизором может жить без gateway.pid."""
        pid_file = contour / "gateway.pid"
        assert not pid_file.exists()
        assert _status(contour / "profiles" / "alpha") == "served"

    def test_recycled_pid_of_a_foreign_process_is_not_running(self, contour):
        """PID из записи достался чужому живому процессу — шлюза нет.

        PID 1 существует всегда и шлюзом не является: раньше хватало самого
        факта «процесс с таким PID жив», и мёртвый мультиплексор продолжал
        числить за собой свои served_profiles.
        """
        import gateway.status as status

        _write_state(
            contour / "gateway_state.json",
            pid=1,
            hermes_home=str(contour),
            gateway_state="running",
            served_profiles=["default", "alpha"],
        )
        (contour / "gateway.pid").write_text(
            json.dumps({"pid": 1, "hermes_home": str(contour), "kind": "hermes-gateway"}),
            encoding="utf-8",
        )
        status._clear_running_pid_cache()
        assert _status(contour / "profiles" / "alpha") == "stopped"
        assert _status(contour) == "stopped"

    def test_restarted_process_with_a_stale_start_time_is_not_running(self, contour):
        """Тот же PID, но процесс стартовал в другое время — запись протухла."""
        import gateway.status as status

        _write_state(
            contour / "gateway_state.json",
            pid=os.getpid(),
            hermes_home=str(contour),
            gateway_state="running",
            start_time=(status._get_process_start_time(os.getpid()) or 10**9) - 4242,
            served_profiles=["default", "alpha"],
        )
        status._clear_running_pid_cache()
        assert _status(contour / "profiles" / "alpha") == "stopped"
        assert _status(contour) == "stopped"

    def test_stopped_multiplexer_does_not_serve(self, contour):
        import gateway.status as status

        _write_state(
            contour / "gateway_state.json",
            pid=os.getpid(),
            hermes_home=str(contour),
            gateway_state="stopped",
            served_profiles=["default", "alpha"],
        )
        status._clear_running_pid_cache()
        assert _status(contour / "profiles" / "alpha") == "stopped"

    def test_allowlist_still_decides_without_a_recorded_set(self, contour):
        """Шлюз старее записи served_profiles: решает конфиг с allowlist."""
        import gateway.status as status

        _write_state(
            contour / "gateway_state.json",
            pid=os.getpid(),
            hermes_home=str(contour),
            gateway_state="running",
            start_time=status._get_process_start_time(os.getpid()),
        )
        (contour / "config.yaml").write_text(
            "gateway:\n  multiplex_profiles: true\n  multiplex_profile_allowlist:\n    - Alpha\n",
            encoding="utf-8",
        )
        status._clear_running_pid_cache()
        assert _status(contour / "profiles" / "alpha") == "served"
        assert _status(contour / "profiles" / "beta") == "stopped"

    def test_recorded_set_beats_config_derivation(self, contour):
        """Конфиг обещает всех, но живой шлюз поднял только alpha."""
        assert _status(contour / "profiles" / "beta") == "stopped"


class TestLivenessLadder:
    def test_resolver_answers_multiplexer_for_a_served_profile(self, contour):
        from gateway.status import (
            profile_platforms_from_multiplexer,
            resolve_gateway_liveness,
        )

        live = resolve_gateway_liveness(
            profile_dir=contour / "profiles" / "alpha",
            health_probe=None,
            use_cache=False,
        )
        assert live.running is True
        assert live.pid == os.getpid()
        assert live.source == "multiplexer"
        plats = profile_platforms_from_multiplexer(live.runtime, "alpha")
        assert plats["telegram"] == {"state": "connected"}
        # Общий api_server обслуживает и alpha (по префиксу /p/alpha/).
        assert plats["api_server"]["state"] == "connected"

    def test_resolver_keeps_stopped_for_an_unserved_profile(self, contour):
        from gateway.status import resolve_gateway_liveness

        live = resolve_gateway_liveness(
            profile_dir=contour / "profiles" / "beta",
            health_probe=None,
            use_cache=False,
        )
        assert live.running is False
        assert live.source == "none"


class TestProfileSurfaces:
    def test_list_profiles_marks_the_served_profile(self, contour):
        from korra_cli.profiles import list_profiles

        by_name = {p.name: p for p in list_profiles()}
        assert by_name["alpha"].gateway_status == "served"
        assert by_name["beta"].gateway_status == "stopped"
        assert by_name["default"].gateway_status == "running"

    def test_cli_profile_list_distinguishes_served_from_stopped(self, contour):
        import argparse

        from korra_cli.main import cmd_profile

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_profile(argparse.Namespace(profile_action="list"))
        out = buf.getvalue()
        alpha_line = next(line for line in out.splitlines() if "alpha" in line)
        beta_line = next(line for line in out.splitlines() if "beta" in line)
        assert "общий шлюз" in alpha_line
        assert "остановлен" in beta_line
        assert "общий шлюз" not in beta_line

    def test_api_profiles_reports_the_served_state(self, contour):
        from korra_cli.profiles import list_profiles
        from korra_cli.web_server import _profile_to_dict

        payload = {p["name"]: p for p in (_profile_to_dict(p) for p in list_profiles())}
        assert payload["alpha"]["gateway_status"] == "served"
        assert payload["beta"]["gateway_status"] == "stopped"
        assert payload["default"]["gateway_status"] == "running"

    def test_api_profiles_fallback_scan_reports_the_served_state(self, contour):
        from korra_cli import profiles as profiles_mod
        from korra_cli.web_server import _fallback_profile_dicts

        payload = {p["name"]: p for p in _fallback_profile_dicts(profiles_mod)}
        assert payload["alpha"]["gateway_status"] == "served"
        assert payload["beta"]["gateway_status"] == "stopped"

    def test_gateway_list_names_the_shared_gateway(self, contour):
        from korra_cli.gateway import _gateway_list

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _gateway_list()
        out = buf.getvalue()
        alpha_line = next(line for line in out.splitlines() if "alpha" in line)
        assert "общий шлюз" in alpha_line


class TestManagementIsAddressed:
    def test_cli_stop_refuses_for_a_served_profile(self, contour, monkeypatch):
        import argparse

        import korra_cli.gateway as gw
        from gateway.restart import GATEWAY_FATAL_CONFIG_EXIT_CODE

        monkeypatch.setenv("HERMES_HOME", str(contour / "profiles" / "alpha"))
        monkeypatch.setattr(gw, "_refuse_from_inside_gateway", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(
            "tools.process_registry._is_supervised_gateway_process", lambda: False
        )
        killed: list = []
        monkeypatch.setattr(gw, "stop_profile_gateway", lambda *a, **k: killed.append("stop") or True)
        monkeypatch.setattr(gw, "_dispatch_via_service_manager_if_s6", lambda *a, **k: killed.append("s6") or True)

        with contextlib.redirect_stdout(io.StringIO()) as buf, pytest.raises(SystemExit) as exc:
            gw.gateway_command(argparse.Namespace(gateway_command="stop", system=False, all=False))
        assert exc.value.code == GATEWAY_FATAL_CONFIG_EXIT_CODE
        assert killed == [], "останов обслуживаемого профиля не должен никого трогать"
        assert "общий шлюз" in buf.getvalue()

    def test_cli_stop_still_works_for_a_profile_with_its_own_gateway(self, contour, monkeypatch):
        import argparse

        import gateway.status as status
        import korra_cli.gateway as gw

        beta = contour / "profiles" / "beta"
        _write_state(
            beta / "gateway_state.json",
            pid=os.getpid(),
            hermes_home=str(beta),
            gateway_state="running",
            start_time=status._get_process_start_time(os.getpid()),
        )
        real = status._read_process_cmdline
        monkeypatch.setattr(
            status,
            "_read_process_cmdline",
            lambda pid: ("python -m korra_cli.main -p beta gateway run" if pid == os.getpid() else real(pid)),
        )
        monkeypatch.setenv("HERMES_HOME", str(beta))
        monkeypatch.setattr(
            "tools.process_registry._is_supervised_gateway_process", lambda: False
        )
        dispatched: list = []
        monkeypatch.setattr(
            gw, "_dispatch_via_service_manager_if_s6", lambda *a, **k: dispatched.append("s6") or True
        )
        with contextlib.redirect_stdout(io.StringIO()):
            gw.gateway_command(argparse.Namespace(gateway_command="stop", system=False, all=False))
        assert dispatched == ["s6"]

    def test_dashboard_refuses_lifecycle_verbs_for_a_served_profile(self, contour, monkeypatch):
        from korra_cli import web_server
        from korra_cli.web_server import multiplexed_profile_refusal

        spawned: list = []
        monkeypatch.setattr(
            web_server, "_spawn_hermes_action", lambda *a, **k: spawned.append(a) or None
        )
        for verb in ("start", "stop", "restart"):
            refusal = multiplexed_profile_refusal("alpha", verb)
            assert refusal and "общий шлюз" in refusal
        assert multiplexed_profile_refusal("beta", "stop") is None
        assert multiplexed_profile_refusal(None, "stop") is None
        assert spawned == []

    def test_dashboard_subcommand_never_retargets_the_shared_gateway(self, contour):
        """Перезапуск одного агента не должен молча перезапускать всех."""
        from korra_cli.web_server import _gateway_subcommand

        assert _gateway_subcommand("alpha", "restart") == ["-p", "alpha", "gateway", "restart"]
