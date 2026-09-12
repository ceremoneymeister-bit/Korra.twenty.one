<p align="center">
  <img src="assets/banner.png" alt="Korra 21" width="100%">
</p>

# Korra 21

**ИИ-сотрудники для бизнеса, которые живут на вашем сервере.** Один контейнер:
панель управления, Telegram-боты, несколько агентов-профилей с собственной
памятью и характером, расписание задач, файлы, голос и картинки. Данные не
покидают вашу машину, модель вы подключаете свою.

*English summary is at the bottom of this page.*

## Установка за одну просьбу вашему агенту

Отправьте своему ИИ-агенту (Claude Code, Codex, Cursor, любому агенту с доступом
к терминалу) одну фразу:

> Установи Korra 21 по инструкции https://github.com/ceremoneymeister-bit/Korra.twenty.one/blob/main/INSTALL.md

Инструкция написана для агента: каждый шаг там — команда и проверка результата.
Человеку она тоже читается. Три сценария:

| Куда ставим | Что получится | Что нужно |
|---|---|---|
| Сервер Linux amd64 с root (рекомендуется) | контур с панелью, ботами, обновлением и откатом одной командой | 4 vCPU, 8 ГБ RAM, 60 ГБ диска, Docker |
| Небольшой VPS без подготовки хоста | тот же контур, без файрвола и ключа администратора | 2 vCPU, 4 ГБ RAM, Docker |
| Ноутбук или рабочий компьютер (Docker Desktop) | локальный агент с панелью на `localhost` | Docker Desktop |

Образ: `ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest` (linux/amd64).
Устанавливать нужно **образ из реестра**, а не локальную сборку: у неё другая
механика слоёв, и расхождения уже ловились.

## Что умеет

- **Панель.** Чат с каждым агентом во вкладках, файлы рабочей папки, ключи и
  настройки, история, обновление контура, диагностика. Слушает только
  `127.0.0.1`, наружу открывается по вашему решению (SSH-туннель или ваш
  реверс-прокси).
- **Агенты-профили.** Каждый профиль — отдельный сотрудник со своим характером
  (`SOUL.md`), памятью, ключами и Telegram-ботом. Все профили обслуживает один
  шлюз, вкладки в панели работают сразу после установки.
- **Telegram.** Бот на профиль, ответы только разрешённым пользователям,
  голосовые сообщения распознаются локальной моделью из образа (русский язык
  без внешних сервисов; с ключом Deepgram точнее).
- **Память и навыки.** Агент помнит договорённости между сессиями, ищет по
  прошлым разговорам, создаёт и улучшает навыки по опыту.
- **Расписание.** Задачи по крону на естественном языке с доставкой в любой
  канал: отчёты, напоминания, проверки.
- **Файлы и картинки.** Загрузка в панели, фото с айфона (HEIC) читаются как
  обычные, генерация и редактирование изображений через подписку ChatGPT
  (GPT Image 2.5) по референсам.
- **Google Workspace.** Gmail, Календарь, Диск, Контакты, Таблицы, Документы —
  подключение из панели, права остаются внутри профиля.
- **Своя модель.** Любой провайдер: ключ Anthropic, OpenAI, OpenRouter, свой
  OpenAI-совместимый прокси, подписка ChatGPT/Codex через OAuth. Запасных
  провайдеров, которые тихо подменяют модель, в Korra нет.

## Как это устроено

Один контейнер, один каталог данных на хосте (`/opt/korra/data`, внутри
контейнера `/opt/data`), владелец `uid 10000`. Всё изменяемое состояние —
конфиг, ключи, сессии, память, файлы — живёт там; обновление образа их не
трогает. Панель и служебный API привязаны к петле; контейнер запускается без
`docker.sock`, без монтирования корня хоста и без кода движка снаружи.

Обновление и откат — host-kit из `docs/client-deploy/`: `update.sh --update`
проверяет свободное место, снимает бэкап, репетирует миграцию схемы на копии
и только потом пересоздаёт контейнер; `update.sh --rollback <job>` возвращает
прежний образ. Каждая операция оставляет полный след в `updates/<job>/`.

## Первые команды

```bash
docker exec -u 10000 korra korra doctor               # диагностика контура
docker exec -u 10000 korra korra model                # выбрать провайдера и модель
docker exec -u 10000 korra korra profile create smm   # новый агент-сотрудник
docker exec -u 10000 korra korra --help
```

Панель: `http://127.0.0.1:9119` (с сервера — через SSH-туннель). Первое
сообщение в чате без провайдера получит честный ответ, что ключ не настроен,
и подсказку, где его добавить.

## Документация

- [INSTALL.md](INSTALL.md) — установка (для агента и для человека), обновление, откат.
- [docs/client-deploy/README.md](docs/client-deploy/README.md) — полный операторский чек-лист раскатки на сервер клиента.
- [docs/client-deploy/PROVIDER.md](docs/client-deploy/PROVIDER.md) — подключение провайдера модели.
- [docs/host-admin-setup.md](docs/host-admin-setup.md) — права агента на хосте и их отзыв.
- [RELEASE_NOTES.md](RELEASE_NOTES.md) — что нового в выпусках.
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev-окружение, тесты, правила изменений.
- Установка из исходников для разработчиков: `scripts/install.sh` (Linux, macOS)
  и `scripts/install.ps1` (Windows PowerShell). Владельцу контура они не нужны:
  рабочий путь — образ из реестра по `INSTALL.md`.

## Безопасность

- Ключи и токены лежат только в `.env` каталога данных с правами `600`; в
  конфиг, git, образ и отчёты они не попадают.
- Telegram-бот отвечает только пользователям из `TELEGRAM_ALLOWED_USERS`.
- Право `sudo` у агента внутри контейнера выключено для клиентских
  установок (`AGENT_SUDO=0`); включает его только владелец машины осознанно.
- Pull request из форков запускается в CI только после ручного одобрения.

## Лицензия

MIT. Проект развивает открытый код других авторов; их уведомления об авторстве
сохранены в [LICENSE](LICENSE) и [NOTICE](NOTICE).

---

## English summary

**Korra 21 is a self-hosted AI staff for a business:** one container with a
control panel, Telegram bots, several agent profiles with their own memory and
personality, scheduled tasks, files, voice and image generation. Data stays on
your machine; you bring your own model provider.

**Install with your AI agent:** send it one line —
*"Install Korra 21 following https://github.com/ceremoneymeister-bit/Korra.twenty.one/blob/main/INSTALL.en.md"*.
The runbook covers a Linux amd64 server (recommended), a small VPS without host
hardening, and a laptop with Docker Desktop. Image:
`ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest`.

The panel binds to `127.0.0.1:9119` only; the container runs without
`docker.sock`, host-root mounts or engine code from outside. Updates and
rollbacks go through `docs/client-deploy/update.sh`. License: MIT; upstream
authorship notices are kept in `LICENSE` and `NOTICE`.
