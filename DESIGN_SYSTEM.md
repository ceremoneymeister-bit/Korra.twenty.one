# Дизайн-система панели Korra 21: фактическая карта управления видом

Дата аудита: 2 сентября 2026 года. Снимок: рабочее дерево `/opt/korra-21` на commit `971eff3fbb472ddde17381e661f667934ed5a0a2`; существующие незакоммиченные изменения владельца не изменялись. Пакет: `@nous-research/ui@0.18.2`.

## Короткий ответ владельцу

> Статус после аудита: найденный разрыв семантических цветов исправлен в
> рабочем дереве. Все 19 `colorOverrides` теперь проходят через runtime-
> индирекцию; `primary` управляет продуктовыми и стандартными заполненными
> кнопками, `ring` — фокусом, `input` — рамками полей. Таблицы ниже сохраняют
> исходное состояние на снимке `971eff3f`, чтобы было видно, почему потребовалась
> правка и какие ограничения относятся к самому npm-пакету.

Менять каждый элемент отдельно **не нужно**. У панели есть центральные рычаги, но они покрывают разные задачи:

1. Тема меняет базовую палитру, шрифты, базовый размер, межстрочный интервал, плотность и часть скруглений сразу во всём интерфейсе.
2. Наш `web/src/index.css` может поверх пакета централизованно поменять почти любую чисто визуальную деталь, не копируя пакет к себе.
3. Вендоринг нужен, когда меняется не CSS, а устройство или поведение компонента: DOM-разметка переключателя, набор вариантов кнопки, встроенные иконки Select/Dialog, портал и API Toast и т. п.

Практический порядок: **сначала рабочие токены темы → затем несколько системных правил в `index.css` → только затем точечный вендоринг структурно неподходящих компонентов**. Полный форк пакета сейчас не оправдан.

На аудируемом снимке было важное ограничение: объект темы обещал 19 семантических `colorOverrides`, но production-сборка использовала только `success` и `warning`. В текущем рабочем дереве разрыв закрыт: все 19 значений проходят через runtime-переменные, а встроенные светлая и тёмная темы задают полный набор. Для совместимости тип допускает неполный набор; пропущенные значения очищаются и получают статический светлый CSS-default, а не наследуют значение предыдущей темы. До первого эффекта `ThemeProvider` документ также использует светлые статические значения, после чего сохранённая тёмная тема применяется целиком.

## Откуда взяты выводы

Основные точки кода:

- модель темы: `web/src/themes/types.ts:21-185`;
- ровно две встроенные темы: `web/src/themes/presets.ts:3-128`;
- перевод темы в CSS-переменные и запись на `<html>`: `web/src/themes/context.tsx:59-173`, `344-401`;
- фактический выбор только `light`/`dark`: `web/src/themes/context.tsx:407-478`; серверные `definition` сейчас не участвуют в `resolveTheme()`;
- локальный CSS/Tailwind: `web/src/index.css:1-337`;
- Tailwind v4 подключён Vite-плагином: `web/vite.config.ts:14`, `71-76`; отдельного `tailwind.config.*` или `postcss.config.*` в репозитории нет;
- исходники пакета: `node_modules/@nous-research/ui/src/ui/components`;
- версия, exports, лицензия и зависимости: `node_modules/@nous-research/ui/package.json:1-128`.

Проверки:

- импорты считались AST-разбором всех `web/src/**/*.{ts,tsx}`, без `*.test.*`; «место» в инвентаре — один JSX opening/self-closing tag в исходнике, а не число реально отрисованных строк после циклов;
- `npm run -s build` успешно собрал 2 227 модулей;
- временно менялись на явно отличимые значения `BRAND_LIME`, `baseSize`, `radius`, `density`, после чего сборка повторялась; исходник затем восстановлен и финальная сборка снова прошла;
- хеш итогового CSS не изменился (`d146e990…`), UI-чанк остался тем же (`ui-Buu472-7.js`), изменились theme-чанк и импортирующие его JS-чанки. Это подтверждает: значения темы применяются в runtime через inline CSS variables, а не вшиваются в CSS при сборке;
- на снимке в собранном CSS были проверены фактические правила: `gap-4` и `p-4` читали `--theme-spacing-mul`, `rounded-lg` — `--theme-radius`, `bg-primary` — `--midground`, `text-success` — `--success`. После исправления финальная production-сборка подтверждает runtime-индирекцию всех 19 семантических цветов.

## 1. Три уровня управления видом

### Уровень A. Токены темы в `presets.ts`

`BuiltinThemeName` допускает только `light` и `dark` (`presets.ts:3-4`), обе темы собраны в `BUILTIN_THEMES` (`125-128`), а default — светлая (`122-123`). Старые и неизвестные имена мигрируют в одну из этих двух (`130-154`). Несмотря на более широкие интерфейсы в `types.ts`, текущий `ThemeProvider` публикует только две встроенные темы и разрешает тему только через `BUILTIN_THEMES` (`context.tsx:421`, `435-438`).

