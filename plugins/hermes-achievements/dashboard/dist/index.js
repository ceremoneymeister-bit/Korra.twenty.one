/* «Достижения» Korra: результаты работы агентов. Использует существующие данные контура без анализа текстов разговоров.
   Первоначальный плагин достижений: @PCinkusz, https://github.com/PCinkusz/hermes-achievements (MIT). */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  const { React } = SDK;
  const h = React.createElement;
  const { Link } = SDK.router;
  const { useState, useEffect, useRef } = React;

  function BenefitsPage() {
    const [data, setData] = useState({});
    const [loading, setLoading] = useState(true);
    const [updatedAt, setUpdatedAt] = useState(null);
    const generation = useRef(0);
    async function refresh() {
      const current = ++generation.current;
      setLoading(true);
      const results = await Promise.allSettled([
        SDK.fetchJSON("/api/plugins/kanban/boards"),
        SDK.api.getCronJobs("all"),
        SDK.api.getProfiles(),
      ]);
      if (current !== generation.current) return;
      const [boards, jobs, profiles] = results.map(function (result) {
        return result.status === "fulfilled" ? result.value : null;
      });
      setData({ boards, jobs, profiles });
      setUpdatedAt(new Date());
      setLoading(false);
    }
    useEffect(function () {
      void refresh();
      return function () { generation.current++; };
    }, []);

    const boards = data.boards && data.boards.boards;
    const total = boards ? boards.reduce((sum, board) => sum + Number(board.total || 0), 0) : null;
    const done = boards ? boards.reduce((sum, board) => sum + Number((board.counts || {}).done || 0), 0) : null;
    const ready = boards ? boards.reduce((sum, board) => sum + Number((board.counts || {}).ready || 0), 0) : null;
    const attention = boards ? boards.reduce((sum, board) => sum + Number((board.counts || {}).blocked || 0) + Number((board.counts || {}).review || 0), 0) : null;
    const jobs = Array.isArray(data.jobs) ? data.jobs : null;
    const successfulJobs = jobs ? jobs.filter(job => job.enabled && job.last_status === "ok" && job.last_run_at).length : null;
    const pausedJobs = jobs ? jobs.filter(job => !job.enabled).length : null;
    const failingJobs = jobs ? jobs.filter(job => job.enabled && (job.last_status === "error" || job.last_delivery_error || job.last_fire_error)).length : null;
    const team = data.profiles && data.profiles.profiles;
    const specialists = team ? team.filter(profile => !profile.is_default).length : null;
    const steps = [
      {
        id: "task", group: "Работа с задачами", title: "Поручения собраны в одном месте",
        value: total, unit: "задач на досках", to: "/kanban", action: "Открыть доску",
        description: "Вынесите из переписки одно конкретное поручение: кто делает, что должно получиться и как проверить результат.",
        evidence: "Учитываются задачи на доступных досках, кроме архивных. Создание карточки ещё не означает запуск агента.",
      },
      {
        id: "result", group: "Работа с задачами", title: "Есть завершённая работа",
        value: done, unit: "задач в колонке «Готово»", to: "/kanban?view=done", action: "Посмотреть результаты",
        description: "Откройте итог в карточке задачи. Если нужна доработка, опишите её и верните поручение агенту.",
        evidence: "Считаются завершённые карточки. Статус показывает итог работы; качество результата оцениваете вы.",
      },
      {
        id: "routine", group: "Регулярные процессы", title: "Рутина выполняется по расписанию",
        value: successfulJobs, unit: "включённых расписаний с успешным последним запуском", to: "/cron", action: "Настроить расписание",
        description: "Начните с одного повторяющегося дела: утренней сводки, проверки заявок или еженедельного отчёта.",
        evidence: "Учитываются только включённые расписания всех агентов, у которых последний запуск завершился успешно. Приостановленные здесь не считаются работающей автоматизацией.",
      },
      {
        id: "team", group: "Своя команда", title: "Агенты для ваших процессов",
        value: specialists, unit: "агентов помимо Корры", to: "/profiles/new", action: "Создать агента",
        description: "Выделите повторяющийся бизнес-процесс отдельному помощнику. Опишите его обязанности и дайте пример хорошего результата.",
        evidence: "Количество созданных агентов без основного. Само создание ещё не подтверждает, что агент обучен и решает ваши задачи.",
      },
    ];
    const unavailable = steps.some(step => step.value === null);
    const next = steps.find(step => step.value === 0);

    return h("div", { className: "korra-benefits", "aria-busy": loading },
      h("section", { className: "kb-benefit-hero" },
        h("div", null,
          h("h2", null, "Результаты работы агентов"),
          h("p", null, "Сводка задач, расписаний и помощников: что уже сделано и что можно поручить дальше. Качество результата и выгоду оцениваете вы.")),
        h("button", { type: "button", className: "neo-button", onClick: () => void refresh(), disabled: loading }, loading ? "Обновляем…" : "Обновить данные")),
      h("section", { className: "kb-benefit-overview" },
        h("div", null,
          h("strong", null, loading ? "Загружаем результаты…" : "Работа вашей команды"),
          h("p", null, "Что уже сделано, что требует вашего решения и какое дело можно поручить следующим.")),
        !loading && h("p", { className: "kb-benefit-updated" }, "Проверено в ", updatedAt && updatedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }))),
      !loading && unavailable && h("div", { className: "kb-benefit-notice", role: "status" },
        "Часть данных сейчас недоступна. Известные результаты показаны ниже; остальные можно проверить кнопкой «Обновить данные»."),
      !loading && (attention > 0 || failingJobs > 0) && h("section", { className: "kb-benefit-next" },
        h("strong", null, "Сейчас нужно ваше внимание"),
        attention > 0 && boards.filter(board => (board.counts?.blocked || 0) + (board.counts?.review || 0) > 0).map(board =>
          h(Link, { key: board.slug, to: "/kanban?" + new URLSearchParams({ board: board.slug, view: "attention" }) },
            `${board.slug === "default" && (!board.name || board.name === "Default") ? "Основная доска" : board.name || board.slug}: нужно решение — ${board.counts?.blocked || 0}, на проверке — ${board.counts?.review || 0}. Открыть →`)),
        failingJobs > 0 && h(Link, { to: "/cron" }, `Расписаний с ошибкой запуска или доставки: ${failingJobs}. Проверить →`)),
      !loading && next && h("section", { className: "kb-benefit-next" },
        h("strong", null, "Следующий шаг: ", next.action.toLocaleLowerCase("ru-RU")),
        h("p", null, next.description),
        h(Link, { className: "neo-button", "data-neo-variant": "primary", to: next.to }, next.action, " →")),
      !loading && !next && !unavailable && h("p", null, "Основа готова. Теперь улучшайте качество: уточняйте инструкции на примерах реальных результатов."),
      h("div", { className: "kb-benefit-grid" }, steps.map(function (step) {
        const achieved = step.value !== null && step.value > 0;
        return h("article", { key: step.id, className: "kb-benefit-card", "data-milestone": step.id },
          h("div", { className: "kb-benefit-card-top" },
            h("span", null, step.group),
            h("span", { className: achieved ? "kb-benefit-state is-complete" : "kb-benefit-state" },
              loading ? "Проверяем" : step.value === null ? "Нет данных" : achieved ? "Есть данные" : "Можно начать")),
          h("h3", null, step.title),
          h("p", null, step.description),
          h("div", { className: "kb-benefit-metric" },
            h("strong", null, loading || step.value === null ? "—" : step.value.toLocaleString("ru-RU")),
            h("span", null, step.unit)),
          step.id === "routine" && pausedJobs > 0 && h("p", { className: "kb-benefit-updated" }, `Приостановлено расписаний: ${pausedJobs}. Они сохраняют историю, но больше не запускаются.`),
          h("details", null, h("summary", null, "Как считаем"), h("p", null, step.evidence)),
          h(Link, { to: step.to, className: "kb-benefit-link" }, step.action, " →"));
      })),
      !loading && ready > 0 && h("p", { className: "kb-benefit-updated" }, `В очереди на досках: ${ready}. Проверьте назначенных исполнителей, если задачи долго не начинают выполняться.`));
  }
  window.__HERMES_PLUGINS__.register("hermes-achievements", BenefitsPage);
})();
