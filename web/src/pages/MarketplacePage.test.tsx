// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.hoisted(() => {
  (window as { __HERMES_BASE_PATH__?: string }).__HERMES_BASE_PATH__ = "/c/demo";
});

import MarketplacePage from "./MarketplacePage";
import { ItemDetails } from "@/components/marketplace/ItemDetails";
import { MARKETPLACE_IN_DEVELOPMENT, MARKETPLACE_SECTIONS, findItem, findSection } from "@/lib/marketplace-catalog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{`${pathname}${search}`}</output>;
}

type PageProps = { inDevelopment?: boolean };
/** По умолчанию — сама витрина, как после 0.21.21; `{ inDevelopment: true }` — закрытая «В разработке». */
const SHOWCASE: PageProps = { inDevelopment: false };

async function open(entry = "/marketplace", props: PageProps = SHOWCASE) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/marketplace" element={<MarketplacePage {...props} />} />
        <Route path="/marketplace/:section" element={<MarketplacePage {...props} />} />
        <Route path="/marketplace/:section/:item" element={<MarketplacePage {...props} />} />
        <Route path="*" element={<p>вне маркетплейса</p>} />
      </Routes>
      <LocationProbe />
    </MemoryRouter>,
  ));
}

async function reopen(entry: string, props: PageProps = SHOWCASE) {
  await act(async () => root.unmount());
  container.remove();
  await open(entry, props);
}

async function click(element: Element | undefined | null) {
  expect(element).toBeTruthy();
  await act(async () => (element as HTMLElement).click());
}

const location = () => container.querySelector('[data-testid="location"]')?.textContent;
const orderHref = (scope: ParentNode) => scope.querySelector<HTMLAnchorElement>('a[href^="https://t.me/"]')!;
const orderText = (anchor: HTMLAnchorElement) => decodeURIComponent(anchor.getAttribute("href")!.split("?text=")[1]);

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
});

describe("в разработке", () => {
  const preview = () => container.querySelector<HTMLElement>("[data-marketplace-preview]")!;
  const soon = () => container.querySelector<HTMLElement>("[data-marketplace-soon]")!;

  it("витрина размыта и недоступна; поверх — «В разработке» с одной строкой", async () => {
    await open("/marketplace", { inDevelopment: true });
    expect(preview().hasAttribute("inert")).toBe(true);
    expect(preview().getAttribute("aria-hidden")).toBe("true");
    expect(preview().querySelectorAll("[data-billboard]")).toHaveLength(4);
    const card = Array.from(soon().children).find((node) => node !== preview())!;
    expect(card.querySelector("h2")?.textContent).toBe("В разработке");
    expect(card.querySelector("p")?.textContent?.trim()).toBe("Скоро здесь появятся первые услуги от команды Korra.");
    expect(card.querySelector("a, button, input, [tabindex]")).toBeNull();
  });

  it("раздел и позиция по прямой ссылке тоже закрыты: «Заказать» и ссылки только внутри inert", async () => {
    await open("/marketplace", { inDevelopment: true });
    for (const entry of ["/marketplace/agents", "/marketplace/agents/carousels", "/marketplace/automation"]) {
      await reopen(entry, { inDevelopment: true });
      expect(soon(), entry).not.toBeNull();
      const order = container.querySelector('a[href^="https://t.me/"]');
      expect(order, entry).not.toBeNull();
      expect(order!.closest("[inert]"), entry).toBe(preview());
      const focusable = Array.from(container.querySelectorAll("a, button, input, select, textarea, [tabindex]"));
      expect(focusable.every((node) => preview().contains(node)), entry).toBe(true);
    }
  });

  it("по умолчанию страница следует MARKETPLACE_IN_DEVELOPMENT", async () => {
    await open("/marketplace", {});
    expect(Boolean(container.querySelector("[data-marketplace-soon]"))).toBe(MARKETPLACE_IN_DEVELOPMENT);
  });
});

