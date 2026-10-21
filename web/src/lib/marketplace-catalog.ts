/**
 * Содержание витрины «Маркетплейс»: разделы, группы, позиции, цены и шаги
 * заказа. Страницы ничего не знают о конкретных позициях — состав меняется
 * правкой этих данных. Поля названы как в исходном каталоге (`price_anchor`,
 * `price_note`, `order_steps`), чтобы правки переносились без переименований.
 * Цены — предложение для оценки владельца, не утверждённый прайс.
 *
 * Здесь только платное: бесплатных агентов добавляют через «+» в «Агентах»,
 * навыки — в «Навыках», подключения — в «Ключах и доступах» (решение 19.10).
 */

import type { MarketplaceIconName } from "@/lib/marketplace-icons";

export type MarketplaceSectionId = "agents" | "skills" | "services" | "automation";

/** Видео-презентация позиции; пока в данных не появится, блок не показывается. */
export interface MarketplacePresentation {
  src: string;
  poster?: string;
  caption?: string;
}

/** Настоящий результат работы: картинка с подписью; пока нет данных — блока нет. */
export interface MarketplaceResult {
  title: string;
  image?: string;
  caption?: string;
}

export interface MarketplaceItem {
  id: string;
  icon: MarketplaceIconName;
  name: string;
  tagline: string;
  price?: string;
  price_note?: string;
  does?: string[];
  gives?: string[];
  needs?: string[];
  /** Пример из практики (автоматизация): без цены и без своей страницы. */
  industry?: string;
  presentation?: MarketplacePresentation;
  results?: MarketplaceResult[];
}

export interface MarketplaceGroup {
  id: string;
  title: string;
  lead: string;
  items: MarketplaceItem[];
}

/** Пример на билборде: подпись и позиция раздела, на страницу которой он ведёт. */
export interface MarketplaceExample {
  item: string;
  label: string;
}

export interface MarketplaceSection {
  id: MarketplaceSectionId;
  title: string;
  promise: string;
  price_anchor: string;
  examples: MarketplaceExample[];
  icon: MarketplaceIconName;
  /** Общая кнопка раздела вместо кнопок на карточках. */
  cta?: { label: string };
  groups: MarketplaceGroup[];
}

export const MARKETPLACE_ORDER_CONTACT = "ceremoneymeister";

/**
 * Витрина пока в разработке (решение 20.10): страница видна размытой, поверх —
 * карточка «В разработке», заказать ничего нельзя. Первые платные позиции
 * откроются в 0.21.21 — тогда здесь `false`.
 */
export const MARKETPLACE_IN_DEVELOPMENT = true;

