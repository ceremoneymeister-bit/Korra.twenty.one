# Installing Korra 21

A runbook for an AI agent installing Korra on the owner's behalf, and for a
human doing the same by hand. Every step is a command plus a check; a step
without its check is not done. The Russian original is [INSTALL.md](INSTALL.md).

Image: `ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest`, linux/amd64
only. Do not build the image locally: the registry image is the tested one.

Pick a scenario:

- **A. Linux server with root** (Ubuntu/Debian, 4 vCPU, 8 GB RAM, 60 GB disk):
  full contour with firewall, one-command update and rollback, backup. Recommended.
- **B. Small VPS without host hardening** (2 vCPU, 4 GB RAM): same contour, no
  firewall setup and no host-admin key.
- **C. Laptop or workstation** with Docker Desktop (macOS, Windows, Linux): a
  local agent with the panel on `localhost`.

Agent: run the commands in order, compare every check with its expectation,
and stop to show the owner the output on any mismatch. Never invent keys or
substitute someone else's proxy address.

---

## A. Linux server with root

### A1. From the owner

- root ssh access and the server IP;
- a model provider key, or the intent to enter it in the panel later;
- optionally a Telegram bot token from @BotFather and the owner's numeric Telegram ID.

### A2. Docker

```bash
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
docker --version && docker info --format '{{.ServerVersion}}'
uname -m        # expect x86_64
```

### A3. Directories and host kit

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

**Check:**

```bash
stat -c '%u:%g %a %n' /opt/korra/data      # 10000:10000 750
python3 /opt/korra/updater.py --capabilities
```

Ownership is set by a separate command and by number: the host has no `passwd`
entry for uid 10000, and on a recent Ubuntu `install -d -o 10000` answers
`invalid user: '10000'`. The check asks for numbers too (`%u:%g`); `%U:%G`
returns `UNKNOWN`. The name mapping lives inside the container by design.

### A4. Image pinned by digest

```bash
docker pull ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest
DIGEST=$(docker image inspect ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest \
  --format '{{index .RepoDigests 0}}' | cut -d@ -f2)
echo "ghcr.io/ceremoneymeister-bit/korra.twenty.one@${DIGEST}" > /opt/korra/IMAGE
cat /opt/korra/IMAGE
```

**Check:**

```bash
docker image inspect "$(cat /opt/korra/IMAGE)" \
  --format '{{.Id}} {{.Size}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
```

### A5. Keys (optional; the panel can take them later)

```bash
cp /opt/korra/env.template /opt/korra/data/.env
chown 10000:10000 /opt/korra/data/.env && chmod 600 /opt/korra/data/.env
${EDITOR:-nano} /opt/korra/data/.env      # provider key; Telegram if wanted
```

Only the provider key is required; leave `API_SERVER_KEY` empty, the contour
generates it.

### A6. Host preparation and first start

```bash
python3 /opt/korra/host-bootstrap.py bootstrap --plan --no-admin \
  --home /opt/korra --data /opt/korra/data --name korra \
  --image "$(cat /opt/korra/IMAGE)" --ssh-port 22

python3 /opt/korra/host-bootstrap.py bootstrap --no-admin \
  --home /opt/korra --data /opt/korra/data --name korra \
  --image "$(cat /opt/korra/IMAGE)" --ssh-port 22
```

`--no-admin` starts the contour without a host-admin key and without `sudo`
for the agent inside the container. Pass your real SSH port. The preflight
needs at least 4 CPUs and 7 GiB RAM; on a smaller machine use scenario B.

**Check:**

```bash
python3 /opt/korra/host-bootstrap.py verify --no-admin \
  --home /opt/korra --data /opt/korra/data --name korra \
  --image "$(cat /opt/korra/IMAGE)" --ssh-port 22
docker ps --filter name=korra --format '{{.Names}} {{.Status}} {{.Image}}'
curl -fsS http://127.0.0.1:9119/api/status | head -c 300
docker exec -u 10000 korra korra --version
docker exec -u 10000 korra korra doctor | tail -25
```

Expected: `verify` prints `{"verified": true}`, the container is `Up`,
`/api/status` answers JSON, `doctor` has no red lines (warnings about an
unconfigured provider are fine).

