"""CLI handlers for ``hermes egress ...``.

Subcommands:
    install  — download the pinned iron-proxy binary
    setup    — interactive wizard: install binary, generate CA, mint tokens, write config
    start    — launch the proxy as a managed subprocess
    stop     — terminate the managed proxy
    status   — show binary version + config presence + listen state + mappings
    disable  — flip ``proxy.enabled`` to False (does not stop a running proxy)
    config   — print the generated proxy.yaml path (for debugging / external review)

The top-level command is ``hermes egress``.  Note that the inbound OAuth
reverse-proxy command (``hermes proxy``) lives elsewhere in
``korra_cli/main.py`` — different direction, different purpose.
"""

from __future__ import annotations

import argparse
import os
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent.proxy_sources import iron_proxy as ip
from korra_cli.config import load_config, save_config


# ---------------------------------------------------------------------------
# Argparse wiring — called from korra_cli.main
# ---------------------------------------------------------------------------


def register_cli(parent_parser: argparse.ArgumentParser) -> None:
    """Attach the egress subcommand tree to a parent parser.

    Called from ``korra_cli.main`` as part of building the top-level
    ``hermes egress`` parser.
    """

    # dest='egress_command' — keeps this subparser tree disjoint from the
    # inbound OAuth ``hermes proxy`` subparser (which uses dest='proxy_command').
    # No runtime collision today since they live in separate parser trees,
    # but a future grep-and-refactor on ``proxy_command`` would otherwise
    # hit both handlers.
    sub = parent_parser.add_subparsers(dest="egress_command")

    install = sub.add_parser(
        "install",
        help=f"Download iron-proxy binary (v{ip._IRON_PROXY_VERSION})",
    )
    install.add_argument(
        "--force", action="store_true",
        help='Загрузить заново, даже если управляемая копия уже существует',
    )
    install.set_defaults(func=cmd_install)

    setup = sub.add_parser(
        "setup",
        help='Мастер: установка, сертификат CA, создание токенов и сохранение настроек',
    )
    setup.add_argument(
        "--tunnel-port", type=int, default=None,
        help=f"Override the tunnel port (default {ip._DEFAULT_TUNNEL_PORT})",
    )
    setup.add_argument(
        "--from-bitwarden", action="store_true",
        help='Получать ключи провайдеров из secrets.bitwarden вместо текущей среды. При недоступности Bitwarden сообщить ошибку без подмены источника.',
    )
    setup.add_argument(
        "--no-bitwarden", action="store_true",
        help='При повторной настройке вернуть credential_source в env; действует после --from-bitwarden',
    )
    setup.add_argument(
        "--rotate-tokens", action="store_true",
        help='Создать новые токены прокси для всех провайдеров. По умолчанию существующие сохраняются, чтобы работающие изолированные среды не получали ошибки 401.',
    )
    setup.add_argument(
        "--restart", dest="restart", action="store_true", default=None,
        help='Автоматически перезапустить работающую службу после сохранения настроек и токенов; обычно в терминале запрашивается подтверждение',
    )
    setup.add_argument(
        "--no-restart", dest="restart", action="store_false",
        help='Не перезапускать службу после настройки. Для применения изменений выполните korra egress restart.',
    )
    setup.set_defaults(func=cmd_setup)

    start = sub.add_parser("start", help='Запустить управляемый iron-proxy')
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help='Остановить управляемый iron-proxy')
    stop.set_defaults(func=cmd_stop)

    restart = sub.add_parser(
        "restart",
        help='Перезапустить управляемый iron-proxy',
    )
    restart.set_defaults(func=cmd_restart)

    reload_p = sub.add_parser(
        "reload",
        help='Перезагрузить правила из proxy.yaml через API управления без перезапуска и разрыва подключений',
    )
    reload_p.set_defaults(func=cmd_reload)

    status = sub.add_parser("status", help='Показать состояние прокси и связи ключей')
    status.add_argument(
        "--show-tokens", action="store_true",
        help='Показать токены прокси; по умолчанию только скрытые префиксы. Токены могут сохраниться в истории терминала.',
    )
    status.set_defaults(func=cmd_status)

    disable = sub.add_parser("disable", help='Отключить подключение прокси')
    disable.set_defaults(func=cmd_disable)

    cfg = sub.add_parser("config", help='Показать путь к созданному proxy.yaml')
    cfg.set_defaults(func=cmd_config)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_install(args: argparse.Namespace) -> int:
    console = Console()
    try:
        binary = ip.install_iron_proxy(force=bool(args.force))
    except Exception as exc:  # noqa: BLE001 — top-level user-facing error funnel
        console.print(f"[red]✗ Ошибка установки:[/red] {exc}")
        console.print(
            "  Установить вручную: https://github.com/ironsh/iron-proxy/releases"
        )
        return 1
    version = ip.iron_proxy_version(binary) or "(версия неизвестна)"
    console.print(f"[green]✓[/green] Установлено: {binary}  {version}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    console = Console()
    console.print(Panel.fit(
        "[bold]Настройка iron-proxy[/bold]\n\n"
        "Направляет исходящий трафик изолированной среды через локальный\n"
        "TLS-прокси, чтобы агенты не видели настоящие ключи API провайдеров.\n\n"
        "[dim]Project: https://github.com/ironsh/iron-proxy  (Apache-2.0)[/dim]",
        border_style="cyan",
    ))

    # ------------------------------------------------------------------ binary
    console.print()
    console.print("[bold]Шаг 1[/bold]  Установка iron-proxy")
    try:
        binary = ip.find_iron_proxy(install_if_missing=False)
        if binary is None:
            console.print("  iron-proxy не найден в PATH; загружаю…")
            binary = ip.install_iron_proxy()
        version = ip.iron_proxy_version(binary) or "(версия неизвестна)"
        console.print(f"  [green]✓[/green] {binary}  {version}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]✗ Ошибка установки: {exc}[/red]")
        return 1

    # ------------------------------------------------------------------ CA
    console.print()
    console.print("[bold]Шаг 2[/bold]  Создание сертификата центра сертификации")
    try:
        ca_crt, ca_key = ip.ensure_ca_cert()
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]✗ Не удалось создать сертификат: {exc}[/red]")
        return 1
    console.print(f"  [green]✓[/green] {ca_crt}")

    # ------------------------------------------------------------------ mint
    console.print()
    console.print("[bold]Шаг 3[/bold]  Создание прокси-токенов для известных провайдеров")

    available_env_names: List[str] = []
    if args.from_bitwarden:
        cfg = load_config()
        bw_cfg = (cfg.get("secrets") or {}).get("bitwarden") or {}
        if not bw_cfg.get("enabled"):
            console.print(
                "  [red]✗ Запрошен --from-bitwarden, но secrets.bitwarden.enabled выключен.[/red]"
            )
            console.print(
                "  Сначала выполните `korra secrets bitwarden setup` или уберите --from-bitwarden."
            )
            return 1
        try:
            from agent.secret_sources import bitwarden as bw
            access_token = os.environ.get(
                bw_cfg.get("access_token_env", "BWS_ACCESS_TOKEN"), ""
            ).strip()
            if not access_token:
                console.print(
                    f"  [red]✗ Запрошен --from-bitwarden, но переменная "
                    f"{bw_cfg.get('access_token_env', 'BWS_ACCESS_TOKEN')} не задана.[/red]"
                )
                return 1
            secrets, _ = bw.fetch_bitwarden_secrets(
                access_token=access_token,
                project_id=bw_cfg.get("project_id", ""),
                cache_ttl_seconds=0,
                use_cache=False,
            )
            available_env_names = list(secrets.keys())
            if not available_env_names:
                console.print(
                    "  [red]✗ Bitwarden вернул пустой список секретов.[/red]\n"
                    "  Проверьте project_id в secrets.bitwarden и права токена BWS на проект."
                )
                return 1
            console.print(
                f"  Из Bitwarden получено переменных окружения: {len(available_env_names)}."
            )
        except Exception as exc:  # noqa: BLE001 — explicit user-facing error
            console.print(
                f"  [red]✗ Не удалось получить секреты Bitwarden: {exc}[/red]"
            )
            console.print(
                "  Исправьте настройки Bitwarden или повторите без --from-bitwarden: "
                "тогда прокси прочитает секреты из окружения основного процесса."
            )
            return 1
    else:
        # Env-based discovery reads os.environ.  Operators commonly keep their
        # provider keys only in ~/.hermes/.env (loaded automatically when the
        # agent runs, but NOT exported into an interactive shell).  Fall back
        # to loading that file so `hermes egress setup` finds the same keys the
        # agent would — otherwise a user with keys solely in .env sees a
        # confusing "no provider keys found" when the keys clearly "exist".
        loaded = _load_env_file_into_environ()
        if loaded:
            console.print(
                f"  [dim]Из ~/.hermes/.env загружены имена ключей провайдеров: {loaded}.[/dim]"
            )

    discovered = ip.discover_provider_mappings(
        available_env_names=available_env_names or None,
    )

    # Preserve tokens for providers we already had unless the operator
    # explicitly requested rotation.  This prevents re-running `hermes
    # egress setup` from invalidating tokens baked into already-running
    # sandboxes.
    existing = ip.load_mappings()
    rotate = bool(getattr(args, "rotate_tokens", False))

    # P3 confirmation gate: --rotate-tokens invalidates every running
    # sandbox's proxy tokens immediately.  An accidental re-run (history
    # scroll-back, tmux paste) is unrecoverable, so require explicit
    # confirmation when there's something to actually rotate.  Skipped
    # when stdin isn't a tty (CI / non-interactive use), in which case
    # the operator passed the flag deliberately.
    if rotate and existing:
        import sys as _sys
        from datetime import datetime as _dt
        if _sys.stdin.isatty():
            console.print(
                "[yellow]⚠[/yellow] --rotate-tokens сделает недействительными токены "
                "во всех работающих средах Korra. До перезапуска запросы будут получать 401."
            )
            try:
                ans = input("Введите 'rotate' для подтверждения: ").strip().lower()
            except EOFError:
                ans = ""
            if ans != "rotate":
                console.print("[yellow]Отменено.[/yellow]")
                return 1
        # Backup the existing mappings before we overwrite.  The
        # resulting ``.rotated-<unix>`` sibling is plain JSON and lets
        # the operator manually recover tokens if they realise the
        # rotation was a mistake.
        try:
            import shutil as _shutil
            state_dir = ip._proxy_state_dir()
            mappings_src = state_dir / "mappings.json"
            if mappings_src.exists():
                ts = _dt.now().strftime("%Y%m%dT%H%M%S")
                backup = state_dir / f"mappings.json.rotated-{ts}"
                _shutil.copy2(str(mappings_src), str(backup))
                console.print(f"  [dim]резервная копия: {backup}[/dim]")
        except OSError as exc:
            console.print(
                f"  [yellow]Не удалось сохранить сопоставления перед заменой токенов: "
                f"{exc}[/yellow]"
            )
    elif rotate and not existing:
        console.print(
            "[dim]При первой настройке --rotate-tokens ничего не меняет: старых токенов ещё нет.[/dim]"
        )

    mappings = ip.merge_mappings(
        existing=existing,
        discovered=discovered,
        rotate=rotate,
    )

    if not mappings:
        console.print(
            "  [yellow]В окружении и Bitwarden не найдены известные ключи API провайдеров.[/yellow]"
        )
        console.print(
            "  Задайте хотя бы одну из переменных и повторите настройку:"
        )
        for env_name in sorted(ip._BEARER_PROVIDERS):
            console.print(f"    - {env_name}")
        return 1

    # Warn the operator about providers we recognize but can't proxy
    # (AWS Bedrock SigV4, GCP Vertex service-account OAuth).  These still
    # work — they just bypass the egress isolation.
    uncovered = ip.discover_uncovered_providers(
        available_env_names=available_env_names or None,
    )
    if uncovered:
        console.print()
        console.print(
            "  [yellow]⚠[/yellow] Найдены переменные провайдеров, которые прокси пока не защищает:"
        )
        for name in uncovered:
            console.print(f"    - {name}")
        console.print(
            "  [dim]Эти провайдеры используют подпись запросов или OAuth из SDK "
            "(SigV4, файлы сервисных аккаунтов), поэтому реальные данные входа "
            "останутся внутри среды. Для них изоляция исходящего трафика неполна.[/dim]"
        )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Переменная провайдера", style="cyan")
    table.add_column("Серверы назначения", style="dim")
    table.add_column("Токен прокси", style="green")
    for m in mappings:
        table.add_row(
            m.real_env_name,
            ", ".join(m.upstream_hosts),
            _redact_token(m.proxy_token),
        )
    console.print(table)

    # ------------------------------------------------------------------ write
    console.print()
    console.print("[bold]Шаг 4[/bold]  Сохранение настроек и сопоставлений")

    cfg = load_config()
    proxy_cfg = cfg.setdefault("proxy", {})
    # ``args.tunnel_port`` is None when the flag was not given; ``0`` is
    # invalid for a TCP listener so we treat it as an explicit refusal
    # and surface a clear error rather than silently substituting the
    # default.
    if args.tunnel_port is not None:
        if args.tunnel_port < 1 or args.tunnel_port > 65534:
            console.print(
                "  [red]✗ --tunnel-port должен быть от 1 до 65534; HTTP использует следующий порт.[/red]"
            )
            return 1
        tunnel_port = int(args.tunnel_port)
    else:
        tunnel_port = int(proxy_cfg.get("tunnel_port", ip._DEFAULT_TUNNEL_PORT))
    proxy_cfg["tunnel_port"] = tunnel_port

    extra_hosts = list(proxy_cfg.get("extra_allowed_hosts") or [])
    allowed = list(ip._DEFAULT_ALLOWED_HOSTS) + [
        h for h in extra_hosts if h not in ip._DEFAULT_ALLOWED_HOSTS
    ]

    audit_log_path = ip._proxy_state_dir() / "audit.log"
    # Pre-create the audit log with 0o600.  On the pinned v0.39 the
    # daemon does NOT write to this file (no ``log.audit_path`` field in
    # its config schema) — it's reserved for the v0.40+ upgrade where
    # per-request records start flowing.  Because the file is
    # non-load-bearing today, a pre-create failure (immutable parent,
    # pre-existing foreign-owned file, full disk) is a WARNING, not a
    # setup abort.
    audit_log_ok = True
    try:
        ip.ensure_audit_log(audit_log_path)
    except RuntimeError as exc:
        audit_log_ok = False
        console.print(f"  [yellow]⚠ {exc}[/yellow]")

    # Allow operator override of the deny list via
    # ``proxy.upstream_deny_cidrs`` — but the default (None) gives a safe
    # default-deny list (loopback, IMDS, RFC1918) that matches the docs
    # promise.
    deny_cidrs = proxy_cfg.get("upstream_deny_cidrs")
    iron_cfg = ip.build_proxy_config(
        mappings=mappings,
        ca_cert=ca_crt,
        ca_key=ca_key,
        tunnel_port=tunnel_port,
        audit_log=audit_log_path,
        allowed_hosts=allowed,
        upstream_deny_cidrs=deny_cidrs,
    )
    cfg_path = ip.write_proxy_config(iron_cfg)
    mappings_path = ip.write_mappings(mappings)
    # Mint (or keep) the management-API bearer key.  The generated config
    # enables a loopback management listener whose /v1/reload lets
    # `hermes egress reload` apply future ruleset changes without a
    # restart; the daemon requires the key env var to be non-empty at
    # startup, so make sure the token exists before first start.
    ip.ensure_management_token()
    console.print(f"  [green]✓[/green] настройки: {cfg_path}")
    console.print(f"  [green]✓[/green] сопоставления: {mappings_path}")
    if audit_log_ok:
        console.print(
            f"  [green]✓[/green] журнал аудита: {audit_log_path} "
            f"[dim](зарезервирован; iron-proxy v0.39 пишет запросы в iron-proxy.log)[/dim]"
        )

    # ------------------------------------------------------------------ enable
    proxy_cfg["enabled"] = True
    proxy_cfg.setdefault("auto_install", True)
    proxy_cfg.setdefault("enforce_on_docker", True)
    # CRITICAL: do NOT silently downgrade credential_source on re-run.
    # If the operator previously configured `bitwarden` mode (e.g. for
    # rotation), running `hermes egress setup` again WITHOUT
    # --from-bitwarden must not rewrite credential_source to "env" —
    # that silently breaks the Bitwarden rotation guarantee the docs
    # make.  Require an explicit --no-bitwarden to switch back.
    existing_source = proxy_cfg.get("credential_source")
    if args.from_bitwarden:
        proxy_cfg["credential_source"] = "bitwarden"
    elif getattr(args, "no_bitwarden", False):
        proxy_cfg["credential_source"] = "env"
        if existing_source == "bitwarden":
            console.print(
                "[yellow]credential_source переключён с bitwarden на env.[/yellow]"
            )
    elif existing_source == "bitwarden":
        # Preserve the existing bitwarden mode.  Surface the decision so
        # the operator knows we kept it.
        console.print(
            "[dim]Сохраняю credential_source=bitwarden из текущих настроек. "
            "Для перехода на переменные окружения укажите --no-bitwarden.[/dim]"
        )
    else:
        proxy_cfg["credential_source"] = "env"
    save_config(cfg)

    live_status = ip.get_status()
    was_running = live_status.pid is not None
    if was_running:
        ip.stop_proxy()

    # Decide whether to (re)start the daemon so the new config/tokens take
    # effect, rather than leaving the operator to remember a manual restart
    # (the #1 UX papercut for this feature).
    #   --restart      → always (re)start, even if nothing was running
    #   --no-restart   → never; leave it as-is and print the manual hint
    #   neither + tty  → ask (only when a daemon was running)
    #   neither + !tty → restart when a daemon was running; otherwise no-op
    #                    (first-time setup never auto-starts — matches the
    #                    "configured, now run start" flow)
    import sys as _sys
    restart_pref = getattr(args, "restart", None)
    if restart_pref is True:
        do_restart = True
    elif restart_pref is False:
        do_restart = False
    elif was_running:
        if _sys.stdin.isatty():
            try:
                ans = input(
                    "  Перезапустить работающий прокси с новыми настройками? [Д/н] "
                ).strip().lower()
            except EOFError:
                ans = ""
            do_restart = ans in ("", "y", "yes", "д", "да")
        else:
            do_restart = True
    else:
        do_restart = False

    if do_restart:
        try:
            new_status = ip.start_proxy(
                install_if_missing=bool(proxy_cfg.get("auto_install", True)),
            )
        except Exception as exc:  # noqa: BLE001 — user-facing funnel
            console.print(
                f"  [yellow]⚠ Не удалось запустить iron-proxy с новыми настройками: {exc}[/yellow]"
            )
            console.print(
                "  Перед запуском новых сред Docker выполните вручную "
                "[cyan]korra egress start[/cyan]."
            )
        else:
            listening = "принимает соединения" if new_status.listening else "ещё не принимает соединения"
            verb = "перезапущен" if was_running else "запущен"
            console.print(
                f"  [green]✓[/green] iron-proxy {verb} с новыми настройками "
                f"(pid={new_status.pid}, port={new_status.tunnel_port}, {listening})"
            )
    elif was_running:
        console.print(
            "  [yellow]⚠ Работающий iron-proxy остановлен: настройки или токены изменились. "
            "Перед запуском новых сред Docker выполните [cyan]korra egress restart[/cyan] "
            "или [cyan]korra egress start[/cyan].[/yellow]"
        )

    console.print()
    console.print(
        "[green]✓ iron-proxy настроен.[/green] "
        "Исходящий трафик изолированных сред будет проходить через него."
    )
    console.print(
        "  Запустить:     [cyan]korra egress start[/cyan]\n"
        "  Перезапустить: [cyan]korra egress restart[/cyan] после повторной настройки\n"
        "  Перечитать:    [cyan]korra egress reload[/cyan] без перезапуска\n"
        "  Состояние:     [cyan]korra egress status[/cyan]\n"
        "  Остановить:    [cyan]korra egress stop[/cyan]\n"
        "  Выключить:     [cyan]korra egress disable[/cyan]"
    )
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    proxy_cfg = cfg.get("proxy") or {}
    if not proxy_cfg.get("enabled"):
        console.print(
            "[yellow]proxy.enabled выключен. Сначала выполните `korra egress setup`.[/yellow]"
        )
        return 1

    # If the operator opted in to Bitwarden-rotation semantics, refresh
    # upstream secrets from BSM at startup.  This is what delivers the
    # rotation guarantee that distinguishes ``credential_source:
    # bitwarden`` from ``credential_source: env``.  Without it, rotating
    # a key in the Bitwarden web app doesn't reach the proxy.
    credential_source = proxy_cfg.get("credential_source", "env")
    bw_cfg = (cfg.get("secrets") or {}).get("bitwarden")
    refresh_bw = (
        credential_source == "bitwarden"
        and bw_cfg is not None
        and bool(bw_cfg.get("enabled"))
    )
    # Silent-degrade guard: the operator explicitly chose
    # ``credential_source: bitwarden``, but secrets.bitwarden has since
    # been disabled or removed.  Proceeding would quietly start on host
    # env — exactly the bug class the BW mode is meant to defeat.  Refuse
    # unless the documented escape hatch is set.
    if credential_source == "bitwarden" and not refresh_bw:
        if bool(proxy_cfg.get("allow_env_fallback", False)):
            console.print(
                "[yellow]⚠ credential_source=bitwarden, но secrets.bitwarden выключен или отсутствует. "
                "Используются секреты окружения (allow_env_fallback=true); новые ключи Bitwarden "
                "не будут подхвачены.[/yellow]"
            )
        else:
            console.print(
                "[red]✗ Запуск запрещён: proxy.credential_source равен 'bitwarden', "
                "но secrets.bitwarden выключен или отсутствует.[/red]"
            )
            console.print(
                "  Включите secrets.bitwarden.enabled: true, переключитесь на окружение "
                "командой `korra egress setup --no-bitwarden` или разрешите резервный "
                "вариант через proxy.allow_env_fallback: true."
            )
            return 1
    # Pass the proxy-side allow_env_fallback opt-in through to
    # start_proxy.  This is a deliberate, documented escape hatch: when
    # set, the daemon silently falls back to host env if BWS is
    # unreachable, instead of raising.  Default is strict (raise).
    if refresh_bw and bw_cfg is not None:
        bw_cfg = dict(bw_cfg)
        bw_cfg["allow_env_fallback"] = bool(
            proxy_cfg.get("allow_env_fallback", False)
        )

    # fail_on_uncovered_providers is intentionally gone: the LLM-specific
    # providers it guarded (Anthropic native, Azure OpenAI, Gemini) are now
    # swapped via per-provider match_headers rules, so the fail-closed tier
    # is empty and the flag would be a dead toggle.

    # stephenschoettler #1: when `credential_source: bitwarden`, the
    # operator picked BWS specifically to get the rotation guarantee —
    # silently falling back to parent-env at start_proxy time reintroduces
    # exactly the bug class the BW mode is supposed to defeat (host env
    # is stale / mismatched).  Pre-check at the wizard layer so we fail
    # loud with actionable error messages BEFORE start_proxy degrades.
    if refresh_bw:
        bw_access_env = (bw_cfg or {}).get("access_token_env", "BWS_ACCESS_TOKEN")
        if not os.environ.get(bw_access_env, "").strip():
            console.print(
                f"[red]✗ Запуск запрещён: credential_source=bitwarden, но "
                f"переменная {bw_access_env} не задана.[/red]"
            )
            console.print(
                "  Экспортируйте токен доступа или выполните "
                "`korra egress setup --no-bitwarden` для перехода на окружение."
            )
            return 1
        if not (bw_cfg or {}).get("project_id"):
            console.print(
                "[red]✗ Запуск запрещён: credential_source=bitwarden, но "
                "secrets.bitwarden.project_id пуст.[/red]"
            )
            console.print(
                "  Настройте проект командой `korra secrets bitwarden setup` или "
                "переключитесь обратно: `korra egress setup --no-bitwarden`."
            )
            return 1

    try:
        status = ip.start_proxy(
            install_if_missing=bool(proxy_cfg.get("auto_install", True)),
            refresh_secrets_from_bitwarden=refresh_bw,
            bitwarden_config=bw_cfg,
        )
    except Exception as exc:  # noqa: BLE001 — top-level user-facing funnel
        console.print(f"[red]✗ Не удалось запустить iron-proxy:[/red] {exc}")
        return 1
    if status.pid:
        listening = (
            "[green]принимает соединения[/green]"
            if status.listening
            else "[yellow]ещё не принимает соединения[/yellow]"
        )
        console.print(
            f"[green]✓[/green] iron-proxy работает  pid={status.pid}  "
            f"port={status.tunnel_port}  {listening}"
        )
    else:
        console.print("[red]✗ iron-proxy не запустился корректно[/red]")
        return 1
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    console = Console()
    if ip.stop_proxy():
        console.print("[green]✓[/green] iron-proxy остановлен")
    else:
        console.print("[dim]iron-proxy не был запущен[/dim]")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    """Stop the running daemon (if any) and start it with the current config.

    The one-command way to apply config changes (new allowlist hosts, rotated
    tokens, a Bitwarden key rotation) without making the operator remember the
    stop/start dance.  Delegates to ``cmd_start`` so all the credential-source
    guards run exactly as they do for ``start``.
    """
    console = Console()
    was_running = ip.stop_proxy()
    if was_running:
        console.print("[dim]Работающий iron-proxy остановлен[/dim]")
    return cmd_start(args)


