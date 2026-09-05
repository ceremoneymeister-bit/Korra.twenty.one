import type { OAuthProvider } from "./api";

const NAMES: Record<string, string> = {
  "openai-codex": "Подписка ChatGPT / Codex",
  "qwen-oauth": "Qwen — вход в аккаунт",
  "minimax-oauth": "MiniMax — вход в аккаунт",
  "xai-oauth": "Grok — подписка SuperGrok / Premium+",
  "copilot-acp": "GitHub Copilot",
  anthropic: "Anthropic — ключ API",
  "claude-code": "Claude Code — требуется дополнительный баланс",
};

/** Старые серверы возвращают прежнюю команду; исправляем и показ, и копирование. */
export function presentOAuthProvider(provider: OAuthProvider): OAuthProvider {
  return {
    ...provider,
    name: NAMES[provider.id] ?? provider.name,
    cli_command: provider.cli_command.replace(/^hermes(?=\s)/, "korra"),
  };
}