Одна правка в `DEFAULT_TYPOGRAPHY` или `DEFAULT_LAYOUT` влияет сразу на обе темы, потому что обе ссылаются на эти объекты. Правка внутри `lightTheme` или `darkTheme` относится только к соответствующему режиму.

Ниже короткие package-файлы вроде `button.tsx` указаны относительно точного каталога `node_modules/@nous-research/ui/src/ui/components/`.

#### Палитра

| Поле | Светлая / тёмная | Что создаёт `applyTheme` | Реально читается |
|---|---|---|---|
| `palette.background.hex`, `.alpha` | `#F7F7F5, 1` / `#150B29, 1` | `--background`, `--background-base`, `--background-alpha` | **Да.** Фон документа, package Card/Input/Switch/Select, `bg-background*` и производные поверхности. |
| `palette.midground.hex`, `.alpha` | `#202124, 1` / `#F4EFFA, 1` | `--midground`, `--midground-base`, `--midground-alpha` | **Да, это главный нейтральный цвет.** Текст, базовые границы, Badge, ListItem, Select и скроллбары. Заполненные кнопки продуктового режима и семантические utility теперь используют собственные overrides. |
| `palette.foreground.hex`, `.alpha` | `#FFFFFF, 0` / `#FFFFFF, 0` | `--foreground`, `--foreground-base`, `--foreground-alpha` | **Частично.** `foreground-alpha` участвует в active-filter Button (`button.tsx:14-15`), `foreground-base` — в Dialog. Наш `text-foreground` намеренно перенаправлен на `midground` (`index.css:232-235`). |
| `palette.warmGlow` | lime rgba | ничего | **Нет.** Поле legacy; `paletteVars()` его не переносит (`context.tsx:73-79`). |
| `palette.noiseOpacity` | `0` / `0` | ничего | **Нет.** Поле legacy; читателей в `web/src` и package components нет. |

Следствие: изменение `darkTheme.palette.midground.hex` одной строкой перекрашивает большую часть нейтрального chrome тёмного интерфейса. Это широкий рычаг для текста и базовых границ; акцент заполненных кнопок продуктового режима теперь независимо задаёт `colorOverrides.primary`.

#### Типографика

| Поле | Сейчас | CSS / действие | Реально читается |
|---|---|---|---|
| `typography.fontSans` | system sans stack | `--theme-font-sans` | **Да.** `html`, `body`, Tailwind `font-sans`, product-mode overrides (`index.css:88-102`, `116-120`, `132-145`). Отдельный font picker может перезаписать его последним (`context.tsx:310-338`, `398-400`). |
| `typography.fontMono` | system mono stack | `--theme-font-mono` | **Да.** `code/kbd/pre/samp`, `font-mono`, `font-mono-ui`, Spinner и другие monospace-компоненты (`index.css:108-120`, `308-315`). |
| `typography.fontDisplay` | не задан, fallback на `fontSans` | `--theme-font-display` | **Нет прямого читателя.** Переменная создаётся, но ни наш CSS, ни используемые компоненты её не используют. Package display-стили используют `font-mondwest`/`font-expanded`. |
| `typography.fontUrl` | не задан | добавляет один `<link rel="stylesheet">` в `<head>` | **Механизм работает**, но обе текущие темы URL не задают. Независимый font picker использует curated URLs из `web/src/themes/fonts.ts:56-144`. |
| `typography.baseSize` | `15px` | `--theme-base-size` | **Да.** `html { font-size: ... }` (`index.css:90-94`), поэтому меняются все rem-размеры; пиксельные размеры не меняются. |
| `typography.lineHeight` | `1.55` | `--theme-line-height` | **Да, по наследованию.** Явные `leading-*` конкретных компонентов имеют приоритет. |
| `typography.letterSpacing` | `0` | `--theme-letter-spacing` | **Да, по наследованию.** Явные `tracking-*` и product-mode `!important` имеют приоритет. |

#### Геометрия и плотность

| Поле | Сейчас | CSS / действие | Реально читается |
|---|---|---|---|
| `layout.radius` | `0.625rem` | `--radius`, `--theme-radius` | **Частично.** `rounded-sm/md/lg/xl` читают `--theme-radius` (`index.css:266-269`). Но многие package-компоненты вообще не имеют rounded-класса, а product-mode кнопки жёстко получают `0.625rem` (`index.css:158-160`). `--radius` отдельно читателей не имеет. |
| `layout.density` | `comfortable` | `--theme-spacing-mul`: compact `0.85`, comfortable `1`, spacious `1.2`; плюс маркер `--theme-density` | **Да для Tailwind spacing.** `p-*`, `m-*`, `gap-*`, `h-*` масштабируются через `--spacing` (`context.tsx:81-104`, `index.css:112-120`). Не меняет явные `px`, `min-h-[44px]` и произвольные значения. `--theme-density` сам нигде не читается. |

