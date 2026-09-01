/**
 * Разбор GFM-таблиц для лёгкого Markdown-рендерера.
 *
 * Живёт отдельно от компонента: так функции можно покрыть тестами и не ломать
 * fast refresh, который требует, чтобы файл компонента экспортировал только
 * компоненты.
 */

/** Разбить строку таблицы на ячейки, не считая экранированный `\|` разделителем. */
export function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells: string[] = [];
  let current = "";
  for (let index = 0; index < trimmed.length; index += 1) {
    const char = trimmed[index];
    if (char === "\\" && trimmed[index + 1] === "|") {
      current += "|";
      index += 1;
      continue;
    }
    if (char === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  cells.push(current.trim());
  return cells;
}

/** Строка вида `|---|:--:|` — та, что отделяет шапку таблицы от данных. */
export function isTableDelimiter(line: string): boolean {
  if (!line.includes("-") || !line.includes("|")) return false;
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{1,}:?$/.test(cell));
}
