"""
CLI commands for the DM pairing system.

Usage:
    hermes pairing list              # Show all pending + approved users
    hermes pairing approve <platform> <request-id|code>  # Approve a pairing request
    hermes pairing revoke <platform> <user_id> # Revoke user access
    hermes pairing clear-pending     # Clear all expired/pending codes
"""

def pairing_command(args):
    """Handle hermes pairing subcommands."""
    from gateway.pairing import PairingStore

    store = PairingStore()
    action = getattr(args, "pairing_action", None)

    if action == "list":
        _cmd_list(store)
    elif action == "approve":
        _cmd_approve(store, args.platform, args.code)
    elif action == "revoke":
        _cmd_revoke(store, args.platform, args.user_id)
    elif action == "clear-pending":
        _cmd_clear_pending(store)
    else:
        print('Использование: korra pairing {list|approve|revoke|clear-pending}')
        print("Подробности: 'korra pairing --help'.")


def _cmd_list(store):
    """List all pending and approved users."""
    pending = store.list_pending()
    approved = store.list_approved()

    if not pending and not approved:
        print('Запросов на подключение пока не было.')
        return

    if pending:
        print(f'\n  Запросы на подключение ({len(pending)}):')
        print(f"  {'Платформа':<12} {'ID запроса':<18} {'ID пользователя':<20} {'Имя':<20} {'Ожидание'}")
        print(f"  {'--------':<12} {'----------':<18} {'-------':<20} {'----':<20} {'---'}")
        for p in pending:
            print(
                f"  {p['platform']:<12} {p.get('request_id') or '-':<18} {p['user_id']:<20} {p.get('user_name') or '':<20} {p['age_minutes']} мин назад"
            )
        print('\n  Подтвердить: korra pairing approve <платформа> <ID-запроса>')
        print('  Также подходит код, который бот отправил пользователю в личном сообщении.')
    else:
        print('\n  Нет запросов на подключение.')

    if approved:
        print(f'\n  Пользователи с доступом ({len(approved)}):')
        print(f"  {'Платформа':<12} {'ID пользователя':<20} {'Имя':<20}")
        print(f"  {'--------':<12} {'-------':<20} {'----':<20}")
        for a in approved:
            print(f"  {a['platform']:<12} {a['user_id']:<20} {(a.get('user_name') or ''):<20}")
    else:
        print('\n  Пользователей с доступом пока нет.')

    print()


def _cmd_approve(store, platform: str, code: str):
    """Approve a pairing request id (from ``pairing list``) or a DM'd code."""
    platform = platform.lower().strip()
    code = code.strip()

    if store.looks_like_request_id(code):
        result = store.approve_request(platform, code)
    else:
        result = store.approve_code(platform, code.upper())
    if result:
        uid = result["user_id"]
        name = result.get("user_name") or ""
        display = f"{name} ({uid})" if name else uid
        print(f'\n  Подтверждено! Пользователь {display} в {platform} получил доступ к боту.')
        print('  Бот узнает его при следующем сообщении.\n')
    elif store._is_locked_out(platform):
        # Disambiguate: approve_code returns None for both invalid codes
        # and lockout. Tell the operator it's lockout so they don't chase
        # a "wrong code" rabbit hole (#10195).
        import time as _time
        limits = store._load_json(store._rate_limit_path())
        lockout_until = limits.get(f"_lockout:{platform}", 0)
        remaining = max(0, int(lockout_until - _time.time()))
        mins = remaining // 60
        print(
            f"\n  Подтверждения в '{platform}' временно заблокированы после слишком большого числа неудачных попыток."
        )
        print(f'  Блокировка снимется примерно через {mins} мин.')
        print(
            "  Для досрочного сброса удалите запись '_lockout:{0}' из platforms/pairing/_rate_limits.json вашего профиля Korra.\n".format(platform)
        )
    else:
        print(f"\n  Запрос или код '{code}' для '{platform}' не найден либо срок его действия истёк.")
        print("  Список запросов: 'korra pairing list'.\n")


def _cmd_revoke(store, platform: str, user_id: str):
    """Revoke a user's access."""
    platform = platform.lower().strip()

    if store.revoke(platform, user_id):
        print(f'\n  Доступ пользователя {user_id} в {platform} отозван.\n')
    else:
        print(f'\n  Пользователь {user_id} не найден в списке доступа {platform}.\n')


def _cmd_clear_pending(store):
    """Clear all pending pairing codes."""
    count = store.clear_pending()
    if count:
        print(f'\n  Удалено запросов на подключение: {count}.\n')
    else:
        print('\n  Нет запросов для удаления.\n')
