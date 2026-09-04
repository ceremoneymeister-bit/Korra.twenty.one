// Node 26 объявил собственные globalThis.localStorage / sessionStorage. Без
// флага `--localstorage-file` они пустые: чтение отдаёт undefined и печатает
// ExperimentalWarning. Vitest в окружении jsdom делает globalThis самим окном,
// а свойства окна переносит только для ключей, которых там ещё нет, — эти уже
// «заняты» самим Node. Итог: в тестах есть document и location, но нет
// localStorage, и любой тест, который чистит его в beforeEach, падает с
// «Cannot read properties of undefined (reading 'clear')».
//
// На Node 24 такого ключа в globalThis нет, jsdom проставляет своё хранилище и
// всё зелено — поэтому расхождение годами жило только в CI, где Node 26.
//
// Ставим совместимую реализацию Storage и только тогда, когда рабочей нет:
// на Node 24 и в браузере этот файл ничего не меняет. В окружении "node"
// (файлы без директивы @vitest-environment jsdom) window отсутствует, и блок
// не выполняется — там хранилища и не ждут.

class MemoryStorage implements Storage {
  #items = new Map<string, string>();

  get length(): number {
    return this.#items.size;
  }

  clear(): void {
    this.#items.clear();
  }

  getItem(key: string): string | null {
    const value = this.#items.get(String(key));
    return value === undefined ? null : value;
  }

  key(index: number): string | null {
    return [...this.#items.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.#items.delete(String(key));
  }

  setItem(key: string, value: string): void {
    this.#items.set(String(key), String(value));
  }

  [name: string]: unknown;
}

function isUsable(candidate: unknown): candidate is Storage {
  if (!candidate || typeof candidate !== "object") return false;
  const storage = candidate as Partial<Storage>;
  return typeof storage.getItem === "function" && typeof storage.clear === "function";
}

const scope = globalThis as Record<string, unknown> & { window?: unknown };

if (typeof scope.window !== "undefined") {
  for (const key of ["localStorage", "sessionStorage"] as const) {
    let current: unknown;
    try {
      // Обращение к заглушке Node печатает ExperimentalWarning — оно
      // безобидно, но пусть не роняет запуск, если поведение сменится.
      current = scope[key];
    } catch {
      current = undefined;
    }
    if (!isUsable(current)) {
      Object.defineProperty(globalThis, key, {
        value: new MemoryStorage(),
        configurable: true,
        writable: true,
      });
    }
  }
}

export {};
