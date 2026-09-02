/**
 * AgentWorkbenchPage — рабочее место команды агентов.
 *
 * Один экран, несколько агентов. Каждая вкладка — самостоятельный чат,
 * адресованный своему профилю Korra. Главное свойство экрана: переключение
 * вкладки НЕ прерывает соседний разговор: пользователь может вернуться позже,
 * когда ответ уже дописан до конца.
 *
 * Отсюда единственное архитектурное требование: все чаты смонтированы
 * одновременно и постоянно, неактивные спрятаны через `display: none`.
 * Размонтирование убило бы вместе с DOM и SSE-поток: `useChatStream` в
 * unmount-эффекте зовёт `abortController.abort()`, то есть вкладка,
 * переключённая на середине ответа, потеряла бы ответ целиком.
 */

import { useCallback, useEffect, useState, type KeyboardEvent } from "react";
import { useLocation, useSearchParams } from "react-router";

import BubbleChatPage from "@/pages/BubbleChatPage";
import { cn } from "@/lib/utils";
import { MAIN_AGENT_TAB } from "@/lib/agent-tabs";
import { useAgentTabs } from "@/hooks/useAgentTabs";

/* ------------------------------------------------------------------ */
/*  AgentWorkbenchPage (default export)                                */
/* ------------------------------------------------------------------ */

