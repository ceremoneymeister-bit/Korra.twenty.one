# How the agent learns (0.21.16)

Two places to keep knowledge, and they are different:

| | Memory | Skill |
|---|---|---|
| What | Short facts: who the owner is, preferences, environment, lessons | A repeatable way of doing a task: steps, pitfalls, checks |
| Tool | `memory` (add / replace / remove; `operations` for a batch) | `skill_manage` (create, patch, edit, delete, ...) |
| Where | `MEMORY.md` (target `memory`), `USER.md` (target `user`) | `$HERMES_HOME/skills/<category>/<name>/SKILL.md` |
| Limit | `memory.memory_char_limit` 2200, `memory.user_char_limit` 1375 chars | description ≤ 60 chars when a skill is created (hard max 1024) |
| Loaded | Into every new session | Index (name + 60-char description) every session, body on demand |

Rule of thumb: a fact goes to memory; "how I did it and would do it again" goes
to a skill. When memory is full, `replace`/`remove` old entries instead of
appending. A skill description says **when to use it** in one short sentence
(the index cuts it at 60 chars). Never store secrets in either.

`memory.write_approval` (default false): when true, writes wait for the owner —
`/memory pending|approve|reject`. External memory backends:
`korra memory setup|status|off`; built-in memory always works,
`korra memory reset` clears MEMORY.md and USER.md.

### What learns on its own (background review)
A copy of the agent reviews a short excerpt of the chat in the background
after the answer, only on an explicit signal (no counters by default):
- **"Remember" / rule for the future** ("запомни", "всегда", "впредь",
  "в следующий раз", "больше не…", "remember", "from now on") — one review.
- **Direct correction** of the agent's result ("не так", "неправильно",
  "надо было…", "that's not right") — one review.
- Long tool work alone is not a signal. One review is one short call over
  the excerpt around the event (about 6 iterations), never the whole chat.
  After two empty corrections in a row, corrections pause until a "remember"
  request or `/refine`. Full memory (no room for an entry) never calls the model.
- If the owner set `memory.nudge_interval` or `skills.creation_nudge_interval`
  explicitly, that part keeps the old counter: every `memory.nudge_interval`
  user turns (10 by default) / after `skills.creation_nudge_interval` tool
  iterations (10 by default) without a `skill_manage` call.
- `auxiliary.background_review.enabled` (default true) turns off only the
  automatic start. Cron jobs never start it. Chat notices:
  `display.memory_notifications` ("on").
- Plugins may veto an event review with the `pre_background_review` hook
  (`{"skip": true, "reason": "..."}`); a failing plugin never blocks learning.

### On request
- `/learn <folder | URL | notes>` — build a skill from materials or from this chat.
- `/refine [topic]` — review this chat now and save lessons to memory/skills;
  works even with automatic review off. Both exist in the CLI, in messengers
  and in the web chat. In the web chat `/refine` answers with a status line;
  the result lands in memory/skills, not in the chat.
- `/journey` (CLI/TUI) or `korra journey list|delete|edit` — what was learned,
  with delete/edit.
- A skill added from outside the gateway (cabinet, CLI, copied files) is in the
  index of the **next new chat**; `/reload-skills` forces a rescan. A chat that
  is already open keeps its old index.

### Curator (skill upkeep)
On by default (`curator.enabled`), runs every 7 days when idle ≥ 2 h
(`interval_hours` 168, `min_idle_hours` 2). Unused skills become stale after
30 days and are **archived** after 90 — never deleted; `korra curator restore NAME`
brings one back. Bundled skills are not archived by default
(`prune_builtins: false`); an owner may turn it on. Hub-installed skills never are. `pin` exempts a skill.
Merging overlapping skills is opt-in (`curator.consolidate: true`).
CLI: `korra curator status|usage|run|pause|resume|pin|unpin|adopt|restore|list-archived|archive|prune|backup|rollback|ledger`.

### When something is not learned
`korra curator status`, `korra journey list`, `/memory pending`, `korra logs`
(background review logs `trigger=… result=none|memory|skill cost_status=…`, and `skipped … reason=memory_full|paused_after_empty_reviews|plugin: …`), `korra skills list`.
