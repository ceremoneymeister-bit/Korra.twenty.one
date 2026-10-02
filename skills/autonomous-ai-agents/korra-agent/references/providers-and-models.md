# Models and subscriptions

Korra 21 uses models **only through subscriptions**: ChatGPT/Codex
(`openai-codex`) and Claude Max (`anthropic`). Paid API keys and paid fallback
are not used. A free ChatGPT/Claude account is not enough — Claude needs Claude Max.

### Connecting
- **Web cabinet (client mode):** «Настройки» → «Ключи и доступы» → card of the
  subscription → sign in (OAuth). The model is chosen in «Настройки» → «Модель»
  (and per agent on its page).
- **CLI:** `korra auth add openai-codex` / `korra auth add anthropic`,
  then `korra model` (picker) or `/model <name> [--global]` in a chat.
- **Check:** `korra auth status`, `korra auth list`, `korra status`.
  Several credentials of one provider form a pool and rotate automatically.

Connections belong to the platform, not to one agent: every profile uses the
same connected subscription unless its own config says otherwise.
The client cabinet hides the raw provider/key pages — if a client asks for
another provider or an API key, say that Korra 21 works on subscriptions and
suggest the closest of the two.

### Model aliases
`/model <alias>` works in the CLI and every chat. User aliases are checked
before the built-in ones (`sonnet`, `opus`, `haiku`, `claude`, `gpt5`, `codex`, …):

```yaml
model_aliases:
  fav:
    model: claude-sonnet-4.6
    provider: anthropic
```
Set with `korra config set model.aliases.fav anthropic/claude-sonnet-4.6`.
`/model fav` is session-scoped; add `--global` to make it the default.

Source of truth: `korra model --help`, `korra auth --help`, `korra_cli/auth.py`.
