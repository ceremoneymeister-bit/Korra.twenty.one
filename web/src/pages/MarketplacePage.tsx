/**
 * Маркетплейс (K21-097): платные услуги команды Korra — витрина из четырёх
 * билбордов, разделы с позициями и страница позиции. Заказ — в чате с нами.
 * Бесплатное живёт на своих местах: агенты — «+» в «Агентах», навыки —
 * «Навыки», подключения — «Ключи и доступы». Состав — в `marketplace-catalog`.
 * Пока `MARKETPLACE_IN_DEVELOPMENT`, любая страница маркетплейса закрыта
 * карточкой «В разработке».
 */

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router";
import {
  MARKETPLACE_IN_DEVELOPMENT,
  MARKETPLACE_SECTIONS,
  findItem,
  findSection,
  type MarketplaceSection,
} from "@/lib/marketplace-catalog";
import { Billboard } from "@/components/marketplace/Billboard";
import { CatalogItemCard, ExampleCard } from "@/components/marketplace/Cards";
import { ItemDetails } from "@/components/marketplace/ItemDetails";
import { BackLink, OrderLink, Price, QUIET_BUTTON_CLASS } from "@/components/marketplace/ui";

const GRID_CLASS = "grid gap-3 md:grid-cols-2 lg:grid-cols-3";

export default function MarketplacePage({ inDevelopment = MARKETPLACE_IN_DEVELOPMENT }: { inDevelopment?: boolean }) {
  return inDevelopment ? <InDevelopment><MarketplaceRoute /></InDevelopment> : <MarketplaceRoute />;
}

/**
 * Страница видна размытой и ни на что не нажимается — ни мышью, ни с клавиатуры,
 * ни для экранного диктора; карточка держится в середине видимой части экрана.
 */
function InDevelopment({ children }: { children: ReactNode }) {
  return (
    <div className="relative" data-marketplace-soon>
      <div inert aria-hidden className="pointer-events-none select-none blur-[5px]" data-marketplace-preview>
        {children}
      </div>
      <div className="absolute inset-0 flex items-start justify-center px-3">
        <div
          className="sticky grid w-full max-w-sm gap-2 bg-[var(--neo-surface)] p-6 text-center shadow-[var(--neo-depth-2)]"
          style={{ top: "max(1rem, calc(50dvh - 8.5rem))", borderRadius: "var(--neo-radius-card)" }}
        >
          <h2 className="text-2xl font-semibold leading-tight">В разработке</h2>
          <p className="text-base leading-relaxed text-balance text-[var(--neo-text-secondary)]">
            Скоро здесь появятся первые услуги от команды Korra.
          </p>
        </div>
      </div>
    </div>
  );
}

function MarketplaceRoute() {
  const { section: sectionId, item: itemId } = useParams();

  if (!sectionId) return <Showcase />;
  const section = findSection(sectionId);
  if (!section) {
    return <NotFound message="Такого раздела нет" links={[{ to: "/marketplace", label: "Вернуться в маркетплейс" }]} />;
  }
  if (itemId) {
    const item = findItem(section, itemId);
    if (!item) {
      return (
        <NotFound
          message="Такой позиции нет"
          links={[
            { to: `/marketplace/${section.id}`, label: `Раздел «${section.title}»` },
            { to: "/marketplace", label: "Вернуться в маркетплейс" },
          ]}
        />
      );
    }
    return (
      <Shell>
        <div className="grid gap-1">
          <BackLink to={`/marketplace/${section.id}`}>{section.title}</BackLink>
        </div>
        <ItemDetails item={item} section={section} />
      </Shell>
    );
  }
  return <SectionView section={section} />;
}

function Shell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const container = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    // Переход внутри маркетплейса открывает новую страницу с начала: прокручивается
    // основной контейнер панели, а не окно, иначе на телефоне позиция открывалась
    // у конца — без названия, цены и «Заказать». Фокус — на заголовок страницы.
    const scroller = container.current?.closest("main");
    if (scroller) scroller.scrollTop = 0;
    container.current?.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
  }, [pathname]);
  return (
    <div ref={container} className="mx-auto grid w-full max-w-6xl content-start gap-5 px-2 py-4 sm:p-4 [&_h2]:outline-none" data-marketplace>
      {children}
    </div>
  );
}

function NotFound({ message, links }: { message: string; links: { to: string; label: string }[] }) {
  return (
    <Shell>
      <div className="grid justify-items-start gap-3 py-6" role="status">
        <h2 tabIndex={-1} className="text-2xl font-semibold">{message}</h2>
        <div className="flex flex-wrap gap-3">
          {links.map((link) => <Link key={link.to} to={link.to} className={QUIET_BUTTON_CLASS}>{link.label}</Link>)}
        </div>
      </div>
    </Shell>
  );
}

function Showcase() {
  return (
    <Shell>
      <h2 tabIndex={-1} className="hidden text-[28px] font-semibold leading-tight lg:block">Маркетплейс</h2>
      <div className="grid gap-4 lg:grid-cols-2 lg:gap-5">
        {MARKETPLACE_SECTIONS.map((section) => <Billboard key={section.id} section={section} />)}
      </div>
    </Shell>
  );
}

function SectionView({ section }: { section: MarketplaceSection }) {
  return (
    <Shell>
      <div className="grid gap-3">
        <BackLink to="/marketplace">Маркетплейс</BackLink>
        <div className="grid gap-2">
          <h2 tabIndex={-1} className="text-2xl font-semibold leading-tight sm:text-[32px]">{section.title}</h2>
          <p className="max-w-[62ch] text-base leading-relaxed text-[var(--neo-text-secondary)]">{section.promise}</p>
        </div>
      </div>

      {section.cta && (
        <div
          className="flex flex-col gap-4 bg-[var(--neo-surface)] p-5 shadow-[var(--neo-depth-2)] sm:flex-row sm:items-center sm:justify-between sm:p-6"
          style={{ borderRadius: "var(--neo-radius-card)" }}
          data-section-cta
        >
          <Price price={section.price_anchor} large />
          <OrderLink subject={{ kind: "automation" }} label={section.cta.label} className="sm:min-w-[220px]" />
        </div>
      )}

      {section.groups.map((group) => (
        <section key={group.id} className="grid gap-3" aria-labelledby={`group-${section.id}-${group.id}`}>
          <div className="grid gap-1">
            <h3 id={`group-${section.id}-${group.id}`} className="text-xl font-semibold">{group.title}</h3>
            <p className="max-w-[70ch] text-sm leading-relaxed text-[var(--neo-text-secondary)]">{group.lead}</p>
          </div>
          <div className={GRID_CLASS}>
            {group.items.map((item) =>
              item.industry && !item.price
                ? <ExampleCard key={item.id} item={item} />
                : <CatalogItemCard key={item.id} item={item} section={section} />,
            )}
          </div>
        </section>
      ))}
    </Shell>
  );
}
