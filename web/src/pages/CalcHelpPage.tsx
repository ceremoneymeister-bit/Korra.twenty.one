/**
 * Справка стола расчётчика.
 *
 * Отдельная страница, а не режим внутри `ClientHelpPage`: та справка написана
 * про «Пульт» и «Студию» — экраны кабинета владельца, которых здесь нет.
 * Инструкция, рассказывающая про несуществующие экраны, хуже отсутствующей:
 * человек ищет кнопку, не находит и решает, что сломана система, а не текст.
 *
 * Порядок разделов повторяет порядок работы, а не порядок меню: сначала то,
 * без чего ничего не считается (данные), потом то, ради чего всё затевалось
 * (заказ), и только потом вспомогательное.
 */

import {
  ArrowRight,
  CheckCircle2,
  CircleHelp,
  Database,
  FileText,
  FolderOpen,
  ListChecks,
  MessageCircle,
  ShieldCheck,
  Star,
} from "lucide-react";
import { Link } from "react-router";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";

import { cn } from "@/lib/utils";

const SECTIONS = [
  ["#start", "С чего начать"],
  ["#data", "Данные предприятия"],
  ["#sources", "Источник у каждой цифры"],
  ["#intake", "Первый тест"],
  ["#orders", "Заказ и его стадии"],
  ["#star", "Предварительная цена"],
  ["#files", "Файлы"],
  ["#agents", "Расчётчики"],
  ["#faq", "Частые вопросы"],
] as const;

function Step({ number, children }: { number: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3 text-sm leading-6 text-muted-foreground">
      <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
        {number}
      </span>
      <span>{children}</span>
    </li>
  );
}

function RouteLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-semibold transition-colors hover:border-primary/45 hover:bg-primary/[0.06]"
    >
      {children}
      <ArrowRight aria-hidden className="size-4" />
    </Link>
  );
}

function Section({
  id,
  icon: Icon,
  title,
  children,
}: {
  id: string;
  icon: typeof Database;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24 space-y-4">
      <h2 className="flex items-center gap-2 text-xl font-semibold">
        <Icon aria-hidden className="size-5 shrink-0 text-primary" />
        {title}
      </h2>
      {children}
    </section>
  );
}

