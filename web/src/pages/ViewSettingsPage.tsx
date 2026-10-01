/**
 * «Настройки → Вид»: тема и «Как переключать агентов» на телефоне.
 *
 * Тема здесь та же, что у переключателя внизу меню, — одна на установку.
 * Способ переключать агентов — личный выбор человека (сервер хранит его по
 * проверенной сессии), и действует он только ниже `lg`: на компьютере агенты
 * остаются вкладками.
 */

import { useEffect, useId, type ComponentType } from "react";
import { useStore } from "@nanostores/react";
import { Check } from "lucide-react";

import { $agentsView, chooseAgentsMobileMode, loadAgentsView, type AgentsMobileMode } from "@/lib/agents-view";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { useTheme } from "@/themes";

import "@/components/agents/mobile/agents-mobile.css";

function MiniTabs() {
  return (
    <span className="k-mini" aria-hidden>
      <span className="k-mini__top"><span className="k-mini__bar" /><span className="k-mini__chip" /></span>
      <span className="k-mini__tray">
        <span className="k-mini__av is-on" />
        <span className="k-mini__av is-run" />
        <span className="k-mini__av is-dec" />
        <span className="k-mini__av" />
        <span className="k-mini__av" />
      </span>
      <span className="k-mini__msg is-bot" />
      <span className="k-mini__msg is-user" />
      <span className="k-mini__msg is-bot" />
      <span className="k-mini__cmp" />
    </span>
  );
}

function MiniRow({ state }: { state?: "dec" | "run" }) {
  return (
    <span className="k-mini__row">
      <span className={`k-mini__av is-lg${state ? ` is-${state}` : ""}`} />
      <span className="k-mini__lines"><span className="k-mini__bar" /><span className="k-mini__bar is-short" /></span>
    </span>
  );
}

function MiniList() {
  return (
    <span className="k-mini" aria-hidden>
      <span className="k-mini__h" />
      <span className="k-mini__sec" />
      <MiniRow state="dec" />
      <MiniRow state="run" />
      <span className="k-mini__sec" />
      <MiniRow />
      <MiniRow />
      <MiniRow />
    </span>
  );
}

const MODES: Array<{ value: AgentsMobileMode; title: string; description: string; preview: ComponentType }> = [
  {
    value: "tabs",
    title: "Вкладки",
    description: "Агенты кружками вверху. Другой агент — одно касание.",
    preview: MiniTabs,
  },
  {
    value: "list",
    title: "Список агентов",
    description: "Сначала все агенты с последним разговором. Агент — на весь экран.",
    preview: MiniList,
  },
];

export default function ViewSettingsPage() {
  const view = useStore($agentsView);
  const { saveState: themeSave, saveError: themeError } = useTheme();
  const themeId = useId();
  const modeId = useId();

  useEffect(() => { void loadAgentsView(); }, []);

  const status = view.status === "saved"
    ? <><Check size={16} aria-hidden /><span><b>Сохранено.</b> Так будет на всех ваших телефонах.</span></>
    : view.status === "saving"
      ? <span>Сохраняем…</span>
      : view.status === "error" || view.status === "conflict"
        ? <span>{view.message}</span>
        : <span>Выбор сохранится сразу и будет работать на всех ваших телефонах.</span>;

  return (
    <div className="k-agents k-view" data-view-settings>
      <section className="k-view__sec" aria-labelledby={themeId}>
        <h2 id={themeId}>Тема</h2>
        <ThemeSwitcher labeled />
        {themeSave === "error" && (
          <p className="k-view__saved is-problem" role="alert">{themeError || "Тема не сохранилась. Повторите выбор."}</p>
        )}
      </section>

      <section className="k-view__sec" aria-labelledby={modeId}>
        <h2 id={modeId}>Как переключать агентов</h2>
        <p className="k-view__hint">На телефоне. Поменять можно в любой момент.</p>
        <div className="k-modes" role="radiogroup" aria-labelledby={modeId}>
          {MODES.map(({ value, title, description, preview: Preview }) => {
            const checked = view.mode === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={checked}
                className="k-mode"
                data-agents-mode={value}
                onClick={() => chooseAgentsMobileMode(value)}
              >
                <Preview />
                <span className="k-mode__t">
                  <span className="k-radio" aria-hidden>{checked && <Check size={14} />}</span>
                  {title}
                </span>
                <span className="k-mode__d">{description}</span>
              </button>
            );
          })}
        </div>
        <p
          className={`k-view__saved${view.status === "error" || view.status === "conflict" ? " is-problem" : ""}`}
          role="status"
          aria-live="polite"
        >
          {status}
        </p>
        <p className="k-view__note">На компьютере агенты остаются вкладками — там ничего не меняется.</p>
      </section>
    </div>
  );
}
