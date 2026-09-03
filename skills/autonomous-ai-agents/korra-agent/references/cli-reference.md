# `korra` CLI Reference

Live sources when anything looks stale: `korra --help`,
`korra <command> --help`, and the command code in the installed build.

### Global Flags

```
korra [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
korra chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
korra setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
korra model                Interactive model/provider picker
korra fallback [add|remove|list]  Fallback provider chain
korra config [show|edit|get|set|unset|path|env-path|check|migrate]
korra login / logout       OAuth sign-in / clear stored auth
korra doctor [--fix]       Check dependencies and config
korra status [--all]       Component status
```

### Tools & Skills

```
korra tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

korra skills list|browse|search QUERY|inspect ID
korra skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
korra skills config        Enable/disable skills per platform
korra skills check|update|uninstall|publish PATH
korra skills tap add REPO  Add a GitHub repo as a skill source
korra bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
korra mcp add NAME (--url or --command) | remove | list | test NAME
korra mcp catalog | install NAME     Curated catalog install
korra mcp configure NAME             Toggle tool selection
korra mcp serve                      Run Korra as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
korra gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `korra photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Source of truth: `korra gateway --help` and the installed platform adapters.

### Sessions

```
korra sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
korra cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
korra webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
korra profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
korra profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
korra auth                 Interactive credential manager
korra auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
korra auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
korra desktop / gui        Native desktop app
korra dashboard            Web admin panel + embedded chat (--stop / --status)
korra proxy                OpenAI-compatible local proxy backed by an OAuth provider
korra portal               Quick setup / sign in via Nous Portal
korra kanban <verb>        Multi-agent work-queue board
korra project              Named multi-folder workspaces
korra skin list|use|set    Switch/tweak skins (see references/themes.md)
korra pets <verb>          Pet mascots (see references/petdex.md)
korra memory setup|status|off|reset   Memory provider
korra secrets bitwarden|onepassword   External secret stores
korra moa                  Mixture-of-Agents slots
korra hooks / security / backup / import / checkpoints / console
korra logs [-f] [errors]   View agent/error logs
korra send                 One-off message through a gateway platform
korra pairing / plugins / insights / journey / computer-use
korra acp                  ACP server (IDE integration)
korra completion bash|zsh|fish
korra update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `korra photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `korra config --help` · `korra config edit` · installed configuration code |
| Tools / toolsets | `korra tools --help` · `korra tools list` · `toolsets.py` |
| Skills catalog | `korra skills --help` · `korra skills browse` |
| Provider setup | `korra model --help` · installed provider plugins |
| Env variables | `korra config env-path` · installed configuration code |
| Gateway logs | `$HERMES_HOME/logs/gateway.log` (or `korra logs`) |
| Sessions | `korra sessions browse` (reads state.db) |
