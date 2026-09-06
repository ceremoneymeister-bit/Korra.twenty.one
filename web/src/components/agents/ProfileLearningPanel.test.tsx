// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { composeSoul } from "@/lib/agent-wizard";

const apiMocks = vi.hoisted(() => ({
  getProfileSoul: vi.fn(),
  updateProfileSoul: vi.fn(),
  getProfileMemory: vi.fn(),
  addProfileMemory: vi.fn(),
  replaceProfileMemory: vi.fn(),
  removeProfileMemory: vi.fn(),
  getProfileMaterials: vi.fn(),
  createProfileMaterial: vi.fn(),
  deleteProfileMaterial: vi.fn(),
  probeProfileChat: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const { probeProfileChat, ...apiOnly } = apiMocks;
  return { ...actual, api: { ...actual.api, ...apiOnly }, probeProfileChat };
});

import { materialFileProblem, materialProbePrompt } from "@/lib/agent-learning";
import ProfileLearningPanel from "./ProfileLearningPanel";

const ENGINE_DEFAULT_SOUL =
  "You are Korra. Be direct: match the length of your reply to the weight of the ask.";

const PROFILE = {
  name: "sekretar",
  path: "/opt/data/profiles/sekretar",
  is_default: false,
  model: "claude-sonnet-5",
  provider: "custom:dario",
  has_env: true,
  skill_count: 59,
  gateway_running: false,
  description: "Секретарь",
  description_auto: false,
  display_name: "Секретарь",
  distribution_name: null,
  distribution_version: null,
  distribution_source: null,
  has_alias: false,
};

const MEMORY = {
  memory: ["Минимальный заказ — 12 изделий.", "Срок изготовления — 10 рабочих дней."],
  user: ["Меня зовут Дмитрий."],
  limits: { memory: 2200, user: 1375 },
  used: { memory: 70, user: 19 },
  enabled: { memory: true, user: true },
};

const MATERIALS = [
  { name: "korra-material-price", title: "Прайс на мебель", kind: "text" as const },
  {
    name: "korra-material-rules",
    title: "Регламент",
    kind: "file" as const,
    filename: "reglament.pdf",
  },
];

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement;
let root: Root;
const onClose = vi.fn();
const onProfileChanged = vi.fn();

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{`${pathname}${search}`}</output>;
}

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function open(section?: "role" | "memory" | "materials" | "check", profile = PROFILE) {
  await render(
    <MemoryRouter initialEntries={["/profiles?agent=sekretar&edit=learning"]}>
      <ProfileLearningPanel
        profile={profile}
        section={section}
        onClose={onClose}
        onProfileChanged={onProfileChanged}
      />
      <LocationProbe />
    </MemoryRouter>,
  );
  await flush();
}

/** Кнопка по точному тексту; вкладки разделов (role=tab) не считаются. */
function findButton(text: string, scope: ParentNode = container) {
  return Array.from(scope.querySelectorAll("button")).find(
    (button) =>
      button.getAttribute("role") !== "tab" && button.textContent?.trim() === text,
  );
}

async function click(element: Element | undefined | null) {
  expect(element).toBeTruthy();
  await act(async () => {
    (element as HTMLElement).click();
  });
  await flush();
}

