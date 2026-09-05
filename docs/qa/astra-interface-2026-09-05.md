Аудит интерфейса Korra 21 — 5 сентября 2026

Работа начата с чистого `main` на `180bdc8c34`. Прочитаны аудит, канон оформления, рецепт контейнера и наброски первого захода. Из набросков использованы идеи нормализации команд подключения и названий страниц; патч целиком не восстанавливался. Файлы чата, форматирования сообщений и создания агентов не редактировались. Чужие коммиты и тесты не присваиваются этому отчёту.

Что обнаружено и изменено:

- **Достижения.** Живой экран подтверждал английские названия, категории с Hermes, награды за объём вызовов и ошибки. Вместо них появился раздел «Польза от агентов»: поручения на досках, завершённые задачи, расписания с успешным последним запуском, собственные агенты. У каждого этапа есть пояснение подсчёта и переход к действию. Источники — существующие API досок, расписаний и профилей. Нет выдуманных оценок экономии или анализа личной переписки. Ошибка отдельного источника показывается как отсутствие данных, а не как нулевой результат.
- **Канбан.** Повседневный экран переработан под поручения агентам: пять основных этапов; дополнительные этапы видны при наличии соответствующих карточек. Новая задача требует понятного задания, ожидаемого результата и исполнителя. Кнопка «Передать агенту» прямо сообщает о возможном автоматическом запуске. Сохранение не обещает черновик. Карточка показывает исполнителя, описание или итог, приоритет, дату и комментарии. Внутри доступны файлы, обсуждение, связанные задачи, история выполнения, редактирование, пауза, продолжение и архив.
- **Переходы.** Перетаскивание открывает подтверждение; завершение требует итога, пауза — причины, доработка — комментария. Отмена ничего не меняет на сервере. «В работе» выставляет настоящий исполнитель. Изменения используют существующие переходы API, включая зависимости и проверку результата. Неуспешный запрос сохраняет введённый текст; повтор создания использует тот же ключ. Причина остановки не выдаётся за готовый результат. Корзину для перетаскивания и массовые технические операции убрал из повседневного экрана; архив сохраняет карточки. Сервер, CLI и данные не менялись.
- **Ключи и настройки.** Старые команды в интерфейсе и буфере обмена заменены на `korra`; русифицированы названия способов входа. Разделы конфигурации получили нормальные названия, техническая подпись поля не дублируется. Рабочий идентификатор `hermes-cli` в форме показывается как «Основные инструменты Korra», с обратимым преобразованием при редактировании. Экспорт называется `korra-config.json`. При сбое загрузки настроек теперь есть ошибка и повтор вместо бесконечного индикатора.
- **Общий интерфейс.** Исправлен заголовок «Создать агента». Неизвестная ссылка получает объяснение и возврат к агентам. «Польза от агентов» вынесена в основное меню. Убраны рамки шапки, меню и выбора темы; статус соединения использует обычный текст и лаймовую точку. Общие кнопки закрытия и подтверждения переведены. Исправлены подписи каталога навыков, старые команды в справке и чужой бренд в описании MCP.
- **Ошибка геометрии окон.** Живой тест скачивания обнаружил двойное центрирование: у высокого окна верхняя координата была −468 px при высоте экрана 1000 px. CSS-анимация повторяла сдвиг, уже заданный через `translate`. Исправлена общая анимация, поэтому исправление распространяется и на другие окна подтверждения. В браузерный тест добавлена проверка границ окна на обычном и мобильном экранах.

Оформление осталось в принятом каноне: Onest, поверхности и тени из общих токенов, лайм заливками, без новых рамок и фокусных обводок. Для текстов поручений используется существующий компонент Markdown через SDK плагинов; сам компонент не менялся в этой работе.

Проверки:

| Проверка | Результат |
|---|---|
| `cd web && npm run -s check` | Код возврата 0; 84 файла тестов, **582 теста прошли**; 0 ошибок TypeScript/ESLint, 41 предупреждение ESLint, как в первом прогоне |
| Собственные модульные тесты | Добавлены 19 проверок: команды, конфигурация, заголовок, польза, поручения и источники навыков |
| Сборка в отдельном worktree | `npm run -s build > /tmp/astra-build.log || exit 1`, код возврата 0; общий рабочий каталог не использовался для сборки |
| Браузерный сценарий | Создание доски и поручения, реальная запись в API, отмена перетаскивания, комментарии, загрузка и скачивание файла с проверкой содержимого, пауза/продолжение, review → done, сохранение после перезагрузки, фильтры, изоляция досок, архив |
| Дополнительные проверки браузера | Показатели пользы, фактическое копирование команды `korra`, ошибка конфигурации 503 с повтором, неизвестная ссылка; светлая/тёмная темы, ширина 390 px, отсутствие горизонтального переполнения страницы, границы диалогов |
| Ошибки JavaScript в сценарии | **0** |
| Настоящий запуск агента | В отдельной тестовой доске диспетчер перевёл поручение в `running`; агент сохранил `2 + 2 = 4`; итог `done`, попытка `completed`, ошибки нет |
| Дополнительный обход | Файлы, расписания, навыки, каналы, MCP, помощь, плагины. На просмотренных основных экранах не осталось английских фраз интерфейса; названия сторонних продуктов сохраняются |

