import { describe, expect, it, vi } from "vitest";

import { flushDraftBeforeAction } from "./calc-rates-draft";

describe("flushDraftBeforeAction", () => {
  it("сохраняет последний снимок формы до действия, не дожидаясь debounce", async () => {
    const snapshot = { pricing: { margin_percent: "27" } };
    const saveSnapshot = vi.fn().mockResolvedValue(undefined);

    await flushDraftBeforeAction({
      dirty: true,
      snapshot,
      generation: 12,
      epoch: 4,
      saveSnapshot,
      pendingSave: Promise.resolve(),
      currentGeneration: () => 12,
    });

    expect(saveSnapshot).toHaveBeenCalledOnce();
    expect(saveSnapshot).toHaveBeenCalledWith(snapshot, 12, 4);
  });

  it("не разрешает действие, если принудительное сохранение упало", async () => {
    const failure = new Error("draft conflict");

    await expect(
      flushDraftBeforeAction({
        dirty: true,
        snapshot: { value: "latest" },
        generation: 2,
        epoch: 9,
        saveSnapshot: vi.fn().mockRejectedValue(failure),
        pendingSave: Promise.resolve(),
        currentGeneration: () => 2,
      }),
    ).rejects.toBe(failure);
  });

  it("ждёт уже начатое сохранение, когда новых правок нет", async () => {
    let release!: () => void;
    const pendingSave = new Promise<void>((resolve) => {
      release = resolve;
    });
    let finished = false;
    const action = flushDraftBeforeAction({
      dirty: false,
      snapshot: {},
      generation: 7,
      epoch: 3,
      saveSnapshot: vi.fn(),
      pendingSave,
      currentGeneration: () => 7,
    }).then(() => {
      finished = true;
    });

    await Promise.resolve();
    expect(finished).toBe(false);
    release();
    await action;
    expect(finished).toBe(true);
  });

  it("останавливает действие, если форма изменилась во время барьера", async () => {
    await expect(
      flushDraftBeforeAction({
        dirty: true,
        snapshot: { value: "captured" },
        generation: 5,
        epoch: 2,
        saveSnapshot: vi.fn().mockResolvedValue(undefined),
        pendingSave: Promise.resolve(),
        currentGeneration: () => 6,
      }),
    ).rejects.toThrow("Данные изменились во время операции");
  });
});