async function enterText(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  await act(async () => {
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

const field = <T extends HTMLElement>(selector: string) =>
  container.querySelector<T>(selector)!;
const location = () => container.querySelector('[data-testid="location"]')?.textContent;

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  apiMocks.getProfileSoul.mockResolvedValue({
    content: composeSoul("Секретарь", "Ведёшь дела."),
    exists: true,
  });
  apiMocks.updateProfileSoul.mockResolvedValue({ ok: true });
  apiMocks.getProfileMemory.mockResolvedValue(MEMORY);
  apiMocks.addProfileMemory.mockResolvedValue({ ok: true });
  apiMocks.replaceProfileMemory.mockResolvedValue({ ok: true });
  apiMocks.removeProfileMemory.mockResolvedValue({ ok: true });
  apiMocks.getProfileMaterials.mockResolvedValue({ materials: MATERIALS });
  apiMocks.createProfileMaterial.mockResolvedValue({ ok: true, name: "korra-material-new" });
  apiMocks.deleteProfileMaterial.mockResolvedValue({ ok: true });
  apiMocks.probeProfileChat.mockResolvedValue({
    ok: true,
    reply: "Минимальный заказ — 12 изделий, срок — 10 рабочих дней.",
    error: "",
    detail: "",
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.clearAllMocks();
});

describe("ProfileLearningPanel — роль и правила", () => {
  it("показывает разделы, имя агента и загружает роль", async () => {
    await open();
    expect(container.textContent).toContain("Обучение и настройки");
    expect(container.textContent).toContain("Секретарь");
    const tabs = Array.from(container.querySelectorAll('[role="tab"]')).map(
      (tab) => tab.textContent,
    );
    expect(tabs).toEqual([
      "Роль и правила",
      "Что важно помнить",
      "Материалы и инструкции",
      "Проверить вопросом",
    ]);
    expect(apiMocks.getProfileSoul).toHaveBeenCalledWith("sekretar");
    expect(field<HTMLTextAreaElement>("#learning-role").value).toContain(
      "Ты — Секретарь, агент в системе Korra.",
    );
    // Роль есть — предупреждения нет, сохранять нечего.
    expect(container.textContent).not.toContain("роль не задана");
    expect(findButton("Сохранить роль")?.disabled).toBe(true);
  });

  it("распознаёт дефолт движка и предлагает русский шаблон, сохраняет роль", async () => {
    apiMocks.getProfileSoul.mockResolvedValue({ content: ENGINE_DEFAULT_SOUL, exists: true });
    await open();

    expect(container.textContent).toContain("роль не задана");
    await click(findButton("Задать роль по-русски"));
    const textarea = field<HTMLTextAreaElement>("#learning-role");
    expect(textarea.value).toBe(composeSoul("Секретарь", ""));

    await enterText(textarea, composeSoul("Секретарь", "Ведёшь мои дела."));
    await click(findButton("Сохранить роль"));
    expect(apiMocks.updateProfileSoul).toHaveBeenCalledWith(
      "sekretar",
      composeSoul("Секретарь", "Ведёшь мои дела."),
    );
    expect(container.textContent).toContain("Сохранено. Изменения применятся в новом разговоре.");
    expect(onProfileChanged).toHaveBeenCalled();
  });

  it("при ошибке загрузки не даёт сохранить пустоту и умеет повторить", async () => {
    apiMocks.getProfileSoul.mockRejectedValueOnce(new Error("500: упало"));
    await open();

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "Не удалось загрузить роль",
    );
    expect(field<HTMLTextAreaElement>("#learning-role").disabled).toBe(true);
    expect(findButton("Сохранить роль")?.disabled).toBe(true);

    await click(findButton("Повторить загрузку"));
    expect(field<HTMLTextAreaElement>("#learning-role").disabled).toBe(false);
    expect(field<HTMLTextAreaElement>("#learning-role").value).toContain("Ведёшь дела.");
  });
});

describe("ProfileLearningPanel — что важно помнить", () => {
  it("показывает записи обоих видов со счётчиком и добавляет новую", async () => {
    await open("memory");

    expect(apiMocks.getProfileMemory).toHaveBeenCalledWith("sekretar");
    expect(container.textContent).toContain("О бизнесе и работе");
    expect(container.textContent).toContain("Минимальный заказ — 12 изделий.");
    expect(container.textContent).toContain("Обо мне");
    expect(container.textContent).toContain("Меня зовут Дмитрий.");
    expect(container.textContent).toContain("занято 70 из 2 200 знаков");

    await enterText(
      field<HTMLTextAreaElement>("#learning-memory-add-memory"),
      "Доставка по городу — бесплатно.",
    );
    const section = container.querySelector('[data-memory-target="memory"]')!;
    await click(findButton("Добавить запись", section));
    expect(apiMocks.addProfileMemory).toHaveBeenCalledWith(
      "sekretar",
      "memory",
      "Доставка по городу — бесплатно.",
    );
    // После записи список перечитан, поле очищено.
    expect(apiMocks.getProfileMemory).toHaveBeenCalledTimes(2);
    expect(field<HTMLTextAreaElement>("#learning-memory-add-memory").value).toBe("");
    expect(onProfileChanged).toHaveBeenCalled();
  });

  it("правит и удаляет запись, передавая её полный старый текст", async () => {
    await open("memory");

    await click(
      container.querySelector('button[aria-label^="Изменить запись: Срок изготовления"]'),
    );
    await enterText(
      field<HTMLTextAreaElement>("#learning-memory-edit-memory"),
      "Срок изготовления — 14 рабочих дней.",
    );
    await click(findButton("Сохранить запись"));
    expect(apiMocks.replaceProfileMemory).toHaveBeenCalledWith(
      "sekretar",
      "memory",
      "Срок изготовления — 10 рабочих дней.",
      "Срок изготовления — 14 рабочих дней.",
    );

    await click(container.querySelector('button[aria-label^="Удалить запись: Меня зовут"]'));
    expect(apiMocks.removeProfileMemory).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Удалить эту запись?");
    await click(findButton("Да, удалить"));
    expect(apiMocks.removeProfileMemory).toHaveBeenCalledWith(
      "sekretar",
      "user",
      "Меня зовут Дмитрий.",
    );
  });

  it("показывает отказ сервера дословно и помечает выключенную память", async () => {
    apiMocks.getProfileMemory.mockResolvedValue({
      ...MEMORY,
      enabled: { memory: true, user: false },
    });
    apiMocks.addProfileMemory.mockRejectedValueOnce(
      new Error('400: {"detail":"Запись не помещается: лимит 2200 знаков."}'),
    );
    await open("memory");

    expect(container.textContent).toContain("отключено");
    await enterText(field<HTMLTextAreaElement>("#learning-memory-add-user"), "Люблю кратко.");
    const section = container.querySelector('[data-memory-target="user"]')!;
    await click(findButton("Добавить запись", section));
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(
      "Запись не помещается: лимит 2200 знаков.",
    );
    // Черновик остаётся — человек поправит и отправит снова.
    expect(field<HTMLTextAreaElement>("#learning-memory-add-user").value).toBe("Люблю кратко.");
  });
});

describe("ProfileLearningPanel — материалы и инструкции", () => {
  it("перечисляет материалы и сохраняет текстовый, предлагая проверить вопросом", async () => {
    await open("materials");

    expect(apiMocks.getProfileMaterials).toHaveBeenCalledWith("sekretar");
    expect(container.textContent).toContain("Прайс на мебель");
    expect(container.textContent).toContain("reglament.pdf");

    await enterText(field<HTMLInputElement>("#learning-material-title"), "Условия доставки");
    await enterText(
      field<HTMLTextAreaElement>("#learning-material-text"),
      "Доставка по городу бесплатна от 30 000 ₽.",
    );
    await click(findButton("Сохранить материал"));
    expect(apiMocks.createProfileMaterial).toHaveBeenCalledWith("sekretar", {
      title: "Условия доставки",
      text: "Доставка по городу бесплатна от 30 000 ₽.",
    });
    expect(container.textContent).toContain(
      "Материал сохранён. Проверьте вопросом, как агент его использует.",
    );

    await click(findButton("Проверить вопросом"));
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe(
      "Проверить вопросом",
    );
    expect(field<HTMLTextAreaElement>("#learning-check-prompt").value).toBe(
      "Прочитай навык «korra-material-new» и его источники, затем ответь: ",
    );
  });

  it("не отправляет неподходящий файл и удаляет материал после подтверждения", async () => {
    await open("materials");

    await click(container.querySelector('[data-material-kind="file"]'));
    const input = field<HTMLInputElement>("#learning-material-file");
    const bad = new File(["x"], "virus.exe");
    Object.defineProperty(input, "files", { value: [bad], configurable: true });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(container.textContent).toContain("Такой файл агент прочитать не сможет");
    await enterText(field<HTMLInputElement>("#learning-material-title"), "Что-то");
    expect(findButton("Сохранить материал")?.disabled).toBe(true);

    await click(container.querySelector('button[aria-label="Удалить материал «Регламент»"]'));
    expect(apiMocks.deleteProfileMaterial).not.toHaveBeenCalled();
    await click(findButton("Да, удалить"));
    expect(apiMocks.deleteProfileMaterial).toHaveBeenCalledWith("sekretar", "korra-material-rules");
  });
});

describe("ProfileLearningPanel — проверить вопросом и ссылки", () => {
  it("задаёт вопрос тем же маршрутом, что и чат, и показывает ответ", async () => {
    await open("check");

    await enterText(
      field<HTMLTextAreaElement>("#learning-check-prompt"),
      "Какой минимальный заказ?",
    );
    await click(findButton("Спросить агента"));
    const [profile, prompt, init] = apiMocks.probeProfileChat.mock.calls[0] as [
      string,
      string,
      { signal?: AbortSignal },
    ];
    expect(profile).toBe("sekretar");
    expect(prompt).toBe("Какой минимальный заказ?");
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(container.textContent).toContain("Минимальный заказ — 12 изделий, срок — 10 рабочих дней.");
  });

  it("отказ доступа объясняет словами владельца", async () => {
    apiMocks.probeProfileChat.mockResolvedValue({
      ok: false,
      reply: "",
      error: "Агент не ответил на контрольное сообщение.",
      detail: "HTTP 401: OAuth access token has expired.",
    });
    await open("check");
    await enterText(field<HTMLTextAreaElement>("#learning-check-prompt"), "Кто ты?");
    await click(findButton("Спросить агента"));
    expect(container.textContent).toContain("Нет доступа к модели");
    expect(container.textContent).toContain("Ответ сервера: HTTP 401");
  });

  it("ведёт в навыки, расписание, модель и чат именно этого агента", async () => {
    await open();
    await click(findButton("Навыки"));
    expect(location()).toBe("/skills?profile=sekretar");
    await click(findButton("Расписание"));
    expect(location()).toBe("/cron?profile=sekretar");
    await click(findButton("Модель"));
    expect(location()).toBe("/profiles?agent=sekretar&edit=model");
    await click(findButton("Открыть чат"));
    expect(location()).toBe("/agents?agent=sekretar");
    await click(findButton("Закрыть"));
    expect(onClose).toHaveBeenCalled();
  });

  it("главный агент в чате — default", async () => {
    await open("check", { ...PROFILE, name: "default", is_default: true, display_name: "Корра" });
    await click(findButton("Открыть чат"));
    expect(location()).toBe("/agents?agent=default");
  });
});

describe("помощники панели", () => {
  it("вопрос к материалу просит прочитать навык и его источники", () => {
    expect(materialProbePrompt({ name: "korra-material-price" }, "Сколько стоит шкаф?")).toBe(
      "Прочитай навык «korra-material-price» и его источники, затем ответь: Сколько стоит шкаф?",
    );
  });

  it("файл проверяется по расширению и размеру до отправки", () => {
    expect(materialFileProblem({ name: "price.PDF", size: 1024 })).toBeNull();
    expect(materialFileProblem({ name: "price.exe", size: 1024 })).toMatch(/прочитать не сможет/);
    expect(materialFileProblem({ name: "big.pdf", size: 11 * 1024 * 1024 })).toMatch(/10 МБ/);
  });
});