With `--no-admin` the check follows what the mode promises: the container runs
the pinned image with a single data volume, panel and API answer on the
loopback, there is no host-admin key and no `sudo` for the agent inside the
container. Without `--no-admin` the same command demands the opposite —
container root and a login with the pinned host-root key — so the mode flag
belongs in it.

### A7. Panel and provider

The panel listens on `127.0.0.1:9119` only. From the owner's machine:

```bash
ssh -N -L 9119:127.0.0.1:9119 root@<server-ip>
# then open http://127.0.0.1:9119
```

The first chat message without a provider gets an honest reply pointing to the
"Keys" section. Or from the terminal:

```bash
docker exec -u 10000 -it korra korra model
docker exec -u 10000 korra korra config get model
```

A ChatGPT/Codex subscription is connected with `korra auth add` inside the
container (browser OAuth on the owner's side). Providers:
`docs/client-deploy/PROVIDER.md` (Russian).

### A8. Telegram (optional)

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USERS` in `.env`, then
`docker restart korra`. Check: the owner gets a reply from the bot, a stranger
does not.

### A9. Update and rollback

```bash
docker pull ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest
NEW=ghcr.io/ceremoneymeister-bit/korra.twenty.one@$(docker image inspect \
  ghcr.io/ceremoneymeister-bit/korra.twenty.one:latest --format '{{index .RepoDigests 0}}' | cut -d@ -f2)
cd /opt/korra && NAME=korra DATA=/opt/korra/data PANEL_PORT=9119 API_PORT=8650 \
  ./update.sh --update "$NEW"
tail -3 /opt/korra/updates.log       # phase=complete status=succeeded
```

Rollback:

```bash
cd /opt/korra && NAME=korra DATA=/opt/korra/data PANEL_PORT=9119 API_PORT=8650 \
  ./update.sh --rollback <job-id from updates.log>
```

---

## B. Small VPS without host hardening

Steps A2–A5 are the same. Instead of `host-bootstrap.py`, start with the launcher:

```bash
cd /opt/korra && NAME=korra DATA=/opt/korra/data PANEL_PORT=9119 API_PORT=8650 \
  TIMEZONE=Europe/Moscow ./up.sh
```

`up.sh` runs the contour with `AGENT_SUDO=0`, host networking, the panel on
loopback, no `docker.sock` and no host-root mount. Firewall and swap stay with
the owner. Check as in A6; continue with A7–A9.

---

## C. Laptop or workstation (Docker Desktop)

No host networking here, so the panel is published on `127.0.0.1`. Data lives
in `~/.korra`.

```bash
mkdir -p ~/.korra
curl -fsSL https://raw.githubusercontent.com/ceremoneymeister-bit/Korra.twenty.one/main/docker-compose.windows.yml \
  -o docker-compose.korra.yml
docker compose -f docker-compose.korra.yml pull
docker compose -f docker-compose.korra.yml up -d
```

On macOS and Linux replace `${USERPROFILE}/.korra` with `${HOME}/.korra` in the file.

**Check:**

```bash
docker compose -f docker-compose.korra.yml ps
curl -fsS http://127.0.0.1:9119/api/status | head -c 200
```

Panel: `http://127.0.0.1:9119`. Provider and Telegram as in A7 and A8, container
name `korra`. Update: `docker compose -f docker-compose.korra.yml pull && docker compose -f docker-compose.korra.yml up -d`.

---

## Final checks (any scenario)

```bash
docker exec -u 10000 korra korra --version
docker exec -u 10000 korra korra doctor | tail -25
docker exec -u 10000 korra korra profile create test-agent   # a new tab appears in the panel
```

Report to the owner: version, panel address, data directory, how to update and
roll back, which keys are still missing. Never paste keys or tokens into the report.

## Troubleshooting

- `docker logs korra --tail 100` — the engine states the cause in plain text.
- `korra doctor` names what is missing; `korra doctor --fix` repairs what can
  be repaired without the owner's data.
- Port in use: set `PANEL_PORT`/`API_PORT` on the start command and use the
  same panel port in the SSH tunnel.
- Data directory owned by someone else: `chown -R 10000:10000 /opt/korra/data`.
