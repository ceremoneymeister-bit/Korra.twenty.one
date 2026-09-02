import {
  ArrowRight,
  Bot,
  CalendarClock,
  CircleHelp,
  History,
  MessageCircle,
  Settings2,
  ShieldCheck,
  Upload,
  Users,
} from "lucide-react";
import { Link } from "react-router";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";

const SECTIONS = [
  ["#start", "С чего начать"],
  ["#agents", "Агенты"],
  ["#files", "Файлы"],
  ["#chat", "Чат и история"],
  ["#schedule", "Задачи"],
  ["#settings", "Настройки"],
  ["#recovery", "Если что-то не работает"],
] as const;

function RouteLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-border bg-background px-4 py-2 text-sm font-semibold text-foreground transition-colors hover:border-primary/45 hover:bg-primary/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
    >
      {children}
      <ArrowRight className="size-4" aria-hidden />
    </Link>
  );
}

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

export default function ClientHelpPage() {
  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col gap-8 pb-16">
      <header className="rounded-2xl border border-primary/20 bg-gradient-to-br from-primary/[0.10] via-card to-card p-5 sm:p-8">
        <div className="flex items-start gap-4">
          <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground">
            <CircleHelp className="size-5" aria-hidden />
          </span>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-primary">
              Инструкция по Korra 21
            </p>
            <h2 className="mt-2 text-2xl font-semibold leading-tight sm:text-3xl">
              Как здесь работать
            </h2>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground sm:text-base">
              Начните с чата, выберите нужного агента, передайте материалы и
              возвращайтесь к результатам через историю. Редкие настройки и
              технические экраны собраны отдельно внизу меню.
            </p>
          </div>
        </div>
      </header>

      <nav aria-label="Разделы инструкции" className="flex gap-2 overflow-x-auto pb-1">
        {SECTIONS.map(([href, label]) => (
          <a
            key={href}
            href={href}
            className="shrink-0 rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground hover:border-primary/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            {label}
          </a>
        ))}
      </nav>

      <section id="start" className="scroll-mt-20 space-y-4">
        <div className="flex items-center gap-3">
          <Bot className="size-5 text-primary" aria-hidden />
          <h2 className="text-xl font-semibold">С чего начать</h2>
        </div>
        <ol className="grid gap-3 md:grid-cols-3">
          <Card className="rounded-xl"><CardContent className="p-5"><Step number={1}>Откройте чат и опишите результат, который нужен.</Step></CardContent></Card>
          <Card className="rounded-xl"><CardContent className="p-5"><Step number={2}>Приложите исходники кнопкой или перетащите их в поле сообщения.</Step></CardContent></Card>
          <Card className="rounded-xl"><CardContent className="p-5"><Step number={3}>Не закрывайте ответ: при обрыве сообщение останется в outbox и его можно повторить.</Step></CardContent></Card>
        </ol>
        <RouteLink to="/chat">Открыть чат</RouteLink>
      </section>

      <section id="agents" className="scroll-mt-20 space-y-4">
        <div className="flex items-center gap-3">
          <Users className="size-5 text-primary" aria-hidden />
          <h2 className="text-xl font-semibold">Агенты</h2>
        </div>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          Вкладки показывают агентов, настроенных для этого контура. Можно
          запустить несколько разговоров и переключаться между ними: скрытая
          вкладка продолжает получать ответ в фоне.
        </p>
        <RouteLink to="/agents">Открыть агентов</RouteLink>
      </section>

      <section id="files" className="scroll-mt-20 space-y-4">
        <div className="flex items-center gap-3">
          <Upload className="size-5 text-primary" aria-hidden />
          <h2 className="text-xl font-semibold">Файлы</h2>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Card className="rounded-xl"><CardContent className="p-5"><h3 className="font-semibold">Мои загрузки</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Исходники попадают в отдельную входящую папку. Их можно переименовать или убрать в восстановимую корзину.</p></CardContent></Card>
          <Card className="rounded-xl"><CardContent className="p-5"><h3 className="font-semibold">Готовые материалы</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Изображения, PDF и текст открываются в предпросмотре; оригинал скачивается отдельной кнопкой.</p></CardContent></Card>
        </div>
        <RouteLink to="/files">Открыть материалы</RouteLink>
      </section>

      <section id="chat" className="scroll-mt-20 space-y-4">
        <div className="flex items-center gap-3">
          <MessageCircle className="size-5 text-primary" aria-hidden />
          <h2 className="text-xl font-semibold">Чат и история</h2>
        </div>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          В истории доступны прежние разговоры из браузера и подключённых
          каналов. Ссылку на конкретный диалог можно открыть напрямую.
        </p>
        <div className="flex flex-wrap gap-2">
          <RouteLink to="/chat">Открыть чат</RouteLink>
          <RouteLink to="/sessions">Открыть историю</RouteLink>
        </div>
      </section>

      <section id="schedule" className="scroll-mt-20 space-y-4">
        <div className="flex items-center gap-3">
          <CalendarClock className="size-5 text-primary" aria-hidden />
          <h2 className="text-xl font-semibold">Задачи</h2>
        </div>
        <ol className="grid gap-3 md:grid-cols-2">
          <Step number={1}>Добавьте понятное название, действие и время. Новая задача сохранится приостановленной.</Step>
          <Step number={2}>Проверьте карточку и включите задачу, подтвердив её точное название.</Step>
          <Step number={3}>Пауза остановит будущие запуски; уже начатый запуск может завершиться.</Step>
          <Step number={4}>После паузы задачу можно убрать из списка, сохранив историю результатов.</Step>
        </ol>
        <RouteLink to="/cron">Открыть задачи</RouteLink>
      </section>

      <section id="settings" className="scroll-mt-20 space-y-4">
        <div className="flex items-center gap-3">
          <Settings2 className="size-5 text-primary" aria-hidden />
          <h2 className="text-xl font-semibold">Настройки и служебное</h2>
        </div>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          «Настройки» содержит ключи, модель, журналы и эту справку.
          «Служебное» открывает расширенные возможности и конфигурацию.
        </p>
      </section>

      <section id="recovery" className="scroll-mt-20 space-y-4">
        <div className="flex items-center gap-3">
          <ShieldCheck className="size-5 text-primary" aria-hidden />
          <h2 className="text-xl font-semibold">Если что-то не работает</h2>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Card className="rounded-xl"><CardContent className="p-5"><h3 className="font-semibold">Сообщение не отправилось</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Черновик остаётся в чате. Проверьте историю перед повтором, если доставка отмечена как неизвестная.</p></CardContent></Card>
          <Card className="rounded-xl"><CardContent className="p-5"><h3 className="font-semibold">Файл не открылся</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Нажмите «Повторить» или скачайте оригинал, если формат нельзя показать в браузере.</p></CardContent></Card>
          <Card className="rounded-xl"><CardContent className="p-5"><h3 className="font-semibold">Данные изменились</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Обновите экран, прочитайте текущую версию и повторите действие.</p></CardContent></Card>
          <Card className="rounded-xl"><CardContent className="p-5"><h3 className="font-semibold">Интерфейс обновился</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Нажмите «Обновить» в уведомлении о новой сборке; сохранённые диалоги и задачи останутся на месте.</p></CardContent></Card>
        </div>
      </section>

      <aside className="rounded-xl border border-success/25 bg-success/[0.06] p-5">
        <div className="flex gap-3">
          <History className="mt-0.5 size-5 shrink-0 text-success" aria-hidden />
          <p className="text-sm leading-6 text-muted-foreground">
            Если нужен контекст прежней работы, откройте историю и продолжите
            нужный диалог — начинать объяснение заново не требуется.
          </p>
        </div>
      </aside>
    </section>
  );
}
