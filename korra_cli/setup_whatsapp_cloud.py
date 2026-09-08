"""
Interactive setup wizard for the WhatsApp Cloud API adapter.

Entry point: ``hermes whatsapp-cloud`` (dispatched from
``cmd_whatsapp_cloud`` in ``korra_cli/main.py``).

Walks the user through the 6 credentials Meta requires + recipient
allowlist, auto-generates the verify token, and prints exact follow-up
instructions for the parts that can't happen inside the wizard process
(starting cloudflared, starting the gateway, configuring Meta's
webhook dashboard, adding their phone to the recipient list).

Heavy emphasis on field-shape validation to catch the most common
configuration mistakes:

- Putting the actual phone number in ``WHATSAPP_CLOUD_PHONE_NUMBER_ID``
  (the field expects Meta's 15-17 digit internal ID, not a phone number).
  This is the #1 trap — caught us during Phase 3 live testing.
- Pasting tokens with trailing whitespace.
- Pasting an OpenAI / Slack / GitHub key by mistake.
- Confusing App ID with WABA ID with Phone Number ID.

Each prompt has contextual help showing exactly where to find the value
in Meta's App Dashboard, with a one-line description and the field's
expected shape ("starts with EAA", "15-17 digits", "32 hex chars", etc.).

The wizard intentionally does NOT smoke-test the webhook itself — the
Hermes gateway and the cloudflared tunnel both run in separate
processes the user starts AFTER this wizard exits, so any in-wizard
probe would fail by design. Instead the final SETUP COMPLETE block
prints the exact curl command the user can run from a third terminal
to verify the loop end-to-end once everything's running.
"""

from __future__ import annotations
from korra_cli.cli_output import line_input

import re
import secrets
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Field-shape validators
# ---------------------------------------------------------------------------
#
# Each validator returns (ok, reason_if_not_ok). The wizard uses them to
# reject obviously-malformed input before saving — saves users a round
# trip with Meta's 401 / 400 errors.


def _validate_phone_number_id(value: str) -> tuple[bool, Optional[str]]:
    """Phone Number ID is a 15-17 digit numeric ID assigned by Meta.

    It's NOT a phone number. The #1 setup mistake is pasting the actual
    phone number (e.g. ``15556422442``) into this field — that's only
    10-11 digits and gets rejected by Graph as "Object with ID does
    not exist."
    """
    if not value:
        return False, "Укажите Phone Number ID"
    s = value.strip()
    if not s.isdigit():
        return False, "Phone Number ID должен состоять из цифр без '+', пробелов и дефисов"
    # Real phone numbers are 10-11 digits (US/CA country code + area code
    # + 7 digits). Meta's internal IDs are 15-17 digits. If we see a
    # phone-number-sized value, the user almost certainly pasted the
    # phone number by mistake.
    if 10 <= len(s) <= 12:
        return False, (
            "Это похоже на номер телефона, но здесь нужен внутренний Phone Number ID Meta "
            "из 15–17 цифр, например '7794189252778687'. Он указан под полем 'From' "
            "на странице API Setup в строке 'Phone number ID'."
        )
    if len(s) < 13:
        return False, "Phone Number ID слишком короткий: ожидается 13–18 цифр"
    if len(s) > 20:
        return False, "Phone Number ID слишком длинный: ожидается 13–18 цифр"
    return True, None


def _validate_waba_id(value: str) -> tuple[bool, Optional[str]]:
    """WABA ID is numeric, similar length range as Phone Number ID."""
    if not value:
        return False, "Укажите WABA ID"
    s = value.strip()
    if not s.isdigit():
        return False, "WABA ID должен состоять из цифр"
    if len(s) < 10 or len(s) > 25:
        return False, "WABA ID имеет неверную длину: ожидается 10–25 цифр"
    return True, None


def _validate_app_id(value: str) -> tuple[bool, Optional[str]]:
    """Meta App ID is numeric, typically 15-16 digits."""
    if not value:
        return False, "Укажите App ID"
    s = value.strip()
    if not s.isdigit():
        return False, "App ID должен состоять из цифр"
    if len(s) < 13 or len(s) > 20:
        return False, "App ID имеет неверную длину: обычно это 15–16 цифр"
    return True, None