#### Семантические цвета `colorOverrides`: состояние снимка до исправления

`ThemeColorOverrides` объявляет 19 полей (`types.ts:132-154`), а `applyTheme` записывает их на `<html>` (`context.tsx:107-149`, `371-382`). На снимке `index.css` объявлял их внутри `@theme inline` (`227-270`) без runtime-индирекции. Tailwind v4 подставлял правую часть прямо в utility: `bg-primary → var(--midground)` и `bg-card → color-mix(... --midground-base, --background-base)`, а не переменные override. Таблица фиксирует именно это историческое состояние; сейчас все строки подключены к соответствующим `--card`, `--primary`, `--input`, `--ring` и другим runtime-переменным.

| Поле | Светлая / тёмная | Встречаемость соответствующих class-token в `web/src` | Фактический статус |
|---|---|---:|---|
| `card` | `#FFFFFF` / `#20123A` | 28, 19 файлов | **Не читается из override**; `bg-card` вычисляется из palette. |
| `cardForeground` | `#202124` / `#F4EFFA` | 1 | **Не читается**; utility берёт `midground`. |
| `popover` | `#FFFFFF` / `#20123A` | 2 | **Не читается**; utility вычисляется из palette. |
| `popoverForeground` | `#202124` / `#F4EFFA` | 0 | **Не читался**, class-token на снимке не использовался. |
| `primary` | `#4F7900` / `#9BE424` | 136, 32 файла | **Не читается из override**; `primary`-классы фактически берут `midground`. |
| `primaryForeground` | `#FFFFFF` / `#150B29` | 9 | **Не читается**; utility берёт `background-base`. |
| `secondary` | `#EFEFEC` / `#25173F` | 8, 4 файла | **Не читается**; utility вычисляется из palette. |
| `secondaryForeground` | `#202124` / `#F4EFFA` | 0 | **Не читался**, class-token на снимке не использовался. |
| `muted` | `#E7E7E3` / `#271B41` | 46, 17 файлов | **Не читается**; utility вычисляется из palette. |
| `mutedForeground` | `#667085` / `#B9ACC9` | 392, 40 файлов | **Не читается из override**; итоговый `text-muted-foreground` использует package token `--color-text-secondary`, производный от `midground`. |
| `accent` | `#EAF7D3` / `#324719` | 2 | **Не читается**; utility вычисляется из palette. |
| `accentForeground` | `#304A00` / `#E3F8C0` | 1 | **Не читается**; utility берёт `midground`. |
| `destructive` | `#B42318` / `#FF6B74` | 112, 29 файлов | **Не читается**; production utility содержит literal `#fb2c36`. |
| `destructiveForeground` | `#FFFFFF` / `#150B29` | 1 | **Не читается**; production utility содержит literal `#fff`. |
| `success` | `#047857` / `#83D95B` | 38, 15 файлов | **Да.** Специально записывается в `--success`; utility читает `var(--success, ...)`. |
| `warning` | `#8A5200` / `#F6C453` | 63, 17 файлов | **Да.** Специально записывается в `--warning`; utility читает `var(--warning, ...)`. |
| `border` | `#CFD1CC` / `#493568` | 196, 40 файлов | **Не читается**; `border-border` вычисляется из `midground-base`. |
| `input` | `#B8BBB4` / `#493568` | 5 | **Не читается**; `border-input` вычисляется из `midground-base`. |
| `ring` | `#4F7900` / `#9BE424` | 5 | **Не читается**; ring/focus utility берёт `midground`. |

Иными словами, на снимке `#9BE424` был лишь предполагаемым тёмным `primary` и `ring`, но не управлял собранными кнопками/focus-ring. После исправления он управляет заполненными кнопками и фокусом; отдельно этот же цвет используется как `seriesColors.inputTokenAccent` на Analytics/Models и в логотипе. `swatchColors` текущий ThemeSwitcher по-прежнему не читает.

#### Остальные доступные поля темы — полный список

