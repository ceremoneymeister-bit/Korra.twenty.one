// @vitest-environment jsdom

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardLayoutPreference } from "@/lib/api";
import {
  useDashboardLayout,
  type UseDashboardLayoutReturn,
} from "./useDashboardLayout";

/** Тот же класс, что бросает `fetchJSON`: хуку важен именно код ответа. */
const { ApiError } = vi.hoisted(() => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      message: string,
      readonly payload?: unknown,
    ) {
      super(message);
      this.name = "ApiError";
    }
  },
}));

const getDashboardLayout = vi.fn();
const setDashboardLayout = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError,
  api: {
    getDashboardLayout: () => getDashboardLayout(),
    setDashboardLayout: (layout: unknown) => setDashboardLayout(layout),
  },
}));

const CATALOG = {
  ids: ["attention", "agents", "metrics"] as const,
  pinned: ["attention"] as const,
};

type Body = Pick<DashboardLayoutPreference, "revision" | "order" | "hidden" | "sizes">;

/** Серверная запись: одна ревизия, монотонная, как у настоящего эндпоинта. */
let server: DashboardLayoutPreference;

function record(hidden: string[], revision: number): DashboardLayoutPreference {
  return {
    version: 1,
    revision,
    initialized: true,
    order: [...CATALOG.ids],
    hidden,
    sizes: { agents: "m", metrics: "m" },
  };
}

/** Отложенный ответ: тест сам решает, когда и чем сервер ответит. */
function deferred<T>() {
  let settle: (value: T) => void = () => {};
  let fail: (reason: unknown) => void = () => {};
  const promise = new Promise<T>((resolve, reject) => {
    settle = resolve;
    fail = reject;
  });
  return { promise, settle, fail };
}

let container: HTMLDivElement;
let root: Root;
let current: UseDashboardLayoutReturn;

function Probe() {
  const value = useDashboardLayout(CATALOG);
  useEffect(() => {
    current = value;
  }, [value]);
  return null;
}

async function mount() {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<Probe />);
  });
  await act(async () => {});
}

