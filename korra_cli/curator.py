"""CLI subcommand: `hermes curator <subcommand>`.

Thin shell around agent/curator.py and tools/skill_usage.py. Renders a status
table, triggers a run, pauses/resumes, and pins/unpins skills.

This module intentionally has no side effects at import time — main.py wires
the argparse subparsers on demand.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _fmt_ts(ts: Optional[str]) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return str(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _print_unmanaged_summary() -> None:
    """Report curation-eligible skills that carry no provenance marker.

    A skill only becomes curator-managed once ``created_by: agent`` lands on
    its usage record, which happens ONLY for background-review creations.
    Skills predating that marker, plus every foreground
    ``skill_manage(create)``, are eligible but unmanaged — no automatic
    transition ever considers them. Printing just the managed count made a
    large library look fully curated while a big slice was untouchable.
    """
    from tools import skill_usage

    try:
        unmanaged = skill_usage.unmanaged_report()
    except Exception:
        return
    if not unmanaged:
        return
    legacy = sum(1 for r in unmanaged if not r.get("has_provenance_key"))
    foreground = len(unmanaged) - legacy
    print(f'Не переданы на обслуживание, без отметки об источнике: {len(unmanaged)}')
    print(f'  Созданы до введения отметок: {legacy}')
    print(f'  Созданы напрямую:           {foreground}')
    print(
        '  Автоматически не архивируются. Передать на обслуживание: korra curator adopt <name>'
    )


def _cmd_status(args) -> int:
    from agent import curator
    from tools import skill_usage

    state = curator.load_state()
    enabled = curator.is_enabled()
    paused = state.get("paused", False)
    last_run = state.get("last_run_at")
    summary = state.get("last_run_summary") or "(none)"
    runs = state.get("run_count", 0)

    status_line = (
        "ENABLED" if enabled and not paused else
        "PAUSED" if paused else
        "DISABLED"
    )
    print(f'Обслуживание навыков: {status_line}')
    print(f'  Запусков:       {runs}')
    print(f'  Последний запуск: {_fmt_ts(last_run)}')
    # Summary may be multi-line when the curator archived skills (the rename
    # map gets appended as `name → umbrella` lines). Indent continuation
    # lines so the block reads as one logical field.
    if "\n" in summary:
        first, *rest = summary.splitlines()
        print(f'  Последний итог: {first}')
        for line in rest:
            print(f"                  {line}")
    else:
        print(f'  Последний итог: {summary}')
    _report = state.get("last_report_path")
    if _report:
        suffix = "" if Path(_report).exists() else " (missing)"
        print(f'  Последний отчёт: {_report}{suffix}')
    _ih = curator.get_interval_hours()
    _interval_label = (
        f"{_ih // 24}d" if _ih % 24 == 0 and _ih >= 24
        else f"{_ih}h"
    )
    print(f'  Интервал:       каждые {_interval_label}')
    print(f'  Считать устаревшим после {curator.get_stale_after_days()} дней без использования')
    print(f'  Архивировать после {curator.get_archive_after_days()} дней без использования')
    print(
        f"  Объединение:    {('вкл.' if curator.get_consolidate() else 'выкл.')}{('' if curator.get_consolidate() else ' (только очистка; объединение моделью включается отдельно)')}"
    )

    rows = skill_usage.curated_report()
    if not rows:
        print('Навыков на обслуживании нет')
        _print_unmanaged_summary()
        return 0

    by_state = {"active": [], "stale": [], "archived": []}
    pinned = []
    agent_count = 0
    bundled_count = 0
    for r in rows:
        state_name = r.get("state", "active")
        by_state.setdefault(state_name, []).append(r)
        if r.get("pinned"):
            pinned.append(r["name"])
        prov = r.get("provenance", "agent")
        if prov == "agent":
            agent_count += 1
        elif prov == "bundled":
            bundled_count += 1

    print(f'Навыков на обслуживании: {len(rows)}; создано агентом: {agent_count}, встроенных: {bundled_count}')
    for state_name in ("active", "stale", "archived"):
        bucket = by_state.get(state_name, [])
        print(f"  {state_name:10s} {len(bucket)}")

    if pinned:
        print(f"Закреплено ({len(pinned)}): {', '.join(pinned)}")

    # Surface the curation blind spot on the managed path too.
    _print_unmanaged_summary()

    # Show top 5 least-recently-active skills. Views and edits are activity too:
    # curator should not report a skill as "never used" right after skill_view()
    # or skill_manage() touched it.
    active = sorted(
        by_state.get("active", []),
        key=lambda r: r.get("last_activity_at") or r.get("created_at") or "",
    )[:5]
    if active:
        print('Пять навыков, которыми дольше всего не пользовались:')
        for r in active:
            last = _fmt_ts(r.get("last_activity_at"))
            print(
                f"  {r['name']:40s}: активность={r.get('activity_count', 0):3d}, использований={r.get('use_count', 0):3d}, просмотров={r.get('view_count', 0):3d}, правок={r.get('patch_count', 0):3d}, последняя активность={last}"
            )

    # Show top 5 most-active and least-active skills by activity_count
    # (use + view + patch). This is a different signal from
    # least-recently-active: activity_count reflects frequency,
    # last_activity_at reflects recency. A skill touched 30 times a year
    # ago is high-frequency but stale; a skill touched once yesterday is
    # recent but low-frequency. Both can matter.
    active_all = by_state.get("active", [])
    if active_all:
        most_active = sorted(
            active_all,
            key=lambda r: (r.get("activity_count") or 0, r.get("last_activity_at") or ""),
            reverse=True,
        )[:5]
        if most_active and (most_active[0].get("activity_count") or 0) > 0:
            print('Пять самых активных навыков:')
            for r in most_active:
                last = _fmt_ts(r.get("last_activity_at"))
                print(
                    f"  {r['name']:40s}: активность={r.get('activity_count', 0):3d}, использований={r.get('use_count', 0):3d}, просмотров={r.get('view_count', 0):3d}, правок={r.get('patch_count', 0):3d}, последняя активность={last}"
                )

        least_active = sorted(
            active_all,
            key=lambda r: (r.get("activity_count") or 0, r.get("last_activity_at") or ""),
        )[:5]
        if least_active:
            print('Пять наименее активных навыков:')
            for r in least_active:
                last = _fmt_ts(r.get("last_activity_at"))
                print(
                    f"  {r['name']:40s}: активность={r.get('activity_count', 0):3d}, использований={r.get('use_count', 0):3d}, просмотров={r.get('view_count', 0):3d}, правок={r.get('patch_count', 0):3d}, последняя активность={last}"
                )

    return 0


def _cmd_run(args) -> int:
    from agent import curator
    if not curator.is_enabled():
        print('Обслуживание отключено. Для включения задайте curator.enabled: true.')
        return 1

    dry = bool(getattr(args, "dry_run", False))
    background = bool(getattr(args, "background", False))
    synchronous = bool(getattr(args, "synchronous", False)) or not background
    # --consolidate forces the LLM umbrella-building pass on for this run,
    # overriding the config default (off). When the flag is absent, pass None
    # so run_curator_review reads curator.consolidate from config.
    consolidate = True if bool(getattr(args, "consolidate", False)) else None
    if dry:
        print('Предварительная проверка навыков: только отчёт, без изменений…')
    else:
        print('Запускаем проверку навыков…')
    if consolidate is None and not curator.get_consolidate():
        print(
            'Объединение выключено: выполняется только проверка устаревания и архивирование. Для объединения моделью добавьте --consolidate или задайте curator.consolidate: true.'
        )

    def _on_summary(msg: str) -> None:
        print(msg)

    result = curator.run_curator_review(
        on_summary=_on_summary,
        synchronous=synchronous,
        dry_run=dry,
        consolidate=consolidate,
    )
    auto = result.get("auto_transitions", {})
    if auto:
        if dry:
            print(
                f"Предварительный просмотр: найдено навыков {auto.get('checked', 0)}; статусы не изменены"
            )
        else:
            print(
                f"Автоматически: проверено={auto.get('checked', 0)}, устарело={auto.get('marked_stale', 0)}, архивировано={auto.get('archived', 0)}, возвращено в работу={auto.get('reactivated', 0)}"
            )
    if not synchronous:
        print('Проверка моделью идёт в фоне. Позже посмотрите korra curator status.')
    if dry:
        if synchronous:
            print(
                'Изменения не внесены. Посмотрите отчёт: korra curator status. Применить: korra curator run без параметров.'
            )
        else:
            print(
                'Изменения не внесены. Когда отчёт будет готов, посмотрите korra curator status. Применить: korra curator run без параметров.'
            )
    return 0


def _cmd_pause(args) -> int:
    from agent import curator
    curator.set_paused(True)
    print('Обслуживание навыков приостановлено')
    return 0


def _cmd_resume(args) -> int:
    from agent import curator
    curator.set_paused(False)
    print('Обслуживание навыков возобновлено')
    return 0


def _cmd_pin(args) -> int:
    from tools import skill_usage
    if not skill_usage.is_agent_created(args.skill):
        print(
            f'«{args.skill}» — встроенный навык или навык из каталога. Закрепление недоступно: обслуживаются только навыки, созданные агентом.'
        )
        return 1
    if not skill_usage.set_pinned(args.skill, True):
        print(
            f'Не удалось закрепить «{args.skill}»: защищённый встроенный или внешний навык. Доступные для обслуживания: korra curator list-unmanaged.'
        )
        return 1
    if not skill_usage.is_curator_managed(args.skill):
        # Unmanaged (pre-marker) skills are never touched by auto-transitions,
        # so "will bypass auto-transitions" overstates what this pin does. The
        # pin IS recorded (and now visible in `curator status`, #92993) but
        # only becomes protective once the skill is adopted. Say so, and point
        # at the handover command (#93002).
        print(
            f'Навык «{args.skill}» закреплён. Сейчас он без обслуживания и не меняется автоматически. Передать на обслуживание: korra curator adopt {args.skill}.'
        )
        return 0
    print(f'Навык «{args.skill}» закреплён и защищён от автоматического изменения статуса')
    return 0


def _cmd_unpin(args) -> int:
    from tools import skill_usage
    if not skill_usage.is_agent_created(args.skill):
        print(
            f'«{args.skill}» — встроенный навык или навык из каталога. Откреплять нечего: обслуживаются только навыки, созданные агентом.'
        )
        return 1
    if not skill_usage.set_pinned(args.skill, False):
        print(
            f'Не удалось открепить «{args.skill}»: защищённый встроенный или внешний навык.'
        )
        return 1
    if not skill_usage.is_curator_managed(args.skill):
        print(
            f'Навык «{args.skill}» откреплён. Он без обслуживания и раньше не менялся автоматически.'
        )
        return 0
    print(f'Навык «{args.skill}» откреплён')
    return 0


def _cmd_list_unmanaged(args) -> int:
    """List curation-eligible skills that carry no provenance marker.

    The same population `status` summarizes, itemized. Useful before deciding
    what to hand over with `adopt`.
    """
    from tools import skill_usage

    rows = skill_usage.unmanaged_report()
    if not rows:
        print('Навыков без обслуживания нет; все подходящие уже подключены')
        return 0

    print(f'Навыки без обслуживания ({len(rows)}):')
    for r in sorted(rows, key=lambda x: x["name"]):
        why = "created_by:null" if r.get("has_provenance_key") else "no marker"
        last = _fmt_ts(r.get("last_activity_at"))
        print(
            f"  {r['name']:44s}: активность {r.get('activity_count', 0):4d}, последняя {last:14s} ({why})"
        )
    print('Передать один: korra curator adopt <name>. Передать все: korra curator adopt --all-unmanaged.')
    return 0


def _cmd_adopt(args) -> int:
    """Hand unmanaged skills to the curator by explicit user declaration.

    Provenance cannot be inferred from telemetry: a high patch count proves
    the agent MAINTAINS a skill, not that it AUTHORED it (the agent edits
    user-written skills on the user's behalf constantly). So adoption is never
    automatic — the user names what they're handing over, or passes
    ``--all-unmanaged`` to hand over every eligible skill at once.
    """
    from tools import skill_usage

    names = list(getattr(args, "skill", None) or [])
    adopt_all = bool(getattr(args, "all_unmanaged", False))
    if adopt_all:
        if names:
            print('Укажите имена навыков или --all-unmanaged, но не вместе')
            return 1
        names = skill_usage.list_unmanaged_skill_names()
        if not names:
            print('Нет навыков для передачи на обслуживание')
            return 0
    if not names:
        print('Укажите навык или добавьте --all-unmanaged')
        return 1

    dry_run = bool(getattr(args, "dry_run", False))
    if dry_run:
        print(f'План передачи на обслуживание, без изменений; навыков: {len(names)}')
        for n in names:
            print(f"  + {n}")
        return 0

    # Bulk adoption is a real lifecycle change (adopted skills become
    # archivable), so confirm unless the caller opted out.
    if adopt_all and not bool(getattr(args, "yes", False)):
        print(f'Передать на обслуживание навыки ({len(names)})?')
        print('  После этого они смогут автоматически устаревать и попадать в архив.')
        try:
            reply = input('  Продолжить? [y/N] ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        if reply not in {"y", "yes"}:
            print('Действие отменено')
            return 1

    failed = 0
    for n in names:
        ok, msg = skill_usage.adopt_skill(n)
        print(f'Обслуживание навыков: {msg}')
        if not ok:
            failed += 1
    if len(names) > 1:
        print(f'Передано на обслуживание: {len(names) - failed}/{len(names)}')
    return 1 if failed else 0


def _cmd_restore(args) -> int:
    from tools import skill_ledger, skill_usage
    tok = skill_ledger.set_ledger_actor("user")
    try:
        ok, msg = skill_usage.restore_skill(args.skill)
    finally:
        skill_ledger.reset_ledger_actor(tok)
    print(f'Обслуживание навыков: {msg}')
    return 0 if ok else 1


def _cmd_archive(args) -> int:
    """Manually archive an agent-created skill. Refuses if pinned.

    The auto-curator archives stale skills on its own schedule; this verb is
    for the user who wants to archive *now* without waiting for a run.
    """
    from tools import skill_ledger, skill_usage
    if skill_usage.get_record(args.skill).get("pinned"):
        print(
            f'Навык «{args.skill}» закреплён. Сначала открепите: korra curator unpin {args.skill}.'
        )
        return 1
    tok = skill_ledger.set_ledger_actor("user")
    try:
        ok, msg = skill_usage.archive_skill(args.skill)
    finally:
        skill_ledger.reset_ledger_actor(tok)
    print(f'Обслуживание навыков: {msg}')
    return 0 if ok else 1


def _idle_days(record: dict) -> Optional[int]:
    """Days since the skill's last activity (view / use / patch).

    Falls back to ``created_at`` so a skill that was authored but never used
    can still be pruned — otherwise never-touched skills would be immortal.
    Returns None only when both fields are missing or unparseable.
    """
    ts = record.get("last_activity_at") or record.get("created_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def _cmd_prune(args) -> int:
    """Bulk-archive curator-managed skills idle for >= N days.

    Pinned skills are exempt. Already-archived skills are skipped. Default
    ``--days 90`` matches a conservative read of the curator's own archive
    threshold; adjust with ``--days``. Use ``--dry-run`` to preview.
    """
    from tools import skill_usage
    days = getattr(args, "days", 90)
    if days < 1:
        print(f'--days должен быть не меньше 1; получено {days}', file=sys.stderr)
        return 2

    dry_run = bool(getattr(args, "dry_run", False))
    skip_confirm = bool(getattr(args, "yes", False))

    candidates = []
    for r in skill_usage.curated_report():
        if r.get("pinned"):
            continue
        if r.get("state") == skill_usage.STATE_ARCHIVED:
            continue
        idle = _idle_days(r)
        if idle is None or idle < days:
            continue
        candidates.append((r["name"], idle))

    if not candidates:
        print(f'Очищать нечего: нет незакреплённых навыков без активности {days} дней или дольше')
        return 0

    candidates.sort(key=lambda c: -c[1])
    print(f'Навыков без активности {days} дней или дольше: {len(candidates)}')
    for name, idle in candidates:
        print(f'  {name:40s}: без активности {idle} дней')

    if dry_run:
        print('Предварительный просмотр: изменения не внесены')
        return 0

    if not skip_confirm:
        try:
            reply = input(f'Архивировать навыки ({len(candidates)})? [y/N] ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print('Действие отменено')
            return 1
        if reply not in {"y", "yes"}:
            print('Действие отменено')
            return 1

    archived = 0
    failures = []
    for name, _ in candidates:
        ok, msg = skill_usage.archive_skill(name)
        if ok:
            archived += 1
        else:
            failures.append((name, msg))

    print(f'Архивировано: {archived}/{len(candidates)}')
    if failures:
        print('Ошибки:')
        for name, msg in failures:
            print(f"  {name}: {msg}")
        return 1
    return 0


def _cmd_backup(args) -> int:
    """Take a manual snapshot of the skills tree. Same mechanism as the
    automatic pre-run snapshot, just user-initiated."""
    from agent import curator_backup
    if not curator_backup.is_enabled():
        print(
            'Резервные копии отключены через curator.backup.enabled: false. Включите их для создания снимка.'
        )
        return 1
    reason = getattr(args, "reason", None) or "manual"
    snap = curator_backup.snapshot_skills(reason=reason)
    if snap is None:
        print('Не удалось создать снимок: резервирование отключено или произошла ошибка записи. См. журналы.')
        return 1
    print(f'Снимок навыков создан: ~/.hermes/skills/.curator_backups/{snap.name}')
    return 0


def _cmd_ledger(args) -> int:
    """List per-mutation audit ledger entries (newest first)."""
    from tools import skill_ledger

    rows = skill_ledger.list_entries(
        skill=getattr(args, "skill", None),
        limit=getattr(args, "limit", None) or 20,
    )
    if not rows:
        print('Журнал изменений пуст либо отключён через skills.ledger.')
        return 0
    print(f"{'id':<14} {'when':<12} {'actor':<8} {'action':<12} навык")
    for r in rows:
        evidence = r.get("evidence") or {}
        extra = ""
        if evidence.get("absorbed_into"):
            extra = f"  → absorbed into '{evidence['absorbed_into']}'"
        elif evidence.get("rollback_target"):
            extra = f"  → rollback of {evidence['rollback_target']}"
        print(
            f"{r.get('id', '?'):<14} {_fmt_ts(r.get('ts')):<12} "
            f"{r.get('actor', '?'):<8} {r.get('action', '?'):<12} "
            f"{r.get('skill', '?')}{extra}"
        )
    print(
        'Отмена одного изменения: korra curator rollback <id>. Снимки всей папки: korra curator rollback --list.'
    )
    return 0


def _cmd_purge(args) -> int:
    """Delete archived skills older than curator.archive_ttl_days.

    Explicit command only — never runs automatically. Respects the ledger:
    each purged skill is captured (before-blobs) and recorded as a 'purge'
    entry, so even a purge is auditable and blob-recoverable.
    """
    from korra_cli.config import cfg_get, load_config
    from tools import skill_ledger
    from tools.skill_usage import _archive_dir

    ttl_days = getattr(args, "days", None)
    if ttl_days is None:
        ttl_days = int(cfg_get(load_config(), "curator", "archive_ttl_days", default=0) or 0)
    if ttl_days <= 0:
        print(
            'Удаление архивов отключено: curator.archive_ttl_days равен 0. Задайте число дней в настройке или через --days N.'
        )
        return 1

    archive_root = _archive_dir()
    if not archive_root.exists():
        print('Папки архивов нет, удалять нечего.')
        return 0

    import shutil
    import time

    cutoff = time.time() - ttl_days * 86400
    candidates = [
        p for p in archive_root.iterdir()
        if p.is_dir() and p.stat().st_mtime < cutoff
    ]
    if not candidates:
        print(f'Нет архивных навыков старше {ttl_days} дней.')
        return 0

    print(f'Архивные навыки старше {ttl_days} дней:')
    for p in sorted(candidates):
        print(f"  {p.name}")
    if getattr(args, "dry_run", False):
        print('Предварительный просмотр: ничего не удалено')
        return 0
    if not getattr(args, "yes", False):
        try:
            ans = input(f'Удалить архивные навыки ({len(candidates)}) безвозвратно? [y/N] ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print('Отменено')
            return 1
        if ans not in {"y", "yes"}:
            print('Отменено')
            return 1

    purged = 0
    for p in sorted(candidates):
        before = skill_ledger.capture_before(
            p, complete_package=True, skill=p.name
        )
        try:
            shutil.rmtree(p)
        except OSError as e:
            print(f'Не удалось удалить архив {p.name}: {e}')
            continue
        skill_ledger.append_entry(
            "purge",
            p.name,
            before=before or [],
            after=[],
            actor="user",
            evidence={"ttl_days": ttl_days},
        )
        purged += 1
    print(f'Удалено архивных навыков: {purged}. Записи добавлены в журнал.')
    return 0


def _cmd_rollback(args) -> int:
    """Restore the skills tree from a snapshot, or a single mutation from
    the audit ledger.

    With a positional ``entry_id``, restores exactly the files touched by
    that one ledger entry (from content-addressed blobs), taking a
    pre-rollback safety ledger entry first — and failing closed when that
    safety capture fails. Without it, behaves as before: whole-tree tarball
    restore. ``--list`` prints available snapshots and exits. ``--id
    <stamp>`` picks a specific snapshot. Without ``-y``, prompts for
    confirmation. A safety snapshot of the current tree is always taken
    first, so rollbacks are themselves undoable.
    """
    from agent import curator_backup

    entry_id = getattr(args, "entry_id", None)
    if entry_id:
        from tools import skill_ledger

        entry = skill_ledger.get_entry(entry_id)
        if entry is None:
            print(
                f'Запись журнала «{entry_id}» не найдена. ID записей: korra curator ledger. Для снимка всей папки используйте --id <snapshot>.'
            )
            return 1
        print(f'Восстановить состояние до записи журнала {entry_id}')
        print(f"  Действие: {entry.get('action', '?')}")
        print(f"  Навык:    {entry.get('skill', '?')}")
        print(f"  Автор:    {entry.get('actor', '?')}")
        print(f"  Время:    {entry.get('ts', '?')}")
        touched = {i.get("path") for i in (entry.get("before") or []) + (entry.get("after") or [])}
        print(f'  Файлы:    {len(touched)}')
        if not getattr(args, "yes", False):
            try:
                ans = input('Восстановить состояние до этого изменения? [y/N] ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                print('Отменено')
                return 1
            if ans not in {"y", "yes"}:
                print('Отменено')
                return 1
        ok, msg = skill_ledger.rollback_entry(entry_id)
        if ok:
            print(f'Обслуживание навыков: {msg}')
            return 0
        print(f'Не удалось восстановить навыки: {msg}')
        return 1

    if getattr(args, "list", False):
        print(curator_backup.summarize_backups())
        return 0

    backup_id = getattr(args, "backup_id", None)
    target_path = curator_backup._resolve_backup(backup_id)
    if target_path is None:
        rows = curator_backup.list_backups()
        if not rows:
            print(
                'Снимков пока нет. Создайте korra curator backup или дождитесь следующего обслуживания.'
            )
        else:
            print(
                f"Снимок по запросу {('id ' + repr(backup_id) if backup_id else 'ваш запрос')} не найден."
            )
            print('Доступно:')
            print(curator_backup.summarize_backups())
        return 1

    manifest = curator_backup._read_manifest(target_path)
    print(f'Восстановить: {target_path.name}')
    if manifest:
        print(f"  Причина:      {manifest.get('reason', '?')}")
        print(f"  Создано:      {manifest.get('created_at', '?')}")
        print(f"  Файлов навыков: {manifest.get('skill_files', '?')}")
        cron = manifest.get("cron_jobs") or {}
        if isinstance(cron, dict):
            if cron.get("backed_up"):
                print(
                    f"  Задач расписания: {cron.get('jobs_count', 0)}; восстановятся только связи с навыками"
                )
            else:
                reason = cron.get("reason", "not captured")
                print(f'  Задачи расписания отсутствуют в снимке: {reason}')
    print(
        'Текущая папка навыков будет заменена. Сначала создаётся резервный снимок для отмены. В существующих задачах расписания восстановятся только поля skills/skill, остальные поля сохранятся.'
    )

    if not getattr(args, "yes", False):
        try:
            ans = input('Продолжить? [y/N] ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print('Отменено')
            return 1
        if ans not in {"y", "yes"}:
            print('Отменено')
            return 1

    ok, msg, _ = curator_backup.rollback(backup_id=target_path.name)
    if ok:
        print(f'Обслуживание навыков: {msg}')
        return 0
    print(f'Не удалось восстановить навыки: {msg}')
    return 1


def _cmd_list_archived(args) -> int:
    """List archived (recoverable) skills."""
    from tools import skill_usage
    names = skill_usage.list_archived_skill_names()
    if not names:
        print('Архивных навыков нет')
        return 0
    for name in names:
        print(name)
    return 0


def _cmd_usage(args) -> int:
    """Show usage telemetry for ALL skills, with provenance.

    Unlike `status` (curator-scoped to curated candidates), this lists
    every skill on disk — bundled built-ins and hub-installed included — so you
    can see how often each is actually used regardless of curation.
    """
    import json as _json
    from tools import skill_usage

    rows = skill_usage.usage_report()

    prov_filter = getattr(args, "provenance", None)
    if prov_filter:
        rows = [r for r in rows if r.get("provenance") == prov_filter]

    sort_key = getattr(args, "sort", "activity")
    if sort_key == "name":
        rows.sort(key=lambda r: r["name"])
    elif sort_key == "recent":
        # Most-recently-active first; never-active sinks to the bottom.
        rows.sort(key=lambda r: r.get("last_activity_at") or "", reverse=True)
    else:  # "activity" (default): most-used first
        rows.sort(key=lambda r: r.get("activity_count", 0), reverse=True)

    if getattr(args, "json", False):
        print(_json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print('Навыки не найдены')
        return 0

    # Provenance tallies for a quick header.
    counts = {"agent": 0, "bundled": 0, "hub": 0}
    for r in rows:
        counts[r.get("provenance", "agent")] = counts.get(r.get("provenance", "agent"), 0) + 1
    print(
        f"Всего навыков: {len(rows)}; создано агентом: {counts['agent']}, встроенных: {counts['bundled']}, из каталога: {counts['hub']}"
    )
    print()
    print(
        f"  {'skill':40s}  {'origin':8s}  {'use':>4s}  {'view':>4s}  {'patch':>5s}  {'act':>4s}  Последняя активность"
    )
    for r in rows:
        last = _fmt_ts(r.get("last_activity_at"))
        print(
            f"  {r['name'][:40]:40s}  "
            f"{r.get('provenance', 'agent'):8s}  "
            f"{r.get('use_count', 0):>4d}  "
            f"{r.get('view_count', 0):>4d}  "
            f"{r.get('patch_count', 0):>5d}  "
            f"{r.get('activity_count', 0):>4d}  "
            f"{last}"
        )
    return 0


# ---------------------------------------------------------------------------
# argparse wiring (called from korra_cli.main)
# ---------------------------------------------------------------------------

def register_cli(parent: argparse.ArgumentParser) -> None:
    """Attach `curator` subcommands to *parent*.

    main.py calls this with the ArgumentParser returned by
    ``subparsers.add_parser("curator", ...)``.
    """
    parent.set_defaults(func=lambda a: (parent.print_help(), 0)[1])
    subs = parent.add_subparsers(dest="curator_command")

    p_status = subs.add_parser("status", help='Показать состояние обслуживания и статистику навыков')
    p_status.set_defaults(func=_cmd_status)

    p_usage = subs.add_parser(
        "usage",
        help='Показать статистику использования всех навыков и их источники: встроенные, из каталога, созданные агентом',
    )
    p_usage.add_argument(
        "--sort", choices=("activity", "recent", "name"), default="activity",
        help='Сортировка: activity — часто используемые (по умолчанию), recent — недавние, name — по имени',
    )
    p_usage.add_argument(
        "--provenance", choices=("agent", "bundled", "hub"), default=None,
        help='Показать навыки только этого происхождения',
    )
    p_usage.add_argument(
        "--json", action="store_true",
        help='Вывести полный отчёт JSON вместо таблицы',
    )
    p_usage.set_defaults(func=_cmd_usage)

    p_run = subs.add_parser("run", help='Запустить проверку навыков сейчас')
    p_run.add_argument(
        "--sync", "--synchronous", dest="synchronous", action="store_true",
        help='Дождаться проверки моделью; по умолчанию для ручного запуска',
    )
    p_run.add_argument(
        "--background", dest="background", action="store_true",
        help='Начать проверку моделью в фоне и сразу вернуть управление',
    )
    p_run.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help='Только отчёт: без изменения состояния, архивирования и объединения навыков',
    )
    p_run.add_argument(
        "--consolidate", dest="consolidate", action="store_true",
        help='Включить объединение навыков моделью для этого запуска. Без параметра выполняется только очистка, если не задано curator.consolidate: true.',
    )
    p_run.set_defaults(func=_cmd_run)

    p_pause = subs.add_parser("pause", help='Приостановить обслуживание навыков до возобновления')
    p_pause.set_defaults(func=_cmd_pause)

    p_resume = subs.add_parser("resume", help='Возобновить обслуживание навыков')
    p_resume.set_defaults(func=_cmd_resume)

    p_pin = subs.add_parser("pin", help='Закрепить навык и защитить от автоматического изменения статуса')
    p_pin.add_argument("skill", help='Имя навыка')
    p_pin.set_defaults(func=_cmd_pin)

    p_unpin = subs.add_parser("unpin", help='Открепить навык')
    p_unpin.add_argument("skill", help='Имя навыка')
    p_unpin.set_defaults(func=_cmd_unpin)

    subs.add_parser(
        "list-unmanaged",
        help='Показать подходящие для обслуживания навыки без отметки об источнике',
    ).set_defaults(func=_cmd_list_unmanaged)

    p_adopt = subs.add_parser(
        "adopt",
        help='Передать неуправляемые навыки на обслуживание; источник декларирует пользователь',
    )
    p_adopt.add_argument(
        "skill", nargs="*",
        help='Имена передаваемых навыков; не указываются с --all-unmanaged',
    )
    p_adopt.add_argument(
        "--all-unmanaged", action="store_true",
        help='Передать все подходящие навыки без отметки об источнике',
    )
    p_adopt.add_argument(
        "--dry-run", action="store_true",
        help='Показать план передачи без записи',
    )
    p_adopt.add_argument(
        "--yes", action="store_true",
        help='Пропустить подтверждение при --all-unmanaged',
    )
    p_adopt.set_defaults(func=_cmd_adopt)

    p_restore = subs.add_parser("restore", help='Восстановить навык из архива')
    p_restore.add_argument("skill", help='Имя навыка')
    p_restore.set_defaults(func=_cmd_restore)

    subs.add_parser("list-archived", help='Показать архивные навыки') \
        .set_defaults(func=_cmd_list_archived)

    p_archive = subs.add_parser(
        "archive",
        help='Архивировать навык вручную: переместить в .archive/ и убрать из контекста',
    )
    p_archive.add_argument("skill", help='Имя навыка')
    p_archive.set_defaults(func=_cmd_archive)

    p_prune = subs.add_parser(
        "prune",
        help='Архивировать обслуживаемые навыки, не использовавшиеся N дней или дольше (по умолчанию 90)',
    )
    p_prune.add_argument(
        "--days", type=int, default=90,
        help='Архивировать навыки, не использовавшиеся минимум N дней (по умолчанию 90)',
    )
    p_prune.add_argument(
        "-y", "--yes", action="store_true",
        help='Пропустить запрос подтверждения',
    )
    p_prune.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help='Показать план архивирования без изменений',
    )
    p_prune.set_defaults(func=_cmd_prune)

    p_backup = subs.add_parser(
        "backup",
        help='Создать снимок папки навыков в tar.gz; также создаётся автоматически перед каждым запуском обслуживания с изменениями',
    )
    p_backup.add_argument(
        "--reason", default=None,
        help='Произвольная метка в manifest.json (по умолчанию manual)',
    )
    p_backup.set_defaults(func=_cmd_backup)

    p_rollback = subs.add_parser(
        "rollback",
        help='Восстановить навыки из снимка или отменить одно изменение по ID журнала; см. korra curator ledger',
    )
    p_rollback.add_argument(
        "entry_id", nargs="?", default=None,
        help='ID записи журнала для отмены одного изменения; без параметра восстанавливается снимок всей папки',
    )
    p_rollback.add_argument(
        "--list", action="store_true",
        help='Показать снимки и выйти без восстановления',
    )
    p_rollback.add_argument(
        "--id", dest="backup_id", default=None,
        help='ID восстанавливаемого снимка из --list; по умолчанию последний',
    )
    p_rollback.add_argument(
        "-y", "--yes", action="store_true",
        help='Пропустить запрос подтверждения',
    )
    p_rollback.set_defaults(func=_cmd_rollback)

    p_ledger = subs.add_parser(
        "ledger",
        help='Показать журнал изменений навыков, сделанных обслуживанием, агентом и пользователем',
    )
    p_ledger.add_argument(
        "--skill", default=None,
        help='Показать записи только этого навыка',
    )
    p_ledger.add_argument(
        "--limit", type=int, default=20,
        help='Максимум записей (по умолчанию 20)',
    )
    p_ledger.set_defaults(func=_cmd_ledger)

    p_purge = subs.add_parser(
        "purge",
        help='Удалить архивные навыки старше curator.archive_ttl_days. Только вручную, с записью в журнал.',
    )
    p_purge.add_argument(
        "--days", type=int, default=None,
        help='Заменить curator.archive_ttl_days для этого запуска',
    )
    p_purge.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help='Показать план очистки без удаления',
    )
    p_purge.add_argument(
        "-y", "--yes", action="store_true",
        help='Пропустить запрос подтверждения',
    )
    p_purge.set_defaults(func=_cmd_purge)


def cli_main(argv=None) -> int:
    """Standalone entry (also usable by korra_cli.main fallthrough)."""
    parser = argparse.ArgumentParser(prog="hermes curator")
    register_cli(parser)
    args = parser.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn is None:
        parser.print_help()
        return 0
    return int(fn(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli_main())
