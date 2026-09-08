"""Human approval copy; canonical detector reasons and permission keys stay unchanged."""

from agent.i18n import get_language


_RUSSIAN_REASONS = {
    'recursive delete of root filesystem': 'удаление корневой файловой системы',
    'recursive delete of system directory': 'удаление системной папки со всем содержимым',
    'recursive delete of home directory': 'удаление домашней папки со всем содержимым',
    'format filesystem (mkfs)': 'форматирование файловой системы (mkfs)',
    'dd to raw block device': 'перезапись диска командой dd',
    'redirect to raw block device': 'перенаправление записи прямо на диск',
    'fork bomb': 'неограниченное создание процессов',
    'kill all processes': 'завершение всех процессов',
    'system shutdown/reboot': 'выключение или перезагрузка системы',
    'init 0/6 (shutdown/reboot)': 'выключение или перезагрузка системы (init 0/6)',
    'systemctl poweroff/reboot': 'выключение или перезагрузка системы (systemctl)',
    'telinit 0/6 (shutdown/reboot)': 'выключение или перезагрузка системы (telinit 0/6)',
    'delete in root path': 'удаление по пути от корня файловой системы',
    'recursive delete': 'удаление папки со всем содержимым',
    'recursive delete (long flag)': 'удаление папки со всем содержимым (длинный параметр)',
    'recursive delete (flags after operands)': 'удаление папки со всем содержимым (параметры после пути)',
    'Windows cmd destructive delete': 'удаление файлов через Windows cmd',
    'Windows PowerShell destructive delete': 'удаление файлов через Windows PowerShell',
    'PowerShell encoded command execution': 'выполнение закодированной команды PowerShell',
    'PowerShell destructive delete (Remove-Item)': 'удаление файлов PowerShell (Remove-Item)',
    'Windows destructive delete (recursive/quiet switch)': 'удаление файлов Windows без дополнительных запросов',
    'pipe remote content to PowerShell (iwr | iex)': 'запуск скачанного кода в PowerShell (iwr | iex)',
    'execute remote content via Invoke-Expression': 'запуск скачанного кода через Invoke-Expression',
    'force kill processes (taskkill /F)': 'принудительное завершение процессов (taskkill /F)',
    'force kill processes (Stop-Process -Force)': 'принудительное завершение процессов (Stop-Process -Force)',
    'format filesystem (Format-Volume)': 'форматирование файловой системы (Format-Volume)',
    'wipe disk (Clear-Disk)': 'очистка диска (Clear-Disk)',
    'disk partitioning (diskpart)': 'изменение разделов диска (diskpart)',
    'format drive (format.com)': 'форматирование диска (format.com)',
    'wipe free space (cipher /w)': 'безвозвратная очистка свободного места (cipher /w)',
    'grant Everyone access (icacls)': 'предоставление доступа всем пользователям (icacls)',
    'reset ACLs recursively (icacls /reset)': 'сброс прав доступа во вложенных папках (icacls /reset)',
    'delete volume shadow copies (vssadmin)': 'удаление теневых копий диска (vssadmin)',
    'delete backups (wbadmin)': 'удаление резервных копий (wbadmin)',
    'modify boot configuration (bcdedit /set)': 'изменение загрузки системы (bcdedit /set)',
    'registry delete (reg delete)': 'удаление записей реестра (reg delete)',
    'registry value delete (Remove-ItemProperty -Force)': 'удаление значения реестра (Remove-ItemProperty -Force)',
    'force stop service (Stop-Service -Force)': 'принудительная остановка службы (Stop-Service -Force)',
    'stop/delete service (sc)': 'остановка или удаление службы (sc)',
    'access to SSH keys (Windows path)': 'доступ к ключам SSH (путь Windows)',
    'access to Korra secrets (Windows path)': 'доступ к секретам Korra (путь Windows)',
    'world/other-writable permissions': 'разрешение записи всем пользователям',
    'recursive world/other-writable (long flag)': 'разрешение записи всем пользователям во вложенных папках',
    'recursive chown to root': 'передача владельцу root папки со всем содержимым',
    'recursive chown to root (long flag)': 'передача владельцу root папки со всем содержимым (длинный параметр)',
    'format filesystem': 'форматирование файловой системы',
    'disk copy': 'копирование диска',
    'write to block device': 'запись прямо на диск',
    'SQL DROP': 'удаление таблицы или базы данных (SQL DROP)',
    'SQL DELETE without WHERE': 'удаление всех строк без условия (SQL DELETE без WHERE)',
    'SQL TRUNCATE': 'очистка таблицы (SQL TRUNCATE)',
    'overwrite system config': 'перезапись системных настроек',
    'stop/restart system service': 'остановка или перезапуск системной службы',
    'force kill processes': 'принудительное завершение процессов',
    'force kill processes (killall -KILL)': 'принудительное завершение процессов (killall -KILL)',
    'force kill processes (killall -s KILL)': 'принудительное завершение процессов (killall -s KILL)',
    'kill processes by regex (killall -r)': 'завершение процессов по шаблону (killall -r)',
    'pipe remote content to shell': 'запуск скачанного кода в командной оболочке',
    'execute remote script via process substitution': 'запуск скачанного скрипта через подстановку процесса',
    'execute remote content via command substitution': 'запуск скачанного кода через подстановку команды',
    'pipe decoded content to shell (possible command obfuscation)': 'запуск декодированного кода в оболочке (возможна маскировка команды)',
    'pipe xxd-decoded content to shell (possible command obfuscation)': 'запуск кода после декодирования xxd (возможна маскировка команды)',
    'pipe tr-transformed output to shell (possible command obfuscation)': 'запуск кода после преобразования tr (возможна маскировка команды)',
    'pipe openssl-decoded content to shell (possible command obfuscation)': 'запуск кода после декодирования openssl (возможна маскировка команды)',
    'overwrite system file via tee': 'перезапись системного файла через tee',
    'overwrite system file via redirection': 'перезапись системного файла через перенаправление вывода',
    'overwrite project env/config via tee': 'перезапись секретов или настроек проекта через tee',
    'overwrite project env/config via redirection': 'перезапись секретов или настроек проекта через перенаправление вывода',
    'xargs with rm': 'удаление файлов через xargs и rm',
    'find -exec/-execdir rm': 'удаление файлов через find -exec/-execdir rm',
    'find -delete': 'удаление найденных файлов (find -delete)',
    'stop/restart hermes gateway (kills running agents)': 'остановка или перезапуск шлюза Korra (прервёт активных агентов)',
    'hermes update (restarts gateway, kills running agents)': 'обновление Korra (перезапустит шлюз и прервёт активных агентов)',
    'docker with remote daemon redirect (-H/--host)': 'подключение Docker к другому серверу (-H/--host)',
    'docker with daemon redirect (--context: alternate daemon)': 'подключение Docker к другому серверу (--context)',
    'docker context use (switches default daemon for future commands)': 'смена сервера Docker по умолчанию (docker context use)',
    'podman with remote daemon redirect (--url/--connection/--identity)': 'подключение Podman к другому серверу (--url/--connection/--identity)',
    'podman remote mode (-r/--remote: remote daemon)': 'удалённый режим Podman (-r/--remote)',
    'docker/podman daemon redirect via environment (DOCKER_HOST/CONTAINER_HOST)': 'смена сервера Docker/Podman через DOCKER_HOST/CONTAINER_HOST',
    'docker compose restart/stop/kill/down (container lifecycle)': 'перезапуск или остановка контейнеров (docker compose)',
    'docker restart/stop/kill (container lifecycle)': 'перезапуск или остановка контейнеров (docker)',
    "start gateway outside systemd (use 'systemctl --user restart hermes-gateway')": 'запуск шлюза вне systemd; используйте штатное управление службой Korra',
    'kill hermes/gateway process (self-termination)': 'завершение процесса Korra или шлюза (остановит агента)',
    'kill process via pgrep/pidof expansion (self-termination)': 'завершение процесса по pgrep/pidof (может остановить агента)',
    'kill process via backtick pgrep/pidof expansion (self-termination)': 'завершение процесса по подстановке pgrep/pidof (может остановить агента)',
    'stop/restart hermes launchd service (kills running agents)': 'остановка или перезапуск службы Korra в launchd (прервёт активных агентов)',
    'copy/move file into system config path': 'копирование или перенос файла в папку системных настроек',
    'overwrite project env/config file': 'перезапись файла секретов или настроек проекта',
    'copy/move file into sensitive credential/SSH/shell-rc path': 'копирование или перенос файла с ключами доступа либо настройками SSH/оболочки',
    'in-place edit of sensitive credential/SSH/shell-rc path': 'изменение файла с ключами доступа либо настройками SSH/оболочки',
    'in-place edit of sensitive credential/SSH/shell-rc path (long flag)': 'изменение файла с ключами доступа либо настройками SSH/оболочки (длинный параметр)',
    'in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)': 'изменение файла с ключами доступа либо настройками SSH/оболочки через perl/ruby',
    'in-place edit of system config': 'изменение системных настроек',
    'in-place edit of system config (long flag)': 'изменение системных настроек (длинный параметр)',
    'in-place edit of Korra config/env': 'изменение настроек или секретов Korra',
    'in-place edit of Korra config/env (long flag)': 'изменение настроек или секретов Korra (длинный параметр)',
    'in-place edit of Korra config/env (perl/ruby)': 'изменение настроек или секретов Korra через perl/ruby',
    'shell execution via heredoc': 'выполнение скрипта оболочки через heredoc',
    'git reset --hard (destroys uncommitted changes)': 'git reset --hard (удалит несохранённые изменения)',
    'git force push (rewrites remote history)': 'принудительная отправка Git (перезапишет удалённую историю)',
    'git force push short flag (rewrites remote history)': 'принудительная отправка Git коротким параметром (перезапишет удалённую историю)',
    'git clean with force (deletes untracked files)': 'git clean (удалит неотслеживаемые файлы)',
    'git branch force delete': 'принудительное удаление ветки Git',
    'git branch force delete (long flags)': 'принудительное удаление ветки Git (длинные параметры)',
    'git branch force delete (long flags, force-first)': 'принудительное удаление ветки Git (параметр force первым)',
    'chmod +x followed by immediate execution': 'разрешение запуска файла (chmod +x) и немедленный запуск',
    'sudo with privilege flag (stdin/askpass/shell/list)': 'sudo с повышением прав (stdin/askpass/shell/list)',
    'sudo with combined-flag privilege escalation': 'sudo с повышением прав через объединённые параметры',
    'script execution via -e/-c flag': 'выполнение скрипта через параметр -e/-c',
    'script execution via heredoc': 'выполнение скрипта через heredoc',
    'dangerous command': 'опасная команда',
    'script execution via -c flag': 'выполнение скрипта через параметр -c',
    'shell command via -c/-lc flag': 'команда оболочки через параметр -c/-lc',
    'command parser limit or malformed executable payload': 'команда слишком сложная или содержит некорректный исполняемый код',
    'execute_code script execution. The script can spawn subprocesses or mutate files without passing through terminal command approval; approval is one-shot for this run.': 'Запуск скрипта execute_code. Он может запускать программы и изменять файлы без отдельного одобрения каждой команды. Разрешение действует только на этот запуск.',
}


def approval_description_for_display(description: str) -> str:
    """Localize built-in reasons, preserving plugin/user text and machine state."""
    if get_language() != "ru" or not description:
        return description
    translated = _RUSSIAN_REASONS.get(description)
    if translated is not None:
        return translated
    if "; " in description:
        return "; ".join(approval_description_for_display(part) for part in description.split("; "))
    prefix = "arbitrary program execution via "
    if description.startswith(prefix):
        return "запуск произвольной программы через " + description[len(prefix):]
    prefix = "Plugin requires approval for "
    if description.startswith(prefix):
        return "Плагину нужно разрешение на " + description[len(prefix):]
    return description


def approval_data_for_display(data: dict) -> dict:
    """Copy an approval for rendering without modifying queued requests."""
    result = dict(data)
    if isinstance(result.get("description"), str):
        result["description"] = approval_description_for_display(result["description"])
    return result
