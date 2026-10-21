import { Link } from "react-router";
import { ArrowUpRight } from "lucide-react";
import { findItem, type MarketplaceSection } from "@/lib/marketplace-catalog";
import { iconFor } from "./ui";

const CHIP_CLASS = "inline-flex min-h-[36px] items-center rounded-full bg-[var(--neo-surface)] px-3.5 text-sm font-medium shadow-[var(--neo-inset-compact)]";
/**
 * Видимая высота 36 px, область нажатия — 44 px; зазор между рядами 8 px, области не перекрываются.
 * В пикселях, а не rem: корневой шрифт панели 13,5 px, и `-inset-y-1` давал только 42,75 px.
 */
const CHIP_HIT_CLASS = "before:absolute before:inset-x-0 before:-inset-y-[4px]";

/**
 * Витринный билборд раздела. Заголовок растянут на всю карточку и ведёт в
 * раздел; примеры поверх него ведут сразу на страницы своих позиций. Крупная
 * композиция из иконки — только с планшета: на телефоне она съедала экран.
 */
export function Billboard({ section }: { section: MarketplaceSection }) {
  const Icon = iconFor(section.icon);
  return (
    <article
      data-billboard={section.id}
      className="group relative isolate flex flex-col gap-5 overflow-hidden bg-[var(--neo-surface)] p-5 shadow-[var(--neo-depth-2)] transition-[box-shadow,transform] duration-300 hover:-translate-y-0.5 hover:shadow-[var(--neo-depth-3)] sm:min-h-[270px] sm:p-7"
      style={{ borderRadius: "var(--neo-radius-card)" }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -right-16 -top-16 -z-10 size-56 sm:-right-6 sm:-top-6 sm:size-80"
        style={{ background: "radial-gradient(circle at center, color-mix(in srgb, var(--primary) 30%, transparent), transparent 68%)" }}
      />
      <div aria-hidden className="pointer-events-none absolute right-7 top-7 hidden size-40 sm:block">
        <span className="absolute inset-0 rounded-full shadow-[var(--neo-inset)]" />
        <span className="absolute inset-[18%] flex items-center justify-center rounded-full bg-[var(--neo-surface)] shadow-[var(--neo-depth-3)] transition-transform duration-500 group-hover:scale-105">
          <Icon className="size-1/2 text-[var(--neo-text-primary)]" strokeWidth={1.4} />
        </span>
        <span className="absolute -left-1 bottom-6 size-8 rounded-full bg-[var(--neo-surface)] shadow-[var(--neo-depth-2)]" />
      </div>

      <div className="grid gap-2.5 sm:max-w-[68%]">
        <h3 className="text-2xl font-semibold leading-tight sm:text-[30px]">
          <Link
            to={`/marketplace/${section.id}`}
            className="outline-none focus-visible:shadow-none! after:absolute after:inset-0 after:rounded-[var(--neo-radius-card)] focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-primary/60"
          >
            {section.title}
          </Link>
        </h3>
        <p className="text-base leading-relaxed text-[var(--neo-text-secondary)]">{section.promise}</p>
      </div>

      <div className="mt-auto grid gap-4">
        <ul className="flex flex-wrap gap-[8px]" aria-label="Например">
          {section.examples.map((example) => {
            const item = findItem(section, example.item);
            return (
              <li key={example.item}>
                {item ? (
                  <Link
                    to={`/marketplace/${section.id}/${item.id}`}
                    className={`${CHIP_CLASS} ${CHIP_HIT_CLASS} relative z-10 outline-none transition-colors hover:bg-primary/15 focus-visible:ring-2 focus-visible:ring-primary/50`}
                    data-example-link={item.id}
                  >
                    {example.label}
                  </Link>
                ) : (
                  <span className={`${CHIP_CLASS} text-[var(--neo-text-secondary)]`}>{example.label}</span>
                )}
              </li>
            );
          })}
        </ul>
        <div className="flex items-center justify-between gap-3">
          <p className="text-lg font-semibold leading-snug" data-price-anchor>{section.price_anchor}</p>
          <span
            aria-hidden
            className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-[var(--neo-depth-1)] transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
          >
            <ArrowUpRight className="size-5" />
          </span>
        </div>
      </div>
    </article>
  );
}
