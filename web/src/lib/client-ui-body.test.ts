// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { markClientUiBody } from "./client-ui-body";

describe("markClientUiBody", () => {
  it("помечает body, чтобы окна-порталы получали шрифт клиентской панели, и снимает метку", () => {
    const portal = document.createElement("div");
    document.body.append(portal);
    expect(portal.closest('[data-client-ui="true"]')).toBeNull();
    const unmark = markClientUiBody();
    expect(portal.closest('[data-client-ui="true"]')).toBe(document.body);
    unmark();
    expect(document.body.dataset.clientUi).toBeUndefined();
    portal.remove();
  });
});
