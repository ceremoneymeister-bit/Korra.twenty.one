// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CommandApprovalCard, type CommandApprovalCardProps } from "./CommandApprovalCard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const NOW = Date.parse("2026-10-19T05:30:00Z");
const DAY = 86_400;
let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  window.__KORRA_OWNER_TIMEZONE__ = "Asia/Novosibirsk";
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  delete window.__KORRA_OWNER_TIMEZONE__;
  vi.useRealTimers();
});

async function render(timestamp: number | undefined, extra: Partial<CommandApprovalCardProps> = {}) {
  const props = { timestamp, decisionKind: "outbound_message" as const, choices: ["once", "deny"] as const };
  await act(async () => root.render(<CommandApprovalCard {...props} choices={[...props.choices]} onDecide={vi.fn()} {...extra} />));
}

it("свежее решение показывает сегодня и время владельца, без предупреждения", async () => {
  await render(Date.parse("2026-10-19T05:16:00Z") / 1000);
  expect(host.querySelector("time")?.textContent).toBe("сегодня в 12:16");
  expect(host.querySelector("time")?.dateTime).toBe("2026-10-19T05:16:00.000Z");
  expect(host.textContent).not.toContain("мог устареть");
});

it("старый текст предупреждает до кнопок, отказ и отправка остаются одним кликом", async () => {
  const onDecide = vi.fn();
  await render(Date.parse("2026-10-03T11:49:00Z") / 1000, { onDecide });
  expect(host.querySelector("time")?.textContent).toBe("03.10.2026 в 18:49");
  expect(host.textContent).toContain("Текст создан 03.10.2026 в 18:49 и мог устареть. Проверьте его перед отправкой.");
  expect(host.textContent!.indexOf("мог устареть")).toBeLessThan(host.textContent!.indexOf("Отправить"));
  const buttons = [...host.querySelectorAll("button")];
  expect(buttons).toHaveLength(2);
  for (const button of buttons) await act(async () => button.click());
  expect(onDecide.mock.calls).toEqual([["once"], ["deny"]]);
});

it.each([[-1, false], [0, false], [0.001, true]])("граница суток: превышение %s секунд", async (over, warning) => {
  await render(NOW / 1000 - DAY - over);
  expect(host.textContent!.includes("мог устареть")).toBe(warning);
});

it("открытая карточка получает предупреждение при переходе границы суток", async () => {
  await render(NOW / 1000 - DAY);
  expect(host.textContent).not.toContain("мог устареть");
  await act(async () => vi.advanceTimersByTime(1));
  expect(host.textContent).toContain("мог устареть");
});

it("сегодня определяется по дню владельца, даже если UTC ещё вчера", async () => {
  vi.setSystemTime(Date.parse("2026-10-18T18:30:00Z"));
  await render(Date.parse("2026-10-18T17:16:00Z") / 1000);
  expect(host.querySelector("time")?.textContent).toBe("сегодня в 00:16");
  await render(Date.parse("2026-10-18T16:59:00Z") / 1000);
  expect(host.querySelector("time")?.textContent).toBe("18.10.2026 в 23:59");
});

it("после сна вкладки focus обновляет день и предупреждение", async () => {
  await render(NOW / 1000);
  expect(host.querySelector("time")?.textContent).toBe("сегодня в 12:30");
  vi.setSystemTime(NOW + 2 * DAY * 1000);
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(host.querySelector("time")?.textContent).toBe("19.10.2026 в 12:30");
  expect(host.textContent).toContain("мог устареть");
});

it.each([undefined, NaN, Infinity, 1e20])("не выдумывает дату, если timestamp непригоден: %s", async timestamp => {
  await render(timestamp);
  expect(host.querySelector("time")).toBeNull();
  expect(host.textContent).not.toContain("мог устареть");
});

it.each(["automation_recipients", "payment"] as const)("предупреждение для %s относится к содержимому решения", async decisionKind => {
  await render(NOW / 1000 - 2 * DAY, { decisionKind });
  expect(host.textContent).toContain(decisionKind === "payment" ? "Проверьте получателя, сумму и валюту" : "Проверьте получателей автоматизации");
  expect(host.textContent).not.toContain("Текст создан");
});

it.each([{ decision: "once" as const }, { expired: true }, { decisionKind: "kanban_question" as const }])("не просит отправить старый текст в завершённом или другом решении %j", async extra => {
  await render(NOW / 1000 - 2 * DAY, extra);
  expect(host.querySelector("time")).not.toBeNull();
  expect(host.textContent).not.toContain("мог устареть");
});
