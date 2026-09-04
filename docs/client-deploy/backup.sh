#!/usr/bin/env bash
# Бэкап контура Korra 21 в облако клиента.
#
#   ./backup.sh            собрать архив, выгрузить, провернуть ротацию
#   ./backup.sh --local    только собрать архив, без выгрузки
#
# В расписание:
#   15 4 * * * /opt/korra/backup.sh >> /var/log/korra-backup.log 2>&1
#
# Архив собирает штатная команда движка: базы снимаются механизмом SQLite,
# который даёт целостную копию под работающей записью. Простой tar каталога
# данных этого не даёт и кладёт в архив порванную базу.
#
# ОБЛАКО ТОЛЬКО КЛИЕНТСКОЕ. Клиентские данные на наш сервер не выгружаются
# ни временно, ни «на всякий случай».
#
# ПОЧЕМУ ЗДЕСЬ НЕТ set -e. Любой архиватор возвращает ненулевой код на
# безобидных вещах: tar отдаёт 1 на «файл изменился при чтении», выгрузка —
# на моргнувшей сети. С set -e скрипт умирает на этом месте, до ротации, и
# копии накапливаются, пока диск не кончится. Симптом при этом молчаливый:
# бэкап «идёт», а место кончается. Поэтому ошибки собираются в код возврата,
# а ротация вынесена в обработчик выхода и выполняется всегда.
set -uo pipefail

# ─── Параметры ──────────────────────────────────────────────────────────────
NAME="${NAME:-korra}"                       # имя контейнера
DATA="${DATA:-/opt/korra/data}"             # каталог данных на хосте
SUBDIR="${SUBDIR:-backups}"                 # подкаталог архивов внутри данных
ENGINE_UID="${ENGINE_UID:-10000}"           # пользователь движка в контейнере

# Куда выгружать: имя remote в rclone плюс путь. Настраивается на облако
# КЛИЕНТА (`rclone config` под его учёткой). Пусто = выгрузки нет, архив
# остаётся только на сервере — это не бэкап, а полумера.
RCLONE_REMOTE="${RCLONE_REMOTE:-}"

KEEP_LOCAL="${KEEP_LOCAL:-3}"               # сколько архивов держать на сервере
KEEP_REMOTE="${KEEP_REMOTE:-14}"            # сколько архивов держать в облаке

DOCKER_BIN="${DOCKER_BIN:-docker}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"

UPLOAD=1
for arg in "$@"; do
    case "$arg" in
        --local) UPLOAD=0 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^#\( \|$\)//'; exit 0 ;;
        *) echo "Неизвестный ключ: $arg (есть --local)" >&2; exit 2 ;;
    esac
done

HOST_DIR="$DATA/$SUBDIR"
CONTAINER_DIR="/opt/data/$SUBDIR"
STATUS=0

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "ОШИБКА: $*"; STATUS=1; }

# ─── Ротация ────────────────────────────────────────────────────────────────
# Вызывается обработчиком выхода, то есть при любом исходе: и когда архив не
# собрался, и когда не прошла выгрузка. Имена архивов начинаются с даты,
# поэтому обычная сортировка строк = сортировка по времени.
rotate_local() {
    [ "$KEEP_LOCAL" -ge 1 ] 2>/dev/null || return 0
    [ -d "$HOST_DIR" ] || return 0
    local old
    old=$(ls -1 "$HOST_DIR"/korra-*.zip 2>/dev/null | sort | head -n -"$KEEP_LOCAL")
    [ -n "$old" ] || return 0
    printf '%s\n' "$old" | while IFS= read -r f; do
        rm -f -- "$f" && log "убран старый локальный архив: $(basename "$f")"
    done
}

