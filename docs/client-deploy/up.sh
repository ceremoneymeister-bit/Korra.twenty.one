#!/usr/bin/env bash
# Запуск и пересоздание контура Korra 21 на сервере клиента.
#
# Образ берётся из файла IMAGE рядом со скриптом (дайджест, а не тег: тег
# завтра указывает на другой образ). Прежний образ сохраняется в IMAGE.prev.
#
#   ./up.sh              пересоздать контейнер на образе из IMAGE
#   ./up.sh --dry-run    напечатать команду запуска и выйти, ничего не делая
#   ./up.sh --no-wait    не ждать, пока поднимется панель
#
# Откат на прежний образ:
#   cp IMAGE.prev IMAGE && ./up.sh
#
# Полномочия намеренно минимальные: без docker.sock, без монтирования корня
# хоста, без монтирования кода движка. Единственный том — каталог данных.
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
    echo "  install -d -o $ENGINE_UID -g $ENGINE_GID -m 750 $DATA" >&2
    exit 2
fi

OWNER=$(stat -c '%u:%g' "$DATA")
if [ "$OWNER" != "$ENGINE_UID:$ENGINE_GID" ]; then
    echo "Каталог данных $DATA принадлежит $OWNER, а движок работает под" >&2
    echo "$ENGINE_UID:$ENGINE_GID. Поправьте владельца:" >&2
    echo "  chown -R $ENGINE_UID:$ENGINE_GID $DATA" >&2
    exit 2
fi

# ─── Команда запуска ────────────────────────────────────────────────────────
# host-сеть — не наследие, а требование кабинета: панель отдаёт пропуск сессии
# в HTML только при бинде на петлю, а любой бинд на 0.0.0.0 (без которого мост
# не пробросить) включает гейт авторизации и пропуск убирает. Подробности —
# в README.md, раздел «Почему host-сеть, а не мост».
RUN_ARGS=(
    docker run -d
    --name "$NAME"
    --network host
    --restart unless-stopped
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

# ─── Пересоздание ───────────────────────────────────────────────────────────
# Прежний образ запоминаем до сноса контейнера — это и есть путь отката.
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
