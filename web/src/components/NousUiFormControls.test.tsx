// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { act, type ReactNode, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import {
  Select,
  SelectOption,
} from "@nous-research/ui/ui/components/select";
import { Switch } from "@nous-research/ui/ui/components/switch";
import { Skeleton, Spinner } from "@nous-research/ui/ui/components/spinner";
import { Textarea } from "@nous-research/ui/ui/components/textarea";

const neoCss = readFileSync(
  "src/themes/neumorphism.css",
  "utf8",
);

let container: HTMLDivElement;
let root: Root;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      addEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: query,
      onchange: null,
      removeEventListener: vi.fn(),
    })),
  );
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("vendored Nous UI form controls", () => {
  it("routes button variants through semantic depth states without changing props", async () => {
    await render(
      <>
        <Button>Primary</Button>
        <Button outlined>Neutral</Button>
        <Button ghost>Ghost</Button>
        <Button destructive>Destructive</Button>
        <Button disabled>Disabled</Button>
      </>,
    );

    const buttons = [...container.querySelectorAll("button")];
    expect(buttons.map((button) => button.dataset.neoVariant)).toEqual([
      "primary",
      "neutral",
      "ghost",
      "destructive",
      "primary",
    ]);
    expect(buttons.every((button) => button.classList.contains("neo-button"))).toBe(
      true,
    );
    expect(buttons.some((button) => /(?:outline|ring)-/.test(button.className))).toBe(
      false,
    );
  });

  it("renders Input as a 44px native field with an announced error state", async () => {
    await render(
      <>
        <label htmlFor="name-input">Name</label>
        <Input
          id="name-input"
          aria-invalid="true"
          aria-describedby="name-error"
          placeholder="Enter a name"
        />
        <span id="name-error">Name is required</span>
      </>,
    );

    const input = container.querySelector("input");
    expect(input).not.toBeNull();
    expect(input?.labels?.[0]?.textContent).toBe("Name");
    expect(input?.className).toContain("min-h-11");
    expect(input?.className).toContain("rounded-lg");
    expect(input?.getAttribute("aria-invalid")).toBe("true");
    expect(input?.getAttribute("aria-describedby")).toBe("name-error");
  });

  it("keeps Switch a named native button and publishes its checked state", async () => {
    function Harness() {
      const [checked, setChecked] = useState(false);
      return (
        <Switch
          aria-label="Enable sync"
          checked={checked}
          onCheckedChange={setChecked}
        />
      );
    }

    await render(<Harness />);
    const control = container.querySelector<HTMLButtonElement>(
      'button[role="switch"]',
    );
    expect(control?.className).toContain("min-h-10");
    expect(control?.getAttribute("aria-checked")).toBe("false");

    await act(async () => control?.click());
    expect(control?.getAttribute("aria-checked")).toBe("true");
  });

  it("keeps Checkbox keyboard-native while exposing a drawable check path", async () => {
    function Harness() {
      const [checked, setChecked] = useState(false);
      return (
        <Checkbox
          aria-label="Keep history"
          checked={checked}
          onCheckedChange={(next) => setChecked(next === true)}
        />
      );
    }

    await render(<Harness />);
    const control = container.querySelector<HTMLButtonElement>(
      'button[role="checkbox"]',
    );
    expect(control?.className).toContain("min-h-10");
    expect(control?.getAttribute("aria-checked")).toBe("false");
    expect(control?.querySelector("path")?.getAttribute("pathLength")).toBe("1");

    await act(async () => control?.click());
    expect(control?.getAttribute("aria-checked")).toBe("true");
  });

  it("links Select to its label and supports active-descendant keyboard selection", async () => {
    function Harness() {
      const [value, setValue] = useState("b");
      return (
        <>
          <label htmlFor="model-select">Model</label>
          <Select id="model-select" value={value} onValueChange={setValue}>
            <SelectOption value="a">Alpha</SelectOption>
            <SelectOption value="b">Beta</SelectOption>
            <SelectOption value="c">Gamma</SelectOption>
          </Select>
        </>
      );
    }

    await render(<Harness />);
    const control = container.querySelector<HTMLButtonElement>(
      'button[role="combobox"]',
    );
    expect(control?.labels?.[0]?.textContent).toBe("Model");
    expect(control?.className).toContain("min-h-11");

    await act(async () =>
      control?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "ArrowDown" }),
      ),
    );
    expect(control?.getAttribute("aria-expanded")).toBe("true");
    expect(control?.getAttribute("aria-activedescendant")).toContain("option-1");
    expect(document.body.querySelectorAll('[role="option"]')).toHaveLength(3);
    expect(document.body.querySelector('[role="option"]')?.className).toContain(
      "min-h-10",
    );

    await act(async () =>
      control?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "End" }),
      ),
    );
    await act(async () =>
      control?.dispatchEvent(
        new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }),
      ),
    );
    expect(control?.textContent).toContain("Gamma");
    expect(control?.getAttribute("aria-expanded")).toBe("false");
  });

  it("provides native textarea, compact activity and block skeleton primitives", async () => {
    await render(
      <>
        <Textarea aria-invalid="true" aria-label="Description" />
        <Spinner aria-label="Loading" role="status" />
        <Skeleton className="h-20" />
      </>,
    );

    expect(container.querySelector("textarea")?.classList.contains("neo-field")).toBe(
      true,
    );
    expect(container.querySelectorAll(".neo-spinner-dot")).toHaveLength(3);
    expect(container.querySelector(".neo-skeleton")?.className).toContain("h-20");
  });

  it("defines keyboard, pointer, error and disabled states using depth tokens", () => {
    expect(neoCss).toContain(".neo-button:not(:disabled):is(:focus-visible");
    expect(neoCss).toContain("[data-demo-state='pressed']");
    expect(neoCss).toContain("[data-demo-state='error']");
    expect(neoCss).toContain(".neo-button:disabled");
    expect(neoCss).toContain("box-shadow: var(--neo-focus-visible)");
    expect(neoCss).toContain(
      "--neo-ease-toggle: cubic-bezier(0.85, 0.05, 0.18, 1.35)",
    );
  });
});
