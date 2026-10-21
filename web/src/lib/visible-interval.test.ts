// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startVisibleInterval } from "./visible-interval";

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("startVisibleInterval", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    setHidden(false);
  });

  it("пропускает тики скрытой вкладки и обновляет сразу при возврате", () => {
    const tick = vi.fn();
    const stop = startVisibleInterval(tick, 5000);
    vi.advanceTimersByTime(5000);
    expect(tick).toHaveBeenCalledTimes(1);

    setHidden(true);
    vi.advanceTimersByTime(20_000);
    expect(tick).toHaveBeenCalledTimes(1);

    setHidden(false);
    expect(tick).toHaveBeenCalledTimes(2);

    stop();
    vi.advanceTimersByTime(20_000);
    setHidden(false);
    expect(tick).toHaveBeenCalledTimes(2);
  });
});
