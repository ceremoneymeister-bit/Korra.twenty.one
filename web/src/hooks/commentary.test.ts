import { expect, it } from "vitest";
import { sessionMessagesToChat } from "./useChatStream";
import { parseSSEBlock } from "@/lib/sse-parser";

it("keeps public commentary separate from final and private reasoning after F5", () => {
  const messages = sessionMessagesToChat("s", [
    { id: 1, role: "user", content: "Проверь" },
    { id: 2, role: "assistant", content: "", display_commentary: ["Проверяю файл."],
      display_reasoning: "", reasoning_content: "PRIVATE_ANALYSIS", tool_calls: [
        { id: "call1", type: "function", function: { name: "read_file", arguments: "{}" } },
      ] },
    { id: 3, role: "assistant", content: "Готово.", display_commentary: ["Проверяю результат."] },
  ] as Parameters<typeof sessionMessagesToChat>[1]);
  expect(messages[1].content).toBe("Готово.");
  expect(messages[1].commentary).toEqual([
    { text: "Проверяю файл.", toolOffset: 0 },
    { text: "Проверяю результат.", toolOffset: 1 },
  ]);
  expect(messages[1].reasoning).toBeUndefined();
  expect(messages[1].turnComplete).toBe(true);
});

it("parses commentary without treating it as final delta", () => {
  expect(parseSSEBlock('event: hermes.assistant.commentary\ndata: {"text":"Проверяю.","already_streamed":false}'))
    .toEqual({ type: "commentary", data: { text: "Проверяю.", already_streamed: false } });
});