| Поле | Во что превращается | Реальный потребитель сейчас |
|---|---|---|
| `name`, `label`, `description` | metadata | `name`/`label` использует ThemeSwitcher; `description` в текущем picker не показан. |
| `layoutVariant`: `standard` / `cockpit` / `tiled` | `data-layout-variant` и `--theme-layout-variant` | App ставит атрибут (`App.tsx:589`, `617`), но CSS-селекторов и ветвления layout по нему нет. Без plugin/customCSS внешний вид не меняется. Обе темы поле не задают. |
| `assets.bg`, `hero`, `logo`, `crest`, `sidebar`, `header` | `--theme-asset-<name>` и `--theme-asset-<name>-raw` | Прямых читателей в текущем shell/package нет; предназначены для plugin/customCSS. Обе темы не задают. |
| `assets.custom` | безопасные ключи становятся `--theme-asset-custom-<key>` + `-raw` | Только plugin/customCSS; текущих значений нет. |
| `customCSS` | содержимое отдельного `<style id="hermes-theme-custom-css">` | **Да, механизм действующий** (`context.tsx:255-273`, `384-386`), но обе темы поле не задают. Это theme-specific мост к уровню B. |
| `componentStyles.card` | произвольные `--component-card-*` | Package `Card` реально читает только `background`, `border-image`, `box-shadow`, `clip-path` (`card.tsx:3-18`). |
| `componentStyles.header` | `--component-header-*` | App реально читает `background`, `border-image`, `clip-path` (`App.tsx:636-647`). |
| `componentStyles.sidebar` | `--component-sidebar-*` | App и ChatPage реально читают `background`, `border-image`, `clip-path` (`App.tsx:687-704`; `ChatPage.tsx:1756-1758`). |
| `componentStyles.tab` | `--component-tab-*` | App реально читает только `clip-path` (`App.tsx:1096-1098`). |
| `componentStyles.footer`, `progress`, `badge`, `backdrop`, `page` | соответствующие `--component-*` | Переменные создаются, но встроенных читателей нет. Их можно прочитать из `customCSS`/plugin. |
| `seriesColors.inputTokenAccent` | `--series-input-token` | **Да.** Models (`ModelsPage.tsx:119-120`) и Analytics (`AnalyticsPage.tsx:153-338`). |
| `seriesColors.outputTokenAccent` | `--series-output-token` | **Да.** Те же два экрана. |
| `swatchColors` | ни во что | **Нет.** Текущий ThemeSwitcher рисует Moon/Sun и не читает swatch (`ThemeSwitcher.tsx:157-190`). |
| `terminalBackground` | theme object + `--theme-terminal-background` | **Да.** ChatPage и HermesConsoleModal читают поле объекта (`ChatPage.tsx:343-345`; `HermesConsoleModal.tsx:361-362`, `475-476`). |
| `terminalForeground` | theme object + `--theme-terminal-foreground` | **Да.** Те же поверхности. |

### Уровень B. Наш Tailwind/CSS без изменения npm-пакета

Tailwind здесь CSS-first, версия 4.3.3. Поэтому «конфиг Tailwind» — не `tailwind.config.js`, а:

- `@import 'tailwindcss'` — `web/src/index.css:1`;
- package fonts/globals — `index.css:8-9`;
- принудительный scan package dist — `index.css:11-13`;
- локальные `@theme inline` для spacing, fonts, shadcn-compatible colors и radii — `index.css:112-120`, `227-270`;
- Vite plugin — `web/vite.config.ts:14`, `75`.

Что можно централизованно сделать у нас без вендоринга:

- переназначить Tailwind semantic tokens или исправить их indirection;
- задать package-indirection `--text-primary`, `--text-secondary`, `--text-tertiary`, `--text-disabled`, `--text-on-accent`, `--text-display` из `node_modules/@nous-research/ui/src/ui/globals.css:122-140`; эти переменные не представлены отдельными полями `DashboardTheme`, но реально питают `text-text-*` utilities;
- добавить общие selector rules для `button`, `input`, `a`, `[role="switch"]`, `[data-slot="dialog-content"]`;
- переопределить шрифты, uppercase/tracking, min-height, border radius, shadows, hover/focus;
- использовать `data-client-ui="true"` как scope только продуктового режима;
- сделать theme-specific CSS через `DashboardTheme.customCSS`;
- использовать уже предусмотренные `componentStyles.card/header/sidebar/tab`.

Это уже делается: `index.css:129-208` заменяет package display-font на системный, снимает uppercase, жёстко скругляет product-mode кнопки, скрывает `arc-border`, увеличивает `text-xs` и даёт touch target 44 px. Значит, npm-пакет не является непробиваемым визуальным слоем.

Ограничения CSS-слоя:

- правило меняет вид, но не DOM, React props, accessibility semantics и event logic;
- часть package-компонентов принимает `className`, и локальный класс идёт после default classes; это удобный точечный override;
- `Toast` не принимает `className` и portaled прямо в `document.body` (`toast.tsx:23-44`), поэтому `[data-client-ui=true] ...` на него не распространяется;
- `ConfirmDialog` и `BottomSheet` также не дают наружу общий `className` для content; у Dialog, напротив, есть `className` и стабильные `data-slot` (`dialog.tsx:24-145`);
- слишком широкие selector rules (`button span`, `body > [role=status]`) хрупки: они могут задеть Select, Switch и другие кнопки.