/** Прокрутить очередь записей: каждый шаг — один await внутри хука. */
async function flush(times = 8) {
  for (let step = 0; step < times; step += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function hide(id: string) {
  return {
    order: [...current.layout.order],
    hidden: [...current.layout.hidden, id],
    sizes: { ...current.layout.sizes },
  };
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  getDashboardLayout.mockReset();
  setDashboardLayout.mockReset();
  server = record([], 4);
  getDashboardLayout.mockImplementation(async () => ({ ...server }));
  setDashboardLayout.mockImplementation(async (body: Body) => {
    if (body.revision !== server.revision) {
      throw new ApiError(409, "409: Дашборд уже изменился в другом окне.", {
        detail: "Дашборд уже изменился в другом окне.",
        preference: { ...server },
      });
    }
    server = { ...server, revision: server.revision + 1, hidden: [...body.hidden] };
    return { ...server };
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("useDashboardLayout", () => {
  it("два быстрых выбора уходят по очереди на серверных ревизиях", async () => {
    await mount();

    await act(async () => {
      current.apply(hide("agents"));
      current.apply({ ...hide("agents"), hidden: ["agents", "metrics"] });
    });
    await flush();

    expect(setDashboardLayout).toHaveBeenCalledTimes(2);
    expect(setDashboardLayout.mock.calls[0][0].revision).toBe(4);
    expect(setDashboardLayout.mock.calls[1][0].revision).toBe(5);
    expect(server.hidden).toEqual(["agents", "metrics"]);
    expect(current.status).toBe("ready");
    expect(current.saving).toBe(false);
  });

  it("проигранный конфликт прекращает очередь: второй выбор не переписывает победителя", async () => {
    await mount();
    // Другое окно успело записать свою доску, пока человек нажимал дважды.
    setDashboardLayout.mockImplementationOnce(async () => {
      server = record(["metrics"], 9);
      throw new ApiError(409, "409: Дашборд уже изменился в другом окне.", {
        detail: "Дашборд уже изменился в другом окне.",
        preference: { ...server },
      });
    });

    await act(async () => {
      current.apply(hide("agents"));
      current.apply({ ...hide("agents"), hidden: ["agents", "metrics"] });
    });
    await flush();

    expect(setDashboardLayout).toHaveBeenCalledTimes(1);
    expect(server.hidden).toEqual(["metrics"]);
    expect(server.revision).toBe(9);
    expect(current.layout.hidden).toEqual(["metrics"]);
    expect(current.status).toBe("conflict");
    expect(current.message).toContain("другом окне");
    expect(current.saving).toBe(false);
  });

  it("после конфликта новый осознанный выбор записывается поверх победителя", async () => {
    await mount();
    setDashboardLayout.mockImplementationOnce(async () => {
      server = record(["metrics"], 9);
      throw new ApiError(409, "409: Дашборд уже изменился в другом окне.", {
        preference: { ...server },
      });
    });

    await act(async () => current.apply(hide("agents")));
    await flush();
    expect(current.status).toBe("conflict");

    await act(async () => current.apply(hide("agents")));
    await flush();

    expect(setDashboardLayout).toHaveBeenLastCalledWith(
      expect.objectContaining({ revision: 9, hidden: ["metrics", "agents"] }),
    );
    expect(server.hidden).toEqual(["metrics", "agents"]);
    expect(current.status).toBe("ready");
    expect(current.message).toBe("");
  });

  it("выбор, сделанный пока идёт разбор отказа, не уходит записью вслепую", async () => {
    await mount();
    // Исход первой записи неизвестен, и хук идёт перечитывать сервер.
    setDashboardLayout.mockRejectedValueOnce(
      new ApiError(500, "500: Сервис временно недоступен."),
    );
    const recovery = deferred<DashboardLayoutPreference>();
    getDashboardLayout.mockImplementationOnce(() => recovery.promise);

    await act(async () => current.apply(hide("agents")));
    await flush(3);
    expect(getDashboardLayout).toHaveBeenCalledTimes(2);

    // Человек нажимает ещё дважды, всё ещё не видя, что на сервере.
    await act(async () => {
      current.apply(hide("metrics"));
      current.apply({ ...hide("metrics"), hidden: ["metrics", "agents"] });
    });

    // Разбор закончен: победила чужая запись.
    server = record(["metrics"], 9);
    await act(async () => recovery.settle({ ...server }));
    await flush();

    expect(setDashboardLayout).toHaveBeenCalledTimes(1);
    expect(server.hidden).toEqual(["metrics"]);
    expect(server.revision).toBe(9);
    expect(current.layout.hidden).toEqual(["metrics"]);
    expect(current.status).toBe("conflict");
    expect(current.saving).toBe(false);

    // Граница ровно здесь: нажатие после завершённого разбора уже пишет.
    await act(async () => current.apply(hide("agents")));
    await flush();
    expect(setDashboardLayout).toHaveBeenCalledTimes(2);
    expect(setDashboardLayout).toHaveBeenLastCalledWith(
      expect.objectContaining({ revision: 9, hidden: ["metrics", "agents"] }),
    );
    expect(current.status).toBe("ready");
  });

  it("отказ без сдвига ревизии объясняет несохранение, а не чужую запись", async () => {
    await mount();
    // Сервер отвечает 409 и на несохранённую запись (политика её не приняла):
    // в теле та же ревизия, что мы отправили, — чужой записи тут не было.
    setDashboardLayout.mockImplementationOnce(async () => {
      throw new ApiError(409, "409: Дашборд не сохранился; обновите экран.", {
        detail: "Дашборд не сохранился; обновите экран.",
        preference: { ...server },
      });
    });

    await act(async () => current.apply(hide("agents")));
    await flush();

    expect(current.message).not.toContain("другом окне");
    expect(current.message).toContain("не сохранилось");
    expect(current.status).toBe("conflict");
    expect(current.layout.hidden).toEqual([]);
    expect(setDashboardLayout).toHaveBeenCalledTimes(1);
  });

  it("сбой связи не выдаётся за чужую запись и не подтверждает несохранённый выбор", async () => {
    await mount();
    setDashboardLayout.mockRejectedValueOnce(
      new ApiError(500, "500: Сервис временно недоступен."),
    );

    await act(async () => current.apply(hide("agents")));
    await flush();

    expect(current.message).not.toContain("другом окне");
    expect(current.message).toContain("не сохранилось");
    expect(current.status).toBe("conflict");
    // Показана серверная запись, а не то, что человек выбрал.
    expect(current.layout.hidden).toEqual([]);
    expect(server.revision).toBe(4);
  });

  it("неизвестный исход подтверждается перечитыванием: запись всё-таки дошла", async () => {
    await mount();
    setDashboardLayout.mockImplementationOnce(async (body: Body) => {
      server = { ...server, revision: server.revision + 1, hidden: [...body.hidden] };
      throw new ApiError(502, "502: Сервис временно недоступен.");
    });

    await act(async () => current.apply(hide("agents")));
    await flush();

    expect(current.layout.hidden).toEqual(["agents"]);
    expect(current.status).toBe("ready");
    expect(current.message).toBe("");
  });

  it("сбой записи и недоступное перечитывание оставляют явную ошибку", async () => {
    await mount();
    setDashboardLayout.mockRejectedValueOnce(new ApiError(503, "503: Панель недоступна."));
    getDashboardLayout.mockRejectedValueOnce(new Error("offline"));

    await act(async () => current.apply(hide("agents")));
    await flush();

    expect(current.status).toBe("error");
    expect(current.message).toContain("Повторить");
    // Запись могла дойти и не подтвердиться — объявлять потерю нечестно.
    expect(current.message).toContain("Не удалось проверить");
    expect(current.message).not.toContain("Изменение не сохранилось");
  });

  it("опоздавшее чтение не отыгрывает назад более новую подтверждённую запись", async () => {
    await mount();
    const stale = deferred<DashboardLayoutPreference>();
    getDashboardLayout.mockImplementationOnce(() => stale.promise);

    let reloading: Promise<void> = Promise.resolve();
    await act(async () => {
      reloading = current.reload();
    });
    // Пока чтение в пути, выбор человека доходит до сервера и подтверждается.
    await act(async () => current.apply(hide("agents")));
    await flush();
    expect(server.revision).toBe(5);

    await act(async () => {
      stale.settle(record([], 4));
      await reloading;
    });

    expect(current.layout.hidden).toEqual(["agents"]);
    expect(current.status).toBe("ready");
  });

  it("опоздавшая ошибка чтения не превращает подтверждённую запись в сбой", async () => {
    await mount();
    const stale = deferred<DashboardLayoutPreference>();
    getDashboardLayout.mockImplementationOnce(() => stale.promise);

    let reloading: Promise<void> = Promise.resolve();
    await act(async () => {
      reloading = current.reload();
    });
    await act(async () => current.apply(hide("agents")));
    await flush();
    expect(current.status).toBe("ready");

    // Чтение, начатое до нажатия, обрывается уже после подтверждённой записи.
    await act(async () => {
      stale.fail(new Error("offline"));
      await reloading;
    });

    expect(current.status).toBe("ready");
    expect(current.message).toBe("");
    expect(current.layout.hidden).toEqual(["agents"]);
  });

  it("опоздавшее чтение не стирает объяснение свежего конфликта", async () => {
    await mount();
    const stale = deferred<DashboardLayoutPreference>();
    getDashboardLayout.mockImplementationOnce(() => stale.promise);

    let reloading: Promise<void> = Promise.resolve();
    await act(async () => {
      reloading = current.reload();
    });
    setDashboardLayout.mockImplementationOnce(async () => {
      server = record(["metrics"], 9);
      throw new ApiError(409, "409: Дашборд уже изменился в другом окне.", {
        preference: { ...server },
      });
    });
    await act(async () => current.apply(hide("agents")));
    await flush();
    expect(current.status).toBe("conflict");

    // Ответ на устаревшее чтение приходит последним — и ничего не решает.
    await act(async () => {
      stale.settle(record([], 4));
      await reloading;
    });

    expect(current.status).toBe("conflict");
    expect(current.message).toContain("другом окне");
    expect(current.layout.hidden).toEqual(["metrics"]);
  });

  it("размонтирование во время записи не трогает состояние, а новый экран читает сервер заново", async () => {
    await mount();
    const pending = deferred<DashboardLayoutPreference>();
    setDashboardLayout.mockImplementationOnce(() => pending.promise);

    await act(async () => current.apply(hide("agents")));
    await act(async () => root.unmount());
    container.remove();
    await act(async () => {
      pending.settle(record(["agents"], 5));
    });
    await flush(2);

    server = record(["metrics"], 7);
    await mount();
    expect(current.layout.hidden).toEqual(["metrics"]);
    expect(current.status).toBe("ready");
  });

  it("без ответа сервера выбор остаётся в окне и не уходит записью", async () => {
    getDashboardLayout.mockRejectedValue(new Error("offline"));
    await mount();

    expect(current.status).toBe("error");
    await act(async () => current.apply(hide("agents")));

    expect(setDashboardLayout).not.toHaveBeenCalled();
    expect(current.message).toContain("в этом окне");
  });
});
