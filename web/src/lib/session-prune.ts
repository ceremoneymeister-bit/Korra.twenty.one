interface SessionPruneResult {
  removed: number
  skipped_open: number
}

export function formatSessionPruneResult(result: SessionPruneResult): string {
  const removed = `Удалено диалогов: ${result.removed}`
  if (!result.skipped_open) return removed

  return `${removed}. Пропущено открытых диалогов: ${result.skipped_open}; очистка удаляет только завершённые диалоги.`
}
