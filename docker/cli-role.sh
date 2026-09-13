#!/bin/sh
# shellcheck shell=sh
# K21-038 — роль команды контейнера: одноразовая CLI или обычный старт.
#
# `docker run <image> korra import ARCHIVE` до этого проходил полный
# супервизируемый путь: s6 поднимал dashboard и шлюзы профилей, те открывали
# целевой DATA за секунды до holder-проверки импорта, и импорт на полностью
# остановленном контуре падал «не удалось проверить всех держателей DATA».
# Обойти это можно было только `--entrypoint /opt/hermes/.venv/bin/korra` —
# знание, которое есть лишь у того, кто уже на это напарывался (миграция
# Марины Павловой 11.09.2026).
#
# Классификация живёт в отдельном файле, а не двумя копиями case в
# entrypoint-dispatch.sh и main-wrapper.sh: расходящиеся копии этого правила
# означали бы контейнер, который не поднимает службы, но всё равно роняет
# импорт (или наоборот).
#
# Использование (оба режима отвечают кодом возврата):
#   sh cli-role.sh oneshot   <argv...>  — 0, если пользовательские службы не нужны
#   sh cli-role.sh needs-root <argv...> — 0, если команде нужен root
#
# Правило намеренно осторожное: незнакомая форма командной строки считается
# обычным стартом. Ошибка в эту сторону оставляет сегодняшнее поведение,
# ошибка в другую — тихо лишила бы контейнер служб.

set -eu

# Подкоманда korra из argv контейнера, или пустая строка.
korra_cli_subcommand() {
    first=1
    while [ $# -gt 0 ]; do
        if [ "$first" = 1 ]; then
            first=0
            # main-wrapper.sh принимает и `korra import …`, и голое `import …`.
            case "${1##*/}" in
                korra|hermes) shift; continue ;;
            esac
        fi
        case "$1" in
            # -p/--profile разбирается до argparse и всегда несёт значение.
            -p|--profile)
                shift
                [ $# -gt 0 ] && shift
                continue
                ;;
            --profile=*) shift; continue ;;
            -*) return 0 ;;
            *) printf '%s\n' "$1"; return 0 ;;
        esac
    done
    return 0
}

mode=""
if [ $# -gt 0 ]; then
    mode="$1"
    shift
fi
subcommand=$(korra_cli_subcommand "$@")

case "$mode" in
    oneshot)
        # Неинтерактивные команды, которые ничего не обслуживают: им нужен
        # только собственный процесс, а поднятые службы им прямо мешают.
        case "$subcommand" in
            import|backup|config) exit 0 ;;
        esac
        exit 1
        ;;
    needs-root)
        # `korra import` доказывает, что DATA никем не удерживается, читая
        # /proc всех процессов начального PID namespace, и восстанавливает
        # владельца каждого файла. Под UID 10000 первое даёт PermissionError
        # («не удалось проверить всех держателей DATA»), второе невозможно.
        case "$subcommand" in
            import) exit 0 ;;
        esac
        exit 1
        ;;
    *)
        echo "cli-role.sh: неизвестный режим '$mode'" >&2
        exit 2
        ;;
esac
