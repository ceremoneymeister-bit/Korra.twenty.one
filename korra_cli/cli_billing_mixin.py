"""Billing and subscription handlers for the interactive CLI (god-file decomposition).

This module hosts the Nous billing/subscription methods lifted out of
``cli.py``'s ``HermesCLI`` class. ``HermesCLI`` inherits
``CLIBillingMixin`` so every ``self.<handler>`` call resolves unchanged
via the MRO — behavior-neutral apart from focused billing fixes.

Import discipline mirrors ``korra_cli.cli_commands_mixin``:
  * Neutral, non-cyclic dependencies are imported at module top level below.
  * cli.py-internal symbols (the ``_cprint``/``_b``/``_d`` helpers and
    display constants) are imported LAZILY inside each method via
    ``from cli import ...``. The mixin never imports ``cli`` at module load
    time, avoiding the cycle created when ``cli.py`` imports this mixin.
"""

from __future__ import annotations



class CLIBillingMixin:
    """Mixin holding interactive-CLI billing and subscription handlers."""

    def _print_nous_credits_block(self) -> bool:
        """Print the Nous dollar balance block (two-bar view) when a Nous account
        is logged in. Returns True if it printed anything.

        Prefers the shared dollar usage model (``agent.billing_usage`` — two-bar
        plan/top-up view, dollars-only, the /usage + /subscription source of
        truth). Falls back to the legacy ``nous_credits_lines`` text only when the
        model is unavailable. Agent-independent (a portal fetch gated on "a Nous
        account is logged in"), so /usage shows the block even in the TUI
        slash-worker subprocess that resumes WITHOUT a live agent. Fail-open and
        wall-clock-bounded; honors HERMES_DEV_CREDITS_FIXTURE for offline testing.
        """
        from cli import _cprint, _b, _d

        try:
            from agent.billing_usage import build_usage_model, format_renews

            usage = build_usage_model()
        except Exception:
            usage = None
            format_renews = None  # type: ignore

        if usage is not None and usage.available and format_renews is not None:
            printed_any = False
            plan = usage.plan_name or ("Free" if usage.status == "free" else None)
            renews_display = getattr(usage, "renews_display", None) or format_renews(usage.renews_at)
            renews = f' · продление {renews_display}' if renews_display else ""
            if plan:
                print()
                _cprint(f"  {_b(f'Тариф: {plan}{renews}')}")
                printed_any = True

            # All lines below go through _cprint (same renderer as the Plan line) so
            # ordering is deterministic: raw print() and _cprint() flush to different
            # buffers under patch_stdout and interleave nondeterministically (the bar
            # would race above/below the Plan line across states). Keep one path.
            for _bar_ln in self._usage_bar_lines(usage, usage.plan_name):
                _cprint(_bar_ln)
                printed_any = True
            if usage.has_topup and usage.total_spendable_usd is not None:
                _cprint(f'  Всего доступно: ${usage.total_spendable_usd:,.2f}')

            if usage.status == "free":
                _cprint(f"  {_d('> Бесплатный тариф: только бесплатные модели. Платные модели можно подключить через /subscription.')}")
                printed_any = True
            elif usage.status == "low":
                _amt = f"${usage.total_spendable_usd:,.2f}" if usage.total_spendable_usd is not None else 'меньше $5'
                _low = f'! Мало средств · {_amt} осталось. Выполните /topup или /subscription.'
                _cprint(f"  {_low}")
                printed_any = True

            if printed_any:
                return True

        # Fallback: legacy text lines (only when the model is unavailable).
        from agent.account_usage import nous_credits_lines

        lines = nous_credits_lines()
        if not lines:
            return False
        print()
        for line in lines:
            print(f"  {line}")
        return True

    def _print_usage_cta(self) -> None:
        """Print the `/usage` call-to-action pointing at /subscription + /topup.

        Mirrors the TUI's ``USAGE_CTA`` (``session.ts``) so every surface ends a
        usage read with the same nudge. Only called when a Nous account is logged
        in (the balance block printed), since both commands are Nous-account only.
        """
        from cli import _cprint, _d

        _cprint(f"  {_d('Изменить тариф — /subscription · пополнить баланс — /topup')}")

    # ------------------------------------------------------------------
    # /subscription — view plan + change it in the browser (CLI surface)
    # ------------------------------------------------------------------

    def _show_subscription(self):
        """`/subscription` (alias `/upgrade`) — view the Nous plan + browser hand-off.

        The CLI mirror of the TUI ``SubscriptionOverlay``: a read of the current
        plan, this cycle's subscription credits, renewal date, and the plans you
        could switch to — then a deep-link to NAS's own ``/manage-subscription``
        page (NOT the Stripe portal; that page routes upgrade→Checkout /
        downgrade→scheduled internally). The terminal NEVER charges for a
        subscription. Fail-open: logged-out / portal hiccup degrades to a clear
        message, never a crash. Mirrors ``_show_billing``
        discipline for the interactive-vs-text split.
        """
        from cli import _cprint, _b, _d

        from agent.subscription_view import build_subscription_state, subscription_manage_url

        state = build_subscription_state()

        if not state.logged_in:
            print()
            if state.error:
                _cprint(f"  💳 {_d(f'Не удалось загрузить подписку: {state.error}')}")
            else:
                _cprint(f"  💳 {_d('Вход в Nous Portal не выполнен.')}")
                print('  Войдите через `korra portal`, затем выполните /subscription.')
            return

        # Team context: no personal plan — teams run on a shared balance.
        if state.context == "team":
            print()
            _cprint(f"  ⚕ {_b('Подписка команды')}")
            print(f"  {'─' * 41}")
            if state.org_name:
                role = {"Owner": "Владелец", "Admin": "Администратор", "Member": "Участник"}.get((state.role or "").title(), state.role or "")
                _org_line = f"Организация: {state.org_name}{(f' · {role}' if role else '')}"
                _cprint(f"  {_d(_org_line)}")
            org = state.org_name or 'организации вашей команды'
            print(f'  Этот терминал подключён к {org}. У команды общий')
            print('  баланс. Для пополнения используйте /topup.')
            _cprint(f"  {_d('Личная подписка доступна в вашей личной учётной записи.')}")
            return

        self._subscription_overview(state, subscription_manage_url(state))

    def _subscription_overview(self, state, manage_url):
        """Print the plan read block, then the browser hand-off to manage it.

        Dollars-only (no "credits") — mirrors the TUI overlay: a status line, the
        shared two-bar dollar usage view (plan + top-up) with the plan name on
        the bar, and state-matched free/low nudges. No in-terminal tier picker —
        the only action is managing the subscription on the portal.
        """
        from cli import _cprint, _b, _d

        # Shared dollar usage model (the only source with top-up dollars).
        from agent.billing_usage import format_renews
        try:
            from agent.billing_usage import build_usage_model

            usage = build_usage_model()
        except Exception:
            usage = None

        c = state.current
        is_free = not (c and c.tier_id)
        can_change = state.can_change_plan

        plan_name = (c.tier_name or c.tier_id) if c else (usage.plan_name if usage else None)
        u_status = getattr(usage, "status", None) if usage else None
        view_only = not can_change
        renews_display = getattr(usage, "renews_display", None) if usage else None
        if not renews_display and c and c.cycle_ends_at:
            renews_display = format_renews(c.cycle_ends_at)

        # Status line — dollars-only, with a "→ Plus" echo of a pending change so
        # the headline itself carries a scheduled downgrade/cancellation.
        _flip = ""
        if c and c.cancel_at_period_end:
            _flip = ' → будет отменён'
        elif c and c.pending_downgrade_tier_name:
            _flip = f" → {c.pending_downgrade_tier_name}"
        if not plan_name:
            status = 'Тариф: Бесплатный · только бесплатные модели'
        elif usage is not None and u_status == "low" and usage.total_spendable_usd is not None:
            _tot = f"${usage.total_spendable_usd:,.2f}"
            status = f'Тариф: {plan_name}{_flip} · {_tot} осталось'
        else:
            _spend = getattr(usage, "total_spendable_usd", None) if usage else None
            _left = f' · ${_spend:,.2f} осталось' if _spend is not None else ""
            _tail = ' · только просмотр' if view_only else (f' · продление {renews_display}' if renews_display else "")
            status = f'Тариф: {plan_name}{_flip}{_left}{_tail}'

        # Lead with the scheduled change (cancel > downgrade) so it can't read as
        # "nothing happened" — mirrors the TUI banner. All-`_cprint` (blanks
        # included) so the block orders deterministically even when piped.
        _trans = None
        if c and c.cancel_at_period_end:
            _when = format_renews(c.cancellation_effective_at) or 'конца оплаченного периода'
            _trans = ((c.tier_name or 'ваш тариф'), "отмена", _when)
        elif c and c.pending_downgrade_tier_name:
            _when = format_renews(c.pending_downgrade_at) or 'конца периода'
            _trans = ((c.tier_name or 'ваш тариф'), c.pending_downgrade_tier_name, _when)
        _cprint("")
        if _trans:
            _from, _to, _when = _trans
            _cprint(f"  ⏳ {_b('Запланированное изменение')}")
            _cprint(f"  {_from} ──▶ {_to}  {_d('· ' + _when)}")
            _cprint(f"  {_d(f'До этого момента у вас остаётся {_from} вместе с балансом тарифа.')}")
            _cprint("")

        _cprint(f"  ⚕ {_b(status)}")
        print(f"  {'─' * 41}")

        # Two-bar dollar usage view — plan name labels the plan bar.
        for _bar_ln in self._usage_bar_lines(usage, plan_name):
            print(_bar_ln)
        if usage and getattr(usage, "has_topup", False) and getattr(usage, "total_spendable_usd", None) is not None:
            print(f'  Всего доступно: ${usage.total_spendable_usd:,.2f}')

        # State-matched nudge (free upsell / low alert; healthy stays silent).
        if is_free:
            _cprint(f"  {_d('> Для платных моделей нужна подписка. Подключите её, чтобы получить доступ.')}")
        elif u_status == "low":
            _amt = f"${usage.total_spendable_usd:,.2f}" if usage is not None and usage.total_spendable_usd is not None else 'меньше $5'
            _low = f'! Мало средств · {_amt} осталось. Пополните баланс или смените тариф, чтобы работа не прервалась.'
            _cprint(f"  {_low}")

        if state.org_name:
            role = {"Owner": "Владелец", "Admin": "Администратор", "Member": "Участник"}.get((state.role or "").title(), state.role or "")
            _org_line = f"Организация: {state.org_name}{(f' · {role}' if role else '')}"
            _cprint(f"  {_d(_org_line)}")
        print(f"  {'─' * 41}")

        # ── Actions ── Members (non-admin) and non-interactive contexts fall back
        # to the portal hand-off; a paid admin/owner gets the full in-terminal
        # change flow (parity with the TUI overlay).
        if not can_change:
            print()
            _cprint(f"  {_d('Менять тариф может только администратор или владелец организации.')}")
            if manage_url:
                print(f'  Настройки на сайте: {manage_url}')
            return

        if not getattr(self, "_app", None):
            # Non-interactive (TUI slash-worker / piped): the modal can't run.
            print()
            if manage_url:
                print(f'  Управление подпиской: {manage_url}')
                print('  Откройте ссылку в браузере, затем повторите /subscription.')
            return

        if is_free:
            # Starting a NEW subscription needs a fresh card — deep-link only.
            # Show the plan catalog, let the user pick, and carry ``plan=<tier_id>``
            # into the portal deep-link so it preselects the chosen plan.
            self._subscription_free_catalog(state, manage_url)
            return

        # Paid + admin/owner + interactive → the in-terminal change flow.
        self._subscription_change_menu(state, manage_url)

    def _open_url_in_browser(self, url: str) -> bool:
        """Open ``url`` in a REAL graphical browser; return whether one opened.

        The one opener behind every "open the portal" path in this mixin. Applies
        the same console-browser / remote-session guard the device-code auth flows
        use (``korra_cli.auth``): ``webbrowser.open()`` returns ``True`` even when
        it launched a text-mode browser (w3m/lynx over SSH) that hijacks the TTY,
        so we refuse those and let the caller print the URL instead. Returns
        ``False`` on any guard refusal or open failure, ``True`` only when a real
        graphical browser launched.
        """
        if not url:
            return False
        try:
            from korra_cli.auth import _can_open_graphical_browser, _is_remote_session

            if _is_remote_session() or not _can_open_graphical_browser():
                return False
        except Exception:
            # Guard unavailable → fall through to a plain best-effort open.
            pass
        try:
            import webbrowser

            return bool(webbrowser.open(url))
        except Exception:
            return False

    def _subscription_free_catalog(self, state, manage_url):
        """Free + admin/owner + interactive: print the plan catalog, pick one, then
        open the portal manage-subscription deep-link with ``plan=<tier_id>``.

        The catalog mirrors the TUI Free rows (name · $/mo · $credits/mo, from the
        same ``tiers[]`` data via the shared ``selectable_tiers`` / ``format_tier_row``
        helpers). Monthly credits are DOLLARS — rendered ``$X credits/mo`` (hidden
        when absent/zero). Starting a NEW subscription needs a fresh card, so the
        only action is the portal hand-off (the terminal never charges here); the
        picked tier rides along as ``plan=`` so the portal preselects it.
        """
        from cli import _cprint, _b, _d

        from agent.subscription_view import (
            format_tier_row,
            selectable_tiers,
            subscription_manage_url,
        )

        tiers = selectable_tiers(state)
        if not tiers:
            # No catalog to show → the plain portal hand-off (no plan= to append).
            self._subscription_open_portal(state, manage_url, verb='Подключить подписку')
            return

        print()
        _cprint(f"  ⚕ {_b('Выберите тариф')}")
        print(f"  {'─' * 41}")
        for i, t in enumerate(tiers, 1):
            print(f"  {i}. {format_tier_row(t)}")
        _cprint(f"  {_d('Для подключения подписки откроется сайт, где можно добавить карту.')}")

        choices = [(t.tier_id, format_tier_row(t), f'подключить {t.name} на сайте') for t in tiers]
        choices.append(("cancel", 'Отмена', 'ничего не менять'))
        raw = self._prompt_text_input_modal(
            title='Подключить подписку',
            detail='Выберите тариф, чтобы открыть его на сайте.',
            choices=choices,
        )
        # The rows are printed numbered, so accept a bare number as a pick (the
        # shared normalizer only knows the confirm-dialog digit aliases).
        _digit = (raw or "").strip()
        if _digit.isdigit() and 1 <= int(_digit) <= len(tiers):
            choice = tiers[int(_digit) - 1].tier_id
        else:
            choice = self._normalize_slash_confirm_choice(raw, choices)
        if not choice or choice == "cancel":
            print('  🟡 Отменено. Подписка не подключена.')
            return
        # Numbered pick → open the portal deep-link directly, with the picked tier's
        # plan= param so the portal preselects it (spec: pick → opens the portal).
        tier_url = subscription_manage_url(state, tier_id=choice) or manage_url
        if not tier_url:
            _cprint(f"  {_d('Ссылка управления недоступна. Проверьте подключение к Nous Portal.')}")
            return
        picked = next((t for t in tiers if t.tier_id == choice), None)
        label = picked.name if picked else 'ваш тариф'
        if self._open_url_in_browser(tier_url):
            print(f'  Открывается сайт для подключения {label}…')
        else:
            # No graphical browser (headless / SSH / console browser): print the
            # link so it stays actionable.
            print(f'  Для подключения {label} откройте ссылку: {tier_url}')
        print('  Завершите действие в браузере, затем повторите /subscription.')

    def _subscription_open_portal(self, state, manage_url, *, verb='Управление подпиской'):
        """Open / copy the manage-subscription URL — the portal hand-off."""
        from cli import _cprint, _d

        if not manage_url:
            print()
            _cprint(f"  {_d('Ссылка управления недоступна. Проверьте подключение к Nous Portal.')}")
            return
        print()
        choices = [
            ("open", verb, 'открыть страницу подписки в браузере'),
            ("copy", 'Скопировать ссылку', 'скопировать ссылку управления подпиской'),
            ("cancel", 'Отмена', 'ничего не менять'),
        ]
        raw = self._prompt_text_input_modal(title=verb, detail="", choices=choices)
        choice = self._normalize_slash_confirm_choice(raw, choices)
        if choice == "open":
            if not self._open_url_in_browser(manage_url):
                print(f'  Откройте ссылку: {manage_url}')
            print()
            print('  Завершите действие в браузере, затем повторите /subscription.')
        elif choice == "copy":
            try:
                self._write_osc52_clipboard(manage_url)
                print(f'  📋 Скопировано: {manage_url}')
            except Exception:
                print(f'  Ссылка управления: {manage_url}')
        else:
            print('  🟡 Отменено.')

    def _subscription_change_menu(self, state, manage_url):
        """The in-terminal change menu for a paid admin/owner (interactive)."""
        c = state.current
        has_pending = bool(c and (c.cancel_at_period_end or c.pending_downgrade_tier_name))
        keep_name = (c.tier_name if c else None) or 'ваш тариф'
        # When a change is already scheduled, undo is the most likely next intent →
        # promote it first (parity with the TUI). The Close row uses value "close"
        # (not "cancel") so typing the word "cancel" — which the alias table would
        # map to a Close row — can't be confused with "Cancel subscription".
        if has_pending:
            choices = [
                ("keep", f'Сохранить {keep_name} (отменить запланированное изменение)', 'отменить запланированное изменение'),
                ("change", 'Сменить тариф', 'выбрать более дорогой или дешёвый тариф из терминала'),
            ]
        else:
            choices = [
                ("change", 'Сменить тариф', 'выбрать более дорогой или дешёвый тариф из терминала'),
                ("cancel_sub", 'Отменить подписку', 'отменить продление после текущего периода'),
            ]
        choices.append(("portal", 'Настроить на сайте', 'открыть страницу оплаты в браузере'))
        choices.append(("close", 'Закрыть', 'ничего не менять'))
        raw = self._prompt_text_input_modal(title='Управление подпиской', detail="", choices=choices)
        choice = self._normalize_slash_confirm_choice(raw, choices)
        if choice == "change":
            self._subscription_pick_tier(state)
        elif choice == "keep":
            self._subscription_apply(state, ("resume", None))
        elif choice == "cancel_sub":
            self._subscription_confirm_cancel(state)
        elif choice == "portal":
            self._subscription_open_portal(state, manage_url)
        else:
            print('  🟡 Закрыто. Тариф не изменён.')

    def _subscription_pick_tier(self, state):
        """Tier picker → preview → confirm (mirrors the TUI picker screen)."""
        from agent.subscription_view import format_tier_row, is_upgrade, selectable_tiers

        c = state.current
        # Selectable = enabled paid tiers other than current (free/no-sub excluded;
        # dropping to free is a cancellation, on the change menu). Sorted by price.
        # Shared with the Free catalog + blocked-preview branch (one derivation).
        selectable = selectable_tiers(state)
        if not selectable:
            print('  Сейчас нет других тарифов для перехода.')
            return
        choices = []
        for t in selectable:
            direction = "upgrade" if is_upgrade(state, t.tier_id) else "downgrade"
            choices.append((t.tier_id, f"{format_tier_row(t)} · {direction}", f'перейти на {t.name}'))
        choices.append(("cancel", 'Назад', 'ничего не менять'))
        raw = self._prompt_text_input_modal(
            title='Сменить тариф',
            detail=f"Сейчас: {(c.tier_name if c else 'Free')}. Выберите тариф, чтобы посмотреть условия перехода.",
            choices=choices,
        )
        choice = self._normalize_slash_confirm_choice(raw, choices)
        if not choice or choice == "cancel":
            print('  🟡 Отменено. Тариф не изменён.')
            return
        self._subscription_preview_and_confirm(state, choice)

    def _subscription_preview_and_confirm(self, state, tier_id, *, allow_stepup=True):
        """Preview the change (chargeless quote), show the effect, then confirm+apply.

        ``allow_stepup=False`` (a post-grant replay) declines a second step-up on a
        repeated scope denial so the flow can't re-prompt/re-open the browser in a
        loop.
        """
        from cli import _cprint, _b, _d

        from agent.subscription_view import subscription_change_preview_from_payload
        from korra_cli.nous_billing import BillingError, BillingScopeRequired, post_subscription_preview

        _cprint(f"  {_d('Проверяю изменение…')}")
        try:
            payload = post_subscription_preview(subscription_type_id=tier_id)
        except BillingScopeRequired:
            if allow_stepup:
                self._subscription_handle_scope_required(state, retry=("preview", tier_id))
            else:
                print('  Разрешение на платежи из этого терминала пока не действует. Повторите авторизацию или измените тариф на сайте.')
            return
        except BillingError as exc:
            self._subscription_render_error(state, exc)
            return
        p = subscription_change_preview_from_payload(payload)
        effect = p.effect
        target = p.target_tier_name or 'выбранный тариф'
        print()
        if effect == "no_op":
            _cprint(f"  {_d(f'У вас уже выбран {target} — менять ничего не нужно.')}")
            return
        if effect not in ("charge_now", "scheduled"):
            # blocked OR an unknown/unexpected effect → fail SAFE (never schedule a
            # real change on an unrecognized string, unlike a bare `else`), and
            # re-offer the portal hand-off like the TUI's blocked branch. The picked
            # tier rides along as plan= only for an UPGRADE hand-off — new-sub /
            # upgrade deep-links carry the plan; downgrades stay native (binding
            # ruling), so a blocked downgrade keeps the generic manage link.
            from agent.subscription_view import is_upgrade, subscription_manage_url

            _plan = tier_id if is_upgrade(state, tier_id) else None
            _cprint(f"  🟡 {p.reason or 'Это изменение нужно подтвердить на сайте управления подпиской.'}")
            _mu = subscription_manage_url(state, tier_id=_plan)
            if _mu:
                print(f'  Настройки на сайте: {_mu}')
            return
        if effect == "charge_now":
            _amt = f"${p.amount_due_now_cents / 100:.2f}" if p.amount_due_now_cents is not None else None
            _cprint(f"  {_b('Подтвердите смену тарифа')}  {_d('· списание сейчас')}")
            if _amt:
                _cprint(f'  Перейти на {target}. Сейчас будет списано {_amt} с учётом остатка оплаченного периода.')
            else:
                _cprint(f'  Перейти на {target}. Разница с учётом остатка оплаченного периода будет списана сейчас.')
            # Best-effort: name the exact card (billing.state), but only when the
            # resolver rung matches what a subscription charge actually uses
            # (subPin / customerDefault — Stripe's own precedence). Any failure or
            # older NAS → the generic line stands.
            _card_line = 'Сумма будет списана с карты вашей подписки.'
            try:
                from agent.billing_view import build_billing_state

                _bs = build_billing_state(timeout=6.0)
                _c = _bs.card if _bs.logged_in else None
                if _c is not None and _c.resolved_via in ("subPin", "customerDefault"):
                    _card_line = f'{_c.masked} — с карты вашей подписки будет списана указанная сумма.'
            except Exception:
                pass
            _cprint(f"  {_d(_card_line)}")
            pay_label = f'Оплатить {_amt} и перейти сейчас' if _amt else 'Перейти сейчас (списать разницу за остаток периода)'
            action = ("upgrade", tier_id)
            # The money-moving row is NOT the default — a bare Enter hits "Go back",
            # so a single stray keystroke can't charge the card.
            confirm_choices = [
                ("cancel", 'Назад', 'не списывать средства'),
                ("yes", pay_label, 'списать средства и сменить тариф сейчас'),
            ]
        else:  # scheduled (whitelisted above)
            _when = p.effective_at[:10] if (p.effective_at and len(p.effective_at) >= 10) else 'конца оплаченного периода'
            _cprint(f"  {_b('Подтвердите смену тарифа')}  {_d('· запланировано · не сегодня')}")
            _cprint(f'  Смена тарифа на {target} вступит в силу {_when}. Сейчас списания не будет; до этого момента действует ваш текущий тариф.')
            pay_label = f'Запланировать переход на {target}'
            action = ("schedule", tier_id)
            confirm_choices = [
                ("yes", pay_label, 'применить изменение'),
                ("cancel", 'Назад', 'ничего не менять'),
            ]
        if p.monthly_credits_delta:
            _cprint(f"  {_d(f'Изменение месячного баланса: {p.monthly_credits_delta}.')}")
        raw = self._prompt_text_input_modal(title=pay_label, detail="", choices=confirm_choices)
        if self._normalize_slash_confirm_choice(raw, confirm_choices) != "yes":
            print('  🟡 Отменено. Тариф не изменён.')
            return
        self._subscription_apply(state, action, allow_stepup=allow_stepup)

    def _subscription_confirm_cancel(self, state):
        """Confirm, then schedule a cancellation at period end."""
        from cli import _cprint, _b, _d

        from agent.billing_usage import format_renews

        c = state.current
        _end = (format_renews(c.cycle_ends_at) if (c and c.cycle_ends_at) else None) or 'конца оплаченного периода'
        print()
        _cprint(f"  {_b('Подтвердите отмену подписки')}  {_d('· запланировано · не сегодня')}")
        _cprint(f"  Отменить {(c.tier_name if c else 'ваш тариф')} — подписка действует до {_end}, затем продлеваться не будет.")
        _cprint(f"  {_d('Оставшийся баланс доступен до конца периода. До его окончания можно возобновить подписку.')}")
        confirm_choices = [
            ("yes", 'Отменить подписку', 'отменить продление после текущего периода'),
            ("cancel", 'Назад', 'сохранить текущий тариф'),
        ]
        raw = self._prompt_text_input_modal(title='Отменить подписку?', detail="", choices=confirm_choices)
        if self._normalize_slash_confirm_choice(raw, confirm_choices) != "yes":
            print('  🟡 Отменено. Ваш тариф не изменён.')
            return
        self._subscription_apply(state, ("cancel", None))

    def _subscription_apply(self, state, action, idempotency_key=None, *, allow_stepup=True):
        """Run the mutation for `action`, handling the scope step-up + the result.

        `action` is one of ("upgrade", tier_id) / ("schedule", tier_id) /
        ("cancel", None) / ("resume", None). insufficient_scope routes to the
        step-up and replays; the upgrade idempotency key is reused across the replay.
        ``allow_stepup=False`` (a post-grant replay) declines a second step-up on a
        repeated scope denial so the flow can't re-prompt/re-open the browser in a loop.
        """
        from cli import _cprint, _d, _DIM, _RST

        from korra_cli.nous_billing import (
            BillingError,
            BillingTransient,
            BillingRemoteSpendingRevoked,
            BillingScopeRequired,
            BillingSessionRevoked,
            delete_subscription_pending_change,
            post_subscription_upgrade,
            put_subscription_pending_change,
        )

        kind, arg = action
        key = None
        if kind == "upgrade":
            from agent.billing_view import new_idempotency_key

            key = idempotency_key or new_idempotency_key()
        try:
            if kind == "upgrade":
                try:
                    res = post_subscription_upgrade(subscription_type_id=arg, idempotency_key=key) or {}
                except BillingScopeRequired:
                    raise  # a scope denial rejects BEFORE charging → route to the step-up
                except (BillingTransient, BillingSessionRevoked, BillingRemoteSpendingRevoked) as exc:
                    # Deterministic PRE-charge typed rejections (429 / 401 / 403) never
                    # reached Stripe → surface the CORRECT recovery (retry_after / re-login /
                    # reconnect), NOT the "maybe charged" ambiguity copy.
                    self._subscription_render_error(state, exc)
                    return
                except BillingError as exc:
                    _status = getattr(exc, "status", None)
                    _code = getattr(exc, "error", None)
                    if _code in ("network_error", "endpoint_unavailable") or _status is None or _status >= 500:
                        # Genuinely INDETERMINATE — transport / unparseable 2xx / a 5xx the
                        # server hit mid-request: NAS may have already prorated + charged.
                        # Steer to a re-check, never a blind retry (a fresh key can't dedup →
                        # a real second charge).
                        self._subscription_render_upgrade_ambiguous(exc)
                    else:
                        # A deterministic 4xx (role_required / no_payment_method / …) → the
                        # normal error copy, not "maybe charged".
                        self._subscription_render_error(state, exc)
                    return
                status = res.get("status")
                name = res.get("targetTierName") or 'ваш новый тариф'
                _url = res.get("recoveryUrl")
                if status == "already_on_tier":
                    _cprint(f'  {_DIM}✓ У вас уже выбран {name}.{_RST}')
                elif status == "upgraded":
                    _cprint(f'  {_DIM}✓ Подключён тариф {name}. Баланс нового тарифа скоро станет доступен.{_RST}')
                elif status == "requires_action":
                    _cprint('  🟡 Для перехода нужно подтверждение банка (3DS). Завершите его на сайте.')
                    if _url:
                        _cprint(f'  Сайт: {_url}')
                elif status == "payment_failed":
                    _cprint('  🔴 Банк отклонил карту. Обновите способ оплаты на сайте и повторите попытку.')
                    if _url:
                        _cprint(f'  Сайт: {_url}')
                else:
                    # Unknown / absent 2xx status → also ambiguous, not a flat failure.
                    self._subscription_render_upgrade_ambiguous(None)
                return
            if kind == "schedule":
                put_subscription_pending_change(subscription_type_id=arg)
                _cprint(f'  {_DIM}✓ Изменение запланировано. До конца оплаченного периода действует текущий тариф, затем он сменится.{_RST}')
            elif kind == "cancel":
                put_subscription_pending_change(cancel=True)
                _cprint(f'  {_DIM}✓ Отмена запланирована. Подписка действует до конца оплаченного периода. Сегодня ничего не меняется.{_RST}')
            elif kind == "resume":
                delete_subscription_pending_change()
                _cprint(f'  {_DIM}✓ Изменение отменено — вы остаётесь на текущем тарифе.{_RST}')
            _cprint(f"  {_d('Для проверки подписки выполните /subscription.')}")
        except BillingScopeRequired:
            if allow_stepup:
                self._subscription_handle_scope_required(state, retry=action, idempotency_key=key)
            else:
                print('  Разрешение на платежи из этого терминала пока не действует. Повторите авторизацию или измените тариф на сайте.')
        except BillingError as exc:
            self._subscription_render_error(state, exc)

    def _subscription_handle_scope_required(self, state, *, retry, idempotency_key=None):
        """insufficient_scope → allow remote spending (step-up), then replay `retry`.

        Mirrors _billing_handle_scope_required: the classic CLI calls
        step_up_nous_billing_scope directly (it opens the browser + blocks), then
        replays the held preview/mutation so the user never re-runs the command.
        """
        from cli import _cprint, _d, _DIM, _RST

        print()
        print('  ! Однократная настройка')
        _cprint(f"  {_d('Чтобы менять тариф из терминала, один раз разрешите платежи. Авторизация откроется в браузере, после неё можно продолжить здесь.')}")
        if not getattr(self, "_app", None):
            print('  Выполните `korra portal`, разрешите платежи из терминала и повторите /subscription.')
            return
        confirm_choices = [
            ("yes", 'Разрешить платежи из терминала', 'открыть браузер для авторизации'),
            ("no", 'Не сейчас', "cancel"),
        ]
        raw = self._prompt_text_input_modal(
            title='Разрешить платежи из терминала',
            detail='Откроется браузер для авторизации этого терминала.',
            choices=confirm_choices,
        )
        if self._normalize_slash_confirm_choice(raw, confirm_choices) != "yes":
            print('  Изменений нет. Разрешите платежи из терминала, когда будете готовы.')
            return
        print('  Открывается браузер для разрешения платежей из терминала…')
        try:
            from korra_cli.auth import step_up_nous_billing_scope

            granted = step_up_nous_billing_scope(open_browser=True)
        except Exception as exc:
            print(f'  Не удалось разрешить платежи из терминала: {exc}')
            return
        if not granted:
            print('  Разрешить платежи из терминала может только администратор или владелец организации.')
            return
        _cprint(f'  {_DIM}✓ Платежи из терминала разрешены.{_RST}')
        # Bust the 30s token cache so the replay uses the freshly-scoped token. The
        # cache still holds the pre-grant unscoped token, and _request only busts it
        # on a 401 (not a 403 scope denial) — without this, the replay would 403
        # again and (before the allow_stepup guard) re-prompt in a loop.
        try:
            from korra_cli import nous_billing as _nb

            _nb.invalidate_cached_token()
        except Exception:
            pass
        # Re-fetch fresh state, then replay the held action ONCE (allow_stepup=False
        # so a repeated scope denial can't re-enter the step-up).
        from agent.subscription_view import build_subscription_state

        try:
            fresh = build_subscription_state()
        except Exception:
            fresh = state
        rkind, rarg = retry
        if rkind == "preview":
            self._subscription_preview_and_confirm(fresh, rarg, allow_stepup=False)
        else:
            self._subscription_apply(fresh, retry, idempotency_key=idempotency_key, allow_stepup=False)

    def _subscription_render_error(self, state, exc):
        """Render a subscription BillingError (a lighter _billing_render_charge_error)."""
        from cli import _cprint

        code = getattr(exc, "error", None)
        msg = str(exc) or 'Произошла ошибка.'
        if code == "insufficient_scope":
            # Defensive: the flow routes scope to the step-up before reaching here.
            _cprint('  🟡 Платежи из терминала пока не разрешены. Подтвердите разрешение и повторите попытку.')
        elif code in ("subscription_mutation_rejected", "preview_rejected"):
            _cprint(f"  🟡 {msg}")
        else:
            _cprint(f"  🔴 {msg}")
        _url = getattr(exc, "portal_url", None)
        if _url:
            _cprint(f'  Сайт: {_url}')

    def _subscription_render_upgrade_ambiguous(self, exc):
        """A charge-route failure (transport / timeout / 500 / unknown status) is
        AMBIGUOUS — NAS may have already prorated + charged. Steer to a re-check,
        never a flat failure that invites a blind retry (mirrors the TUI's
        upgradeResult(null) — the CLI can't persist the key across a command re-run,
        so a re-check is the safe path)."""
        from cli import _cprint, _d

        _cprint('  🟡 Не удалось подтвердить переход. Списание с карты могло произойти.')
        _cprint(f"  {_d('Перед повторной попыткой проверьте свой тариф через /subscription.')}")
        _url = getattr(exc, "portal_url", None) if exc is not None else None
        if _url:
            _cprint(f'  Сайт: {_url}')

    # ------------------------------------------------------------------
    # /billing — Phase 2b Remote Spending (CLI surface, all 5 screens)
    # ------------------------------------------------------------------

    def _show_billing(self, command: str = "/topup"):
        """`/topup` — Remote Spending for Nous (one interactive modal).

        ZERO sub-commands: any argument is ignored. Bare ``/topup`` always
        opens the Overview (Screen 1), whose numbered menu is the *only* way to
        reach the Buy / Auto-reload / Monthly-limit sub-screens. (Per the unified
        UX spec §0.4 — ``/topup buy`` etc. are gone; we don't error on a stray
        arg, we just open the menu.)

        Interactive CLI uses the prompt_toolkit modal; non-interactive contexts
        (TUI slash-worker / no live app) render text + the portal deep-link, never
        prompting (the URL is the affordance), same discipline as ``_show_subscription``.
        All money is Decimal end-to-end; the terminal never collects card details.
        """
        from cli import _cprint, _d

        from agent.billing_view import build_billing_state

        state = build_billing_state()
        if not state.logged_in:
            print()
            if state.error:
                _msg = f'Не удалось загрузить данные оплаты: {state.error}'
                _cprint(f"  💳 {_d(_msg)}")
            else:
                _cprint(f"  💳 {_d('Вход в Nous Portal не выполнен.')}")
                print('  Войдите через `korra portal`, затем выполните /topup.')
            return

        # Any sub-arg is intentionally ignored — always open the menu.
        self._billing_overview(state)

    def _billing_portal_hint(self, state, *, reason: str = "") -> None:
        """Print a portal deep-link line (the funnel for portal-only actions)."""
        url = getattr(state, "portal_url", None)
        if not url:
            return
        if reason:
            print(f"  {reason}")
        print(f'  Настройки на сайте: {url}')

    def _billing_overview(self, state):
        """Screen 1 — overview: balance in title, two-bar dollar usage, action menu.

        Dollars-only (no "credits") — mirrors the TUI /topup overlay: balance
        leads in the title, the shared plan + top-up bars render below, then the
        reordered menu (Add funds first). No scope preflight — remote spending
        is discovered reactively when a charge 403s insufficient_scope.
        """
        from cli import _cprint, _b, _d

        from agent.billing_view import format_money

        # Shared dollar usage model (plan + top-up bars), same source as /usage.
        try:
            from agent.billing_usage import build_usage_model

            usage = build_usage_model()
        except Exception:
            usage = None

        print()
        _cprint(f"  💳 {_b(f'Пополнение · баланс {format_money(state.balance_usd)}')}")
        if state.org_name:
            role = {"Owner": "Владелец", "Admin": "Администратор", "Member": "Участник"}.get((state.role or "").title(), state.role or "")
            _org_line = f"Организация: {state.org_name}{(f' · {role}' if role else '')}"
            _cprint(f"  {_d(_org_line)}")
        print(f"  {'─' * 41}")

        # Two-bar dollar usage view (plan name on the plan bar; top-up below).
        for _bar_ln in self._usage_bar_lines(usage, getattr(usage, "plan_name", None)):
            print(_bar_ln)

        ar = state.auto_reload
        if ar is not None:
            if ar.enabled:
                print(
                    f'  Автопополнение включено: при балансе ниже {format_money(ar.threshold_usd)} → пополнить до {format_money(ar.reload_to_usd)}'
                )
            else:
                print('  Автопополнение выключено')
        # Card presence at a glance: which card a charge would use (with why —
        # "the card on your subscription"), or that none is saved. Only for the
        # full-menu case (admin + billing on) — others get the portal note below.
        if state.can_change_plan and state.cli_billing_enabled:
            if state.card is not None:
                print(f'  Карта: {state.card.display}')
            else:
                _cprint(f"  {_d('Сохранённой карты нет. Добавьте её через «Пополнить».')}")
        print(f"  {'─' * 41}")

        # Action gating: admin + kill-switch for charge/auto-reload; everyone gets portal.
        if not state.can_change_plan:
            _cprint(f"  {_d('Платёжные операции доступны только администратору или владельцу организации.')}")
            self._billing_portal_hint(state)
            return
        if not state.cli_billing_enabled:
            _cprint(f"  {_d('Платежи из терминала выключены для этой организации.')}")
            self._billing_portal_hint(
                state,
                reason='Администратор может включить пополнение здесь в настройках агента на сайте Nous.',
            )
            return

        # A missing card does NOT gate the whole overview — the org may already have
        # balance, auto-reload, or a limit to view/manage. The card only matters at
        # CHARGE time: "Add funds" -> _billing_buy_flow, which detects no card and
        # hands off to the portal there. So always show the full menu below.

        # Non-interactive (slash-worker / no live app): no modal, no sub-command
        # advertising — just the portal funnel (the URL is the affordance).
        if not getattr(self, "_app", None):
            self._billing_portal_hint(state)
            return

        # One-time vs automatic — the two ways to add funds, the distinction stated
        # up front in each first sentence (parity with the desktop revamp's split
        # copy). "credits" stays out of the dollars-only /topup surface: "Add funds
        # now" carries the one-time meaning without it.
        _cprint(f"  {_d('Пополнить баланс сейчас: разовое списание, средства доступны сегодня.')}")
        if (
            ar is not None
            and ar.enabled
            and ar.reload_to_usd is not None
            and ar.reload_to_usd.is_finite()
            and ar.threshold_usd is not None
            and ar.threshold_usd.is_finite()
        ):
            _auto_line = (
                f'При низком балансе: автоматически списывать с {format_money(ar.reload_to_usd)}, если баланс ниже {format_money(ar.threshold_usd)}.'
            )
        else:
            _auto_line = (
                'Автоматически списывать средства с карты, когда баланс опускается ниже указанного порога.'
            )
        _cprint(f"  {_d(_auto_line)}")
        print(f"  {'─' * 41}")

        # Add funds first, then settings, then the scopeless browser handoff.
        # No "Allow Remote Spending" item — that's discovered at pay time.
        # "Add funds" charges in-terminal against the org's portal-saved card
        # (server-held via POST /charge — no card ref leaves the client). A
        # missing card is NOT gated here: the buy flow reacts to the server's
        # no_payment_method 403 and hands off to the portal at charge time.
        choices = [
            ("buy", 'Пополнить', 'разовое списание, средства доступны сегодня'),
            ("auto", 'Автопополнение', 'автоматически пополнять баланс при нехватке средств'),
            ("limit", 'Месячный лимит', 'посмотреть месячный лимит расходов'),
            ("portal", 'Настроить на сайте', 'открыть страницу оплаты в браузере'),
            ("cancel", 'Отмена', 'ничего не менять'),
        ]
        # The overview summary is already printed above; the modal only needs to
        # present the action menu — repeating the title/balance reads as a dupe.
        raw = self._prompt_text_input_modal(
            title='Пополнить баланс', detail="",
            choices=choices,
        )
        choice = self._normalize_slash_confirm_choice(raw, choices)
        if choice == "buy":
            self._billing_buy_flow(state)
        elif choice == "auto":
            self._billing_auto_reload_flow(state)
        elif choice == "limit":
            self._billing_limit_screen(state)
        elif choice == "portal":
            self._billing_open_portal(state)
        else:
            print('  Отменено.')

    def _usage_bar_lines(self, usage, plan_name) -> list:
        """The plan + top-up dollar bars as ready-to-print lines (filled = remaining).

        Returns [] when there's nothing to draw. The caller resolves ``plan_name``
        (the plan-bar label) and picks its own print fn — block ordering differs
        per surface (``_cprint`` vs ``print`` under patch_stdout). One source of
        truth for the bar format across /usage, /subscription, and /topup.
        """
        lines: list = []
        pb = getattr(usage, "plan_bar", None) if usage else None
        if pb is not None and pb.total_usd > 0:
            filled = max(0, min(10, round(pb.fill_fraction * 10)))
            bar = ("█" * filled) + ("░" * (10 - filled))
            pct_s = f' · {pb.pct_used}% использовано' if pb.pct_used is not None else ""
            label = (plan_name or "тариф").ljust(8)[:8]
            lines.append(f'  {label}[{bar}]  ${pb.remaining_usd:,.2f} осталось из ${pb.total_usd:,.2f}{pct_s}')
        tb = getattr(usage, "topup_bar", None) if usage else None
        if tb is not None and tb.remaining_usd > 0:
            lines.append(f"  {'баланс'.ljust(8)}[{'█' * 10}]  ${tb.remaining_usd:,.2f} · без срока действия")
        return lines

    def _billing_open_portal(self, state):
        url = getattr(state, "portal_url", None)
        if not url:
            print('  Ссылка на сайт недоступна.')
            return
        if not self._open_url_in_browser(url):
            print(f'  Откройте ссылку: {url}')
        print('  Завершите изменения оплаты в браузере.')

    def _billing_require_admin(self, state) -> bool:
        """Guard charge/auto-reload entry points; print + return False if blocked."""
        from cli import _cprint, _d

        if not state.can_change_plan:
            print()
            _cprint(f"  💳 {_d('Платёжные операции доступны только администратору или владельцу организации.')}")
            self._billing_portal_hint(state)
            return False
        if not state.cli_billing_enabled:
            print()
            _cprint(f"  💳 {_d('Платежи из терминала выключены для этой организации.')}")
            self._billing_portal_hint(
                state,
                reason='Перед пополнением администратор может разрешить платежи в настройках агента на сайте Nous.',
            )
            return False
        return True

    def _billing_add_card_flow(self, state):
        """No saved card → guide adding one on the portal, with a re-check loop.

        Cards are added on the portal (never in-terminal). "I've added it" re-fetches
        billing state so the purchase continues right here once the card is saved —
        this also recovers a transient miss (the card display is best-effort
        server-side). Returns the refreshed state (card present), or None to abandon.
        """
        from cli import _cprint, _b, _d, _DIM, _RST

        print()
        _cprint(f"  💳 {_b('Сначала добавьте карту')}")
        _cprint('  Сохранённой карты нет.')
        _cprint(f"  {_d('Один раз добавьте карту на странице оплаты Nous. После этого можно пополнять баланс из терминала.')}")
        choices = [
            ("portal", 'Добавить карту на сайте', 'открыть страницу оплаты в браузере'),
            ("recheck", 'Карта добавлена — проверить снова', 'найти карту и продолжить'),
            ("cancel", 'Назад', 'ничего не менять'),
        ]
        for _ in range(8):  # bounded: portal-open plus a handful of re-checks
            raw = self._prompt_text_input_modal(title='Добавить карту', detail="", choices=choices)
            choice = self._normalize_slash_confirm_choice(raw, choices)
            if choice == "portal":
                self._billing_open_portal(state)
                _cprint(f"  {_d('Добавьте карту на странице оплаты, затем нажмите здесь «Проверить снова».')}")
                continue
            if choice == "recheck":
                from agent.billing_view import build_billing_state

                try:
                    fresh = build_billing_state()
                except Exception:
                    fresh = None
                if fresh is not None and fresh.logged_in:
                    state = fresh
                if state.card is not None:
                    _cprint(f'  {_DIM}✓ Карта найдена: {state.card.display} — продолжаю.{_RST}')
                    return state
                print('  Карта ещё не добавлена. Завершите добавление на сайте и проверьте снова.')
                continue
            break
        print('  Отменено. Баланс не пополнен.')
        return None

    def _billing_buy_flow(self, state):
        """Screen 2 (preset select) → Screen 3 (confirm + charge + poll)."""
        from cli import _cprint, _b

        from agent.billing_view import format_money, validate_charge_amount

        if not self._billing_require_admin(state):
            return

        # No card / scope preflight here — that's the rejected anti-pattern. We let
        # the charge fly and react to whatever 403 the server returns: scope first
        # (insufficient_scope → in-flight reauth), then card (no_payment_method →
        # portal handoff via _billing_render_charge_error). Mirrors the server's gate
        # order; the user only hits the flow they actually need.

        # Screen 3 — preset selection.
        if not getattr(self, "_app", None):
            presets = ", ".join(format_money(p) for p in state.charge_presets)
            print()
            _cprint(f"  💳 {_b('Пополнить')}")
            print(f'  Суммы на выбор: {presets}')
            print('  Для оплаты запустите эту команду в интерактивном терминале Korra.')
            self._billing_portal_hint(state)
            return

        # No card on file → the guided ADD-CARD path first (portal + re-check),
        # so the user isn't walked through picking an amount that will 403.
        # Returns refreshed state with a card, or None (abandoned).
        if state.card is None:
            state = self._billing_add_card_flow(state)
            if state is None or state.card is None:
                return

        preset_choices = []
        for p in state.charge_presets:
            preset_choices.append((str(p), format_money(p), 'разовое пополнение баланса'))
        preset_choices.append(("custom", 'Другая сумма…', 'указать свою сумму'))
        preset_choices.append(("cancel", 'Отмена', 'ничего не менять'))

        card = state.card
        detail = f'Способ оплаты: {card.display}' if card else 'Сохранённой карты нет'
        raw = self._prompt_text_input_modal(
            title='Пополнить', detail=detail, choices=preset_choices,
        )
        choice = self._normalize_slash_confirm_choice(raw, preset_choices)
        if not choice or choice == "cancel":
            print('  Отменено. Баланс не пополнен.')
            return

        from decimal import Decimal

        if choice == "custom":
            entered = self._prompt_text_input('  Сумма (USD): ')
            if entered is None:
                # None = cancelled (e.g. slash-worker can't prompt off-thread).
                print('  Отменено. Баланс не пополнен.')
                return
            v = validate_charge_amount(
                entered or "", min_usd=state.min_usd, max_usd=state.max_usd
            )
            if not v.ok:
                print(f"  🔴 {v.error}")
                return
            amount = v.amount
        else:
            try:
                amount = Decimal(choice)
            except Exception:
                print('  🔴 Неверный выбор.')
                return

        self._billing_confirm_and_charge(state, amount)

    def _billing_confirm_and_charge(self, state, amount):
        """Screen 3 — confirm total + consent, charge, then poll to settlement."""
        from cli import _cprint, _b, _d

        from agent.billing_view import format_money, new_idempotency_key

        card = state.card
        print()
        _cprint(f"  💳 {_b('Подтвердите оплату')}")
        print(f"  {'─' * 41}")
        print(f'  Итого: {format_money(amount)}')
        if card:
            print(f'  Способ оплаты: {card.display}')
            # Provenance-less payloads (older NAS) keep the generic line; when
            # the resolver says WHY this card, the Payment line carries it.
            if card.provenance is None:
                _cprint(f"  {_d('Сумма будет списана с карты, сохранённой на сайте.')}")
        print(f"  {'─' * 41}")
        _consent = (
            'Подтверждая, вы разрешаете Nous Research списать указанную сумму с вашей карты.'
        )
        _cprint(f"  {_d(_consent)}")

        confirm_choices = [
            ("pay", f'Оплатить {format_money(amount)} сейчас', 'отправить платёж'),
            ("portal", 'Настроить на сайте', 'настроить карту и оплату в браузере'),
            ("cancel", 'Назад', 'не списывать средства'),
        ]
        if not getattr(self, "_app", None):
            print('  Для подтверждения оплаты откройте интерактивный терминал Korra.')
            return
        raw = self._prompt_text_input_modal(
            title=f'Оплатить {format_money(amount)}?',
            detail=(card.display if card else 'карта не добавлена'),
            choices=confirm_choices,
        )
        choice = self._normalize_slash_confirm_choice(raw, confirm_choices)
        if choice == "portal":
            self._billing_open_portal(state)
            return
        if choice != "pay":
            print('  Отменено. Баланс не пополнен.')
            return

        # Submit the charge with a fresh idempotency key (reused on retry).
        from korra_cli.nous_billing import (
            BillingError,
            BillingScopeRequired,
            post_charge,
        )

        key = new_idempotency_key()
        try:
            result = post_charge(amount_usd=amount, idempotency_key=key)
        except BillingScopeRequired:
            # In-flight reauth: allow remote spending, then resume THIS charge
            # (press-Enter beat) — no command re-run. Reuses the same idem key.
            self._billing_handle_scope_required(state, amount=amount, idempotency_key=key)
            return
        except BillingError as exc:
            self._billing_render_charge_error(state, exc)
            return

        charge_id = result.get("chargeId")
        if not charge_id:
            print('  🔴 Сервис не вернул ID платежа. Проверьте оплату на сайте.')
            return
        _cprint(f"  {_d('Платёж отправлен — ожидаю подтверждения зачисления…')}")
        self._billing_poll_charge(state, charge_id, amount)

    def _billing_poll_charge(self, state, charge_id, amount):
        """Poll loop: 2s interval, 5-min cap, cancellable. settled = ledger truth."""
        import time as _time

        from agent.billing_view import format_money
        from korra_cli.nous_billing import (
            BillingError,
            BillingTransient,
            get_charge_status,
        )

        deadline = _time.time() + 300  # 5-minute cap
        interval = 2.0
        while _time.time() < deadline:
            try:
                status = get_charge_status(charge_id)
            except BillingTransient as exc:
                # Retry-after, NOT a failure — back off and keep polling.
                wait = exc.retry_after or 5
                _time.sleep(min(wait, 30))
                continue
            except BillingError as exc:
                print(f'  🔴 Не удалось проверить платёж: {exc}')
                return

            state_str = status.get("status")
            if state_str == "settled":
                amt = status.get("amountUsd")
                from agent.billing_view import parse_money

                shown = format_money(parse_money(amt)) if amt else format_money(amount)
                print(f'  ✓ {shown} зачислено на баланс.')
                return
            if state_str == "failed":
                self._billing_render_charge_failed(state, status.get("reason"))
                return
            # pending → wait and poll again
            _time.sleep(interval)

        # Past the cap with no terminal state = timeout (not an error).
        print('  🟡 Платёж обрабатывается дольше 5 минут. Это не означает отказ. Чуть позже проверьте /billing или сайт.')
        self._billing_portal_hint(state)

    def _billing_render_charge_failed(self, state, reason):
        """Branch the poll `failed` reasons to the right copy + portal funnel."""
        reason = (reason or "").strip()
        if reason == "authentication_required":
            print('  🔴 Банк требует подтверждение (3DS). Завершите его на сайте для оплаты.')
        elif reason == "payment_method_expired":
            print('  🔴 Срок действия карты истёк. Обновите её на сайте.')
        elif reason == "card_declined":
            print('  🔴 Банк отклонил карту. Попробуйте другую карту на сайте.')
        else:
            print(f"  🔴 Платёж не прошёл ({reason or 'processing_error'}).")
        self._billing_portal_hint(state)

    def _billing_render_charge_error(self, state, exc):
        """Render a typed BillingError at submit time (pre-poll)."""
        from korra_cli.nous_billing import (
            BillingTransient,
            BillingRemoteSpendingRevoked,
            BillingSessionRevoked,
        )

        code = getattr(exc, "error", None)
        actor = getattr(exc, "actor", None)
        portal_url = getattr(exc, "portal_url", None) or getattr(state, "portal_url", None)
        if isinstance(exc, BillingRemoteSpendingRevoked) or code == "remote_spending_revoked":
            # CF-4: this terminal's spend was revoked. Recovery is reconnect.
            who = ('Администратор запретил платежи из этого терминала.'
                   if actor == "admin"
                   else 'Вы запретили платежи из этого терминала.')
            print(f'  🔴 {who} Для восстановления доступа войдите заново через `korra portal`.')
        elif isinstance(exc, BillingSessionRevoked) or code == "session_revoked":
            print('  🔴 Вы вышли из учётной записи. Войдите заново через `korra portal`.')
        elif code == "no_payment_method":
            print('  💳 Карта не добавлена. Пополните баланс и настройте оплату на сайте.')
        elif code in ("cli_billing_disabled", "remote_spending_disabled") or \
                getattr(exc, "code", None) == "remote_spending_disabled":
            print('  Платежи из терминала выключены для этой учётной записи. Администратор может включить их в настройках агента на сайте Nous.')
        elif code == "role_required":
            print('  Пополнять баланс может администратор или владелец организации. Обратитесь к нему или откройте сайт управления оплатой.')
        elif code == "idempotency_conflict":
            print('  🔴 Этот ID платежа уже использован для другой суммы. Начните новое пополнение.')
        elif code == "monthly_cap_exceeded":
            remaining = (getattr(exc, "payload", {}) or {}).get("remainingUsd")
            if remaining is not None:
                print(f'  🔴 Достигнут месячный лимит расходов — осталось ${remaining} до лимита.')
            else:
                print('  🔴 Достигнут месячный лимит расходов.')
        elif isinstance(exc, BillingTransient):
            wait = getattr(exc, "retry_after", None)
            mins = f' (повторите через ~{max(1, round(wait / 60))} мин)' if wait else ""
            print(f'  🟡 Сейчас слишком много платежей{mins}. Это не означает отказ в оплате.')
        elif code == "insufficient_scope":
            # Never leak the raw billing:manage scope (the post-grant replay can
            # re-raise it if the grant raced) — the concept is "Remote Spending".
            print('  🔴 Разрешите платежи из терминала через /topup и повторите попытку.')
        else:
            print(f"  🔴 {exc}")
        if portal_url:
            print(f'  Сайт: {portal_url}')

    def _billing_handle_scope_required(self, state, *, amount=None, idempotency_key=None):
        """403 insufficient_scope → in-flight reauth, then resume the held charge.

        The buy path discovers remote spending isn't allowed only when the
        charge 403s — there is no preflight. We allow it in-flight ("Allow
        Remote Spending" → browser device-flow), then on return ask the user to
        press Enter to resume the held ``amount`` (reusing ``idempotency_key`` so
        the resumed charge collapses with the original). Never leaks the raw
        billing:manage scope.
        """
        from cli import _cprint, _d

        from agent.billing_view import format_money

        amount_str = format_money(amount) if amount is not None else 'ваше пополнение'
        print()
        print('  ! Однократная настройка')
        _cprint(f"  {_d(f'Для оплаты из этого терминала нужно однократное разрешение. Авторизация откроется в браузере, затем {amount_str} продолжится здесь.')}")
        if not getattr(self, "_app", None):
            print('  Выполните `korra portal`, разрешите платежи из терминала и повторите попытку.')
            return
        confirm_choices = [
            ("yes", 'Разрешить платежи из терминала', 'открыть браузер для авторизации'),
            ("no", 'Не сейчас', "cancel"),
        ]
        raw = self._prompt_text_input_modal(
            title='Разрешить платежи из терминала',
            detail='Откроется браузер для авторизации этого терминала.',
            choices=confirm_choices,
        )
        choice = self._normalize_slash_confirm_choice(raw, confirm_choices)
        if choice != "yes":
            print('  Списания не было. Когда будете готовы разрешить платежи из терминала, выполните /topup.')
            return
        print('  Открывается браузер для разрешения платежей из терминала…')
        try:
            from korra_cli.auth import step_up_nous_billing_scope

            granted = step_up_nous_billing_scope(open_browser=True)
        except Exception as exc:
            print(f'  Не удалось разрешить платежи из терминала: {exc}')
            return
        if not granted:
            print('  Платежи из терминала должен разрешить администратор или владелец организации. Списания с карты не было.')
            return

        # Granted. The token now carries the scope, but the ORG kill-switch
        # (cli_billing_enabled) is a separate gate — re-fetch /state so we don't
        # over-promise when a charge would still hit cli_billing_disabled.
        from agent.billing_view import build_billing_state

        fresh = build_billing_state()
        if not (fresh.logged_in and fresh.cli_billing_enabled):
            print('  Терминал получил разрешение на платежи, но они ещё выключены для организации. Администратор может включить их в настройках агента на сайте Nous. Затем повторите /topup.')
            self._billing_portal_hint(fresh)
            return

        # Scope granted + org kill-switch on — but a charge still needs a card on
        # file. If there's none, this is a half-done state: say so and route to the
        # portal to top up / manage billing, rather than a bare "✓ enabled" that reads as done.
        if fresh.card is None:
            print('  ✓ Платежи из терминала разрешены, но карта ещё не добавлена.')
            _cprint(f"  {_d('Для продолжения пополните баланс и настройте оплату на сайте.')}")
            self._billing_portal_hint(fresh)
            return

        # Nothing to resume (scope-required hit outside a charge, e.g. auto-reload
        # config) → just tell the user it's ready.
        if amount is None:
            print('  ✓ Платежи из терминала разрешены. Для продолжения выполните /topup.')
            return

        # Press-Enter beat: the user is back from the browser; resume the held
        # purchase on an explicit confirm (reassuring, not silent).
        print('  ✓ Платежи из терминала разрешены.')
        resume_choices = [
            ("resume", f'Продолжить пополнение на {format_money(amount)} ', 'завершить приостановленную оплату'),
            ("cancel", 'Отмена', 'не списывать средства'),
        ]
        raw = self._prompt_text_input_modal(
            title='Продолжить пополнение',
            detail=f'{format_money(amount)} можно завершить. Нажмите Enter, чтобы продолжить.',
            choices=resume_choices,
        )
        if self._normalize_slash_confirm_choice(raw, resume_choices) != "resume":
            print('  Отменено. Баланс не пополнен.')
            return

        # Replay the held charge, reusing the original idempotency key so a
        # double-submit collapses to one charge.
        from korra_cli.nous_billing import BillingError, post_charge

        from agent.billing_view import new_idempotency_key

        key = idempotency_key or new_idempotency_key()
        try:
            result = post_charge(amount_usd=amount, idempotency_key=key)
        except BillingError as exc:
            self._billing_render_charge_error(fresh, exc)
            return
        charge_id = result.get("chargeId")
        if not charge_id:
            print('  Сервис не вернул ID платежа. Проверьте оплату на сайте.')
            return
        _cprint(f"  {_d('Продолжаю пополнение — ожидаю подтверждения зачисления…')}")
        self._billing_poll_charge(fresh, charge_id, amount)

    def _billing_auto_reload_flow(self, state):
        """Screen 4 — auto-reload config: threshold + reload-to → PATCH.

        Prefills the current values from ``state.auto_reload``. Validates both
        amounts (2dp, within bounds, ``reload_to > threshold``). When auto-reload
        is already on, offers a "Turn off" path (PATCH ``enabled:false``).
        """
        from cli import _cprint, _b, _d

        from agent.billing_view import format_money, validate_charge_amount

        if not self._billing_require_admin(state):
            return

        card = state.card
        ar = state.auto_reload
        currently_on = bool(ar and ar.enabled)

        print()
        _cprint(f"  💳 {_b('Автопополнение')}")
        print(f"  {'─' * 41}")
        _cprint(f"  {_d('Автоматически пополнять баланс, когда средств мало.')}")
        if card:
            print(f'  Сохранённая карта: {card.masked}')
        else:
            print('  Карта не добавлена. Настройте оплату на сайте.')
            self._billing_portal_hint(state)
            return
        if currently_on:
            print(
                f'  Сейчас: при балансе ниже {format_money(ar.threshold_usd)} → пополнить до {format_money(ar.reload_to_usd)}'
            )

        if not getattr(self, "_app", None):
            print('  Для настройки автопополнения откройте интерактивный терминал Korra.')
            self._billing_portal_hint(state)
            return

        # When already enabled, let the user turn it off without re-entering values.
        if currently_on:
            top_choices = [
                ("edit", 'Изменить пороги', 'изменить условия и сумму пополнения'),
                ("off", 'Выключить', 'отключить автопополнение'),
                ("cancel", 'Отмена', 'ничего не менять'),
            ]
            raw = self._prompt_text_input_modal(
                title='Автопополнение',
                detail=(
                    f'Включено: если ниже {format_money(ar.threshold_usd)} → пополнить до {format_money(ar.reload_to_usd)}'
                ),
                choices=top_choices,
            )
            top = self._normalize_slash_confirm_choice(raw, top_choices)
            if top == "off":
                self._billing_auto_reload_disable(state)
                return
            if top != "edit":
                print('  🟡 Отменено.')
                return

        # Field 1 — threshold (prefilled when editing an existing config).
        cur_thr = format_money(ar.threshold_usd) if currently_on else None
        thr_prompt = '  Если баланс станет ниже (USD)'
        thr_prompt += f" [{cur_thr}]: " if cur_thr else ": "
        threshold_raw = self._prompt_text_input(thr_prompt)
        if threshold_raw is None:
            # None = cancelled (e.g. slash-worker can't prompt off-thread).
            print('  🟡 Отменено.')
            return
        if not (threshold_raw or "").strip() and currently_on:
            threshold_amt = ar.threshold_usd  # keep current value on empty input
        else:
            tv = validate_charge_amount(
                threshold_raw or "", min_usd=state.min_usd, max_usd=state.max_usd
            )
            if not tv.ok or tv.amount is None:
                print(f"  🔴 {tv.error}")
                return
            threshold_amt = tv.amount

        # Field 2 — reload-to (prefilled when editing an existing config).
        cur_rel = format_money(ar.reload_to_usd) if currently_on else None
        rel_prompt = '  Пополнять баланс до (USD)'
        rel_prompt += f" [{cur_rel}]: " if cur_rel else ": "
        reload_raw = self._prompt_text_input(rel_prompt)
        if reload_raw is None:
            print('  🟡 Отменено.')
            return
        if not (reload_raw or "").strip() and currently_on:
            reload_amt = ar.reload_to_usd  # keep current value on empty input
        else:
            rv = validate_charge_amount(
                reload_raw or "", min_usd=state.min_usd, max_usd=state.max_usd
            )
            if not rv.ok or rv.amount is None:
                print(f"  🔴 {rv.error}")
                return
            reload_amt = rv.amount

        if reload_amt is None or threshold_amt is None or reload_amt <= threshold_amt:
            print('  🔴 Сумма пополнения должна быть больше порога срабатывания.')
            return

        print()
        _ar_consent = (
            f'Подтверждая, вы разрешаете Nous Research списывать средства с {card.masked} каждый раз, когда ваш баланс достигает {format_money(threshold_amt)}. Отключить можно здесь или на сайте в любой момент.'
        )
        _cprint(f"  {_d(_ar_consent)}")
        confirm_choices = [
            ("agree", 'Согласиться и включить', 'включить автопополнение'),
            ("cancel", 'Отмена', 'ничего не менять'),
        ]
        raw = self._prompt_text_input_modal(
            title='Включить автопополнение?',
            detail=f'Ниже {format_money(threshold_amt)} → пополнить до {format_money(reload_amt)}',
            choices=confirm_choices,
        )
        choice = self._normalize_slash_confirm_choice(raw, confirm_choices)
        if choice != "agree":
            print('  🟡 Отменено.')
            return

        from korra_cli.nous_billing import (
            BillingError,
            BillingScopeRequired,
            patch_auto_top_up,
        )

        try:
            patch_auto_top_up(
                enabled=True, threshold=float(threshold_amt), top_up_amount=float(reload_amt)
            )
        except BillingScopeRequired:
            self._billing_handle_scope_required(state)
            return
        except BillingError as exc:
            self._billing_render_charge_error(state, exc)
            return
        print(f'  ✅ Автопополнение включено: при балансе ниже {format_money(threshold_amt)} → пополнить до {format_money(reload_amt)}.')

    def _billing_auto_reload_disable(self, state):
        """Turn off auto-reload (PATCH ``enabled:false``).

        The endpoint requires ``threshold``/``topUpAmount`` in the body even when
        disabling, so we echo back the current values (falling back to 0).
        """
        from korra_cli.nous_billing import (
            BillingError,
            BillingScopeRequired,
            patch_auto_top_up,
        )

        ar = state.auto_reload
        thr = float(ar.threshold_usd) if ar and ar.threshold_usd is not None else 0.0
        rel = float(ar.reload_to_usd) if ar and ar.reload_to_usd is not None else 0.0
        try:
            patch_auto_top_up(enabled=False, threshold=thr, top_up_amount=rel)
        except BillingScopeRequired:
            self._billing_handle_scope_required(state)
            return
        except BillingError as exc:
            self._billing_render_charge_error(state, exc)
            return
        print('  ✅ Автопополнение выключено.')

    def _billing_limit_screen(self, state):
        """Screen 5 — monthly spend limit (read-only; cap is portal-only)."""
        from cli import _cprint, _b, _d

        from agent.billing_view import format_money

        print()
        _cprint(f"  💳 {_b('Месячный лимит расходов')}")
        print(f"  {'─' * 41}")
        cap = state.monthly_cap
        if cap is None or cap.limit_usd is None:
            _cprint(f"  {_d('Месячный лимит не указан. Он настраивается на сайте.')}")
        else:
            spent = format_money(cap.spent_this_month_usd)
            limit = format_money(cap.limit_usd)
            ceiling = ' (лимит по умолчанию)' if cap.is_default_ceiling else ""
            print(f'  {spent} из {limit} использовано за месяц{ceiling}')
        _limit_note = (
            'Месячный лимит задаётся на сайте; в терминале он доступен только для просмотра.'
        )
        _cprint(f"  {_d(_limit_note)}")
        self._billing_portal_hint(state)