export default function AgentWorkbenchPage() {
  // Состав вкладок — реальные профили контура (см. lib/agent-tabs.ts):
  // главная «Корра» есть всегда, остальные приезжают из /api/profiles и
  // подхватываются без перезагрузки страницы.
  const { tabs, refresh } = useAgentTabs();
  const [selectedId, setActiveId] = useState<string>(MAIN_AGENT_TAB.profile);
  // Профиль удалили, пока его вкладка была выбрана, — показываем главную,
  // иначе экран остался бы без единой панели. Производное значение, а не
  // эффект: лишний каскад рендеров тут ни к чему.
  const activeId = tabs.some((tab) => tab.profile === selectedId)
    ? selectedId
    : MAIN_AGENT_TAB.profile;
  const [streamingByProfile, setStreamingByProfile] = useState<
    Record<string, boolean>
  >({});
  // Адресный черновик предназначен ОДНОМУ агенту. Держим его здесь и раздаём
  // адресно: сам чат `?draft=` не читает — несколько
  // смонтированных экземпляра приняли бы его каждый на свой счёт.
  const [draftByProfile, setDraftByProfile] = useState<Record<string, string>>(
    {},
  );
  const [searchParams, setSearchParams] = useSearchParams();
  const { pathname } = useLocation();

  // Экран смонтирован постоянно (см. App.tsx), поэтому возврат на него — это
  // не монтирование, а смена маршрута. Владелец создал профиль на соседнем
  // экране и вернулся сюда — вкладка должна быть уже здесь, а не через
  // полминуты опроса.
  useEffect(() => {
    if ((pathname.replace(/\/$/, "") || "/") === "/agents") void refresh();
  }, [pathname, refresh]);

  useEffect(() => {
    const agent = searchParams.get("agent")?.trim();
    const draft = searchParams.get("draft")?.trim();
    if (!agent) return;
    if (!tabs.some((tab) => tab.profile === agent)) {
      // Диплинк на профиль, которого в составе ещё нет (только что создан):
      // перечитываем список и оставляем параметры до его появления.
      void refresh();
      return;
    }
    setActiveId(agent);
    if (draft) setDraftByProfile((previous) => ({ ...previous, [agent]: draft }));
    // Параметры снимаем сразу: иначе возврат на экран назад-вперёд подставил
    // бы тот же текст поверх уже набранного.
    setSearchParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        next.delete("agent");
        next.delete("draft");
        return next;
      },
      { replace: true },
    );
  }, [searchParams, setSearchParams, tabs, refresh]);

  const clearDraft = useCallback((profile: string) => {
    setDraftByProfile((previous) => {
      if (previous[profile] === undefined) return previous;
      const next = { ...previous };
      delete next[profile];
      return next;
    });
  }, []);

  // Стабильная ссылка + выход без записи, когда значение не изменилось. Колбэк
  // зовётся из эффекта дочернего чата, и новый объект состояния на каждый
  // вызов означал бы бесконечный цикл рендеров.
  const handleStreamingChange = useCallback(
    (profile: string, streaming: boolean) => {
      setStreamingByProfile((previous) =>
        previous[profile] === streaming
          ? previous
          : { ...previous, [profile]: streaming },
      );
    },
    [],
  );

  // role="tablist" обещает управление стрелками — выполняем обещание, иначе
  // роль врёт скринридеру. Фокус ведём за активной вкладкой: панели всё равно
  // смонтированы, переключение бесплатно.
  const onTabKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const delta =
        event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (delta === 0 || tabs.length === 0) return;
      event.preventDefault();
      const currentIndex = tabs.findIndex((tab) => tab.profile === activeId);
      const index = currentIndex >= 0 ? currentIndex : 0;
      const next = tabs[(index + delta + tabs.length) % tabs.length];
      setActiveId(next.profile);
      // Фокус переносим на кнопку, к которой только что уехало выделение.
      window.requestAnimationFrame(() => {
        document.getElementById(`agent-tab-${next.profile}`)?.focus();
      });
    },
    [activeId, tabs],
  );

  return (
    // Ту же полную высоту, что и у одиночного чата, даёт обёртка в App.tsx
    // (маршрут /agents получает `flex flex-1 flex-col` и `pb-0`).
    <div className="flex h-full min-h-0 flex-col">
      {/* Полоса вкладок тянется от края до края — отрицательные поля гасят
          горизонтальный padding обёртки, внутренние возвращают его тексту. */}
      <div className="shrink-0 -mx-3 border-b border-border px-3 sm:-mx-6 sm:px-6">
        <div
          role="tablist"
          aria-label="Агенты"
          onKeyDown={onTabKeyDown}
          className="flex items-stretch gap-1 overflow-x-auto"
        >
          {tabs.map((tab) => {
            const active = tab.profile === activeId;
            const streaming = streamingByProfile[tab.profile] === true;
            return (
              <button
                key={tab.profile}
                id={`agent-tab-${tab.profile}`}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls={`agent-panel-${tab.profile}`}
                tabIndex={active ? 0 : -1}
                // Описание роли — подсказкой: на вкладке имя, не абзац.
                title={tab.description}
                // Точка на вкладке видна глазом, но не слышна: состояние
                // проговариваем в имени кнопки.
                aria-label={
                  streaming ? `${tab.label} — агент отвечает` : undefined
                }
                onClick={() => setActiveId(tab.profile)}
                className={cn(
                  // 44 px по высоте и sentence case — канон продукта:
                  // вкладка агента это имя человека за работой, а не
                  // системный ярлык.
                  "relative flex min-h-[44px] items-center gap-2 px-4 py-2.5",
                  "font-sans text-[0.9375rem] leading-snug",
                  "whitespace-nowrap cursor-pointer transition-colors",
                  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
                  active
                    ? "font-semibold text-midground"
                    : "text-text-secondary hover:text-text-primary",
                )}
              >
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-0 bottom-0 h-px bg-midground"
                    style={{ mixBlendMode: "plus-lighter" }}
                  />
                )}
                <span>{tab.label}</span>
                {streaming && (
                  <span
                    aria-hidden
                    title="Агент отвечает"
                    className="size-1.5 shrink-0 rounded-full bg-primary animate-pulse"
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {tabs.map((tab) => (
          <div
            key={tab.profile}
            id={`agent-panel-${tab.profile}`}
            role="tabpanel"
            aria-labelledby={`agent-tab-${tab.profile}`}
            // display:none, а НЕ снятие с монтирования — на этом держится
            // весь экран (см. шапку файла).
            style={{ display: tab.profile === activeId ? undefined : "none" }}
            className="flex min-h-0 flex-1 flex-col"
          >
            <BubbleChatPage
              agentProfile={tab.profile}
              onStreamingChange={handleStreamingChange}
              draft={draftByProfile[tab.profile] ?? null}
              onDraftConsumed={() => clearDraft(tab.profile)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