В репозитории уже есть локальный пример «не форкать весь kit»: `web/src/components/ProductButton.tsx:15-59`. Он даёт спокойную product-кнопку и сейчас используется в `CronPage.tsx` и `FilesPage.tsx`.

### Уровень C. Внутренности package-компонентов

Токенами нельзя изменить:

- DOM Button: `Typography as="button"`, внутренний `arc-border`, пустой spacer и абсолютный icon slot (`button.tsx:104-159`);
- матрицу вариантов/размеров и жёсткие bevel-shadows Button (`button.tsx:8-100`);
- DOM Switch: `<button role="switch">` + один внутренний `<span>`-thumb, размеры `h-5 w-9` / `h-3.5 w-3.5`, translate `0.5/4` (`switch.tsx:7-47`);
- внутренние SVG `CheckGlyph`/`ChevronDownGlyph` Select (`select.tsx:123-223`) и `XIcon` Dialog (`dialog.tsx:148-163`);
- custom listbox implementation и keyboard/event logic Select (`select.tsx:32-189`);
- portal, позицию, два типа и animation contract Toast (`toast.tsx:8-48`);
- portal/drag threshold/scroll lock BottomSheet (`bottom-sheet.tsx:15-218`);
- Radix composition и внутренний WarningTriangle ConfirmDialog (`confirm-dialog.tsx:1-117`).

Чистую форму, цвет, размер и тень часто ещё можно нарисовать поверх этой разметки CSS-ом. **Вендоринг обязателен только если новый дизайн требует другой разметки, другого API/варианта, другой встроенной иконки или другого поведения.** Редактировать `node_modules` напрямую нельзя: следующая установка пакетов сотрёт изменения.

## 2. «Хочу изменить X → правь Y»

| Желание | Центральная точка | Что охватит | Оценка |
|---|---|---|---|
| Цвет обычных package-кнопок | `colorOverrides.primary/primaryForeground`; реализация `button.tsx:17-100` дополнена стабильным scoped CSS-правилом | Все обычные заполненные Button в продуктовом режиме | Одна theme-правка. Ghost/destructive/outlined/invert и disabled сохраняют собственную семантику; новая структура — точечный vendor Button. |
| Цвет product-кнопок | `web/src/components/ProductButton.tsx:31-47`; после исправления token wiring — `colorOverrides.primary` | Сейчас только Cron/Files и любые будущие импорты wrapper | Одна правка wrapper на весь его охват; не нужен полный vendor. |
| Скругление всего интерфейса | `presets.ts:22-25` → `DEFAULT_LAYOUT.radius`; mapping `index.css:266-269` | Все `rounded-sm/md/lg/xl` | Одна правка на обе темы, **частично**. Для product Button отдельно убрать/изменить hardcode `index.css:158-160`; package-компоненты без rounded не изменятся. |
| Базовый размер шрифта | `presets.ts:14-20` → `baseSize`; consumer `index.css:90-94` | Весь rem-based текст и размеры | Одна правка на обе темы. Explicit px остаются прежними. |
| Гарнитура | `DEFAULT_TYPOGRAPHY.fontSans/fontMono` (`presets.ts:14-20`) или готовый picker `themes/fonts.ts:56-144` | Sans/mono по всему документу | Одна theme-правка или выбор в UI. Для внешнего webfont нужен также разрешённый `fontUrl`. |
| Больше/меньше воздуха | `DEFAULT_LAYOUT.density` (`presets.ts:22-25`) → multiplier `context.tsx:81-104` → `index.css:112-120` | Tailwind spacing/size scale package и нашего UI | Одна правка на обе темы: `compact`, `comfortable`, `spacious`. Не охватит arbitrary px/44px. |
| Совсем другой переключатель | `node_modules/.../switch.tsx:7-47`; 9 JSX-мест в 7 файлах | Switch | Цвет можно CSS/palette, root принимает `className`; thumb/DOM/animation/API — **точечно вендорить Switch**. Это дешёвый кандидат, полный fork не нужен. |
| Поверхность карточек | `colorOverrides.card/cardForeground` или точечный `componentStyles.card.background`; readers `card.tsx:14-18` | Все utility-card поверхности; `componentStyles` — package Card | Одна theme-правка. Padding/header/title typography (`card.tsx:38-84`) — CSS или точечный vendor. |
| Тени карточек | `componentStyles.card.boxShadow` | Все package Card | Одна правка темы. |
| Единая система теней модалок/меню/кнопок | Наш `index.css`; текущие hardcodes перечислены в `web/src/lib/dashboard-modal-shell.ts:19`, Button `button.tsx:8-13`, Toast/BottomSheet/Dialog | Разные семейства элементов | Нет рабочего theme shadow-scale. Нужны несколько CSS rules/wrappers; Button bevel или структурные отличия — selective vendor. |
| Цвет ссылок | Markdown: `web/src/components/Markdown.tsx:421-429`; для всего приложения — selector `a[href]` в `index.css` | Markdown отдельно или все ссылки | Нет link-token. Одна CSS-правка возможна, но нужно исключить NavLink/button-like links. Вендоринг не нужен. |
| Вид input-полей | `colorOverrides.input/ring`; package `input.tsx:3-18` (65 JSX-мест/21 файл); общие правила в `index.css` | Package Input, textarea и combobox в продуктовом режиме | Цвет рамки и фокуса — theme-правка; радиус/высота — CSS/palette/density, без vendor. |
| Вид тостов/уведомлений | Package `toast.tsx:8-45`; animations также `index.css:279-287` | 17 JSX-тостов + plugin registry | Success-color работает через token; position/font/uppercase/markup зашиты, `className` нет, portal вне scope. Для небольшого restyle — аккуратный global CSS; для нового вида/API — точечно vendor Toast. |
| Набор и стиль иконок | Наши imports `lucide-react` в 45 файлах; package glyphs в Checkbox/Select/Dialog | Большинство app icons / несколько package-internal icons | Нет icon-token. Цвет часто наследуется; смена icon family требует править imports/wrapper. Встроенные package SVG — selective vendor соответствующего компонента. |
| Вид бокового меню | shell `web/src/App.tsx:687-930`, item `1072-1126`; surface tokens `componentStyles.sidebar.*`; состав `web/src/lib/product-nav.ts:20-150` | Весь sidebar | Фон/clip/border-image — одна theme-правка. Ширина, группировка, item layout, collapse — править наш App/CSS, **не package и не vendor**. |
| Цвет статусов success/warning/error | `colorOverrides.success/warning/destructive` в обеих темах | Badges, notices, status text и destructive utilities | Одна theme-правка на режим; все три проходят через runtime tokens. |
| Вид dialog/modal | Package `dialog.tsx:24-145`, local shell `web/src/lib/dashboard-modal-shell.ts:8-19` | 7 Dialog content sites + ручные модалки | Dialog имеет `className`/`data-slot`: CSS/local shell обычно достаточно. Другой DOM/focus model — selective vendor. |
| Цвет терминала/чата | `terminalBackground`/`terminalForeground` в `presets.ts:45-46`, `92-93` | Embedded chat terminal и Hermes console | По одной правке на тему, vendor не нужен. |
| Цвет графиков token input/output | `seriesColors` в `presets.ts:47-50`, `94-97` | Analytics + Models | Одна правка на тему, vendor не нужен. |

