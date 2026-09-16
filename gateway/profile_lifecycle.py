"""Reconcile multiplex profile membership without restarting the gateway."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("gateway.run")
PROFILE_SCAN_SECONDS = 5


def profile_homes_snapshot(config):
    """Read published profiles, never a half-written create/clone/template.

    Call from a worker thread: a concurrent profile creation can hold the
    cross-process publication lock while copying its files.
    """
    from korra_cli.profiles import profile_creation_lock, profiles_to_serve

    with profile_creation_lock():
        return list(
            profiles_to_serve(
                multiplex=True,
                profile_allowlist=getattr(config, "multiplex_profile_allowlist", None),
            )
        )


def _home_identity(home):
    try:
        stat = Path(home).stat()
        return str(home), stat.st_dev, stat.st_ino
    except FileNotFoundError:
        return str(home), None, None


@dataclass(eq=False)
class ProfileGeneration:
    home: Path
    identity: tuple


def profile_generation(runner, name):
    return getattr(runner, "_live_profile_generations", {}).get(name)


def profile_is_current(runner, name, generation):
    generations = getattr(runner, "_live_profile_generations", None)
    if generations is None:
        # Legacy single-adapter callers have no multiplex lifecycle owner.
        return True
    if generation is None or generations.get(name) is not generation:
        return False
    from korra_cli.profiles import named_profile_is_deleted, normalize_profile_name

    allowed = getattr(runner.config, "multiplex_profile_allowlist", None)
    return (
        _home_identity(generation.home) == generation.identity
        and not named_profile_is_deleted(generation.home)
        and (
            allowed is None
            or name
            in {
                normalize_profile_name(entry)
                for entry in allowed
                if isinstance(entry, str)
            }
        )
    )


def adapter_claims(runner):
    """Include live and connecting adapters, plus primary retry reservations."""
    from korra_cli.profiles import get_active_profile_name

    active = get_active_profile_name() or "default"
    claimed = {}
    maps = [(active, getattr(runner, "adapters", {}))]
    maps.extend(getattr(runner, "_profile_adapters", {}).items())
    for name, adapters in maps:
        for platform, adapter in adapters.items():
            for claim in (
                runner._adapter_credential_claim(platform, adapter),
                runner._adapter_listener_claim(platform, adapter),
            ):
                if claim is not None:
                    claimed[claim] = name
    for info in getattr(runner, "_failed_platforms", {}).values():
        for key in ("credential_claim", "listener_claim"):
            if isinstance(info.get(key), tuple):
                claimed[info[key]] = active
    for (name, _platform), (_adapter, claims) in getattr(
        runner, "_connecting_profile_adapters", {}
    ).items():
        for claim in claims:
            claimed[claim] = name
    return claimed


def reserve_connecting_adapter(runner, name, platform, adapter):
    claims = tuple(
        c
        for c in (
            runner._adapter_credential_claim(platform, adapter),
            runner._adapter_listener_claim(platform, adapter),
        )
        if c is not None
    )
    if not hasattr(runner, "_connecting_profile_adapters"):
        runner._connecting_profile_adapters = {}
    runner._connecting_profile_adapters[name, platform] = (adapter, claims)


def release_connecting_adapter(runner, name, platform, adapter):
    pending = getattr(runner, "_connecting_profile_adapters", {})
    entry = pending.get((name, platform))
    if entry is not None and entry[0] is adapter:
        pending.pop((name, platform))


async def remove_profile(runner, name):
    """Invalidate first so cancelled/late connects cannot revive this profile."""
    runner._live_profile_generations.pop(name, None)
    pending = (getattr(runner, "_profile_failed_platforms", None) or {}).pop(name, {})
    tasks = [task for task in pending.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=runner._adapter_disconnect_timeout_secs())
    adapters = runner._profile_adapters.pop(name, {})
    for platform, adapter in adapters.items():
        await runner._bounded_adapter_teardown(adapter, platform, profile=name)
    for attr in (
        "pairing_stores",
        "_busy_input_modes_by_profile",
        "_busy_text_modes_by_profile",
    ):
        getattr(runner, attr, {}).pop(name, None)
    logger.info("Stopped serving removed multiplex profile '%s'", name)


async def reconcile_profiles(runner):
    from korra_cli.profiles import get_active_profile_name
    from gateway.run import MultiplexConfigError, SecondaryPortBindingConfigError
    from gateway.pairing import PairingStore
    from gateway.status import write_runtime_status

    if not getattr(runner.config, "multiplex_profiles", False):
        return 0
    live = getattr(runner, "_running", False)
    active = get_active_profile_name() or "default"
    homes = await asyncio.to_thread(profile_homes_snapshot, runner.config)
    if live and not runner._running:
        return 0
    if not hasattr(runner, "_live_profile_generations"):
        runner._live_profile_generations = {}
    generations = runner._live_profile_generations
    desired = {name: home for name, home in homes if name != active}
    for name, generation in list(generations.items()):
        if name not in desired or _home_identity(desired[name]) != generation.identity:
            await remove_profile(runner, name)

    connected = 0
    for name, home in desired.items():
        if name in generations:
            continue
        if live and not runner._running:
            return connected
        identity = _home_identity(home)
        if identity[1] is None:
            # Deleted after enumeration: scoped plugin/config setup can mkdir
            # this path, so do not even enter startup for the stale entry.
            continue
        generations[name] = ProfileGeneration(Path(home), identity)
        try:
            connected += await runner._start_one_profile_adapters(
                name, home, adapter_claims(runner)
            )
        except SecondaryPortBindingConfigError as exc:
            logger.warning(
                "Skipping secondary profile '%s' due to port-binding config error: %s",
                name,
                exc,
            )
        except MultiplexConfigError:
            if not live:
                raise
            # A newly added unsafe bot must not stop already working profiles.
            logger.exception(
                "Skipping secondary profile '%s' due to security config error", name
            )
        except Exception:
            logger.exception("Failed to start adapters for profile '%s'", name)
            await remove_profile(runner, name)

    if live and not runner._running:
        return connected
    # Re-read after network awaits; never publish a profile deleted mid-connect.
    homes = await asyncio.to_thread(profile_homes_snapshot, runner.config)
    desired = {name: home for name, home in homes if name != active}
    for name, generation in list(generations.items()):
        if name not in desired or _home_identity(desired[name]) != generation.identity:
            await remove_profile(runner, name)
    if live and not runner._running:
        return connected
    # Served means eligible for shared HTTP routing/cron, including profiles
    # without bot adapters. Preserve the existing status contract.
    served = [active] + sorted(
        name for name, home in desired.items() if Path(home).is_dir()
    )
    try:
        for name in served:
            if name not in runner.pairing_stores:
                runner.pairing_stores[name] = (
                    runner.pairing_store
                    if name == active
                    else PairingStore(profile=name)
                )
        write_runtime_status(served_profiles=served)
    except Exception:
        logger.debug("could not record served_profiles", exc_info=True)
    return connected


async def watch_profiles(runner):
    while runner._running:
        await runner._start_secondary_profile_adapters()
        await asyncio.sleep(PROFILE_SCAN_SECONDS)


async def stop_profile_watcher(runner):
    task = getattr(runner, "_profile_watcher_task", None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.wait([task], timeout=runner._adapter_disconnect_timeout_secs())
    # Invalidate startup bridges and any connect that ignores cancellation.
    getattr(runner, "_live_profile_generations", {}).clear()
