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
A copy of the agent reviews the conversation in the background after the answer:
- **Memory:** every `memory.nudge_interval` user turns (10).
- **Skills:** after `skills.creation_nudge_interval` tool iterations (10) with
  no `skill_manage` call in between.
- `auxiliary.background_review.enabled` (default true) turns off only the
  automatic start. Cron jobs never start it. Chat notices:
  `display.memory_notifications` ("on").

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
brings one back. With `prune_builtins` (true) bundled skills can be archived
too; skills installed from the hub never are. `pin` exempts a skill.
Merging overlapping skills is opt-in (`curator.consolidate: true`).
CLI: `korra curator status|usage|run|pause|resume|pin|unpin|adopt|restore|list-archived|archive|prune|backup|rollback|ledger`.

### When something is not learned
`korra curator status`, `korra journey list`, `/memory pending`, `korra logs`
(background review logs `result=memory|skill`), `korra skills list`.
