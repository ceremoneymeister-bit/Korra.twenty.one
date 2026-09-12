# Установка Korra 21

Инструкция для ИИ-агента, который ставит Korra по просьбе владельца машины, и
для человека, который делает то же руками. Каждый шаг — команда и проверка.
Шаг без проверки не считается выполненным.

Образ: `ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest`, только
linux/amd64. Собирать образ локально не нужно и не стоит: у локальной сборки
другая механика слоёв.

Выберите сценарий:

- **A. Сервер Linux с root** (Ubuntu/Debian, 4 vCPU, 8 ГБ RAM, 60 ГБ диска) —
  полный контур: файрвол, обновление и откат одной командой, бэкап. Рекомендуется.
- **B. Небольшой VPS без подготовки хоста** (2 vCPU, 4 ГБ RAM) — тот же контур
  без файрвола и без ключа администратора хоста.
- **C. Ноутбук или рабочий компьютер** с Docker Desktop (macOS, Windows, Linux) —
  локальный агент с панелью на `localhost`.

Агенту: выполняйте команды по порядку, сверяйте каждую проверку с ожиданием,
при расхождении остановитесь и покажите владельцу вывод команды. Не выдумывайте
ключи и не подставляйте чужие адреса прокси.

---

## A. Сервер Linux с root

### A1. Что нужно от владельца

- root-доступ на сервер (ssh) и его IP;
- ключ провайдера модели или готовность ввести его в панели;
- при желании — токен Telegram-бота от @BotFather и числовой Telegram ID владельца.

### A2. Docker

```bash
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
docker --version && docker info --format '{{.ServerVersion}}'
uname -m        # ожидается x86_64
```

### A3. Каталоги и host-kit

Каталог раскатки `/opt/korra` (скрипты, файл `IMAGE`) и каталог данных
`/opt/korra/data` (всё состояние контура, владелец `uid 10000`).

```bash
install -d -m 755 /opt/korra
mkdir -p /opt/korra/data
chown 10000:10000 /opt/korra/data && chmod 750 /opt/korra/data
cd /opt/korra
for f in up.sh update.sh updater.py host-bootstrap.py backup.sh dependencies.lock.json env.template; do
  curl -fsSL "https://raw.githubusercontent.com/ceremoneymeister-bit/Korra.twenty.one/main/docs/client-deploy/$f" -o "$f"
done
chown root:root /opt/korra/*
chmod 755 up.sh update.sh updater.py host-bootstrap.py backup.sh
chmod 644 dependencies.lock.json env.template
```

**Проверка:**

```bash
stat -c '%u:%g %a %n' /opt/korra/data      # 10000:10000 750
python3 /opt/korra/updater.py --capabilities
```

Владельца назначаем отдельной командой и числами: пользователя с uid 10000 в
`passwd` хоста нет, и на свежей Ubuntu `install -d -o 10000` отвечает
`invalid user: '10000'`. Спрашиваем тоже числа (`%u:%g`) — `%U:%G` ответит
`UNKNOWN`. Сопоставление имени живёт внутри контейнера, так и задумано.

### A4. Образ, закреплённый дайджестом

Тег `latest` завтра укажет на другой образ; контур закрепляется дайджестом,
чтобы обновление и откат были воспроизводимы.

```bash
docker pull ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest
DIGEST=$(docker image inspect ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest \
  --format '{{index .RepoDigests 0}}' | cut -d@ -f2)
echo "ghcr.io/ceremoneymeister-bit/korra.twenty.one@${DIGEST}" > /opt/korra/IMAGE
cat /opt/korra/IMAGE
```

**Проверка:**

```bash
docker image inspect "$(cat /opt/korra/IMAGE)" \
  --format '{{.Id}} {{.Size}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
```

Версия движка печатается уже из работающего контейнера:
`docker exec -u 10000 korra korra --version` (шаг A6).

### A5. Ключи (можно пропустить и ввести в панели)

Контур на первом запуске сам кладёт в каталог данных `config.yaml` и `.env` из
своих шаблонов. Если ключи известны заранее, положите `.env` до запуска:

```bash
cp /opt/korra/env.template /opt/korra/data/.env
chown 10000:10000 /opt/korra/data/.env && chmod 600 /opt/korra/data/.env
${EDITOR:-nano} /opt/korra/data/.env      # заполнить ключ провайдера, Telegram при необходимости
```

Обязателен только ключ провайдера; `API_SERVER_KEY` оставьте пустым — контур
создаст его сам.

### A6. Подготовка хоста и первый запуск

`host-bootstrap.py` ставит UFW, fail2ban, OpenSSH, создаёт своп при RAM до 8 ГиБ,
разрешает ваш SSH-порт до включения файрвола и запускает контейнер. Сначала план
без изменений, потом выполнение. Укажите реальный SSH-порт.

```bash
python3 /opt/korra/host-bootstrap.py bootstrap --plan --no-admin \
  --home /opt/korra --data /opt/korra/data --name korra \
  --image "$(cat /opt/korra/IMAGE)" --ssh-port 22

python3 /opt/korra/host-bootstrap.py bootstrap --no-admin \
  --home /opt/korra --data /opt/korra/data --name korra \
  --image "$(cat /opt/korra/IMAGE)" --ssh-port 22
```

`--no-admin` запускает контур без ключа администратора хоста и без `sudo` у
агента внутри контейнера. Это правильный режим для установки по просьбе:
владелец может позже выдать права осознанно (`docs/host-admin-setup.md`).
Preflight требует не менее 4 CPU и 7 ГиБ RAM; на меньшей машине переходите к
сценарию B.

**Проверка:**