## 3. Инвентарь реально используемых компонентов

Package-код импортируется в 46 production-файлах `web/src`. Используются 22 package component module paths и 36 экспортированных React-компонентов/примитивов. Дополнительно используются три hooks: `useBelowBreakpoint` (3 ссылки/3 файла), `useConfirmDelete` (12/9), `useToast` (17/17).

В таблице «JSX-мест» — статические места в исходниках. `plugins/registry.ts` отдельно экспортирует часть компонентов в plugin SDK; там JSX-место равно нулю, но runtime-плагины могут создавать сколько угодно экземпляров.

| Package module | Экспорт | JSX-мест | Файлов |
|---|---|---:|---:|
| `badge` | `Badge` | 113 | 21 |
| `bottom-sheet` | `BottomSheet` | 2 | 2 |
| `button` | `Button` | 226 | 31 |
| `card` | `Card` | 86 | 23 |
| `card` | `CardContent` | 82 | 22 |
| `card` | `CardDescription` | 6 | 2 |
| `card` | `CardHeader` | 20 | 11 |
| `card` | `CardTitle` | 20 | 11 |
| `checkbox` | `Checkbox` | 9 | 6 |
| `confirm-dialog` | `ConfirmDialog` | 8 | 7 |
| `command-block` | `CopyButton` | 2 | 2 |
| `dialog` | `Dialog` | 7 | 6 |
| `dialog` | `DialogClose` | 0 + registry | 1 |
| `dialog` | `DialogContent` | 7 | 6 |
| `dialog` | `DialogDescription` | 7 | 6 |
| `dialog` | `DialogFooter` | 4 | 4 |
| `dialog` | `DialogHeader` | 7 | 6 |
| `dialog` | `DialogTitle` | 7 | 6 |
| `segmented` | `FilterGroup` | 4 | 1 |
| `typography/h2` | `H2` | 18 | 8 |
| `input` | `Input` | 65 | 21 |
| `label` | `Label` | 95 | 17 |
| `list-item` | `ListItem` | 12 | 9 |
| `segmented` | `Segmented` | 7 | 3 |
| `select` | `Select` | 21 | 12 |
| `selection-switcher` | `SelectionSwitcher` | 1 | 1 |
| `select` | `SelectOption` | 43 | 12 |
| `separator` | `Separator` | 0 + registry | 1 |
| `spinner` | `Spinner` | 97 | 25 |
| `stats` | `Stats` | 2 | 2 |
| `switch` | `Switch` | 9 | 7 |
| `tabs` | `Tabs` | 0 + registry | 1 |
| `tabs` | `TabsList` | 0 + registry | 1 |
| `tabs` | `TabsTrigger` | 0 + registry | 1 |
| `toast` | `Toast` | 17 | 18, включая registry |
| `typography/index` | `Typography` | 9 | 4 |