Ранние общие прогоны попадали на незавершённую параллельную работу над Markdown и мастером агентов. Их файлы не исправлялись и тесты не отключались; итоговый общий прогон полностью зелёный.

Журналы: [проверка кода](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-check.log>), [сборка](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-build.log>), [браузерный сценарий](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-e2e.log>). Воспроизводимый сценарий: [scripts/astra-ui-e2e.py](/opt/korra-21/scripts/astra-ui-e2e.py). Он проверяет имя контейнера, временный каталог, отсутствие Telegram и отключённый автоматический диспетчер до любых действий.

Снимки:

| Экран | Снимки |
|---|---|
| Исходное состояние | [Достижения](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-before-achievements-20260905.png>), [канбан](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-before-kanban-20260905.png>), [ключи](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-before-env-20260905.png>), [конфигурация](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-before-config-20260905.png>) |
| Польза от агентов | [Тёмная](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-benefits-dark.png>), [светлая](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-benefits-light.png>), [мобильная](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-benefits-mobile.png>) |
| Доска | [Тёмная](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-kanban-dark.png>), [светлая](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-kanban-light.png>), [пустая](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-kanban-empty.png>), [мобильная](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-kanban-mobile.png>) |
| Поручение | [Создание](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-kanban-create.png>), [проверка результата](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-kanban-result.png>), [форма на телефоне](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-kanban-mobile-form.png>), [живой агент](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-worker-result.png>) |
| Общие экраны | [Ключи](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-keys-dark.png>), [настройки](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-config-dark.png>), [ошибка настроек](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-config-error.png>), [неизвестный раздел](</root/Antigravity/projects/Korra 21/ui pack/qa/astra-missing-section.png>) |

Границы работы и очистка:

- Использовался только контейнер `astra-ui`, образ `korra.twenty.one:candidate-20260904`, панель 9140 и API 8670. Конфигурация владельца читалась только для создания изолированной копии. В копии удалены Telegram на всех уровнях, в `.env` оставлены только два ключа из рецепта; внешние платформы очищены, автозапуск канбана отключён. Единственный проверочный запуск агента выполнен вручную на своей доске.
- `astra-ui`, `/tmp/astra-ui-data.f47d5sv0`, служебный файл `/tmp/astra-ui-meta.json` и собственный worktree `/tmp/astra-build` удалены после проверки. Боевые контейнеры и `/root/.korra21` не изменялись.
- Единственный ранее существовавший внешний артефакт, который мешал заданному рецепту: каталог `/tmp/astra-build` от старого захода. Перед созданием нового он целиком сохранён как `/tmp/astra-build.before-20260905-ui`; эта копия оставлена. Другие внешние записи — только разрешённые временные данные, снимки и журналы QA. Исходные аудит, палитра, рецепт и резервные копии не редактировались.
- Коммиты делались через `git commit --only` с явными путями. Пуш, сборка образа и выкладка не выполнялись.

Ограничения и идеи для следующего шага:

1. **Экономический эффект.** Часы и деньги стоит считать после согласования исходных трудозатрат с клиентом. Число запросов или вызовов инструментов не доказывает экономию, поэтому таких показателей нет.
2. **Проверка результата по умолчанию.** Сервер сохраняет прежние правила: агент может сам завершить задачу или отправить её на проверку. Обязательное согласование каждой работы — отдельная настройка процесса, её не навязывал перед первым выпуском.
3. **Прямые ссылки из сводки на конкретную карточку.** Сейчас сводка ведёт в раздел досок, где выбирается нужная доска. Для адресных ссылок нужен устойчивый адрес, учитывающий доску и агента; полезно сделать отдельным улучшением вместе с уведомлениями.
4. **Шаблоны бизнес-поручений.** Продажи, закупки и отчётность стоит превратить в готовые заготовки после получения примеров работы первого клиента, чтобы не навязывать ему придуманный процесс.
5. **Каталог файлов и часовой пояс контура.** Рецепт запуска явно задаёт корень файлов `/opt/data`, перекрывая уже имеющийся безопасный каталог `workspace` для режима fleet. Оператору стоит проверить это переопределение и часовой пояс при выкладке. Настройки боевого контура не менялись.
6. **Совместимость.** Имена внутренних API, плагина `hermes-achievements` и идентификатор набора инструментов на диске сохранены. В исходном YAML технические значения остаются исходными; русская форма не подменяет их несуществующими именами. Старый API значков оставлен для совместимости, новый экран его не вызывает.

Для выкладки оператором нужны собранный `korra_cli/web_dist` и файлы обоих dashboard-плагинов, включая `dist` и манифесты. Одна замена основного фронта не обновит код канбана и пользы. Изменения Python для этой работы не требуются.

Коммиты и полный список файлов этой работы:

`f6dce80807` — Русифицированы ключи, настройки и названия разделов панели.