function Stages() {
  const stages: [string, string, string][] = [
    ["Вход", "Что считаем", "чертёж, количество и ревизия КД"],
    ["Состав", "Из чего состоит изделие", "детали, сборки и количества"],
    ["Маршрут", "Как изготавливаем", "операции, исполнение и владелец стоимости"],
    ["Расчёт", "Сколько это стоит внутри", "заготовка, нормы времени и услуги"],
    ["Книга", "Как собран итог", "себестоимость и отдельная цена заказчику"],
    ["QA", "Что проверено", "механический, технологический и коммерческий гейты"],
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {stages.map(([name, question, what], index) => (
        <Card key={name}>
          <CardContent className="space-y-1 p-4">
            <span className="text-xs font-semibold text-primary">
              Этап {index + 1}
            </span>
            <p className="text-base font-semibold">{name}</p>
            <p className="text-sm text-muted-foreground">{question}: {what}.</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export default function CalcHelpPage() {
  return (
    <div className="mx-auto w-full max-w-4xl space-y-10 px-4 py-6 sm:px-6">
      <Card>
        <CardContent className="space-y-4 p-5 sm:p-6">
          <div className="flex items-start gap-4">
            <span className="grid size-11 shrink-0 place-items-center rounded-full bg-primary/15 text-primary">
              <CircleHelp aria-hidden className="size-5" />
            </span>
            <div className="space-y-3">
              <p className="text-xs font-semibold tracking-wide text-primary">
                Инструкция по расчётчику
              </p>
              <h1 className="text-2xl font-semibold">Как здесь работать</h1>
              <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
                Расчётчик считает стоимость изготовления по чертежу и данным
                предприятия. Начните с проверки прайсов, норм и правил на
                экране «Данные», затем сравните тестовый расчёт с утверждённым
                примером.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-3">
            <RouteLink to="/rates">Открыть «Данные»</RouteLink>
            <RouteLink to="/orders">Посмотреть заказы</RouteLink>
          </div>
        </CardContent>
      </Card>

      <nav aria-label="Разделы справки" className="flex flex-wrap gap-2">
        {SECTIONS.map(([href, label]) => (
          <a
            key={href}
            href={href}
            className={cn(
              "inline-flex min-h-[44px] items-center rounded-lg border border-border px-3.5 py-2",
              "text-sm transition-colors hover:border-primary/45 hover:bg-primary/[0.06]",
            )}
          >
            {label}
          </a>
        ))}
      </nav>

      <Section id="start" icon={CheckCircle2} title="С чего начать">
        <ol className="space-y-3">
          <Step number={1}>
            Откройте «Данные» и проверьте опубликованный набор: материалы,
            оборудование, нормы, накладные, политику цены и источники ставок.
          </Step>
          <Step number={2}>
            Нажмите «Проверить». Проверяет не браузер, а движок — он же потом и
            считает, поэтому его ответ и есть правда о ваших данных.
          </Step>
          <Step number={3}>
            Нажмите «Опубликовать». С этого момента расчётчики считают по новым
            цифрам сразу — перезапускать ничего не нужно.
          </Step>
        </ol>
      </Section>

      <Section id="data" icon={Database} title="Данные предприятия">
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Экран «Данные» редактирует заготовительные операции, материалы,
          оборудование, нормы, накладные и политику цены. Расчётчики используют
          только опубликованную человеком ревизию — у каждой ставки остаётся
          автор и источник.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Расширенный парк процессов и реестр скалярных и матричных ставок
          редактируются в этой же форме. Матрица хранит оси, границы диапазонов,
          единицы, НДС и источник; парк связывает каждую операцию с нужной
          ставкой и явно отмечает обязательную ручную сверку.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Каждая публикация создаёт ревизию. Старые ревизии не переписываются:
          видно, что было, когда изменилось и с каким комментарием. К прошлой
          можно вернуться — она пройдёт проверку заново.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Заполнять всё сразу не нужно. Публиковать можно сколько угодно раз, и
          недостающее дозаполняется по ходу.
        </p>
      </Section>

      <Section id="intake" icon={MessageCircle} title="Как провести первый тест">
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Первый тест проходит в кабинете. Подготовьте чертёж одной детали,
          количество и проверенный расчёт для сравнения. Загрузите исходники в
          «Файлы», затем откройте нужного расчётчика и укажите номер тестового
          заказа, количество и путь к чертежу.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Созданная расчётчиком карточка появится в «Заказах». Проверяйте
          входные данные и каждую стадию по очереди. В карточке видны
          недостающие сведения и следующее действие.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Приём целой папки в заказ и подключение Telegram будут проверяться
          отдельными шагами. Перед рабочим запуском нужно подтвердить
          актуальные данные предприятия и сверить итоговую цену с эталоном.
        </p>
        <div className="flex flex-wrap gap-3">
          <RouteLink to="/files">Открыть «Файлы»</RouteLink>
          <RouteLink to="/agents">Открыть расчётчиков</RouteLink>
        </div>
      </Section>

      <Section id="sources" icon={ShieldCheck} title="Источник у каждой цифры">
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          У каждой ставки обязательно указывается, откуда она взята: прайс
          поставщика, КП, справочник норм. Строка без источника подсвечена, а
          кнопка «Опубликовать» выключена, пока такие строки есть — и рядом
          написано, каких именно не хватает.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Это нужно не для порядка. Цена уходит заказчику, и через полгода
          вопрос «почему здесь 95 рублей» задают вам, а не системе. Источник —
          это ответ на него, записанный тогда же, когда цифра.
        </p>
      </Section>

      <Section id="orders" icon={ListChecks} title="Заказ и его стадии">
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Новый заказ проходит шесть крупных этапов. Полоса этапов строится по
          реально сохранённым составу, маршруту, расчёту, книге и QA-квитанциям.
          Технический статус показывается рядом, чтобы его можно было сверить с
          карточкой workflow.
        </p>
        <Stages />
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          На экране «Заказы» первой строкой стоит следующее действие человека.
          Статус «Готово для решения человека» означает, что три QA-гейта
          получили PASS; решение по расчёту и отправка заказчику остаются за
          человеком.
        </p>
      </Section>

      <Section id="star" icon={Star} title="Предварительная цена">
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Предварительная цена означает, что до окончательного решения остаётся
          основание для сверки: котировка материала, КП подрядчика, ручная
          проверка операции или другой коммерческий блокер. Каждый такой пункт
          перечислен в карточке заказа.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Звёздочка по-прежнему отмечает неподтверждённую цену материала.
          Снабжение закрывает её актуальной котировкой; цену подрядчика и ручные
          сверки закрывают своими действиями.
        </p>
      </Section>

      <Section id="files" icon={FolderOpen} title="Файлы">
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          В «Моих загрузках» лежит то, что принесли вы: чертежи, прайсы,
          справочники. В «Готовых материалах» — то, что сделали агенты. Разделены
          они затем, чтобы результат работы нельзя было спутать с исходником и
          случайно затереть.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Удалённое попадает в корзину и оттуда восстанавливается. Офисные файлы
          и PDF открываются прямо здесь, скачивать для просмотра не нужно.
        </p>
      </Section>

      <Section id="agents" icon={MessageCircle} title="Расчётчики">
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Три вкладки ведут специализации: заготовка и снабжение, маршрут,
          нормы времени и расчёт операций. Workflow связывает их результаты в
          одну книгу и показывает, какое действие требуется следующим.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Для уточнений после создания карточки пишите обычными словами.
          Указывайте номер заказа, позицию, количество и ревизию чертежа,
          чтобы расчётчик работал с нужным комплектом исходников.
        </p>
        <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
          Если связь оборвалась во время ответа, сообщение не теряется: оно
          сохраняется и его видно на экране. Повтор не запускает работу второй
          раз — система помнит, что этот вопрос уже задан.
        </p>
      </Section>

      <Section id="faq" icon={FileText} title="Частые вопросы">
        <div className="space-y-4">
          <div className="space-y-1">
            <p className="text-base font-semibold">
              Я поменяла прайс — нужно ли что-то перезапускать?
            </p>
            <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
              Нет. Следующий расчёт пойдёт по новым цифрам сразу после
              публикации.
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-base font-semibold">
              Что будет со старыми заказами после смены ставок?
            </p>
            <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
              Они останутся посчитанными по прежним. Система не смешивает данные
              разных ревизий в одном расчёте — иначе половина цены была бы по
              старому прайсу, а половина по новому, и разобраться в этом уже
              нельзя.
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-base font-semibold">
              Кнопка «Опубликовать» серая. Почему?
            </p>
            <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
              В форме есть строки без источника. Причина написана прямо над
              кнопкой, а сами строки подсвечены.
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-base font-semibold">
              Агент ошибся в расчёте. Что делать?
            </p>
            <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
              Напишите, что именно неверно, и верните расчёт на нужный этап.
              После исправления проверьте три QA-квитанции текущей ревизии.
            </p>
          </div>
        </div>
      </Section>
    </div>
  );
}
