# Managed external effects

Ordinary work and owner-configured automations run autonomously. One-off
third-party messages and payments require an exact owner decision, bound to
one immutable payload rather than a session-wide grant.

For an automation, the owner confirms recipients or a changing source once
at setup. An explicit choice in chat or the cabinet already counts. In chat,
`cronjob(..., recipients_confirmed=true)` records that choice in the existing
job fields; this is accepted only in a live owner turn. If the agent proposes
recipients itself, the existing `automation_recipients` card asks the owner.
Use `deliver` for fixed delivery targets and `audience` for a source such as
"clients with birthdays today, from Bitrix".

Inside that job, `send_message` and `korra send` send to confirmed recipients
without per-message decisions and still mask secrets. A new recipient outside
the fixed list pauses the job and creates one recipient card shared across
attempts and texts. Approval permits future sends, without sending the held
text itself. A denial keeps the job paused. An unrelated owner pause is
preserved. Jobs belonging to non-owners and one-off sends retain their guards.

Source membership is still selected by the agent under the confirmed
`audience` instruction; there is no independently verified source snapshot.
The agent must report successful send results separately from failed/unknown
outcomes. This is not a durable, system-generated receipt summary. Direct
Telethon or other custom senders must be connected to a managed transport;
setting `audience` alone does not mediate their network calls.

## Covered routes

The durable Decision Center contract covers:

- `send_message`, including built-in transports and plugin handlers;
- cron result and error delivery;
- Yuanbao direct messages.

For an outbound message the payload includes channel, account, recipient,
text and attachments. Attachments are copied into a private content-addressed
spool before the decision is shown. An edited payload needs a new decision.
After an unknown transport outcome Korra does not replay the effect blindly.

A reply to the owner in the current chat is not a third-party send. An explicit
owner-operated `korra send` command is also an owner action, not an agent grant.

Payment has the same durable recipient/amount/currency decision shape, but
0.21.10 has no safe payment executor. Approval therefore performs no payment
and fails closed.

## Deliberate boundary

This release does not claim universal effect mediation. Arbitrary commands in
`terminal` or `execute_code`, authenticated browser sessions, external MCP
write tools, and credential-bearing integration CLIs such as Google Workspace
or Himalaya can act outside the managed routes. Prompt instructions, command
names and regular expressions are not treated as a security boundary.

Existing tools and user-defined deny rules remain available and unchanged.
Operators must not describe a direct route as protected by the Decision Center.
A future host-side capability broker will move effect-capable credentials out
of general execution processes and connect supported adapters to the exact
decision ledger.
