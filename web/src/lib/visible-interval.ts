/**
 * Периодический опрос только для видимой вкладки.
 *
 * Скрытая вкладка не тратит запросы: тик пропускается, пока `document.hidden`.
 * Вернувшись на вкладку, владелец сразу видит свежее — по `visibilitychange`
 * опрос выполняется немедленно, не дожидаясь следующего тика. Так же устроен
 * общий опрос ходов чата (`chat-runs.ts`). Первый вызов — на усмотрение
 * вызывающего: здесь только тики и возврат на вкладку.
 */
export function startVisibleInterval(tick: () => void, ms: number): () => void {
  const isHidden = () => typeof document !== "undefined" && document.hidden;
  const timer = window.setInterval(() => {
    if (!isHidden()) tick();
  }, ms);
  const resume = () => {
    if (!isHidden()) tick();
  };
  document.addEventListener("visibilitychange", resume);
  return () => {
    window.clearInterval(timer);
    document.removeEventListener("visibilitychange", resume);
  };
}