rotate_remote() {
    [ -n "$RCLONE_REMOTE" ] || return 0
    [ "$UPLOAD" = 1 ] || return 0
    [ "$KEEP_REMOTE" -ge 1 ] 2>/dev/null || return 0
    command -v "$RCLONE_BIN" >/dev/null 2>&1 || return 0
    local old
    old=$("$RCLONE_BIN" lsf --files-only "$RCLONE_REMOTE" 2>/dev/null \
          | grep -E '^korra-.*\.zip$' | sort | head -n -"$KEEP_REMOTE")
    [ -n "$old" ] || return 0
    printf '%s\n' "$old" | while IFS= read -r f; do
        "$RCLONE_BIN" deletefile "$RCLONE_REMOTE/$f" >/dev/null 2>&1 \
            && log "убран старый архив в облаке: $f" \
            || log "не удалось убрать архив в облаке: $f"
    done
}

on_exit() {
    rotate_local
    rotate_remote
}
trap on_exit EXIT

# ─── Один прогон за раз ─────────────────────────────────────────────────────
# Вчерашний прогон, застрявший на выгрузке, не должен запускать второй архив
# поверх первого.
if command -v flock >/dev/null 2>&1; then
    exec 9>"/tmp/korra-backup-$NAME.lock"
    if ! flock -n 9; then
        log "предыдущий бэкап ещё идёт — выхожу"
        exit 0
    fi
fi

# ─── Сбор архива ────────────────────────────────────────────────────────────
STAMP=$(date '+%Y%m%d-%H%M%S')
FILE="korra-$STAMP.zip"
HOST_FILE="$HOST_DIR/$FILE"

RUNNING=$("$DOCKER_BIN" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)
if [ "$RUNNING" != "true" ]; then
    fail "контейнер $NAME не запущен, архив не собрать"
    log "ротация всё равно отработает — чтобы диск не заполнялся молча"
    exit "$STATUS"
fi

log "собираю архив $FILE"
if ! "$DOCKER_BIN" exec -u "$ENGINE_UID" "$NAME" \
        korra backup -o "$CONTAINER_DIR/$FILE" 2>&1 | sed 's/^/    /'; then
    fail "команда бэкапа вернула ненулевой код"
fi

# Проверяем результат, а не код возврата: код мог быть нулевым при пустом
# или обрезанном файле.
if [ ! -s "$HOST_FILE" ]; then
    fail "архив $HOST_FILE не появился или пуст"
    # Битый файл убираем сразу: иначе он займёт место в ротации и будет
    # выглядеть как копия, которой на самом деле нет.
    rm -f -- "$HOST_FILE"
    exit "$STATUS"
fi

if command -v unzip >/dev/null 2>&1; then
    if unzip -tqq "$HOST_FILE" >/dev/null 2>&1; then
        log "архив читается целиком"
    else
        fail "архив не проходит проверку целостности"
        rm -f -- "$HOST_FILE"
        exit "$STATUS"
    fi
elif [ "$(head -c 2 "$HOST_FILE")" != "PK" ]; then
    fail "архив не похож на zip"
    rm -f -- "$HOST_FILE"
    exit "$STATUS"
else
    log "unzip не установлен — проверена только сигнатура файла"
fi

SIZE=$(du -h "$HOST_FILE" | cut -f1)
log "архив готов: $HOST_FILE ($SIZE)"

# ─── Выгрузка ───────────────────────────────────────────────────────────────
if [ "$UPLOAD" = 0 ]; then
    log "выгрузка пропущена (--local)"
    exit "$STATUS"
fi

if [ -z "$RCLONE_REMOTE" ]; then
    fail "RCLONE_REMOTE не задан — архив остался только на этом сервере"
    exit "$STATUS"
fi

if ! command -v "$RCLONE_BIN" >/dev/null 2>&1; then
    fail "нет $RCLONE_BIN — архив остался только на этом сервере"
    exit "$STATUS"
fi

log "выгружаю в $RCLONE_REMOTE"
if "$RCLONE_BIN" copy "$HOST_FILE" "$RCLONE_REMOTE" 2>&1 | sed 's/^/    /'; then
    if "$RCLONE_BIN" lsf --files-only "$RCLONE_REMOTE" 2>/dev/null \
            | grep -qxF "$FILE"; then
        log "выгружено и подтверждено в облаке: $FILE"
    else
        fail "выгрузка прошла, но файла $FILE в облаке не видно"
    fi
else
    fail "выгрузка не прошла — архив остался на сервере"
fi

exit "$STATUS"
