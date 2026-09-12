#!/usr/bin/env bash
# Запуск и пересоздание контура Korra 21 на сервере клиента.
#
# Образ берётся из файла IMAGE рядом со скриптом (дайджест, а не тег: тег
# завтра указывает на другой образ). Прежний образ сохраняется в IMAGE.prev.
#
#   ./up.sh              первый запуск на образе из IMAGE
#   ./up.sh --dry-run    напечатать команду запуска и выйти, ничего не делая
#   ./up.sh --no-wait    не ждать, пока поднимется панель
#
# Обновление/откат работающей установки: ./update.sh --help
#
# Полномочия намеренно минимальные: без docker.sock, без монтирования корня
# хоста, без монтирования кода движка. Кроме каталога данных допускается
# только отдельный read-only file mount операторского Google OAuth-клиента.
set -euo pipefail

# ─── Параметры контура ──────────────────────────────────────────────────────
# Любой из них можно переопределить переменной окружения при запуске:
#   PANEL_PORT=9219 ./up.sh

NAME="${NAME:-korra}"                       # имя контейнера
DATA="${DATA:-/opt/korra/data}"             # каталог данных на хосте
PANEL_PORT="${PANEL_PORT:-9119}"            # панель, только на петле
API_PORT="${API_PORT:-8650}"                # служебный API шлюза, только на петле
TIMEZONE="${TIMEZONE:-Europe/Moscow}"       # часовой пояс контура и расписаний
ENGINE_UID="${ENGINE_UID:-10000}"           # владелец файлов данных
ENGINE_GID="${ENGINE_GID:-10000}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"         # сколько ждать панель после старта
CONTAINER_CPUS="${CONTAINER_CPUS:-}"
CONTAINER_MEMORY="${CONTAINER_MEMORY:-}"

# Право sudo без пароля у агента ВНУТРИ контейнера.
#   0 — снято (дефолт для клиентского контура);
#   1 — включено (дефолт самого образа: контур владельца, который сам
#       администрирует свою машину из панели).
# С sudo агент может переписать движок внутри контейнера, и никакой рантаймовый
# страж этого не остановит. Значение для клиента — решение владельца.
AGENT_SUDO="${AGENT_SUDO:-0}"

# Порт панели 9119 согласован с ключом кабинета: он выдаётся с ограничением
# permitopen="127.0.0.1:9119". Другой порт требует дописать второе permitopen
# в строке ключа на этом сервере, иначе туннель кабинета не откроется.

# ─── Разбор ключей ──────────────────────────────────────────────────────────
DRY_RUN=0
WAIT=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --no-wait) WAIT=0 ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^#\( \|$\)//'; exit 0 ;;
        *) echo "Неизвестный ключ: $arg (есть --dry-run, --no-wait)" >&2; exit 2 ;;
    esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE_FILE="$HERE/IMAGE"
# Путь операторского OAuth-клиента канонический и не переопределяется: этот же
# источник сверяет апдейтер при каждой пересборке контейнера, а переменная
# окружения молча смонтировала бы в контур любой файл хоста.
GOOGLE_OAUTH_CLIENT="$HERE/google/oauth_client.json"

# ─── Проверки до запуска ────────────────────────────────────────────────────
if [ ! -s "$IMAGE_FILE" ]; then
    echo "Нет файла $IMAGE_FILE с образом." >&2
    echo "Положите туда дайджест, например:" >&2
    echo "  echo 'ghcr.io/ceremoneymeister-bit/korra.twenty.one@sha256:...' > $IMAGE_FILE" >&2
    exit 2
fi
IMAGE=$(tr -d '[:space:]' < "$IMAGE_FILE")

if [ ! -d "$DATA" ]; then
    echo "Нет каталога данных $DATA. Создайте его до запуска:" >&2
    # Владелец назначается числами и отдельной командой: записи с uid 10000 в
    # passwd минимального хоста нет, и install -d -o отвечает invalid user.
    echo "  mkdir -p $DATA && chown $ENGINE_UID:$ENGINE_GID $DATA && chmod 750 $DATA" >&2
    exit 2
fi

OWNER=$(stat -c '%u:%g' "$DATA")
if [ "$OWNER" != "$ENGINE_UID:$ENGINE_GID" ]; then
    echo "Каталог данных $DATA принадлежит $OWNER, а движок работает под" >&2
    echo "$ENGINE_UID:$ENGINE_GID. Поправьте владельца:" >&2
    echo "  chown -R $ENGINE_UID:$ENGINE_GID $DATA" >&2
    exit 2
fi

