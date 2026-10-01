import { describe, expect, it } from "vitest";
import { presentOAuthProvider } from "./oauth-presentation";
import type { OAuthProvider } from "./api";

describe("Команды подключения", () => {
  it.each(["openai-codex", "qwen-oauth", "minimax-oauth", "xai-oauth", "copilot-acp"])("исправляет команду %s и сохраняет аргументы", (id) => {
    const provider = { id, name: id, cli_command: `hermes auth add ${id}` } as OAuthProvider;
    expect(presentOAuthProvider(provider).cli_command).toBe(`korra auth add ${id}`);
    expect(provider.cli_command).toBe(`hermes auth add ${id}`);
  });
  it("не переписывает произвольные команды и неизвестные имена", () => {
    const provider = { id: "custom", name: "Мой провайдер", cli_command: "custom-auth --name hermes" } as OAuthProvider;
    expect(presentOAuthProvider(provider)).toEqual(provider);
  });
});


it("объясняет подписку Claude Max без требования дополнительного баланса", () => {
  expect(presentOAuthProvider({ id: "claude-code", name: "old", cli_command: "hermes auth" } as OAuthProvider).name).toBe("Claude Code — подписка Claude Max");
});