def _validate_app_secret(value: str) -> tuple[bool, Optional[str]]:
    """App Secret is a 32-character lowercase hex string."""
    if not value:
        return False, "Укажите App Secret"
    s = value.strip()
    if not re.fullmatch(r"[0-9a-f]+", s.lower()):
        return False, (
            "App Secret должен быть шестнадцатеричной строкой из цифр 0–9 и букв a–f. "
            "Скопируйте поле 'App secret' из Settings → Basic, а не другой токен."
        )
    if len(s) != 32:
        return False, f"App Secret должен содержать ровно 32 шестнадцатеричных символа; получено {len(s)}"
    return True, None


def _validate_access_token(value: str) -> tuple[bool, Optional[str]]:
    """Meta access tokens start with ``EAA`` and are 100-300+ characters.

    Both temp tokens (24h) and System User permanent tokens share this
    prefix. We don't try to distinguish them.
    """
    if not value:
        return False, "Укажите токен доступа"
    s = value.strip()
    if not s.startswith("EAA"):
        # Diagnose common paste mistakes
        if s.startswith("sk-"):
            return False, (
                "Это ключ OpenAI с префиксом 'sk-', а нужен токен WhatsApp Meta. "
                "Токены Meta начинаются с 'EAA'."
            )
        if s.startswith("xoxb-") or s.startswith("xoxp-"):
            return False, (
                "Это токен Slack, а нужен токен WhatsApp Meta. "
                "Токены Meta начинаются с 'EAA'."
            )
        if s.startswith("ghp_") or s.startswith("gho_"):
            return False, (
                "Это токен GitHub, а нужен токен WhatsApp Meta. "
                "Токены Meta начинаются с 'EAA'."
            )
        return False, (
            "Токены WhatsApp Meta начинаются с 'EAA'. Скопируйте токен из API Setup → "
            "'Generate access token' или постоянный токен из Business Settings → "
            "System Users → 'Generate token'."
        )
    if len(s) < 100:
        return False, f"Токен доступа слишком короткий: {len(s)} символов, ожидается не менее 100"
    return True, None


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _prompt(message: str, default: Optional[str] = None, secret: bool = False) -> str:
    """Read one line of input. Returns "" on EOF / Ctrl+C / empty input.

    The ``default`` parameter is shown to the user but NOT auto-applied
    on empty input — callers handle the "user kept existing" case
    explicitly so they can distinguish between a real value and a
    display preview (e.g. ``"abc12345..."`` for masked secrets).

    ``secret=True`` reads via ``getpass`` so credentials are not echoed
    to the terminal (or left in scrollback).
    """
    try:
        suffix = f" [{default}]" if default else ""
        if secret and sys.stdin.isatty():
            import getpass

            raw = getpass.getpass(f"{message}{suffix} (ввод скрыт): ").strip()
        else:
            raw = line_input(f"{message}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return raw


def _prompt_validated(
    message: str,
    validator,
    *,
    current: Optional[str] = None,
    help_text: Optional[str] = None,
    secret: bool = False,
) -> Optional[str]:
    """Repeat the prompt until the user enters a valid value or aborts.

    Returns the validated value, or None if the user gave up (empty
    response after an error, or Ctrl+C). ``current`` is shown as a
    default for re-runs of the wizard with existing config.
    """
    if help_text:
        for line in help_text.strip().splitlines():
            print(f"  {line}")
    attempts = 0
    while True:
        attempts += 1
        value = _prompt(f"  → {message}", default=current, secret=secret)
        if not value:
            return None
        ok, reason = validator(value)
        if ok:
            return value.strip()
        print(f"    ✗ {reason}")
        if attempts >= 3:
            try:
                cont = input("    Попробуйте ещё раз или нажмите Enter, чтобы пропустить: ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if not cont:
                return None
            attempts = 0


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


def run_whatsapp_cloud_setup() -> int:
    """Interactive wizard for the WhatsApp Cloud API adapter.

    Returns 0 on full success, 1 on user abort, 2 on partial completion
    (some fields written but the user bailed before finishing).
    """
    from korra_cli.config import get_env_value, save_env_value

    print()
    print("⚕ Настройка WhatsApp Business Cloud API")
    print("=" * 50)
    print()
    print("Этот мастер подключит Korra к официальному Cloud API Meta")
    print("для надёжной работы с WhatsApp:")
    print()
    print("  • без QR-кодов и отдельного процесса Node.js")
    print("  • стабильное подключение без риска блокировки аккаунта")
    print("  • нужен бизнес-аккаунт, личный WhatsApp не подходит")
    print("  • нужен публичный адрес вебхука: Cloudflare Tunnel, ngrok")
    print("    или собственный обратный прокси с TLS")
    print()
    print("Если приложение Meta ещё не настроено, сначала выполните эти шаги,")
    print("а затем снова запустите мастер:")
    print()
    print("  1. https://developers.facebook.com/apps → Create App")
    print("     → 'Connect with customers through WhatsApp'")
    print("  2. App Dashboard → WhatsApp → API Setup")
    print("  3. Нажмите 'Generate access token'. Для начала подойдёт временный")
    print("     токен на 24 часа; позже замените его постоянным System User token.")
    print()
    try:
        proceed = input("Нажмите Enter для продолжения или Ctrl+C для отмены… ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nНастройка отменена.")
        return 1

    print()
    print("─" * 50)
    print("ШАГ 1 — Phone Number ID")
    print("─" * 50)
    current_phone_id = get_env_value("WHATSAPP_CLOUD_PHONE_NUMBER_ID") or None
    phone_id = _prompt_validated(
        "Phone Number ID",
        _validate_phone_number_id,
        current=current_phone_id,
        help_text=(
            "Откройте App Dashboard → WhatsApp → API Setup →\n"
            "'Send and receive messages'. Под списком 'From' найдите строку\n"
            "'Phone number ID' со значением из 15–17 цифр, например\n"
            "'7794189252778687'. Это не сам номер телефона (+1 555-…)."
        ),
    )
    if not phone_id:
        if current_phone_id:
            phone_id = current_phone_id
            print(f"  ✓ Сохранено прежнее значение: {phone_id}")
        else:
            print("\n✗ Phone Number ID обязателен. Настройка прервана.")
            return 1
    else:
        save_env_value("WHATSAPP_CLOUD_PHONE_NUMBER_ID", phone_id)
        print(f"  ✓ Сохранено: {phone_id}")
    print()

    print("─" * 50)
    print("ШАГ 2 — Токен доступа")
    print("─" * 50)
    current_token = get_env_value("WHATSAPP_CLOUD_ACCESS_TOKEN") or None
    current_display = (current_token[:15] + "...") if current_token else None
    token = _prompt_validated(
        "Токен доступа",
        _validate_access_token,
        current=current_display,
        secret=True,
        help_text=(
            "Получить токен можно двумя способами:\n\n"
            "  (а) ВРЕМЕННЫЙ — App Dashboard → WhatsApp → API Setup →\n"
            "      'Generate access token'. Действует 24 часа и подходит\n"
            "      для первой проверки.\n\n"
            "  (б) ПОСТОЯННЫЙ — токен System User:\n"
            "      • business.facebook.com → Settings → System users →\n"
            "        Add → Admin role\n"
            "      • Assign Assets → ваше приложение (Manage app) и\n"
            "        аккаунт WhatsApp (Manage WABAs)\n"
            "      • Generate token → expiration: Never → permissions:\n"
            "        business_management, whatsapp_business_messaging,\n"
            "        whatsapp_business_management\n\n"
            "Токены начинаются с 'EAA'."
        ),
    )
    # If they had a current token and just hit Enter, keep it.
    if not token:
        if current_token:
            token = current_token
            print("  ✓ Сохранён прежний токен")
        else:
            print("\n✗ Токен доступа обязателен. Настройка прервана.")
            return 1
    else:
        save_env_value("WHATSAPP_CLOUD_ACCESS_TOKEN", token)
        print("  ✓ Сохранено; токен скрыт")
    print()

    print("─" * 50)
    print("ШАГ 3 — App Secret для проверки подписи вебхука")
    print("─" * 50)
    current_secret = get_env_value("WHATSAPP_CLOUD_APP_SECRET") or None
    current_secret_display = (current_secret[:8] + "...") if current_secret else None
    app_secret = _prompt_validated(
        "App Secret",
        _validate_app_secret,
        current=current_secret_display,
        secret=True,
        help_text=(
            "Откройте App Dashboard → Settings → Basic → поле 'App secret'.\n"
            "Нажмите 'Show' и введите пароль Facebook. Если кнопки нет,\n"
            "проверьте, есть ли у вас роль Admin приложения. Значение состоит\n"
            "из 32 шестнадцатеричных символов. Без App Secret входящие\n"
            "POST-запросы вебхука будут отклоняться с HTTP 503."
        ),
    )
    if not app_secret:
        if current_secret:
            app_secret = current_secret
            print("  ✓ Сохранён прежний App Secret")
        else:
            print("\n⚠ App Secret пропущен: входящие вебхуки будут отклоняться,")
            print("   пока вы не зададите WHATSAPP_CLOUD_APP_SECRET вручную.")
    else:
        save_env_value("WHATSAPP_CLOUD_APP_SECRET", app_secret)
        print("  ✓ Сохранено; секрет скрыт")
    print()

    print("─" * 50)
    print("ШАГ 4 — App ID и WABA ID, необязательно, для аналитики")
    print("─" * 50)
    current_app_id = get_env_value("WHATSAPP_CLOUD_APP_ID") or None
    app_id = _prompt_validated(
        "App ID (необязательно; Enter — пропустить)",
        lambda v: (True, None) if not v else _validate_app_id(v),
        current=current_app_id,
        help_text=(
            "App ID указан вверху страницы App Dashboard → Settings → Basic.\n"
            "Обычно это 15–16 цифр. Для сообщений не нужен, но пригодится для аналитики."
        ),
    )
    if app_id:
        save_env_value("WHATSAPP_CLOUD_APP_ID", app_id)
        print(f"  ✓ Сохранено: {app_id}")
    elif current_app_id:
        print(f"  ✓ Сохранено прежнее значение: {current_app_id}")

    current_waba_id = get_env_value("WHATSAPP_CLOUD_WABA_ID") or None
    waba_id = _prompt_validated(
        "WABA ID (необязательно; Enter — пропустить)",
        lambda v: (True, None) if not v else _validate_waba_id(v),
        current=current_waba_id,
        help_text=(
            "WhatsApp Business Account ID указан вверху страницы App Dashboard →\n"
            "WhatsApp → API Setup в поле 'WhatsApp Business Account ID'.\n"
            "Это число примерно из 15 цифр. Для сообщений не нужен."
        ),
    )
    if waba_id:
        save_env_value("WHATSAPP_CLOUD_WABA_ID", waba_id)
        print(f"  ✓ Сохранено: {waba_id}")
    elif current_waba_id:
        print(f"  ✓ Сохранено прежнее значение: {current_waba_id}")
    print()

    print("─" * 50)
    print("ШАГ 5 — Проверочный токен, создаётся автоматически")
    print("─" * 50)
    current_verify = get_env_value("WHATSAPP_CLOUD_VERIFY_TOKEN") or None
    if current_verify:
        print(f"  Уже задан проверочный токен ({current_verify[:8]}…).")
        try:
            regen = input("  Создать новый? [д/Н]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            regen = "n"
        if regen in {"y", "yes", "д", "да"}:
            verify_token = secrets.token_urlsafe(32)
            save_env_value("WHATSAPP_CLOUD_VERIFY_TOKEN", verify_token)
            print(f"  ✓ Новый проверочный токен: {verify_token}")
        else:
            verify_token = current_verify
            print("  ✓ Сохранён прежний проверочный токен")
    else:
        verify_token = secrets.token_urlsafe(32)
        save_env_value("WHATSAPP_CLOUD_VERIFY_TOKEN", verify_token)
        print(f"  ✓ Создан: {verify_token}")
    print()
    print("  → СКОПИРУЙТЕ ТОКЕН СЕЙЧАС. Он понадобится в настройках")
    print("    вебхука Meta на следующем шаге.")
    print()

    print("─" * 50)
    print("ШАГ 6 — Список разрешённых получателей")
    print("─" * 50)
    print()
    print("  Кто может писать боту? Укажите через запятую номера с кодом страны")
    print("  без '+', пробелов и дефисов. '*' разрешает сообщения от всех;")
    print("  используйте его только со списком получателей Meta в режиме разработки.")
    print()
    current_allow = get_env_value("WHATSAPP_CLOUD_ALLOWED_USERS") or None
    allow_default = current_allow if current_allow else None
    try:
        allowed = line_input(
            f"  → Разрешённые пользователи{' [' + allow_default + ']' if allow_default else ''}: "
        ).strip() or (allow_default or "")
    except (EOFError, KeyboardInterrupt):
        allowed = ""
    if allowed:
        # Light normalization — strip spaces and dashes from each entry.
        allowed = ",".join(
            re.sub(r"[\s\-+]", "", part) for part in allowed.split(",") if part.strip()
        )
        save_env_value("WHATSAPP_CLOUD_ALLOWED_USERS", allowed)
        print(f"  ✓ Сохранено: {allowed}")
    else:
        print("  ⚠ Список пуст: все входящие сообщения будут отклоняться.")
        print("    Запустите мастер снова или задайте WHATSAPP_CLOUD_ALLOWED_USERS вручную.")
    print()

    print("─" * 50)
    print("НАСТРОЙКА ЗАВЕРШЕНА — следующие шаги")
    print("─" * 50)
    print()
    print("  Для сообщений WhatsApp Korra нужен публичный HTTPS-адрес.")
    print("  Рекомендуем Cloudflare Tunnel: бесплатно, без проброса портов")
    print("  и отдельной настройки DNS.")
    print()
    print("    1. Один раз установите cloudflared:")
    print("         Windows:  winget install Cloudflare.cloudflared")
    print("         macOS:    brew install cloudflared")
    print("         Linux:    https://github.com/cloudflare/cloudflared/releases")
    print()
    print("       Альтернативы: ngrok или собственный домен с обратным прокси и TLS.")
    print()
    print("    2. Запустите туннель в отдельном терминале:")
    print("         cloudflared tunnel --url http://localhost:8090")
    print("       Запишите выданный адрес https://<случайное-имя>.trycloudflare.com.")
    print()
    print("    3. В другом терминале запустите шлюз Korra:")
    print("         korra gateway")
    print()
    print("    4. В третьем терминале проверьте доступность, подставив адрес туннеля:")
    print()
    print("         curl 'https://YOUR-TUNNEL.trycloudflare.com/whatsapp/webhook?\\")
    print(f"               hub.mode=subscribe&hub.verify_token={verify_token}&\\")
    print("               hub.challenge=hello'")
    print()
    print("       Ожидается HTTP 200 с текстом 'hello'.")
    print("       Также выполните: curl https://YOUR-TUNNEL.trycloudflare.com/health")
    print("       В JSON должно быть verify_token_configured: true.")
    print()
    print("    5. Укажите туннель в Meta:")
    print("         App Dashboard → WhatsApp → Configuration → Edit webhook")
    print("         Callback URL: <tunnel-url>/whatsapp/webhook")
    print(f"         Verify Token: {verify_token}")
    print("         → нажмите 'Verify and save'")
    print("         → затем 'Manage' → подпишитесь на поле 'messages'")
    print()
    print("    6. Добавьте свой телефон в список получателей Meta:")
    print("         App Dashboard → WhatsApp → API Setup → 'To' →")
    print("         'Manage phone number list'")
    print()
    print("    7. Отправьте личное сообщение на тестовый номер бота.")
    print()
    print("─" * 50)
    print("Необязательно: оформите профиль бота WhatsApp")
    print("─" * 50)
    print()
    print("  Имя и фото бота видны в заголовках чатов и списке контактов.")
    print("  Они настраиваются в Meta Business Manager, а не в этом мастере:")
    print()
    effective_waba = waba_id or current_waba_id
    if effective_waba:
        print("    • Имя и фото профиля:")
        print("        https://business.facebook.com/wa/manage/phone-numbers/"
              f"?waba_id={effective_waba}")
    else:
        print("    • Имя и фото профиля:")
        print("        https://business.facebook.com/wa/manage/phone-numbers/")
        print("        (выберите на странице свой WhatsApp Business Account)")
    print("        Изменения имени проходят проверку Meta примерно 24–48 часов.")
    print()
    print("    • Сведения, описание, сайт, часы работы и категория:")
    print("        на этой же странице нажмите номер телефона → 'Edit profile'.")
    print()
    print("    • Зелёная отметка подтверждённого бизнеса:")
    print("        пройдите проверку Meta в Business Manager → Security Center →")
    print("        Start Verification.")
    print()
    print("  Документация: https://hermes-agent.nousresearch.com/docs/user-guide/")
    print("        messaging/whatsapp-cloud")
    print()
    return 0