Суммарно это 1 013 статических JSX-тегов package-компонентов. Самые дорогие для массовой миграции: Button, Badge, Spinner, Card/CardContent, Label и Input. Самые дешёвые структурные кандидаты на selective vendor: Switch (9 мест), BottomSheet (2), CopyButton (2), Stats (2).

Отдельная цена смены import paths в тестах: `web/src/components/ChatSidebar.test.tsx:64-70` мокает package Button, Badge и Card.

## 4. Что реально означает вендоринг

### Измеренный объём package

- `node_modules/@nous-research/ui/src`: 148 файлов;
- логический размер: 1 296 723 bytes; занимаемое дисковое место `du -sh`: 1.7 MB;
- в `src/ui/components` — 82 верхнеуровневых filesystem entry (75 файлов + 7 каталогов), рекурсивно 70 non-story `.tsx` implementations;
- весь установленный package занимает 4.1 MB;
- package metadata говорит `MIT` (`package.json:5`); при копировании надо сохранить исходный license/copyright notice. В опубликованном package отдельного `LICENSE` файла не найдено, поэтому это нужно отдельно проверить перед переносом, а не потерять notice молча.

Runtime dependencies package по его `package.json:53-62` и фактически установленным версиям:

- `@nanostores/react@1.1.0`, `nanostores@1.4.2`;
- `radix-ui@1.6.7`;
- `class-variance-authority@0.7.1`, `clsx@2.1.1`, `tailwind-merge@3.6.0`;
- `sanitize-html@2.17.6`;
- `tw-animate-css@1.4.0`;
- `unicode-animations@1.0.3`.

У package также есть peer dependencies на React/ReactDOM и тяжёлые optional surfaces (`three`, `@react-three/fiber`, Observable Plot, Leva, GSAP, Motion). Часть уже есть в `web/package.json`; полный перенос всё равно обязан проверить весь import graph, а не только компоненты из инвентаря.

### Работы при полном переносе

Полный source vendor — это не «скопировать одну папку». Минимальный список:

1. Перенести runtime source, CSS, fonts/assets и license notice в стабильный локальный namespace.
2. Заменить 22 component module paths в 46 production-файлах и три hook paths; обновить три test mocks.
3. Заменить package CSS imports и `@source '../node_modules/@nous-research/ui/dist'` в `web/src/index.css:8-13`.
4. Сделать прямыми dependencies всё, что локальный source теперь импортирует; сейчас часть библиотек приходит только транзитивно через `@nous-research/ui`.
5. Обновить Vite split: текущий `ui`-чанк создаётся regex по `node_modules/@nous-research/ui` (`web/vite.config.ts:138-141`). Локальный source в него не попадёт и перераспределится по app/route chunks.
6. Проверить fonts (`Collapse`, `Mondwest`, `Rules*`), portals, Radix focus/keyboard behavior, responsive BottomSheet, plugin registry и обе темы.
7. Провести typecheck, unit tests, production build и визуальный/a11y smoke всех основных страниц.

Текущая production-сборка даёт ориентир: отдельный `ui` chunk — 289.43 kB raw / 94.68 kB gzip; общий CSS — 132.89 kB raw / 19.61 kB gzip. Сам по себе vendor не гарантирует уменьшения: локальный код просто перестанет попадать в текущую chunk-group, пока конфиг не будет обновлён.

Если удалить npm dependency до выполнения списка выше, сборка сразу потеряет 22 component exports, 3 hooks, `styles/fonts.css` и `styles/globals.css`. Даже если TypeScript затем собрать, пропущенные fonts/globals изменят внешний вид и Tailwind source scanning.

Оценка объёма: механический полный перенос — несколько десятков import/CSS/build изменений; безопасная миграция — минимум несколько рабочих дней с просмотром ключевых экранов. Дальше каждый upstream update превращается из одного осознанного bump exact-версии `0.18.2` в ручной diff/merge локального форка. Это постоянная стоимость, а не разовая копия 1.7 MB.