GOOGLE_OAUTH_ARGS=()
# -L рядом с -e намеренно: оборванный symlink для -e не существует, и контур
# поднимался бы без операторского клиента, ни слова об этом не сказав.
if [ -e "$GOOGLE_OAUTH_CLIENT" ] || [ -L "$GOOGLE_OAUTH_CLIENT" ]; then
    if [ "$AGENT_SUDO" != 0 ]; then
        echo "Google OAuth requires the client --no-admin contour (AGENT_SUDO=0) so the agent holds no host-root grant." >&2
        exit 2
    fi
    if [ -L "$GOOGLE_OAUTH_CLIENT" ] || [ ! -f "$GOOGLE_OAUTH_CLIENT" ]; then
        echo "Google OAuth credential must be a regular, non-symlink file: $GOOGLE_OAUTH_CLIENT" >&2
        exit 2
    fi
    GOOGLE_OWNER_MODE=$(stat -c '%u:%g:%a' "$GOOGLE_OAUTH_CLIENT")
    if [ "$GOOGLE_OWNER_MODE" != "0:$ENGINE_GID:640" ]; then
        echo "Google OAuth credential must be root:$ENGINE_GID mode 0640; got $GOOGLE_OWNER_MODE" >&2
        exit 2
    fi
    GOOGLE_OAUTH_ARGS=(
        --mount "type=bind,src=$GOOGLE_OAUTH_CLIENT,dst=/run/korra-secrets/google-oauth-client.json,readonly"
        -e KORRA_GOOGLE_OAUTH_CLIENT_PATH=/run/korra-secrets/google-oauth-client.json
    )
fi

# ─── Свой сервер Telegram Bot API ───────────────────────────────────────────
# Сервер, запущенный с --local, файлы по HTTP не отдаёт: на запрос он называет
# абсолютный путь на своём диске. Контур обязан видеть тот же путь, иначе
# каждое голосовое и каждое фото приходят к агенту как 404, владелец читает
# «InvalidToken», а повтор не помогает никогда. Ровно так молча ломалось
# медиа на трёх установках до 12.09.2026 — симптом прожил девять дней.
#
# Каталог ищется по порту из конфига самого контура, а не по первому
# попавшемуся контейнеру: на одном хосте живут несколько контуров, у каждого
# свой сервер на своём порту и со своим каталогом.
BOT_API_ARGS=()
BOT_API_DIR="${BOT_API_DIR:-}"
BOT_API_DEST="${BOT_API_DEST:-}"
if [ -z "$BOT_API_DIR" ] && [ -s "$DATA/config.yaml" ]; then
    # Совпадает только со своим сервером: у адреса Bot API путь /bot на конце,
    # чего нет у прочих локальных адресов в конфиге (например, у провайдера).
    BOT_API_PORT=$(grep -oE 'https?://(127\.0\.0\.1|localhost):[0-9]+/bot$' "$DATA/config.yaml" \
        | head -1 | sed -E 's#.*:([0-9]+)/bot$#\1#')
    if [ -n "$BOT_API_PORT" ] && command -v docker >/dev/null 2>&1; then
        for candidate in $(docker ps -q 2>/dev/null); do
            CANDIDATE_CMD=$(docker inspect "$candidate" --format '{{range .Config.Cmd}}{{println .}}{{end}}' 2>/dev/null || true)
            printf '%s\n' "$CANDIDATE_CMD" | grep -qx -- '--local' || continue
            printf '%s\n' "$CANDIDATE_CMD" | grep -qx -- "--http-port=$BOT_API_PORT" || continue
            BOT_API_DEST=$(printf '%s\n' "$CANDIDATE_CMD" | sed -n 's/^--dir=//p' | head -1)
            [ -n "$BOT_API_DEST" ] || continue
            BOT_API_DIR=$(docker inspect "$candidate" \
                --format "{{range .Mounts}}{{if eq .Destination \"$BOT_API_DEST\"}}{{.Source}}{{end}}{{end}}" 2>/dev/null || true)
            break
        done
    fi
fi
if [ -n "$BOT_API_DIR" ]; then
    BOT_API_DEST="${BOT_API_DEST:-/opt/data/telegram-bot-api}"
    if [ ! -d "$BOT_API_DIR" ]; then
        echo "Каталог своего Telegram Bot API не найден: $BOT_API_DIR" >&2
        exit 2
    fi
    # Только чтение: скачанные файлы пишет сервер бота, контур их читает.
    BOT_API_ARGS=(
        --mount "type=bind,src=$BOT_API_DIR,dst=$BOT_API_DEST,readonly"
        -e KORRA_TELEGRAM_LOCAL_ROOT="$BOT_API_DEST"
    )
    printf 'Свой Telegram Bot API: %s → %s (только чтение)\n' "$BOT_API_DIR" "$BOT_API_DEST"
fi

