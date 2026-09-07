/**
 * Барьер перед публикацией или откатом справочника ставок.
 *
 * Автосохранение намеренно отложено на две секунды, поэтому нажатие кнопки
 * сразу после ввода обязано сначала записать текущий снимок формы. Держим
 * порядок отдельно от React-компонента, чтобы это денежное правило имело
 * прямой регрессионный тест.
 */

export interface DraftActionBarrierOptions {
  dirty: boolean;
  snapshot: Record<string, unknown>;
  generation: number;
  epoch: number;
  saveSnapshot: (
    snapshot: Record<string, unknown>,
    generation: number,
    epoch: number,
  ) => Promise<void>;
  pendingSave: Promise<void>;
  currentGeneration: () => number;
}

export async function flushDraftBeforeAction({
  dirty,
  snapshot,
  generation,
  epoch,
  saveSnapshot,
  pendingSave,
  currentGeneration,
}: DraftActionBarrierOptions): Promise<void> {
  if (dirty) {
    await saveSnapshot(snapshot, generation, epoch);
  } else {
    await pendingSave;
  }

  if (currentGeneration() !== generation) {
    throw new Error(
      "Данные изменились во время операции; проверьте форму и повторите",
    );
  }
}