```bash
python3 /opt/korra/host-bootstrap.py verify --no-admin \
  --home /opt/korra --data /opt/korra/data --name korra \
  --image "$(cat /opt/korra/IMAGE)" --ssh-port 22
docker ps --filter name=korra --format '{{.Names}} {{.Status}} {{.Image}}'
curl -fsS http://127.0.0.1:9119/api/status | head -c 300
docker exec -u 10000 korra korra doctor | tail -25
```

Ожидание: `verify` печатает `{"verified": true}`, контейнер `Up`,
`/api/status` отвечает JSON с версией, `doctor` без красных строк
(предупреждения о ненастроенном провайдере допустимы).

С `--no-admin` проверка идёт по обещаниям этого режима: контейнер работает на
закреплённом образе с единственным томом данных, панель и API отвечают на
петле, ключа администратора хоста нет, `sudo` у агента в контейнере нет. Без
`--no-admin` та же команда требует обратного — контейнерного root и входа по
закреплённому host-root ключу, — поэтому ключ режима в ней обязателен.

### A7. Панель и провайдер

Панель слушает только `127.0.0.1:9119`. С рабочей машины владельца:

```bash
ssh -N -L 9119:127.0.0.1:9119 root@<ip-сервера>
# затем в браузере: http://127.0.0.1:9119
```

Первое сообщение в чате без провайдера получит ответ, что ключ не настроен, и
подсказку про раздел «Ключи». Добавьте ключ там или в терминале:

```bash
docker exec -u 10000 -it korra korra model      # выбрать провайдера и модель
docker exec -u 10000 korra korra config get model
```

Подписка ChatGPT/Codex подключается командой `korra auth add` внутри контейнера
(OAuth в браузере владельца). Подробности провайдеров:
`docs/client-deploy/PROVIDER.md`.

### A8. Telegram (по желанию)

В `.env`: `TELEGRAM_BOT_TOKEN` от @BotFather и `TELEGRAM_ALLOWED_USERS` с
числовым ID владельца, затем `docker restart korra`. Проверка: сообщение боту
получает ответ; чужой пользователь ответа не получает.

### A9. Обновление и откат

```bash
docker pull ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest
NEW=ghcr.io/ceremoneymeister-bit/korra.twenty.one@$(docker image inspect \
  ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest --format '{{index .RepoDigests 0}}' | cut -d@ -f2)
cd /opt/korra && NAME=korra DATA=/opt/korra/data PANEL_PORT=9119 API_PORT=8650 \
  ./update.sh --update "$NEW"
tail -3 /opt/korra/updates.log       # phase=complete status=succeeded
```

Апдейтер проверяет место, снимает бэкап, репетирует миграцию схемы на копии
данных и только потом пересоздаёт контейнер. Откат:

```bash
cd /opt/korra && NAME=korra DATA=/opt/korra/data PANEL_PORT=9119 API_PORT=8650 \
  ./update.sh --rollback <job-id из updates.log>
```

Бэкап в облако владельца: `backup.sh` (S3-совместимое хранилище, настройки в
заголовке скрипта).

---

## B. Небольшой VPS без подготовки хоста

Шаги A2–A5 те же. Вместо `host-bootstrap.py` — прямой запуск лаунчером:

```bash
cd /opt/korra && NAME=korra DATA=/opt/korra/data PANEL_PORT=9119 API_PORT=8650 \
  TIMEZONE=Europe/Moscow ./up.sh
```

`up.sh` по умолчанию запускает контур с `AGENT_SUDO=0`, в host-сети, панель на
петле, без `docker.sock` и без монтирования корня. Файрвол и своп остаются на
владельце: панель и API снаружи не видны и так, но SSH и остальные порты хоста
закрывайте сами.

**Проверка:** как в A6 (`docker ps`, `/api/status`, `korra doctor`). Дальше —
A7, A8, A9 без изменений.

---

## C. Ноутбук или рабочий компьютер (Docker Desktop)

Здесь нет host-сети, поэтому панель публикуется портом на `127.0.0.1`. Данные
живут в `~/.korra`.

```bash
mkdir -p ~/.korra
curl -fsSL https://raw.githubusercontent.com/ceremoneymeister-bit/Korra.twenty.one/main/docker-compose.windows.yml \
  -o docker-compose.korra.yml
docker compose -f docker-compose.korra.yml pull
docker compose -f docker-compose.korra.yml up -d
```

На macOS и Linux замените в файле `${USERPROFILE}/.korra` на `${HOME}/.korra`.

**Проверка:**

```bash
docker compose -f docker-compose.korra.yml ps          # gateway и dashboard: running
curl -fsS http://127.0.0.1:9119/api/status | head -c 200
```

Панель: `http://127.0.0.1:9119`. Провайдер и Telegram — как в A7 и A8, имя
контейнера `korra`. Обновление: `docker compose -f docker-compose.korra.yml pull && docker compose -f docker-compose.korra.yml up -d`.

---

## Что проверить в конце (любой сценарий)

```bash
docker exec -u 10000 korra korra --version
docker exec -u 10000 korra korra doctor | tail -25
docker exec -u 10000 korra korra profile create test-agent   # вкладка появится в панели
```

Отчёт владельцу: версия, адрес панели, где лежит каталог данных, как обновить и
откатить, какие ключи ещё не заданы. Ключи и токены в отчёт не вставлять.

## Если что-то не так

- `docker logs korra --tail 100` — движок пишет причину прямым текстом.
- `korra doctor` называет недостающее по разделам, `korra doctor --fix` чинит
  то, что чинится без данных владельца.
- Порт занят: `PANEL_PORT`/`API_PORT` в команде запуска, порт панели тогда
  укажите и в SSH-туннеле.
- Каталог данных с чужим владельцем: `chown -R 10000:10000 /opt/korra/data`.