# ─── Команда запуска ────────────────────────────────────────────────────────
# host-сеть — не наследие, а требование кабинета: панель отдаёт пропуск сессии
# в HTML только при бинде на петлю, а любой бинд на 0.0.0.0 (без которого мост
# не пробросить) включает гейт авторизации и пропуск убирает. Подробности —
# в README.md, раздел «Почему host-сеть, а не мост».
RESOURCE_ARGS=()
if [ -n "$CONTAINER_CPUS" ]; then RESOURCE_ARGS+=(--cpus "$CONTAINER_CPUS"); fi
if [ -n "$CONTAINER_MEMORY" ]; then RESOURCE_ARGS+=(--memory "$CONTAINER_MEMORY"); fi
RUN_ARGS=(
    docker run -d
    --name "$NAME"
    --network host
    --restart unless-stopped
    "${RESOURCE_ARGS[@]}"
    "${GOOGLE_OAUTH_ARGS[@]}"
    "${BOT_API_ARGS[@]}"
    # Журнал контейнера без ротации за полгода съедает диск клиента молча.
    --log-opt max-size=50m --log-opt max-file=3
    -e KORRA_UID="$ENGINE_UID" -e KORRA_GID="$ENGINE_GID"
    -e KORRA_AGENT_SUDO="$AGENT_SUDO"
    -e KORRA_DASHBOARD=1
    -e KORRA_DASHBOARD_HOST=127.0.0.1
    -e KORRA_DASHBOARD_PORT="$PANEL_PORT"
    # Файловый корень панели НЕ задаём намеренно: в режиме fleet движок сам
    # сужает его до /opt/data/workspace. Явное значение перебивает эту защиту
    # и открывает клиенту весь каталог данных — там ключи, базы и журналы.
    # Рабочие файлы клиента живут в workspace, туда же агент их и кладёт.
    -e KORRA_UI_MODE=fleet
    -e API_SERVER_PORT="$API_PORT"
    -e API_SERVER_PROXY_TARGET="http://127.0.0.1:$API_PORT"
    -e KORRA_TIMEZONE="$TIMEZONE"
    -e KORRA_OWNER_TIMEZONE="$TIMEZONE"
    -v "$DATA":/opt/data
    "$IMAGE" gateway run
)

if [ "$DRY_RUN" = 1 ]; then
    printf 'Сухой прогон, ничего не запущено.\n\n'
    printf '%q ' "${RUN_ARGS[@]}"
    printf '\n'
    exit 0
fi

# Пересоздание существующего контура проходит host updater: drain, полный
# snapshot, проверка и откат. Самостоятельный up.sh остаётся первым запуском.
if docker inspect "$NAME" >/dev/null 2>&1 && [ -z "${KORRA_UPDATER_JOB:-}" ]; then
    echo 'Контейнер уже существует. Используйте update.sh --update <образ|tar>.' >&2
    exit 2
fi

# Прогрев происходит на закреплённом образе до запуска gateway. В данных
# ставятся ровно версии из manifest, проверяются импорты и metadata.
if [ "${KORRA_UPDATER_ROLLBACK:-0}" != 1 ]; then
    python3 "$HERE/updater.py" --warm-deps
fi

# ─── Пересоздание ───────────────────────────────────────────────────────────
# Прежний образ — диагностический указатель; откат данных выполняет updater.
CUR=$(docker inspect "$NAME" --format '{{.Config.Image}}' 2>/dev/null || true)
if [ -n "$CUR" ] && [ "$CUR" != "$IMAGE" ]; then
    echo "$CUR" > "$HERE/IMAGE.prev"
    echo "Прежний образ записан в IMAGE.prev: $CUR"
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

# Порт освободился вместе со старым контейнером — теперь видно, не занял ли его
# кто-то ещё. В host-сети это реальный конфликт, а не теоретический.
if command -v ss >/dev/null 2>&1; then
    if ss -ltnH "sport = :$PANEL_PORT" 2>/dev/null | grep -q .; then
        echo "Порт $PANEL_PORT на хосте уже занят другим процессом:" >&2
        ss -ltnpH "sport = :$PANEL_PORT" >&2 || true
        exit 1
    fi
fi

"${RUN_ARGS[@]}" >/dev/null
echo "$NAME запущен на образе $IMAGE"

if [ "$WAIT" = 0 ]; then
    exit 0
fi

# ─── Ожидание панели ────────────────────────────────────────────────────────
printf 'Жду панель на 127.0.0.1:%s' "$PANEL_PORT"
deadline=$(( $(date +%s) + WAIT_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS -m 3 "http://127.0.0.1:$PANEL_PORT/api/status" >/dev/null 2>&1; then
        printf '\nПанель отвечает. Контур поднялся.\n'
        exit 0
    fi
    printf '.'
    sleep 3
done

printf '\nПанель не ответила за %s с. Последние строки журнала:\n' "$WAIT_SECONDS"
docker logs --tail 40 "$NAME" 2>&1 || true
exit 1
