import { afterEach, describe, expect, it, vi } from "vitest";
import { artifactUrl } from "./chat-artifacts";

describe("artifactUrl", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("adds the loopback query token for browser-native media requests", () => {
    vi.stubGlobal("window", { __HERMES_SESSION_TOKEN__: "loopback-token" });
    const url = artifactUrl("client/artifacts/storm.mp4");
    expect(url).toContain("path=client%2Fartifacts%2Fstorm.mp4");
    expect(url).toContain("inline=1");
    expect(url).toContain("token=loopback-token");
  });

  it("relies on the authenticated cookie when no loopback token exists", () => {
    vi.stubGlobal("window", {});
    expect(artifactUrl("client/artifacts/storm.png")).not.toContain("token=");
  });

  it("never exposes the session token in fleet-interface media URLs", () => {
    vi.stubGlobal("window", {
      __HERMES_SESSION_TOKEN__: "must-not-reach-access-logs",
      __KORRA_UI_MODE__: "fleet",
    });
    expect(artifactUrl("client/artifacts/storm.png")).not.toContain("token=");
  });

  it("binds previews to the reviewed artifact bytes", () => {
    vi.stubGlobal("window", { __HERMES_SESSION_TOKEN__: "loopback-token" });
    const sha256 = "a".repeat(64);
    expect(artifactUrl("client/artifacts/storm.mp4", true, sha256)).toContain(
      `expected_sha256=${sha256}`,
    );
  });
});
