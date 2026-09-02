import { describe, expect, it } from 'vitest'

import { formatSessionPruneResult } from './session-prune'

describe('formatSessionPruneResult', () => {
  it('reports open sessions skipped by the prune safety guard', () => {
    expect(formatSessionPruneResult({ removed: 0, skipped_open: 2 })).toBe(
      'Удалено диалогов: 0. Пропущено открытых диалогов: 2; очистка удаляет только завершённые диалоги.'
    )
  })

  it('keeps the existing success message when nothing was skipped', () => {
    expect(formatSessionPruneResult({ removed: 1, skipped_open: 0 })).toBe('Удалено диалогов: 1')
  })
})
