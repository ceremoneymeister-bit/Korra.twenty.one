import { describe, expect, it } from "vitest";
import { ChatHistoryCache, chatHistoryKey, type CachedChatHistory } from "./chat-history-cache";

function history(text = "hello"): CachedChatHistory {
  return { messages: [{ id: "1", role: "user", content: text, timestamp: 1 }], history: { cursor: 1, hasOlder: true } };
}

describe("K21-207 bounded tab memory", () => {
  it("evicts the least recently visited conversation as a whole", () => {
    const cache = new ChatHistoryCache(2);
    const a = history();
    cache.set("a", a);
    cache.set("b", history());
    expect(cache.get("a")).toBe(a);
    cache.set("c", history());
    expect(cache.get("b")).toBeUndefined();
    expect(cache.get("a")).toBe(a);
    expect(cache.get("c")).toBeDefined();
  });
  it("bounds serialized memory as well as conversation count and rejects an oversized entry", () => {
    const value = history();
    const size = JSON.stringify(value).length * 2;
    const cache = new ChatHistoryCache(8, size * 2);
    cache.set("a", value);
    cache.set("b", history());
    cache.set("c", history());
    expect(cache.get("a")).toBeUndefined();
    cache.set("c", history("x".repeat(size * 2)));
    expect(cache.get("c")).toBeUndefined();
    expect(cache.get("b")).toBeDefined();
    cache.clear();
    expect(cache.get("b")).toBeUndefined();
  });
  it("isolates the server base path, profile, and session without delimiter collisions", () => {
    const cache = new ChatHistoryCache();
    const key = chatHistoryKey("/agent/a", "owner", "s:1");
    const value = history();
    cache.set(key, value);
    expect(cache.get(chatHistoryKey("/agent/b", "owner", "s:1"))).toBeUndefined();
    expect(cache.get(chatHistoryKey("/agent/a", "neighbor", "s:1"))).toBeUndefined();
    expect(cache.get(chatHistoryKey("/agent/a", "owner:s", "1"))).toBeUndefined();
    expect(cache.get(key)).toBe(value);
  });
});