export const MARKETPLACE_SECTIONS: MarketplaceSection[] = [
    {
      id: "agents",
      title: "Агенты под ваш бизнес",
      promise: "Специалист с вашими правилами, примерами и подключениями. Настраиваем и вместе получаем первый результат.",
      price_anchor: "от 4 990 ₽",
      examples: [
        { item: "carousels", label: "Карусели" },
        { item: "meeting-notes", label: "Протоколы встреч" },
        { item: "sales-control", label: "Контроль продаж" }
      ],
      icon: "Users",
      groups: [
        {
          id: "custom",
          title: "Специалисты",
          lead: "Цена разовая: настройка под вас и первый результат.",
          items: [
            {
              id: "carousels",
              icon: "GalleryHorizontal",
              name: "Карусели для Instagram и VK",
              tagline: "Серии слайдов в стиле вашего бренда: по вашим референсам, с текстом прямо на слайдах.",
              price: "9 900 ₽",
              price_note: "разово, с настройкой",
              does: [
                "Собирает карусель из темы, статьи или готового текста",
                "Держит ваш стиль: цвета, шрифты и подачу берёт из референсов",
                "Рисует надписи на слайдах без ручной вёрстки",
                "Пишет подпись к посту и предлагает варианты обложки"
              ],
              gives: [
                "Агента-дизайнера, настроенного под ваш бренд",
                "Ваши референсы и правила стиля в его памяти",
                "Первую карусель, которую доводим вместе"
              ],
              needs: [
                "Подписка ChatGPT для генерации изображений"
              ]
            },
            {
              id: "reels",
              icon: "Clapperboard",
              name: "Монтаж рилсов",
              tagline: "Из сырой записи — готовые вертикальные ролики с субтитрами и обложкой.",
              price: "14 900 ₽",
              price_note: "разово, с настройкой",
              does: [
                "Нарезает длинную запись на короткие ролики",
                "Убирает паузы и добавляет субтитры",
                "Собирает ролик в формате 9:16",
                "Готовит обложку и подпись к публикации"
              ],
              gives: [
                "Агента-монтажёра с вашим стилем субтитров и обложек",
                "Шаблон ролика под ваш аккаунт",
                "Первые ролики из вашей записи"
              ],
              needs: [
                "Запись, которую нужно смонтировать"
              ]
            },
            {
              id: "smm",
              icon: "Megaphone",
              name: "SMM-менеджер",
              tagline: "Контент-план и посты вашим голосом — к нужной дате, на ваше согласование.",
              price: "9 900 ₽",
              price_note: "разово, с настройкой",
              does: [
                "Составляет контент-план на месяц",
                "Пишет посты в вашем тоне по примерам",
                "Присылает готовые посты на согласование к нужной дате",
                "Подбирает темы из ваших новостей и вопросов клиентов"
              ],
              gives: [
                "Агента с вашим голосом и темами",
                "Контент-план на первый месяц",
                "Расписание подготовки постов в «Задачах»"
              ],
              needs: [
                "Примеры ваших постов"
              ]
            },
            {
              id: "sales-control",
              icon: "TrendingUp",
              name: "Контроль отдела продаж",
              tagline: "Каждое утро — сводка по воронке, зависшие сделки и просроченные задачи из вашей CRM.",
              price: "14 900 ₽",
              price_note: "разово, с настройкой",
              does: [
                "Находит сделки без движения и просроченные задачи",
                "Собирает утреннюю сводку руководителю",
                "Отвечает на вопросы по воронке и по конкретной сделке"
              ],
              gives: [
                "Агента, который знает вашу воронку и менеджеров",
                "Утреннюю сводку по расписанию",
                "Карточку «Продажи» на дашборде"
              ],
              needs: [
                "amoCRM или Битрикс24 на тарифе с доступом по API"
              ]
            },
            {
              id: "meeting-notes",
              icon: "NotebookPen",
              name: "Протоколы встреч",
              tagline: "Расшифровывает записи созвонов и выдаёт протокол: решения, задачи, сроки.",
              price: "4 990 ₽",
              price_note: "разово, с настройкой",
              does: [
                "Расшифровывает аудио и видео встреч",
                "Выделяет решения, задачи и ответственных",
                "Готовит протокол в вашем формате",
                "Напоминает о сроках по задачам из протокола"
              ],
              gives: [
                "Агента с вашим шаблоном протокола",
                "Протокол первой встречи"
              ],
              needs: [
                "Запись встречи"
              ]
            },
            {
              id: "client-support",
              icon: "MessagesSquare",
              name: "Консультант для ваших клиентов",
              tagline: "Отвечает клиентам по вашей базе знаний и передаёт вам заявки.",
              price: "14 900 ₽",
              price_note: "разово, с настройкой",
              does: [
                "Отвечает на частые вопросы по вашим материалам",
                "Узнаёт, что нужно клиенту, и собирает заявку",
                "Вопрос без ответа в материалах или просьбу позвать человека передаёт вам"
              ],
              gives: [
                "Бота для клиентов с вашей базой знаний",
                "Правила: что отвечать самому, что передавать вам",
                "Проверку на ваших настоящих вопросах"
              ],
              needs: [
                "Ваши материалы: прайс, условия, ответы на частые вопросы"
              ]
            }
          ]
        }
      ]
    },
    {
      id: "skills",
      title: "Навыки для ваших агентов",
      promise: "Научим агентов вашему способу работы или подключим их к вашему сервису.",
      price_anchor: "от 4 990 ₽",
      examples: [
        { item: "personal-skill", label: "Личный навык" },
        { item: "service-skill", label: "Подключение к сервису" }
      ],
      icon: "Sparkles",
      groups: [
        {
          id: "custom-skill",
          title: "Навык под вашу задачу",
          lead: "Опишите, как вы делаете работу, — оформим это в навык, и агент будет делать так же.",
          items: [
            {
              id: "personal-skill",
              icon: "PenLine",
              name: "Личный навык",
              tagline: "Ваш способ работы — правила, шаблоны и проверки — в одном навыке для агента.",
              price: "от 4 990 ₽",
              price_note: "разово",
              does: [
                "Записываем ваш порядок работы и примеры хорошего результата",
                "Добавляем шаблоны документов и проверки перед сдачей",
                "Проверяем навык на ваших настоящих задачах"
              ],
              gives: [
                "Навык в вашей Korra, который можно дать любому агенту"
              ],
              needs: [
                "Пример задачи и результат, который вас устраивает"
              ]
            },
            {
              id: "service-skill",
              icon: "Plug",
              name: "Навык с подключением к вашему сервису",
              tagline: "Агент научится работать с вашей CRM, 1С, сайтом или кабинетом поставщика.",
              price: "от 14 900 ₽",
              price_note: "разово",
              does: [
                "Подключаем сервис через его API или браузер",
                "Учим агента читать нужные данные и выполнять разрешённые действия",
                "Проверяем на ваших данных и показываем, что получилось"
              ],
              gives: [
                "Навык и подключение, которые остаются в вашей Korra"
              ],
              needs: [
                "Доступ к сервису"
              ]
            }
          ]
        }
      ]
    },
    {
      id: "services",
      title: "Сервисы",
      promise: "Видео, голос и музыка для ваших агентов. Оплата в рублях, без зарубежной карты.",
      price_anchor: "от 990 ₽ в месяц",
      examples: [
        { item: "video-gen", label: "Видео" },
        { item: "voice-gen", label: "Озвучка" },
        { item: "music-gen", label: "Музыка" }
      ],
      icon: "Wand2",
      groups: [
        {
          id: "generation",
          title: "Генерация",
          lead: "Подключаем сервис к вашей Korra, оплата — нам в рублях. Агенты пользуются им по вашей просьбе.",
          items: [
            {
              id: "video-gen",
              icon: "Video",
              name: "Видео по описанию",
              tagline: "Короткие ролики и анимация из текста или фото — для рекламы и соцсетей.",
              price: "от 1 990 ₽",
              price_note: "в месяц",
              does: [
                "Ролик из текстового описания",
                "Анимация вашего фото или товара",
                "Вертикальный и горизонтальный формат"
              ],
              gives: [
                "Пакет на месяц — объём согласуем под вашу задачу до оплаты",
                "Агенты делают видео по вашей просьбе"
              ]
            },
            {
              id: "voice-gen",
              icon: "Mic",
              name: "Озвучка и голос",
              tagline: "Естественный голос для роликов, презентаций и ответов агента.",
              price: "от 990 ₽",
              price_note: "в месяц",
              does: [
                "Озвучка текста на русском и других языках",
                "Выбор голоса под ваш бренд",
                "Голос для роликов и презентаций"
              ],
              gives: [
                "Пакет на месяц — объём согласуем под вашу задачу до оплаты"
              ]
            },
            {
              id: "music-gen",
              icon: "Music",
              name: "Музыка",
              tagline: "Музыка для роликов и короткие джинглы по описанию.",
              price: "от 990 ₽",
              price_note: "в месяц",
              does: [
                "Фоновая музыка нужной длины и настроения",
                "Короткие джинглы и заставки"
              ],
              gives: [
                "Пакет на месяц — объём согласуем под вашу задачу до оплаты"
              ]
            }
          ]
        }
      ]
    },
    {
      id: "automation",
      title: "Автоматизация под ключ",
      promise: "Берём процесс вашего бизнеса целиком: от входящих документов до готового результата.",
      price_anchor: "от 100 000 ₽ · цена после разбора задачи",
      examples: [
        { item: "drawings-estimate", label: "Расчёт по чертежам" },
        { item: "construction-route", label: "Документы стройки" },
        { item: "finance-report", label: "Финансовый отчёт" }
      ],
      icon: "Workflow",
      cta: {
        label: "Обсудить задачу"
      },
      groups: [
        {
          id: "cases",
          title: "Что мы уже делаем",
          lead: "Процессы, которые сейчас внедряем у наших клиентов. Ваш разберём так же: что на входе, что на выходе, кто проверяет.",
          items: [
            {
              id: "drawings-estimate",
              icon: "Ruler",
              name: "Расчёт заказа по чертежам",
              industry: "Завод металлоконструкций",
              tagline: "Агент читает комплект чертежей и считает стоимость заказа по нормам завода."
            },
            {
              id: "construction-route",
              icon: "HardHat",
              name: "Документы стройки по маршруту",
              industry: "Строительная компания",
              tagline: "Ведомости, сметы и предложения поставщиков проходят через агентов; дальше идёт только проверенное."
            },
            {
              id: "production-plan",
              icon: "Factory",
              name: "Расчёт, снабжение и план производства",
              industry: "Производство",
              tagline: "Три агента: считают заказ по справочнику, готовят закупку и план смен."
            },
            {
              id: "finance-report",
              icon: "Landmark",
              name: "Финансовый отчёт из выписок",
              industry: "Малый бизнес",
              tagline: "Банковские выписки превращаются в движение денег и отчёт в вашей форме."
            },
            {
              id: "ai-sales-head",
              icon: "BarChart3",
              name: "ИИ-руководитель отдела продаж",
              industry: "Детские товары",
              tagline: "Разбор работы менеджеров по CRM и звонкам с задачами на следующий день."
            }
          ]
        }
      ]
    }
  ];

