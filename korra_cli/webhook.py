"""hermes webhook — manage dynamic webhook subscriptions from the CLI.

Usage:
    hermes webhook subscribe <name> [options]
    hermes webhook list
    hermes webhook remove <name>
    hermes webhook test <name> [--payload '{"key": "value"}']

Subscriptions persist to ~/.hermes/webhook_subscriptions.json and are
hot-reloaded by the webhook adapter without a gateway restart.
"""

import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Dict

from korra_constants import display_hermes_home
from utils import atomic_replace
from korra_cli.config import cfg_get


_SUBSCRIPTIONS_FILENAME = "webhook_subscriptions.json"
_SUBSCRIPTIONS_FILE_MODE = 0o600


def _hermes_home() -> Path:
    from korra_constants import get_hermes_home
    return get_hermes_home()


def _subscriptions_path() -> Path:
    return _hermes_home() / _SUBSCRIPTIONS_FILENAME


def _load_subscriptions() -> Dict[str, dict]:
    path = _subscriptions_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_subscriptions(subs: Dict[str, dict]) -> None:
    path = _subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # webhook_subscriptions.json contains per-route HMAC secrets — write
    # via tempfile + chmod 0o600 before the atomic rename so a permissive
    # umask cannot leave the secrets readable to other local users in the
    # window between create and rename.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(subs, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, _SUBSCRIPTIONS_FILE_MODE)
        atomic_replace(tmp_path, path)
        # Re-assert after rename in case the destination existed with a
        # broader mode and atomic_replace preserved it.
        os.chmod(path, _SUBSCRIPTIONS_FILE_MODE)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _get_webhook_config() -> dict:
    """Load webhook platform config. Returns {} if not configured."""
    try:
        from korra_cli.config import load_config
        cfg = load_config()
        return cfg_get(cfg, "platforms", "webhook", default={})
    except Exception:
        return {}


def _is_webhook_enabled() -> bool:
    return bool(_get_webhook_config().get("enabled"))


def _get_webhook_base_url() -> str:
    wh = _get_webhook_config().get("extra", {})
    host = wh.get("host")
    port = wh.get("port", 8644)
    display_host = "localhost" if not host or host in {"0.0.0.0", "::"} else host
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    return f"http://{display_host}:{port}"


def _setup_hint() -> str:
    _dhh = display_hermes_home()
    return f'\n  Вебхуки не включены. Настроить их можно так:\n\n  1. Запустите мастер подключения:\n     korra gateway setup\n\n  2. Или добавьте в {_dhh}/config.yaml:\n     platforms:\n       webhook:\n         enabled: true\n         extra:\n           port: 8644\n           secret: "ваш-секрет-hmac"\n\n  Затем запустите шлюз: korra gateway run\n'


def _require_webhook_enabled() -> bool:
    """Check webhook is enabled. Print setup guide and return False if not."""
    if _is_webhook_enabled():
        return True
    print(_setup_hint())
    return False


def webhook_command(args):
    """Entry point for 'hermes webhook' subcommand."""
    sub = getattr(args, "webhook_action", None)

    if not sub:
        print('Использование: korra webhook {subscribe|list|remove|test}')
        print("Подробности: 'korra webhook --help'.")
        return

    if not _require_webhook_enabled():
        return

    if sub in {"subscribe", "add"}:
        _cmd_subscribe(args)
    elif sub in {"list", "ls"}:
        _cmd_list(args)
    elif sub in {"remove", "rm"}:
        _cmd_remove(args)
    elif sub == "test":
        _cmd_test(args)


