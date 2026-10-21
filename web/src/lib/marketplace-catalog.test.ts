import { describe, expect, it } from "vitest";
import {
  MARKETPLACE_ORDER_CONTACT,
  MARKETPLACE_SECTIONS,
  buildOrderLink,
  findItem,
  findSection,
  orderLabel,
  panelAddress,
  priceLine,
} from "./marketplace-catalog";
import { MARKETPLACE_ICONS } from "./marketplace-icons";

const allItems = MARKETPLACE_SECTIONS.flatMap((section) => section.groups.flatMap((group) => group.items));

describe("данные маркетплейса", () => {
  it("четыре раздела в порядке витрины", () => {
    expect(MARKETPLACE_SECTIONS.map((section) => section.id)).toEqual(["agents", "skills", "services", "automation"]);
    for (const section of MARKETPLACE_SECTIONS) {
      expect(section.examples.length).toBeGreaterThanOrEqual(2);
      expect(section.examples.length).toBeLessThanOrEqual(3);
      expect(section.price_anchor).not.toBe("");
    }
  });

  it("у каждой позиции со страницей есть цена, id уникальны в разделе", () => {
    for (const section of MARKETPLACE_SECTIONS) {
      const ids = section.groups.flatMap((group) => group.items).map((item) => item.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
    for (const item of allItems.filter((entry) => entry.does)) expect(item.price, item.id).toBeTruthy();
  });

  it("иконки названы существующими", () => {
    for (const name of [...allItems.map((item) => item.icon), ...MARKETPLACE_SECTIONS.map((section) => section.icon)]) {
      expect(MARKETPLACE_ICONS[name]).toBeTruthy();
    }
  });

  it("только платное: у каждой позиции со страницей есть цена, бесплатных нет", () => {
    for (const item of allItems) {
      expect(item.price ?? "", item.id).not.toMatch(/бесплатно/i);
      if (item.does) expect(item.price, item.id).toBeTruthy();
    }
  });

  it("пример на билборде называет позицию своего раздела, без повторов", () => {
    for (const section of MARKETPLACE_SECTIONS) {
      const ids = section.groups.flatMap((group) => group.items).map((item) => item.id);
      for (const example of section.examples) expect(ids, `${section.id}: ${example.item}`).toContain(example.item);
      expect(new Set(section.examples.map((example) => example.item)).size).toBe(section.examples.length);
    }
  });

  it("находит раздел и позицию со страницей; кейс автоматизации страницы не имеет", () => {
    const agents = findSection("agents")!;
    expect(findItem(agents, "carousels")?.name).toBe("Карусели для Instagram и VK");
    expect(findItem(agents, "nothing")).toBeUndefined();
    expect(findSection("nothing")).toBeUndefined();
    expect(findItem(findSection("automation")!, "finance-report")).toBeUndefined();
  });
});

describe("кнопка заказа позиции", () => {
  const section = (id: string) => findSection(id)!;
  it("у позиции со страницей — «Заказать», у кейса автоматизации кнопки нет", () => {
    expect(orderLabel(findItem(section("agents"), "reels")!, section("agents"))).toBe("Заказать");
    expect(orderLabel(section("automation").groups[0].items[0], section("automation"))).toBeNull();
  });
});

describe("ссылка заказа", () => {
  const panel = panelAddress("app.korra-agent.online", "/c/ivanov");

  it("собирает адрес панели из хоста и префикса", () => {
    expect(panel).toBe("app.korra-agent.online/c/ivanov");
    expect(panelAddress("korra.local", "")).toBe("korra.local");
  });

  it("кодирует текст целиком и включает имя, цену и панель", () => {
    const link = buildOrderLink({ kind: "item", item: { name: "Монтаж рилсов", price: "14 900 ₽", price_note: "разово, с настройкой" } }, panel);
    expect(link.startsWith(`https://t.me/${MARKETPLACE_ORDER_CONTACT}?text=`)).toBe(true);
    expect(link).not.toContain(" ");
    const text = decodeURIComponent(link.split("?text=")[1]);
    expect(text).toBe("Здравствуйте! Хочу из маркетплейса Korra: «Монтаж рилсов» (14 900 ₽, разово, с настройкой). Моя Korra: app.korra-agent.online/c/ivanov.");
  });

  it("не ломается на спецсимволах в имени", () => {
    const link = buildOrderLink({ kind: "item", item: { name: "A&B=C?#", price: "1 ₽" } }, "h");
    expect(link.split("?text=")).toHaveLength(2);
    expect(link).not.toMatch(/text=[^&]*[&#]/);
    expect(decodeURIComponent(link.split("?text=")[1])).toContain("«A&B=C?#» (1 ₽)");
  });

  it("для автоматизации — своё сообщение", () => {
    const text = decodeURIComponent(buildOrderLink({ kind: "automation" }, panel).split("?text=")[1]);
    expect(text).toBe("Здравствуйте! Хочу обсудить автоматизацию под ключ. Моя Korra: app.korra-agent.online/c/ivanov.");
  });

  it("цена с пояснением собирается без пустых частей", () => {
    expect(priceLine({ price: "от 4 990 ₽" })).toBe("от 4 990 ₽");
    expect(priceLine({ price: "от 990 ₽", price_note: "в месяц" })).toBe("от 990 ₽, в месяц");
  });
});
