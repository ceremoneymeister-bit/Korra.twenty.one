import { expect, it } from "vitest";
import { cabinetLogoutPath } from "./cabinet-session";

it.each([
  ["/c/probe", "/cab/logout"], ["/c/probe/", "/cab/logout"],
  ["/cab/c/probe", "/cab/cab/logout"], ["/mounted/c/probe", "/mounted/cab/logout"],
  ["", null], ["/hermes", null], ["//foreign/c/probe", null], ["/../c/probe", null],
])("routes %s to the session-owning cabinet", (base, expected) => {
  expect(cabinetLogoutPath(base)).toBe(expected);
});
