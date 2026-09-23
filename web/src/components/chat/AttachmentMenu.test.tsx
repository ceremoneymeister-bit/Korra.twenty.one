// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AttachmentMenu } from "./AttachmentMenu";

let host: HTMLDivElement;
let root: Root;
beforeEach(() => { host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

async function open() {
  const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="Прикрепить файл"]')!;
  await act(async () => { trigger.focus(); trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
  return document.querySelector('[role="menu"]');
}

it("opens a compact menu with the device picker and the originals toggle chosen before upload", async () => {
  const onPickFiles = vi.fn();
  const onOriginalsChange = vi.fn();
  await act(async () => root.render(<AttachmentMenu disabled={false} onPickFiles={onPickFiles} originals={false} onOriginalsChange={onOriginalsChange} />));
  expect(document.querySelector('[role="menu"]')).toBeNull();
  const menu = await open();
  expect(menu).not.toBeNull();
  const toggle = menu!.querySelector('[role="menuitemcheckbox"]')!;
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  expect(toggle.textContent).toContain("Отправлять оригиналы фото");
  expect(toggle.textContent).toContain("Выбор — до загрузки");
  await act(async () => { toggle.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
  expect(onOriginalsChange).toHaveBeenCalledWith(true);
  // Меню не закрылось: состояние переключателя видно после нажатия.
  expect(document.querySelector('[role="menu"]')).not.toBeNull();
  const pick = menu!.querySelector('[role="menuitem"]')!;
  expect(pick.textContent).toContain("Загрузить с устройства");
  await act(async () => { pick.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); });
  expect(onPickFiles).toHaveBeenCalledOnce();
});

it("shows the enabled state and stays disabled with the composer", async () => {
  await act(async () => root.render(<AttachmentMenu disabled={false} onPickFiles={vi.fn()} originals onOriginalsChange={vi.fn()} />));
  const menu = await open();
  const toggle = menu!.querySelector('[role="menuitemcheckbox"]')!;
  expect(toggle.getAttribute("aria-checked")).toBe("true");
  expect(toggle.textContent).toContain("Без сжатия");
  await act(async () => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  await act(async () => root.render(<AttachmentMenu disabled onPickFiles={vi.fn()} originals onOriginalsChange={vi.fn()} />));
  expect(host.querySelector<HTMLButtonElement>('button[aria-label="Прикрепить файл"]')!.disabled).toBe(true);
});

it("opens existing files separately from the device file chooser", async () => {
  const onPickFiles = vi.fn(), onPickWorkspace = vi.fn();
  await act(async () => root.render(<AttachmentMenu disabled={false} onPickFiles={onPickFiles} onPickWorkspace={onPickWorkspace} originals={false} onOriginalsChange={vi.fn()} />));
  const menu = await open();
  const item = Array.from(menu!.querySelectorAll('[role="menuitem"]')).find(node => node.textContent?.includes("Выбрать из файлов Korra"))!;
  await act(async () => item.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(onPickWorkspace).toHaveBeenCalledOnce();
  expect(onPickFiles).not.toHaveBeenCalled();
});

it("does not move focus back to the menu trigger while the device chooser is opening", async () => {
  const onPickFiles = vi.fn();
  await act(async () => root.render(<AttachmentMenu disabled={false} onPickFiles={onPickFiles} originals={false} onOriginalsChange={vi.fn()} />));
  const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="Прикрепить файл"]')!;
  const menu = await open();
  const item = Array.from(menu!.querySelectorAll('[role="menuitem"]'))
    .find(node => node.textContent?.includes("Загрузить с устройства"))!;
  await act(async () => item.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(onPickFiles).toHaveBeenCalledOnce();
  expect(document.activeElement).not.toBe(trigger);
});