### Три стратегии

| Стратегия | Плюсы | Цена/риск | Вывод |
|---|---|---|---|
| Вендорить всё | Полный контроль над всеми 82 entry и поведением | 148 файлов, 46 consumers, styles/fonts/hooks/deps/chunking; ручные обновления всего пакета | **Не рекомендуется сейчас.** Мы используем только 22 component module paths, а большинство желаемых изменений визуальные. |
| Вендорить точечно | Контроль именно над неподходящей структурой; остальной kit обновляется как раньше | Нужно держать совместимый API и сменить imports конкретного компонента | **Рекомендуется только по факту structural mismatch.** Первые кандидаты: Switch/Toast; Button — только если CSS/wrapper не покрывает новый дизайн. |
| Не вендорить, токены + наш CSS | Минимальная поддержка, одна правка каскадируется, package остаётся обновляемым | DOM/behavior не меняются; scoped selector package-кнопки надо повторно проверять после обновления package | **Основная стратегия для первого редизайна.** Палитра/типографика/плотность/componentStyles и небольшой локальный CSS-system pass уже дают центральное управление видом. |

Итоговая рекомендация: **не форкать весь `@nous-research/ui`**. Формировать визуальный результат через уже подключённый semantic token layer в `presets.ts` + `index.css`. После макетов составить короткий список компонентов, где нужен другой DOM/interaction, и перенести только их. Это сохраняет package updates и резко уменьшает площадь тестирования.

## 5. Быстрые победы без вендоринга

Ниже — отдельные эксперименты, не готовая палитра; их не следует применять все разом без просмотра light/dark. Каждая строка — один центральный token change или одно theme-field addition.

1. **Сделать тёмный canvas глубже.** `darkTheme.palette.background.hex` (`presets.ts:84`) заменить `#150B29` на, например, `#0E0718`. Потемнеют canvas, package-поверхности и поля, использующие `background*`; контраст светлого `midground` вырастет.

2. **Смягчить белый chrome тёмной темы.** `darkTheme.palette.midground.hex` (`presets.ts:85`) заменить `#F4EFFA` на, например, `#E6DAF2`. Одновременно станут мягче основной текст, нейтральные линии и hover. Акцент заполненных кнопок останется независимым в `colorOverrides.primary`.

3. **Увеличить читаемость целиком.** `DEFAULT_TYPOGRAPHY.baseSize` (`presets.ts:17`) поменять `15px` → `16px`. Вырастут rem-текст и rem-spacing; явные px и touch targets останутся прежними.

4. **Добавить воздуха или уплотнить панель.** `DEFAULT_LAYOUT.density` (`presets.ts:24`) поменять `comfortable` → `spacious` (multiplier 1.2) или `compact` (0.85). Все обычные Tailwind padding/gap/height изменятся пропорционально в обеих темах.

5. **Сменить характер шрифта одной строкой без сетевого font.** `DEFAULT_TYPOGRAPHY.fontSans` (`presets.ts:15`) заменить на системный serif stack `Georgia, Cambria, "Times New Roman", Times, serif`. Это сразу даст принципиально другой характер всему body UI. Для брендового webfont нужны две согласованные настройки: `fontSans` и `fontUrl`, либо готовый picker.

6. **Сделать геометрию мягче.** `DEFAULT_LAYOUT.radius` (`presets.ts:23`) поменять `0.625rem` → `1rem`. Все `rounded-sm/md/lg/xl` станут круглее. Caveat: product-mode buttons сейчас всё равно зафиксированы в `index.css:158-160`, поэтому для абсолютно единого результата нужно убрать этот hardcode.

7. **Дать всем package-карточкам объём без обхода 86 мест.** В нужную тему добавить одной строкой `componentStyles: { card: { boxShadow: "0 18px 50px rgba(0,0,0,.24)" } }`. Package Card читает эту переменную inline; все его экземпляры получат тень.

8. **Перекрасить статусные акценты.** Поменять `colorOverrides.success`, `warning` или `destructive` в нужной теме. Все три подтверждённо доходят до production utilities и меняют badges/notices/status text.

После исправления wiring `colorOverrides.primary`, `ring`, `card`, `mutedForeground`, `border`, `input` и `destructive` стали рабочими центральными рычагами. Встроенные темы задают их полностью; неполные пользовательские темы безопасно используют CSS fallback.

## Решение в одной фразе

У Korra 21 есть рабочая центральная тема и сильный локальный CSS-слой; полный вендоринг — слишком дорогая реакция на визуальный редизайн. Следующий дизайн-проход можно строить на центральных токенах, затем переопределять стабильные визуальные правила у себя, а вендорить только те 1–3 компонента, где макет действительно требует другого устройства или поведения.