describe("витрина", () => {
  it("четыре билборда: заголовок ведёт в раздел, есть обещание, цена и примеры", async () => {
    await open();
    const titles = Array.from(container.querySelectorAll<HTMLAnchorElement>("[data-billboard] h3 a"));
    expect(titles.map((link) => link.getAttribute("href"))).toEqual([
      "/marketplace/agents", "/marketplace/skills", "/marketplace/services", "/marketplace/automation",
    ]);
    const agents = container.querySelector('[data-billboard="agents"]')!;
    expect(agents.querySelector("h3")?.textContent).toBe("Агенты под ваш бизнес");
    expect(agents.querySelector("[data-price-anchor]")?.textContent).toBe("от 4 990 ₽");
    expect(Array.from(agents.querySelectorAll("li")).map((node) => node.textContent)).toEqual([
      "Карусели", "Протоколы встреч", "Контроль продаж",
    ]);
    expect(container.textContent).not.toContain("Готовые агенты, навыки, сервисы");
  });

  it("клик по заголовку билборда ведёт в раздел", async () => {
    await open();
    await click(container.querySelector('[data-billboard="skills"] h3 a'));
    expect(location()).toBe("/marketplace/skills");
    expect(container.textContent).toContain("Личный навык");
  });

  it("пример ведёт прямо на страницу позиции", async () => {
    await open();
    const chip = container.querySelector<HTMLAnchorElement>('[data-billboard="agents"] [data-example-link="carousels"]')!;
    expect(chip.getAttribute("href")).toBe("/marketplace/agents/carousels");
    await click(chip);
    expect(location()).toBe("/marketplace/agents/carousels");
    expect(container.querySelector("[data-item-page] h2")?.textContent).toBe("Карусели для Instagram и VK");
  });

  it("примеры автоматизации без своей страницы — подписи, а не ссылки", async () => {
    await open();
    const automation = container.querySelector('[data-billboard="automation"]')!;
    expect(automation.querySelectorAll("li")).toHaveLength(3);
    expect(automation.querySelector("[data-example-link]")).toBeNull();
    expect(automation.querySelector("li")?.textContent).toBe("Расчёт по чертежам");
  });

  it("ссылки не вложены друг в друга", async () => {
    await open();
    expect(container.querySelector("a a")).toBeNull();
  });
});

