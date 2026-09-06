// @vitest-environment jsdom

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

function render(content: string, streaming = false) {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(
    <Markdown content={content} streaming={streaming} />,
  );
  return host;
}

describe("Чтение ответа агента", () => {
  it("сохраняет подпункты и пояснение внутри соответствующего шага", () => {
    const host = render(
      "1. Соберите заявки.\n   Сначала из почты.\n   - Новые клиенты\n   - Постоянные клиенты\n2. Назначьте ответственного.",
    );
    const steps = host.querySelectorAll("ol > li");
    expect(steps).toHaveLength(2);
    expect(steps[0].querySelectorAll("ul > li")).toHaveLength(2);
    expect(steps[0].textContent).toContain("Сначала из почты.");
    expect(steps[1].textContent).toBe("Назначьте ответственного.");
  });

  it("не начинает продолжение плана заново с единицы и не разрывает список пустой строкой", () => {
    const host = render(
      "3. Проверьте сроки.\n\n4. Подтвердите план.\n\nДалее — встреча.",
    );
    expect(host.querySelectorAll("ol")).toHaveLength(1);
    expect(host.querySelector("ol")?.start).toBe(3);
    expect(host.querySelectorAll("ol > li")).toHaveLength(2);
    expect(host.querySelector("ol")?.textContent).not.toContain("Далее");
  });

  it("собирает цитату со списком в отдельный смысловой блок", () => {
    const host = render(
      "Ответ клиента:\n> **Нужно к пятнице.**\n>\n> - Доставка утром\n> - Оплата по счёту\n\nПроверьте наличие.",
    );
    const quote = host.querySelector("blockquote");
    expect(quote?.querySelector("strong")?.textContent).toBe(
      "Нужно к пятнице.",
    );
    expect(quote?.querySelectorAll("li")).toHaveLength(2);
    expect(quote?.textContent).not.toContain("Проверьте");
    expect(host.textContent).not.toContain(">");
  });

  it("сохраняет код, включая короткую ограду внутри длинной", () => {
    const code = "```пример```\n  сумма = 100 * 2\n";
    const host = render("````text\n" + code + "````");
    expect(host.querySelector("pre code")?.textContent).toBe(code.trimEnd());
    expect(host.querySelector("strong")).toBeNull();
  });

  it("сохраняет код внутри шага и границы следующего шага", () => {
    const host = render(
      "1. Посчитайте:\n\n   ```python\n   total = 100 * 2\n   ```\n2. Сохраните итог.",
    );
    expect(host.querySelector("ol > li pre code")?.textContent).toBe(
      "total = 100 * 2",
    );
    expect(host.querySelectorAll("ol > li")).toHaveLength(2);
  });

  it("сохраняет форматирование таблицы и выравнивание чисел", () => {
    const host = render(
      "| Действие | Бюджет |\n| :--- | ---: |\n| **Реклама** | 30 000 ₽ |\n| Звонки | 0 ₽ |",
    );
    expect(host.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(host.querySelector("tbody strong")?.textContent).toBe("Реклама");
    expect(host.querySelectorAll("th")[1].style.textAlign).toBe("right");
    expect(host.querySelectorAll("td")[1].style.textAlign).toBe("right");
    expect(host.querySelector("th")?.scope).toBe("col");
  });

  it("не исполняет HTML и ссылки с опасной схемой из ответа", () => {
    const host = render(
      "> <img src=x onerror=alert(1)>\n\n[Открыть](javascript:alert) и [Источник](https://example.com).",
    );
    expect(host.querySelector("img")).toBeNull();
    expect(host.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(host.querySelectorAll("a")).toHaveLength(1);
    expect(host.querySelector("a")?.getAttribute("href")).toBe(
      "https://example.com",
    );
  });

  it("показывает недописанный блок кода и единственный курсор в конце потока", () => {
    const host = render("- Пример:\n  ```python\n  total = 2", true);
    const code = host.querySelector("li pre code");
    expect(code?.textContent).toBe("total = 2");
    expect(code?.querySelectorAll(".korra-markdown__caret")).toHaveLength(1);
    expect(host.querySelectorAll(".korra-markdown__caret")).toHaveLength(1);
  });

  it("поток сохраняет один курсор даже на пустой цитате или пункте", () => {
    for (const content of ["", "> ", "- ", "1. ", "## Начало\n\n> - "]) {
      const host = render(content, true);
      expect(host.querySelectorAll(".korra-markdown__caret")).toHaveLength(1);
    }
  });

  it("разделяет новый абзац и следующий список, сохраняя начало нумерации", () => {
    const host = render(
      "3. Третий шаг.\n\nПояснение между списками.\n\n4. Четвёртый шаг.",
    );
    expect([...host.querySelectorAll("ol")].map(list => list.start)).toEqual([
      3, 4,
    ]);
    expect(host.querySelector("ol")?.textContent).not.toContain("Пояснение");
  });

  it("сохраняет обычный текст и переносы, не выдумывая заголовков", () => {
    const content =
      "Итог недели\nВыручка выросла.\n\nСледующая встреча — в среду.";
    const host = render(content);
    expect(host.querySelectorAll("p")).toHaveLength(2);
    expect(host.querySelector("br")).not.toBeNull();
    expect(host.querySelector("h1,h2,h3")).toBeNull();
    expect(host.textContent).toContain("Следующая встреча — в среду.");
  });

  it("не путает тильды, заголовки и разметку с содержимым кода", () => {
    const code = "# Заголовок\n**Это код**\n~~~";
    const host = render("~~~~text\n" + code + "\n~~~~\n\n###### Примечание");
    expect(host.querySelector("pre code")?.textContent).toBe(code);
    expect(host.querySelector("strong")).toBeNull();
    expect(host.querySelector("h6")?.textContent).toBe("Примечание");
  });

  it("сохраняет читаемый текст при чрезмерной вложенности", () => {
    const host = render("> ".repeat(40) + "Текст клиента");
    expect(host.textContent).toContain("Текст клиента");
  });
});
