/**
 * Русские подписи к описаниям опасных команд.
 *
 * Движок отдаёт описание паттерна по-английски: строки живут в
 * `DANGEROUS_PATTERNS` (`tools/approval.py`) и попадают в карточку как есть.
 * В интерфейсе Korra английского быть не должно, а в двух строках движка
 * встречается ещё и прежнее имя форка — оно тем более не должно доезжать до
 * владельца.
 *
 * Сопоставление идёт по точной строке, потому что описания стабильны и заданы
 * литералами. Незнакомое остаётся как есть: молча показать чужой текст лучше,
 * чем угадать смысл и соврать про то, что команда делает. Так же поступаем с
 * находками tirith — они собираются на лету и словарём не покрываются.
 *
 * Когда предупреждений несколько, ядро склеивает их через «; »
 * (`combined_desc` в `check_all_command_guards`), поэтому сначала пробуем
 * строку целиком, а уже потом разбираем её по частям.
 */

const APPROVAL_DESCRIPTIONS: Record<string, string> = {
  // ── Удаление ──────────────────────────────────────────────────────────
  "recursive delete": "рекурсивное удаление",
  "recursive delete (long flag)": "рекурсивное удаление",
  "recursive delete (flags after operands)": "рекурсивное удаление",
  "delete in root path": "удаление в корневом каталоге",
  "find -delete": "удаление найденных файлов (find -delete)",
  "find -exec/-execdir rm": "удаление найденных файлов (find -exec rm)",
  "xargs with rm": "удаление списком (xargs rm)",
  "git clean with force (deletes untracked files)":
    "git clean -f — удаление неотслеживаемых файлов",
  "git branch force delete": "принудительное удаление ветки git",
  "git branch force delete (long flags)": "принудительное удаление ветки git",
  "git branch force delete (long flags, force-first)":
    "принудительное удаление ветки git",
  "git reset --hard (destroys uncommitted changes)":
    "git reset --hard — потеря незакоммиченных правок",
  "git force push (rewrites remote history)":
    "принудительный push — перезапись истории на сервере",
  "git force push short flag (rewrites remote history)":
    "принудительный push — перезапись истории на сервере",
  "delete backups (wbadmin)": "удаление резервных копий (wbadmin)",
  "delete volume shadow copies (vssadmin)":
    "удаление теневых копий тома (vssadmin)",
  "Windows destructive delete (recursive/quiet switch)":
    "разрушительное удаление в Windows",
  "Windows cmd destructive delete": "разрушительное удаление через cmd",
  "Windows PowerShell destructive delete":
    "разрушительное удаление через PowerShell",
  "PowerShell destructive delete (Remove-Item)":
    "разрушительное удаление через PowerShell (Remove-Item)",
  "registry delete (reg delete)": "удаление ключа реестра",
  "registry value delete (Remove-ItemProperty -Force)":
    "удаление значения реестра",

  // ── Диски и файловые системы ──────────────────────────────────────────
  "format filesystem": "форматирование файловой системы",
  "format filesystem (Format-Volume)": "форматирование тома",
  "format drive (format.com)": "форматирование диска",
  "wipe disk (Clear-Disk)": "полная очистка диска",
  "wipe free space (cipher /w)": "затирание свободного места на диске",
  "disk copy": "посекторное копирование диска",
  "disk partitioning (diskpart)": "разметка диска",
  "write to block device": "запись напрямую на диск",
  "modify boot configuration (bcdedit /set)": "правка загрузчика системы",

  // ── Скачать и выполнить ───────────────────────────────────────────────
  "pipe remote content to shell": "скачать из сети и сразу выполнить",
  "pipe remote content to PowerShell (iwr | iex)":
    "скачать из сети и сразу выполнить в PowerShell",
  "execute remote content via command substitution":
    "выполнение скачанного через подстановку команды",
  "execute remote script via process substitution":
    "выполнение скачанного через подстановку процесса",
  "execute remote content via Invoke-Expression":
    "выполнение скачанного через Invoke-Expression",
  "pipe decoded content to shell (possible command obfuscation)":
    "выполнение декодированного текста — возможно, команда спрятана",
  "pipe openssl-decoded content to shell (possible command obfuscation)":
    "выполнение декодированного через openssl — возможно, команда спрятана",
  "pipe tr-transformed output to shell (possible command obfuscation)":
    "выполнение преобразованного через tr — возможно, команда спрятана",
  "pipe xxd-decoded content to shell (possible command obfuscation)":
    "выполнение декодированного через xxd — возможно, команда спрятана",
  "PowerShell encoded command execution":
    "запуск закодированной команды PowerShell",
  "shell execution via heredoc": "запуск скрипта из heredoc",
  "chmod +x followed by immediate execution":
    "выдача прав на запуск и немедленный запуск",
  "fork bomb": "форк-бомба — исчерпание процессов",

  // ── Процессы и службы ─────────────────────────────────────────────────
  "force kill processes": "принудительное завершение процессов",
  "force kill processes (Stop-Process -Force)":
    "принудительное завершение процессов",
  "force kill processes (killall -KILL)": "принудительное завершение процессов",
  "force kill processes (killall -s KILL)":
    "принудительное завершение процессов",
  "force kill processes (taskkill /F)": "принудительное завершение процессов",
  "kill all processes": "завершение всех процессов",
  "kill processes by regex (killall -r)":
    "завершение процессов по шаблону имени",
  "kill hermes/gateway process (self-termination)":
    "завершение процесса самой Корры",
  "kill process via pgrep/pidof expansion (self-termination)":
    "завершение процесса по поиску pid — можно убить саму Корру",
  "kill process via backtick pgrep/pidof expansion (self-termination)":
    "завершение процесса по поиску pid — можно убить саму Корру",
  "stop/restart system service": "остановка или перезапуск системной службы",
  "stop/delete service (sc)": "остановка или удаление службы",
  "force stop service (Stop-Service -Force)":
    "принудительная остановка службы",
  "stop/restart hermes gateway (kills running agents)":
    "перезапуск шлюза Корры — оборвёт работающих агентов",
  "stop/restart hermes launchd service (kills running agents)":
    "перезапуск службы Корры — оборвёт работающих агентов",
  "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')":
    "запуск шлюза мимо systemd",
  "hermes update (restarts gateway, kills running agents)":
    "обновление Корры — перезапустит шлюз и оборвёт работающих агентов",

  // ── Контейнеры ────────────────────────────────────────────────────────
  "docker restart/stop/kill (container lifecycle)":
    "остановка или перезапуск контейнера",
  "docker compose restart/stop/kill/down (container lifecycle)":
    "остановка или перезапуск контейнеров compose",
  "docker context use (switches default daemon for future commands)":
    "смена контекста docker — следующие команды уйдут другому демону",
  "docker with daemon redirect (--context: alternate daemon)":
    "docker с перенаправлением на другой демон",
  "docker with remote daemon redirect (-H/--host)":
    "docker с перенаправлением на удалённый демон",
  "docker/podman daemon redirect via environment (DOCKER_HOST/CONTAINER_HOST)":
    "перенаправление docker/podman на другой демон через переменную окружения",
  "podman remote mode (-r/--remote: remote daemon)":
    "podman в удалённом режиме",
  "podman with remote daemon redirect (--url/--connection/--identity)":
    "podman с перенаправлением на удалённый демон",

  // ── Права доступа ─────────────────────────────────────────────────────
  "world/other-writable permissions": "права на запись всем подряд",
  "recursive world/other-writable (long flag)":
    "рекурсивная выдача прав на запись всем подряд",
  "recursive chown to root": "рекурсивная смена владельца на root",
  "recursive chown to root (long flag)":
    "рекурсивная смена владельца на root",
  "grant Everyone access (icacls)": "выдача доступа группе «Все»",
  "reset ACLs recursively (icacls /reset)":
    "рекурсивный сброс прав доступа",
  "sudo with privilege flag (stdin/askpass/shell/list)":
    "sudo с повышением прав",
  "sudo with combined-flag privilege escalation": "sudo с повышением прав",

  // ── Секреты и конфигурация ────────────────────────────────────────────
  "access to SSH keys (Windows path)": "доступ к ключам SSH",
  "access to Korra secrets (Windows path)": "доступ к секретам Корры",
  "in-place edit of Korra config/env": "правка конфига Корры на месте",
  "in-place edit of Korra config/env (long flag)":
    "правка конфига Корры на месте",
  "in-place edit of Korra config/env (perl/ruby)":
    "правка конфига Корры на месте",
  "in-place edit of sensitive credential/SSH/shell-rc path":
    "правка файла с доступами на месте",
  "in-place edit of sensitive credential/SSH/shell-rc path (long flag)":
    "правка файла с доступами на месте",
  "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)":
    "правка файла с доступами на месте",
  "in-place edit of system config": "правка системного конфига на месте",
  "in-place edit of system config (long flag)":
    "правка системного конфига на месте",
  "copy/move file into sensitive credential/SSH/shell-rc path":
    "запись файла в каталог с доступами",
  "copy/move file into system config path":
    "запись файла в системный конфиг",
  "overwrite project env/config file": "перезапись конфига проекта",
  "overwrite project env/config via redirection":
    "перезапись конфига проекта через перенаправление",
  "overwrite project env/config via tee":
    "перезапись конфига проекта через tee",
  "overwrite system config": "перезапись системного конфига",
  "overwrite system file via redirection":
    "перезапись системного файла через перенаправление",
  "overwrite system file via tee": "перезапись системного файла через tee",

  // ── Базы данных ───────────────────────────────────────────────────────
  "SQL DROP": "удаление таблицы или базы (SQL DROP)",
  "SQL TRUNCATE": "очистка таблицы целиком (SQL TRUNCATE)",
  "SQL DELETE without WHERE": "удаление всех строк таблицы (DELETE без WHERE)",

  // ── Произвольный код ──────────────────────────────────────────────────
  "execute_code script execution. The script can spawn subprocesses or mutate files without passing through terminal command approval; approval is one-shot for this run.":
    "запуск произвольного кода. Скрипт может сам запускать программы и менять файлы, минуя проверку команд; разрешение действует только на этот запуск.",
};

/** Первая буква заглавная — подзаголовок карточки читается как фраза. */
function capitalize(text: string): string {
  return text ? text[0]!.toUpperCase() + text.slice(1) : text;
}

/**
 * Перевести описание опасности на русский.
 *
 * Незнакомое возвращается без изменений: показать чужой текст честнее, чем
 * угадать смысл команды.
 */
export function translateApprovalDescription(raw?: string): string {
  const text = raw?.trim() ?? "";
  if (!text) return "";

  const whole = APPROVAL_DESCRIPTIONS[text];
  if (whole) return capitalize(whole);

  // Несколько предупреждений на одну команду ядро склеивает через «; ».
  const parts = text.split("; ");
  if (parts.length === 1) return text;
  const translated = parts.map((part) => {
    const hit = APPROVAL_DESCRIPTIONS[part.trim()];
    return hit ?? part;
  });
  return capitalize(translated.join("; "));
}
