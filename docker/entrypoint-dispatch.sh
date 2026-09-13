#!/bin/sh
# shellcheck shell=sh
# Entry-point dispatcher for runtimes that may or may not give the image
# ownership of PID 1.
#
# Normal Docker / Podman path: this script is PID 1, so we delegate to
# s6-overlay's /init exactly as before and keep the full supervision tree.
#
# Wrapped-runtime path (Fly Machines, `docker run --init`, some Nomad/K8s
# setups): the platform's own init is already PID 1 and execs the image
# entrypoint as a child. s6-overlay aborts there with "can only run as pid 1",
# so we run the stage2 bootstrap directly and then exec the main wrapper
# without /init.

set -e

# K21-038: одноразовые команды CLI (`import`, `backup`, `config`) идут мимо
# /init. Иначе s6 сперва поднимает пользовательские службы, dashboard и шлюзы
# профилей открывают целевой DATA, и `korra import` на полностью остановленном
# контуре отказывается работать: «не удалось проверить всех держателей DATA».
# Бутстрап stage2 при этом выполняется — владелец тома, права и конфиг нужны и
# здесь; не выполняется только подъём служб, которые такой команде мешают.
# Путь тот же, которым уже живут обёрнутые runtime (см. ниже).
if sh /opt/hermes/docker/cli-role.sh oneshot "$@"; then
    echo "[korra] одноразовая команда CLI: пользовательские службы не поднимаются (K21-038)" >&2
    export PATH="/command:/package/admin/s6/command:${PATH}"
    KORRA_ONESHOT_CLI=1 /opt/hermes/docker/stage2-hook.sh
    exec /opt/hermes/docker/main-wrapper.sh "$@"
fi

if [ "$$" -eq 1 ]; then
    exec /init /opt/hermes/docker/main-wrapper.sh "$@"
fi

echo "[hermes] WARNING: container entrypoint is not PID 1; skipping s6-overlay /init and falling back to direct bootstrap. Supervised services are unavailable in this runtime, but the requested command will still run." >&2
# /init normally seeds PATH with s6's helpers; the non-PID-1 fallback skips it.
export PATH="/command:/package/admin/s6/command:${PATH}"
/opt/hermes/docker/stage2-hook.sh
exec /opt/hermes/docker/main-wrapper.sh "$@"