describe("переход внутри маркетплейса", () => {
  it("открывает новую страницу с начала основного контейнера и ставит фокус на заголовок", async () => {
    container = document.createElement("div");
    const main = document.createElement("main");
    container.append(main);
    document.body.append(container);
    root = createRoot(main);
    await act(async () => root.render(
      <MemoryRouter initialEntries={["/marketplace/agents"]}>
        <Routes>
          <Route path="/marketplace/:section" element={<MarketplacePage inDevelopment={false} />} />
          <Route path="/marketplace/:section/:item" element={<MarketplacePage inDevelopment={false} />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>,
    ));
    main.scrollTop = 1384;
    await click(main.querySelector('a[href="/marketplace/agents/client-support"]'));
    expect(location()).toBe("/marketplace/agents/client-support");
    expect(main.scrollTop).toBe(0);
    expect(document.activeElement?.textContent).toBe("Консультант для ваших клиентов");
  });
});

describe("только платное", () => {
  it("ни в одном разделе нет бесплатных позиций и переходов из маркетплейса в настройки", async () => {
    await open();
    for (const section of MARKETPLACE_SECTIONS) {
      await reopen(`/marketplace/${section.id}`);
      expect(container.textContent, section.id).not.toMatch(/бесплатно/i);
      expect(container.querySelector('a[href^="/env"], a[href^="/profiles"], a[href^="/skills"]'), section.id).toBeNull();
    }
  });

  it("прежние адреса бесплатного ведут в понятное «нет»", async () => {
    await open("/marketplace/services/photo-gen");
    expect(container.textContent).toContain("Такой позиции нет");
    await reopen("/marketplace/skills/markitdown");
    expect(container.textContent).toContain("Такой позиции нет");
  });
});

describe("разделы", () => {
  it("агенты: одна группа специалистов; назад — на витрину", async () => {
    await open("/marketplace/agents");
    expect(Array.from(container.querySelectorAll("h3[id^=group-]")).map((node) => node.textContent)).toEqual(["Специалисты"]);
    expect(container.querySelectorAll("[data-item]")).toHaveLength(6);
    await click(Array.from(container.querySelectorAll("a")).find((node) => node.textContent === "Маркетплейс"));
    expect(location()).toBe("/marketplace");
  });

  it("у позиции — цена с пояснением и «Заказать» в Telegram; название ведёт на страницу", async () => {
    await open("/marketplace/agents");
    const card = container.querySelector('[data-item="reels"]')!;
    expect(card.textContent).toContain("14 900 ₽");
    expect(card.textContent).toContain("разово, с настройкой");
    const order = orderHref(card);
    expect(order.textContent).toBe("Заказать");
    expect(order.target).toBe("_blank");
    expect(order.rel).toContain("noopener");
    await click(card.querySelector('a[href="/marketplace/agents/reels"]'));
    expect(location()).toBe("/marketplace/agents/reels");
  });

  it("навыки и сервисы: только позиции с заказом", async () => {
    await open("/marketplace/skills");
    expect(Array.from(container.querySelectorAll("[data-item]")).map((node) => node.getAttribute("data-item"))).toEqual([
      "personal-skill", "service-skill",
    ]);
    expect(orderHref(container.querySelector('[data-item="personal-skill"]')!).textContent).toBe("Заказать");
    await reopen("/marketplace/services");
    expect(Array.from(container.querySelectorAll("[data-item]")).map((node) => node.getAttribute("data-item"))).toEqual([
      "video-gen", "voice-gen", "music-gen",
    ]);
    expect(container.querySelectorAll('[data-item] a[href^="https://t.me/"]')).toHaveLength(3);
  });

  it("автоматизация: кейсы без цены и кнопок, одна общая кнопка с ценой раздела", async () => {
    await open("/marketplace/automation");
    expect(container.querySelectorAll("[data-example]")).toHaveLength(5);
    expect(container.querySelector("[data-example] a")).toBeNull();
    expect(container.querySelector("[data-example]")!.textContent).toContain("Завод металлоконструкций");
    const buttons = container.querySelectorAll('a[href^="https://t.me/"]');
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toBe("Обсудить задачу");
    expect(container.querySelector("[data-section-cta]")!.textContent).toContain("от 100 000 ₽ · цена после разбора задачи");
    expect(orderText(buttons[0] as HTMLAnchorElement)).toBe("Здравствуйте! Хочу обсудить автоматизацию под ключ. Моя Korra: " + window.location.host + "/c/demo.");
  });
});

describe("страница позиции", () => {
  it("показывает имя, цену, «Заказать» и блоки данных; без презентации и результатов", async () => {
    await open("/marketplace/agents/carousels");
    const page = container.querySelector("[data-item-page]")!;
    expect(page.querySelector("h2")?.textContent).toBe("Карусели для Instagram и VK");
    expect(page.textContent).toContain("9 900 ₽");
    const headings = Array.from(page.querySelectorAll("h3")).map((node) => node.textContent);
    expect(headings).toEqual(["Что делает", "Что вы получите", "Что нужно", "Как это будет"]);
    expect(page.textContent).toContain("Собирает карусель из темы");
    expect(page.textContent).toContain("Подписка ChatGPT для генерации изображений");
    expect(page.querySelectorAll("ol li")).toHaveLength(3);
    expect(page.textContent).not.toContain("Презентация");
    expect(page.textContent).not.toContain("Результаты");
    const order = orderHref(page);
    expect(order.textContent).toBe("Заказать");
    expect(orderText(order)).toBe(
      `Здравствуйте! Хочу из маркетплейса Korra: «Карусели для Instagram и VK» (9 900 ₽, разово, с настройкой). Моя Korra: ${window.location.host}/c/demo.`,
    );
    expect(order.href).toContain("https://t.me/ceremoneymeister?text=");
  });

  it("без «Что нужно» блока нет", async () => {
    await open("/marketplace/services/video-gen");
    const headings = Array.from(container.querySelectorAll("[data-item-page] h3")).map((node) => node.textContent);
    expect(headings).toEqual(["Что делает", "Что вы получите", "Как это будет"]);
  });

  it("«Презентация» и «Результаты» появляются, только когда есть данные", async () => {
    const base = findItem(findSection("agents")!, "carousels")!;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    const item = {
      ...base,
      presentation: { src: "/media/carousels.mp4", caption: "Как это работает" },
      results: [{ title: "Карусель для кофейни", image: "/media/cafe.png", caption: "Итог за день" }],
    };
    await act(async () => root.render(<MemoryRouter><ItemDetails item={item} section={findSection("agents")!} /></MemoryRouter>));
    expect(container.querySelector("[data-presentation] video")?.getAttribute("src")).toBe("/media/carousels.mp4");
    expect(container.querySelector("[data-results] img")?.getAttribute("alt")).toBe("Карусель для кофейни");
    expect(container.textContent).toContain("Итог за день");
  });

  it("неизвестный раздел и неизвестная позиция — понятное сообщение со ссылкой назад", async () => {
    await open("/marketplace/nothing");
    expect(container.textContent).toContain("Такого раздела нет");
    expect(container.querySelector('a[href="/marketplace"]')).not.toBeNull();
    await reopen("/marketplace/agents/nothing");
    expect(container.textContent).toContain("Такой позиции нет");
    expect(container.querySelector('a[href="/marketplace/agents"]')).not.toBeNull();
    expect(container.querySelector('a[href="/marketplace"]')).not.toBeNull();
    // кейс автоматизации своей страницы не имеет
    await reopen("/marketplace/automation/finance-report");
    expect(container.textContent).toContain("Такой позиции нет");
  });
});