export const MARKETPLACE_ORDER_STEPS: string[] = [
  "Нажимаете «Заказать» — откроется чат с нами в Telegram с готовым сообщением.",
  "Уточняем задачу и подтверждаем цену.",
  "Настраиваем всё в вашей Korra и вместе получаем первый результат."
];

export function findSection(id: string | undefined): MarketplaceSection | undefined {
  return MARKETPLACE_SECTIONS.find((section) => section.id === id);
}

/** Позиция со своей страницей: только та, у которой есть описание «Что делает». */
export function findItem(section: MarketplaceSection, id: string | undefined): MarketplaceItem | undefined {
  return section.groups
    .flatMap((group) => group.items)
    .find((item) => item.id === id && hasOwnPage(item));
}

export function hasOwnPage(item: MarketplaceItem): boolean {
  return Boolean(item.does?.length);
}

/** Надпись кнопки заказа. `null` — у позиции-примера кнопки нет, заказ общий у раздела. */
export function orderLabel(item: MarketplaceItem, section: MarketplaceSection): string | null {
  return hasOwnPage(item) ? section.cta?.label ?? "Заказать" : null;
}

/** Цена с пояснением одной строкой — так её видит тот, кому уходит заказ. */
export function priceLine(item: Pick<MarketplaceItem, "price" | "price_note">): string {
  return [item.price, item.price_note].filter(Boolean).join(", ");
}

/** Адрес панели без протокола: хост и префикс, под которым она открыта (`/c/<slug>` в кабинете). */
export function panelAddress(host: string, basePath: string): string {
  return `${host}${basePath}`;
}

export type OrderSubject = { kind: "item"; item: Pick<MarketplaceItem, "name" | "price" | "price_note"> } | { kind: "automation" };

/** Ссылка на чат в Telegram с готовым сообщением; текст кодируется целиком. */
export function buildOrderLink(subject: OrderSubject, panel: string, contact = MARKETPLACE_ORDER_CONTACT): string {
  const request = subject.kind === "automation"
    ? "Хочу обсудить автоматизацию под ключ."
    : `Хочу из маркетплейса Korra: «${subject.item.name}» (${priceLine(subject.item)}).`;
  const text = `Здравствуйте! ${request} Моя Korra: ${panel}.`;
  return `https://t.me/${contact}?text=${encodeURIComponent(text)}`;
}