def cmd_reload(args: argparse.Namespace) -> int:
    """Hot-reload the running daemon's ruleset via the management API.

    Applies allowlist / token / mapping changes already written to
    proxy.yaml WITHOUT restarting the daemon — no dropped connections, no
    restart window.  When the change involves new upstream SECRETS (a
    Bitwarden rotation, a newly added provider key), use
    ``hermes egress restart`` instead: the daemon reads real credentials
    from its own environment at spawn time, and a reload does not
    re-populate that env.
    """
    console = Console()
    try:
        ip.reload_proxy()
    except Exception as exc:  # noqa: BLE001 — top-level user-facing funnel
        console.print(f"[red]✗ Не удалось перечитать настройки:[/red] {exc}")
        return 1
    console.print(
        "[green]✓[/green] Правила iron-proxy перечитаны без перезапуска; "
        "соединения сохранены"
    )
    console.print(
        "[dim]Новые секреты назначения, заменённые ключи и новые провайдеры "
        "требуют `korra egress restart`: процесс читает данные входа при запуске.[/dim]"
    )
    return 0


def format_status_text(*, show_tokens: bool = False) -> str:
    """Plain-text egress status for slash commands, Dashboard, and Desktop."""
    cfg = load_config()
    proxy_cfg = cfg.get("proxy") or {}
    status = ip.get_status()

    def yn(value: bool) -> str:
        return "да" if value else "нет"

    lines = [
        "Состояние прокси исходящего трафика",
        "",
        f"Включён: {yn(bool(proxy_cfg.get('enabled')))}",
        f"Программа: {status.binary_path or '(не найдена)'}",
        f"Версия программы: {status.binary_version or '(неизвестна)'}",
        f"Настройки: {status.config_path or '(не созданы)'}",
        f"Сертификат CA: {status.ca_cert_path or '(не создан)'}",
        f"Порт туннеля: {status.tunnel_port}",
        f"Процесс: pid {status.pid}" if status.pid else "Процесс: остановлен",
        f"Принимает соединения: {yn(status.listening)}",
        f"Источник данных входа: {proxy_cfg.get('credential_source', 'env')}",
        f"Защита Docker: {yn(bool(proxy_cfg.get('enforce_on_docker', True)))}",
        "Область действия: в этой версии только среда Docker",
    ]

    mappings = ip.load_mappings()
    if mappings:
        lines.extend(["", "Сопоставления токенов:"])
        for m in mappings:
            tok = m.proxy_token if show_tokens else _redact_token(m.proxy_token)
            lines.append(f"  - {m.real_env_name}: {tok} ({', '.join(m.upstream_hosts)})")

    uncovered = ip.discover_uncovered_providers()
    if uncovered:
        lines.extend([
            "",
            "Незащищённые провайдеры; реальные данные входа видны внутри среды:",
        ])
        for name in uncovered:
            lines.append(f"  - {name}")

    if bool(proxy_cfg.get("enabled")) and not status.configured:
        lines.extend(["", "Далее выполните `korra egress setup`, чтобы создать токены и proxy.yaml."])
    elif bool(proxy_cfg.get("enabled")) and not (status.pid and status.listening):
        lines.extend(["", "Перед запуском сред Docker выполните `korra egress start`."])

    return "\n".join(lines)


