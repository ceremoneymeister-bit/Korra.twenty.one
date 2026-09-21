import { describe, expect, it } from "vitest";

import {
  TAB_DRAG_THRESHOLD_PX,
  hasCrossedDragThreshold,
  isReorderPointer,
  shouldStartTabDrag,
} from "./tab-drag-gesture";

describe("жест на полосе вкладок агентов", () => {
  it("перетаскивает только мышью", () => {
    expect(isReorderPointer({ pointerType: "mouse" })).toBe(true);
    expect(isReorderPointer({ pointerType: "touch" })).toBe(false);
    expect(isReorderPointer({ pointerType: "pen" })).toBe(false);
  });

  it("без pointerType остаётся десктопное поведение", () => {
    // jsdom и браузеры без Pointer Events не сообщают тип указателя;
    // трактовать это как палец значило бы отключить перестановку на десктопе.
    expect(isReorderPointer({})).toBe(true);
    expect(isReorderPointer({ pointerType: "" })).toBe(true);
  });

  it("палец на полосе не начинает перетаскивание — жест остаётся прокрутке", () => {
    expect(shouldStartTabDrag({ pointerType: "touch", button: 0 }, false)).toBe(false);
    expect(shouldStartTabDrag({ pointerType: "mouse", button: 0 }, false)).toBe(true);
  });

  it("не перетаскивает с кнопки «⋮» и не с основной кнопки мыши", () => {
    expect(shouldStartTabDrag({ pointerType: "mouse", button: 0 }, true)).toBe(false);
    expect(shouldStartTabDrag({ pointerType: "mouse", button: 2 }, false)).toBe(false);
  });

  it("порог считает по модулю сдвига в обе стороны", () => {
    expect(hasCrossedDragThreshold(100, 100 + TAB_DRAG_THRESHOLD_PX)).toBe(true);
    expect(hasCrossedDragThreshold(100, 100 - TAB_DRAG_THRESHOLD_PX)).toBe(true);
    expect(hasCrossedDragThreshold(100, 100 + TAB_DRAG_THRESHOLD_PX - 1)).toBe(false);
    expect(hasCrossedDragThreshold(Number.NaN, 100)).toBe(false);
  });
});
