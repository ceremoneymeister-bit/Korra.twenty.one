# Delegation, cron, kanban

Values below are the 0.21.16 defaults. Live check: `korra <command> --help`,
`korra config get <key>`. For memory, skills, curator and background review
see `references/learning.md`.

### Delegation (`delegate_task`)

A subagent with its own context and terminal.

- **Single:** `delegate_task(goal, context)`. **Batch:** `tasks=[{goal, ...}, ...]`
  runs in parallel, capped by `delegation.max_concurrent_children` (default **10**).
- **Background:** `background=true` returns a handle; the result re-enters the chat
  as a new turn. Not durable — if the parent process exits the child is lost.
  For work that must outlive the process use `cronjob`.
- **Roles:** `leaf` (default) cannot re-delegate; `orchestrator` can, up to
  `delegation.max_spawn_depth` (default 1, `orchestrator_enabled` true).
- **Limits:** `max_iterations` 250, `child_timeout_seconds` 0 (none),
  `max_summary_chars` 24000. Children inherit the parent's model unless
  `delegation.model`/`provider` is set; they do not auto-approve dangerous
  commands (`subagent_auto_approve` false).
- A "capped at N" report is usually the model limiting itself: check the
  config value above before believing it.

### Cron (`cronjob` tool, «Задачи» in the cabinet, `korra cron`)

Actions: create, list, update, pause, resume, remove, run.

- **Schedules:** interval (`30m`, `every 2h`), one-off (`in 30m`, ISO time),
  `every monday 9am`, 5-field cron (`0 9 * * *`).
- **Kinds:** a normal job runs the agent with a prompt (optional `skills`,
  `model`, `workdir`, `context_from`); `reminder` sends exact text with no model;
  `script` with `no_agent=true` runs only a script.
- **Delivery (`deliver`):** `origin` (where it was created), `local`, `all`,
  a bot chat, or `platform:chat_id:thread`. A recipient chosen while the job
  runs has to be confirmed by the owner.
- **Limits:** a run is interrupted after `KORRA_CRON_TIMEOUT` seconds
  (default 600). Cron runs keep memory on but never start background review.
  `.tick.lock` prevents duplicate ticks across processes.
- **CLI:** `korra cron list|create|edit|pause|resume|run|remove|status|runs|incidents|notepad|doctor`.
  `/cron` works in the CLI only; in chats ask the agent to use the tool.

### Kanban (board for several agents)

Durable board; the cabinet shows it as «Канбан-доска», CLI `korra kanban <verb>`,
chat `/kanban`.

- The main chat gets the `kanban_*` tools when `kanban.chat_tools: "main"`
  (`kanban_show/list/create/link/comment/attach/unblock`, and for workers
  `complete/block/request_review/request_changes/heartbeat`).
- The dispatcher runs inside the gateway (`kanban.dispatch_in_gateway: true`,
  every `dispatch_interval_seconds` = 60): claims ready tasks and starts the
  assigned profile. After `failure_limit` (2) failed starts the task is blocked.
  With `review_dispatch` a finished task goes to review before it is done.
  `auto_subscribe_on_create` subscribes the creator to updates.
- The board is the hard boundary; a tenant is a soft namespace inside it.

Source of truth: `korra kanban --help`, `korra cron --help`, the `delegation`
and `kanban` sections of `config_defaults.py`.