def _cmd_subscribe(args):
    name = args.name.strip().lower().replace(" ", "-")
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', name):
        print(f"Ошибка: неверное имя '{name}'. Используйте строчные латинские буквы, цифры, дефис или подчёркивание.")
        return

    subs = _load_subscriptions()
    is_update = name in subs

    secret = args.secret or secrets.token_urlsafe(32)
    events = [e.strip() for e in args.events.split(",")] if args.events else []

    route = {
        "description": args.description or f'Подписка, созданная агентом: {name}',
        "events": events,
        "secret": secret,
        "prompt": args.prompt or "",
        "skills": [s.strip() for s in args.skills.split(",")] if args.skills else [],
        "deliver": args.deliver or "log",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if getattr(args, "deliver_only", False):
        if route["deliver"] == "log":
            print(
                "Ошибка: --deliver-only требует получателя --deliver (telegram, discord, slack, github_comment и другие). Значение 'log' здесь не подходит."
            )
            return
        route["deliver_only"] = True

    script = getattr(args, "script", "") or ""
    if script.strip():
        route["script"] = script.strip()

    if args.deliver_chat_id:
        route["deliver_extra"] = {"chat_id": args.deliver_chat_id}

    subs[name] = route
    _save_subscriptions(subs)

    base_url = _get_webhook_base_url()
    status = 'Обновлена' if is_update else 'Создана'

    print(f'\n  {status} подписка на вебхук: {name}')
    print(f'  Адрес:  {base_url}/webhooks/{name}')
    print(f'  Секрет: {secret}')
    if events:
        print(f"  События: {', '.join(events)}")
    else:
        print('  События: все')
    print(f"  Доставка: {route['deliver']}")
    if route.get("deliver_only"):
        print('  Режим: прямая доставка без агента и расходов на модель')
    if route.get("prompt"):
        prompt_preview = route["prompt"][:80] + ("..." if len(route["prompt"]) > 80 else "")
        label = 'Сообщение' if route.get("deliver_only") else 'Запрос'
        print(f"  {label}: {prompt_preview}")
    if route.get("script"):
        print(f"  Скрипт: {route['script']}")
    print('\n  Настройте отправку POST-запросов из вашего сервиса на адрес выше.')
    print('  Используйте секрет для проверки подписи HMAC-SHA256.')
    print('  Для получения событий шлюз должен работать: korra gateway run.\n')


def _cmd_list(args):
    subs = _load_subscriptions()
    if not subs:
        print('  Созданных подписок на вебхуки нет.')
        print('  Создать: korra webhook subscribe <имя>')
        return

    base_url = _get_webhook_base_url()
    print(f'\n  Подписки на вебхуки ({len(subs)}):\n')
    for name, route in subs.items():
        events = ", ".join(route.get("events", [])) or "(все)"
        deliver = route.get("deliver", "log")
        if route.get("deliver_only"):
            deliver = f'{deliver} (напрямую, без агента)'
        desc = route.get("description", "")
        print(f"  ◆ {name}")
        if desc:
            print(f"    {desc}")
        print(f'    Адрес:    {base_url}/webhooks/{name}')
        print(f'    События:  {events}')
        print(f'    Доставка: {deliver}')
        if route.get("script"):
            print(f"    Скрипт:   {route['script']}")
        print()


def _cmd_remove(args):
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        print(f"  Подписка '{name}' не найдена.")
        print('  Маршруты из config.yaml нельзя удалить этой командой.')
        return

    del subs[name]
    _save_subscriptions(subs)
    print(f'  Подписка на вебхук удалена: {name}')


def _cmd_test(args):
    """Send a test POST to a webhook route."""
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        print(f"  Подписка '{name}' не найдена.")
        return

    route = subs[name]
    secret = route.get("secret", "")
    base_url = _get_webhook_base_url()
    url = f"{base_url}/webhooks/{name}"

    payload = args.payload or '{"test": true, "event_type": "test", "message": "Проверка вебхука Korra"}'

    import hmac
    import hashlib
    sig = "sha256=" + hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    print(f'  Отправляю тестовый POST-запрос на {url}')
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=payload.encode(),
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "test",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print(f'  Ответ ({resp.status}): {body}')
    except Exception as e:
        print(f'  Ошибка: {e}')
        print('  Проверьте, запущен ли шлюз: korra gateway run.')