- [plugins/hermes-achievements/dashboard/manifest.json](/opt/korra-21/plugins/hermes-achievements/dashboard/manifest.json)
- [plugins/kanban/dashboard/manifest.json](/opt/korra-21/plugins/kanban/dashboard/manifest.json)
- [web/src/App.tsx](/opt/korra-21/web/src/App.tsx)
- [web/src/components/AutoField.tsx](/opt/korra-21/web/src/components/AutoField.tsx)
- [web/src/components/OAuthProvidersCard.tsx](/opt/korra-21/web/src/components/OAuthProvidersCard.tsx)
- [web/src/i18n/ru.ts](/opt/korra-21/web/src/i18n/ru.ts)
- [web/src/lib/config-presentation.test.ts](/opt/korra-21/web/src/lib/config-presentation.test.ts)
- [web/src/lib/config-presentation.ts](/opt/korra-21/web/src/lib/config-presentation.ts)
- [web/src/lib/oauth-presentation.test.ts](/opt/korra-21/web/src/lib/oauth-presentation.test.ts)
- [web/src/lib/oauth-presentation.ts](/opt/korra-21/web/src/lib/oauth-presentation.ts)
- [web/src/lib/resolve-page-title.test.ts](/opt/korra-21/web/src/lib/resolve-page-title.test.ts)
- [web/src/lib/resolve-page-title.ts](/opt/korra-21/web/src/lib/resolve-page-title.ts)
- [web/src/pages/ClientHelpPage.tsx](/opt/korra-21/web/src/pages/ClientHelpPage.tsx)
- [web/src/pages/ConfigPage.tsx](/opt/korra-21/web/src/pages/ConfigPage.tsx)

`bc0bf155fc` — Достижения заменены подтверждённой пользой и следующими шагами для бизнеса.

- [plugins/hermes-achievements/dashboard/dist/index.js](/opt/korra-21/plugins/hermes-achievements/dashboard/dist/index.js)
- [plugins/hermes-achievements/dashboard/dist/style.css](/opt/korra-21/plugins/hermes-achievements/dashboard/dist/style.css)
- [web/src/plugins/benefits.test.tsx](/opt/korra-21/web/src/plugins/benefits.test.tsx)

`83e0d1f59f` — Канбан превращён в понятную доску поручений с проверкой результата.

- [plugins/kanban/dashboard/dist/index.js](/opt/korra-21/plugins/kanban/dashboard/dist/index.js)
- [plugins/kanban/dashboard/dist/style.css](/opt/korra-21/plugins/kanban/dashboard/dist/style.css)
- [web/src/plugins/kanban.test.tsx](/opt/korra-21/web/src/plugins/kanban.test.tsx)
- [web/src/plugins/registry.ts](/opt/korra-21/web/src/plugins/registry.ts)

`a258f793d9` — Исправлены положение окон, навигация и оставшиеся служебные тексты.

- [plugins/hermes-achievements/dashboard/dist/index.js](/opt/korra-21/plugins/hermes-achievements/dashboard/dist/index.js)
- [web/src/App.tsx](/opt/korra-21/web/src/App.tsx)
- [web/src/components/SidebarFooter.tsx](/opt/korra-21/web/src/components/SidebarFooter.tsx)
- [web/src/components/SidebarStatusStrip.tsx](/opt/korra-21/web/src/components/SidebarStatusStrip.tsx)
- [web/src/components/ThemeSwitcher.tsx](/opt/korra-21/web/src/components/ThemeSwitcher.tsx)
- [web/src/contexts/PageHeaderProvider.tsx](/opt/korra-21/web/src/contexts/PageHeaderProvider.tsx)
- [web/src/lib/oauth-presentation.ts](/opt/korra-21/web/src/lib/oauth-presentation.ts)
- [web/src/lib/resolve-page-title.test.ts](/opt/korra-21/web/src/lib/resolve-page-title.test.ts)
- [web/src/lib/resolve-page-title.ts](/opt/korra-21/web/src/lib/resolve-page-title.ts)
- [web/src/lib/skill-source-label.test.ts](/opt/korra-21/web/src/lib/skill-source-label.test.ts)
- [web/src/lib/skill-source-label.ts](/opt/korra-21/web/src/lib/skill-source-label.ts)
- [web/src/pages/ConfigPage.tsx](/opt/korra-21/web/src/pages/ConfigPage.tsx)
- [web/src/pages/SkillsPage.tsx](/opt/korra-21/web/src/pages/SkillsPage.tsx)
- [web/src/themes/neumorphism.css](/opt/korra-21/web/src/themes/neumorphism.css)
- [web/src/vendor/nous-ui/ui/components/confirm-dialog.tsx](/opt/korra-21/web/src/vendor/nous-ui/ui/components/confirm-dialog.tsx)
- [web/src/vendor/nous-ui/ui/components/dialog.tsx](/opt/korra-21/web/src/vendor/nous-ui/ui/components/dialog.tsx)

Отдельный завершающий коммит добавляет этот отчёт и воспроизводимый сценарий браузерной проверки.
