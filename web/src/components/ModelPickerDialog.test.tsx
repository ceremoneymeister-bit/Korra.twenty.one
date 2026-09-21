// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/i18n";
import { DeleteConfirmDialog } from "./DeleteConfirmDialog";
import { ModelPickerDialog } from "./ModelPickerDialog";

let container: HTMLDivElement;
let root: Root;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<I18nProvider>{ui}</I18nProvider>));
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function button(name: string) {
  return Array.from(document.body.querySelectorAll("button")).find(
    (item) => item.textContent?.trim() === name,
  ) as HTMLButtonElement | undefined;
}

function buttonContaining(name: string) {
  return Array.from(document.body.querySelectorAll("button")).find(
    (item) => item.textContent?.includes(name),
  ) as HTMLButtonElement | undefined;
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.setItem("hermes-dashboard-lang", "ru");
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("ModelPickerDialog — продуктовый выбор модели", () => {
  it("показывает русские имена поставщиков и сохраняет осознанный выбор", async () => {
    const onApply = vi.fn().mockResolvedValue({ confirm_required: false });
    const onClose = vi.fn();
    await render(
      <ModelPickerDialog
        alwaysGlobal
        loader={async () => ({
          provider: "openai-codex",
          model: "gpt-5.6-sol",
          providers: [
            {
              name: "ChatGPT or Codex Subscription",
              slug: "openai-codex",
              models: ["gpt-5.6-sol"],
              is_current: true,
            },
            {
              name: "Anthropic",
              slug: "anthropic",
              models: ["claude-opus-4.7"],
            },
          ],
        })}
        onApply={onApply}
        onClose={onClose}
        title="Выбрать основную модель"
      />,
    );
    await flush();

    expect(document.body.textContent).toContain("Подписка ChatGPT / Codex");
    expect(document.body.textContent).toContain("Сейчас выбрана");
    expect(document.body.textContent).toContain("Выбор будет действовать в новых чатах");

    await act(async () => buttonContaining("Anthropic (Claude)")?.click());
    await act(async () => buttonContaining("claude-opus-4.7")?.click());
    await act(async () => button("Выбрать модель")?.click());
    await flush();

    expect(onApply).toHaveBeenCalledWith({
      confirmExpensiveModel: false,
      provider: "anthropic",
      model: "claude-opus-4.7",
      persistGlobal: true,
    });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("объясняет пустого поставщика как настройку, а не как ноль моделей", async () => {
    await render(
      <ModelPickerDialog
        alwaysGlobal
        loader={async () => ({
          providers: [{ name: "OpenRouter", slug: "openrouter", models: [], total_models: 0 }],
        })}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    await flush();

    expect(document.body.textContent).toContain("нужно подключить");
    expect(document.body.textContent).toContain("У этого поставщика пока нет доступных моделей");
  });
});

describe("DeleteConfirmDialog — безопасное удаление", () => {
  it("сначала фокусирует отмену и точно называет сохранённые данные", async () => {
    const onCancel = vi.fn();
    await render(
      <DeleteConfirmDialog
        open
        loading={false}
        onCancel={onCancel}
        onConfirm={vi.fn()}
        title="Удалить чат?"
        description="Чат исчезнет. Память агента и файлы останутся."
      />,
    );

    expect(document.activeElement?.textContent).toBe("Отмена");
    expect(document.body.textContent).toContain("Память агента и файлы останутся");

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