def cmd_status(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    proxy_cfg = cfg.get("proxy") or {}
    status = ip.get_status()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("Включён", _yn(bool(proxy_cfg.get("enabled"))))
    table.add_row("Программа", str(status.binary_path or "[dim](не найдена)[/dim]"))
    table.add_row("Версия программы", status.binary_version or "[dim](неизвестна)[/dim]")
    table.add_row("Настройки", str(status.config_path or "[dim](не созданы)[/dim]"))
    table.add_row("Сертификат CA", str(status.ca_cert_path or "[dim](не создан)[/dim]"))
    table.add_row("Порт туннеля", str(status.tunnel_port))
    table.add_row("Процесс", f"pid {status.pid}" if status.pid else "[dim](остановлен)[/dim]")
    table.add_row("Принимает соединения", _yn(status.listening))
    table.add_row("Источник данных входа", str(proxy_cfg.get("credential_source", "env")))
    table.add_row("Защита Docker", _yn(bool(proxy_cfg.get("enforce_on_docker", True))))
    console.print(table)

    mappings = ip.load_mappings()
    if mappings:
        console.print()
        console.print("[bold]Сопоставления токенов[/bold]")
        m_table = Table(show_header=True, header_style="bold")
        m_table.add_column("Переменная", style="cyan")
        m_table.add_column("Назначение", style="dim")
        m_table.add_column("Токен прокси", style="green")
        for m in mappings:
            tok = m.proxy_token if args.show_tokens else _redact_token(m.proxy_token)
            m_table.add_row(m.real_env_name, ", ".join(m.upstream_hosts), tok)
        console.print(m_table)
        if args.show_tokens:
            console.print(
                "[yellow]⚠[/yellow] Токены прокси показаны полностью и могут остаться "
                "в истории терминала. После команды очистите историю."
            )

    # Surface uncovered providers so the operator knows the isolation
    # boundary is incomplete for those upstreams.
    uncovered = ip.discover_uncovered_providers()
    if uncovered:
        console.print()
        console.print(
            "[yellow]Незащищённые провайдеры[/yellow]; "
            "реальные данные входа видны внутри среды:"
        )
        for name in uncovered:
            console.print(f"  - {name}")

    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    console = Console()
    cfg = load_config()
    proxy_cfg = cfg.setdefault("proxy", {})
    if not proxy_cfg.get("enabled"):
        console.print("[dim]proxy.enabled уже выключен.[/dim]")
        return 0
    proxy_cfg["enabled"] = False
    save_config(cfg)
    console.print("[green]✓[/green] proxy.enabled выключен")
    # Use the public get_status() pid (which already incorporates the
    # _pid_alive check) instead of reaching into ip._read_pid().  That
    # private accessor only proves the pidfile is non-empty — a stale
    # pidfile from a crashed previous run would fire the warning
    # spuriously.
    if ip.get_status().pid is not None:
        console.print(
            "  iron-proxy продолжает работать. Чтобы остановить его, выполните "
            "[cyan]korra egress stop[/cyan]."
        )
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    console = Console()
    status = ip.get_status()
    if status.config_path is None:
        console.print(
            "[yellow](настройки не созданы; выполните `korra egress setup`)[/yellow]"
        )
        return 1
    console.print(str(status.config_path))
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_env_file_into_environ() -> int:
    """Backfill provider keys from ``~/.hermes/.env`` into ``os.environ``.

    ``hermes egress setup`` discovers providers by reading ``os.environ``, but
    many operators keep their keys ONLY in ``~/.hermes/.env`` (which the agent
    loads at runtime but which is NOT exported into an interactive shell).
    Without this, ``setup`` reports "no provider keys found" even though the
    keys plainly exist — a confusing first-run papercut.

    Only fills names that aren't already set in the process env (an exported
    value always wins), and only for known bearer-provider names so we don't
    slurp unrelated secrets into the process. Returns the count of names added.
    """
    try:
        from korra_cli.config import load_env
    except ImportError:
        return 0
    try:
        file_env = load_env()
    except Exception:  # noqa: BLE001 — best-effort convenience, never fatal
        return 0
    added = 0
    known = set(ip._BEARER_PROVIDERS) | set(ip._NON_BEARER_PROVIDERS)
    for name in known:
        if name in os.environ and os.environ[name].strip():
            continue
        val = (file_env.get(name) or "").strip()
        if val:
            os.environ[name] = val
            added += 1
    return added


def _yn(value: bool) -> str:
    return "[green]да[/green]" if value else "[dim]нет[/dim]"


def _redact_token(token: str) -> str:
    if len(token) < 16:
        return token
    return f"{token[:12]}…{token[-4:]}"
