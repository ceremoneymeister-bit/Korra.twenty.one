# Managed external effects

Korra 0.21.10 separates ordinary autonomous work from two effects that need an
exact owner decision: sending a message to a third party and paying money. The
decision is bound to one immutable payload and cannot become a session-wide or
permanent grant.

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
