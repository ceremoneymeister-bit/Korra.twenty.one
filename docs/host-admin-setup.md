# Отзывной root контура: операторский SSH/CLI

Штатный путь — docs/client-deploy/host-bootstrap.py на сервере клиента.
Его выполняет владелец host-root по уже проверенному SSH-соединению.
Web-installer отсутствует. Bootstrap включает admin mode: sudo без пароля
в контейнере и отдельный ключ этого контура к хосту. Явный --no-admin
оставляет контейнер без sudo и не выдаёт managed host grant.

Полномочия полные: sudo -n id -u и ssh host id -u возвращают 0.
Нет forced command, docker.sock, bind корня хоста или fleet private key.
Сохраняются единственный DATA bind и host networking нативного up.sh.

## Bootstrap

Поддерживаются Linux x86_64, Ubuntu/Debian с systemd. Control kit принадлежит
root, расположен вне DATA и не доступен агенту на запись. До изменяющих команд
проверяются root, ОС/архитектура, immutable digest, пути/владелец DATA, диск
и отдельные panel/API/admin/operator-SSH порты. Root account должен допускать
public-key login без PAM: locked/unknown account останавливает bootstrap.
Скрипт не меняет глобальную политику root account. Настройте её через
операторскую консоль; password authentication отдельного listener выключена.

Разместите рядом up.sh, update.sh, updater.py, host-bootstrap.py, backup.sh,
dependencies.lock.json и env.template. Существующий клиентский up.sh сохраняет
NAME/DATA/порты/timezone этой установки. Пример выделенного контура:

    python3 /opt/korra/host-bootstrap.py bootstrap \
      --home /opt/korra --data /opt/korra/data --name korra \
      --image ghcr.io/ceremoneymeister-bit/korra.twenty.one@sha256:<64-hex> \
      --panel-port 9119 --api-port 8650 --admin-port 22021 --ssh-port 22 --plan

Plan только читает preflight. Уберите --plan для выполнения; для opt-out
добавьте --no-admin. Второй контур получает свои DATA/name и свободные порты.
UUID создаёт нативный korra_cli.install_identity; новый формат identity не вводится.

Host provisioning устанавливает отсутствующие пакеты через apt: OpenSSH, UFW,
fail2ban и Docker при его отсутствии. Docker CE не заменяется docker.io.
Активный swap сохраняется; без него создаются managed swap 4 GiB и запись
fstab. UFW сначала разрешает указанный операторский SSH-порт, затем включает
deny incoming/allow outgoing. Fail2ban защищает внешний SSH через systemd
backend. Эти компоненты принадлежат хосту, а не image.

Далее загружается immutable image, проверяется linux/amd64 и запускается
нативный up.sh с явными UID/GID/admin mode. Существующий контейнер проверяется
нативным updater: имя, bind, команда, сеть, порты, image, UID/GID/admin mode
должны совпасть. Bootstrap не пересоздаёт его. Смена образа/режима — отдельная
операция updater. Повторные успешные команды сохраняют identity/private key
и не дублируют SSH Include/fstab.

## Per-contour OpenSSH

На каждый install_id создаются DATA/.ssh/id_ed25519_host (UID/GID движка,
0600), korra-host.conf и korra-host-known_hosts. Каталог .ssh имеет 0700.
Alias host/korra-host использует строгий pin без DNS trust, UpdateHostKeys,
SSH agent или TOFU. Pin берётся из локального host Ed25519 key на уже
доверенном хосте; сеть не становится источником доверия.

Root-managed /etc/korra-host-admin/<install_id>/ содержит inventory.json,
authorized_keys и sshd_config. Inventory хранит DATA/name/port, публичные hashes
и состояние grant. Отдельный korra-host-admin-<install_id>.service слушает
только 127.0.0.1:<admin-port> и использует KillMode=control-group.
Глобальный /root/.ssh/authorized_keys и основной sshd не меняются.

Private key не попадает в образ или root inventory. DATA publication использует
открытые directory descriptors и no-follow, чтобы подмена ссылки агентом не
перенаправила host-root writes. Include добавляется перед прежними SSH
настройками; чужие записи сохраняются. Verify идёт от UID движка с точным
отдельным config; обычное использование — ssh host id -u.

UsePAM=no сохраняет SSH-процессы в cgroup unit, без PAM session migration.
Root account проверяется до grant. Состояние active записывается после
проверок полного root; промежуточное activating не считается успехом.

## Rotate, revoke и opt-out

Для rotate используйте те же home/data/name/image/порты/UID/GID:

    python3 /opt/korra/host-bootstrap.py rotate \
      --home /opt/korra --data /opt/korra/data --name korra \
      --image ghcr.io/ceremoneymeister-bit/korra.twenty.one@sha256:<64-hex> \
      --admin-port 22021

Сначала очищается авторизация старого ключа и останавливается только этот
unit с обычными сессиями. Затем создаётся новый ключ, проверяется локальный
host pin, запускается listener и подтверждается root. Старый ControlMaster
не переносится через ротацию. Это короткое окно обслуживания.

Revoke не зависит от доступности контейнера или DATA:

    python3 /opt/korra/host-bootstrap.py revoke \
      --home /opt/korra --data /opt/korra/data --name korra

По единственной записи root inventory ставится revoking, удаляется grant,
проверяется фактический KillMode=control-group, останавливается и отключается
соответствующий unit. Это закрывает существующие ControlMaster/обычные SSH
sessions. При ошибке stop/проверки остаётся revoking и ненулевой exit:
удаление authorized_keys само по себе не выдаётся за полный отзыв.
Успешный повторный revoke идемпотентен.

Bootstrap --no-admin отзывает имеющийся managed host grant. Для снятия
container-root работающего admin-контейнера нужно контролируемое пересоздание
с AGENT_SUDO=0; restart не очищает слой. Updater фиксирует UID/GID/AGENT_SUDO
в receipt, переносит их в launcher и проверяет после запуска. Явный forward
AGENT_SUDO override меняет режим; rollback возвращает исходный.
Изменение UID/GID требует отдельной подготовки данных.

Изменённые host pin, private key, authorized_keys, server config,
name/data/port или identity не чинятся молча через grant/bootstrap:
нужны проверка и explicit rotate. Неизвестный private key без inventory
не усыновляется. Старые глобальные grants/aliases из прежнего runbook
требуют отдельной адресной миграции/отзыва; CLI их не угадывает.

## Restore и граница отзыва

Переносимый import исключает install_id и .ssh: новый контур получает новую
identity и ключ. Explicit same-host restore требует root inventory/unit той же
установки. Без них автоматическое принятие restored private key запрещено.
Root control kit/inventory сохраняется оператором отдельно от переносимого backup.

Полный host-root охватывает весь хост, включая соседние контуры. После
компрометации root мог создать иной ключ, unit, cron или вывести процесс из
нашего cgroup. Revoke закрывает managed grant и обычные сессии; это не обещание
немедленно удалить persistence. Инцидент требует отдельной проверки,
восстановления доверенного хоста и ротации секретов.

Native policy: systemd.kill(5), KillMode=control-group; sshd_config(5),
AuthorizedKeysFile/UsePAM/PermitRootLogin. Code acceptance использует реальные
эпhemeral OpenSSH keys/sshd/clients с synthetic filesystem и подменёнными
Docker/systemctl/package/firewall boundaries. Production provisioning,
root account policy и live services во время code audit не изменяются.
