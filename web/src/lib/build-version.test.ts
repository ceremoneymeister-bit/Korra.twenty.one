import { afterEach, describe, expect, it, vi } from "vitest";

import { isStaleBuild, loadedBuild } from "./build-version";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("loadedBuild", () => {
  it("reads the build stamped into this tab's HTML", () => {
    vi.stubGlobal("window", { __KORRA_BUILD__: " 93671cd77237 " });
    expect(loadedBuild()).toBe("93671cd77237");
  });

  it("stays silent when the server stamped nothing", () => {
    vi.stubGlobal("window", {});
    expect(loadedBuild()).toBe("");
  });

  it("does not throw without a browser", () => {
    vi.stubGlobal("window", undefined);
    expect(loadedBuild()).toBe("");
  });
});

describe("isStaleBuild", () => {
  it("spots a deploy that happened after the tab was opened", () => {
    expect(isStaleBuild("93671cd77237", "a0e76c43fea7")).toBe(true);
  });

  it("stays quiet while the tab matches the server", () => {
    expect(isStaleBuild("93671cd77237", "93671cd77237")).toBe(false);
  });

  it("stays quiet when either side has no signal", () => {
    // Установка не из образа: файла сборки нет, сервер отдаёт пустое значение.
    // Пустота обязана читаться как «сигнала нет», иначе владелец получит вечное
    // предложение обновиться, которое ничего не чинит.
    expect(isStaleBuild("93671cd77237", "")).toBe(false);
    expect(isStaleBuild("", "a0e76c43fea7")).toBe(false);
    expect(isStaleBuild(null, undefined)).toBe(false);
  });

  it("ignores surrounding whitespace on both sides", () => {
    expect(isStaleBuild(" 93671cd77237", "93671cd77237 ")).toBe(false);
  });
});
