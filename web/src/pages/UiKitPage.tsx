import { useEffect, useState, type ReactNode } from "react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@nous-research/ui/ui/components/card";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@nous-research/ui/ui/components/dialog";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { ListItem } from "@nous-research/ui/ui/components/list-item";
import {
  Select,
  SelectOption,
} from "@nous-research/ui/ui/components/select";
import { Separator } from "@nous-research/ui/ui/components/separator";
import {
  Skeleton,
  Spinner,
} from "@nous-research/ui/ui/components/spinner";
import { Switch } from "@nous-research/ui/ui/components/switch";
import {
  Tabs,
  TabsList,
  TabsTrigger,
} from "@nous-research/ui/ui/components/tabs";
import { Textarea } from "@nous-research/ui/ui/components/textarea";
import { Toast } from "@nous-research/ui/ui/components/toast";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@nous-research/ui/ui/components/tooltip";
import { useTheme } from "@/themes";

const DEMO_STATES = [
  "default",
  "hover",
  "pressed",
  "focus",
  "error",
  "disabled",
] as const;

type DemoState = (typeof DEMO_STATES)[number];

export default function UiKitPage() {
  const { setTheme, themeName } = useTheme();
  const [switchOn, setSwitchOn] = useState(true);
  const [checked, setChecked] = useState(true);
  const [selectValue, setSelectValue] = useState("active");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [toast, setToast] = useState<{
    message: string;
    type: "error" | "success";
  } | null>({ message: "Настройки сохранены", type: "success" });

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("theme");
    if ((requested === "light" || requested === "dark") && requested !== themeName) {
      setTheme(requested);
    }
  }, [setTheme, themeName]);

  return (
    <div
      className="h-full overflow-y-auto bg-[var(--neo-surface)] px-4 py-8 text-[var(--neo-text-primary)] sm:px-8"
      data-theme={themeName}
      data-testid="korra-ui-kit"
    >
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-12 pb-20">
        <header className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <p className="mb-2 text-sm text-[var(--neo-accent-line)]">Korra 21</p>
            <h1 className="text-3xl font-semibold">Неоморфный UI-набор</h1>
            <p className="mt-2 max-w-2xl text-[var(--neo-text-secondary)]">
              Реальные компоненты панели, интерактивные состояния и
              клавиатурный фокус. Нажимайте Tab для проверки маршрута фокуса.
            </p>
          </div>
          <div aria-label="Тема витрины" className="flex gap-3">
            <Button
              aria-pressed={themeName === "light"}
              onClick={() => setTheme("light")}
              outlined={themeName !== "light"}
            >
              Светлая
            </Button>
            <Button
              aria-pressed={themeName === "dark"}
              onClick={() => setTheme("dark")}
              outlined={themeName !== "dark"}
            >
              Тёмная
            </Button>
          </div>
        </header>

        <ShowcaseSection
          description="Главное действие всегда лаймовое; вторичные действия обозначаются глубиной."
          title="Button"
        >
          <StateGrid>
            {DEMO_STATES.map((state) => (
              <StateCell key={state} state={state}>
                <Button
                  aria-invalid={state === "error" || undefined}
                  disabled={state === "disabled"}
                >
                  Сохранить
                </Button>
              </StateCell>
            ))}
          </StateGrid>
          <div className="flex flex-wrap gap-4">
            <Button outlined>Вторичная</Button>
            <Button ghost>Без хрома</Button>
            <Button destructive>Удалить</Button>
            <Button destructive ghost>
              Отменить доступ
            </Button>
          </div>
        </ShowcaseSection>

        <ShowcaseSection
          description="Inset-глубина вместо рамки; ошибка и клавиатурный фокус — внутренний цветной край."
          title="Input и Textarea"
        >
          <StateGrid>
            {DEMO_STATES.map((state) => (
              <StateCell key={state} state={state}>
                <Label htmlFor={`input-${state}`}>Название</Label>
                <Input
                  aria-invalid={state === "error" || undefined}
                  defaultValue={state === "error" ? "Недопустимое имя" : undefined}
                  disabled={state === "disabled"}
                  id={`input-${state}`}
                  placeholder="Новая автоматизация"
                />
              </StateCell>
            ))}
          </StateGrid>
          <StateGrid>
            {DEMO_STATES.map((state) => (
              <StateCell key={`textarea-${state}`} state={state}>
                <Label htmlFor={`textarea-${state}`}>Описание</Label>
                <Textarea
                  aria-invalid={state === "error" || undefined}
                  disabled={state === "disabled"}
                  id={`textarea-${state}`}
                  placeholder="Добавьте контекст…"
                  rows={2}
                />
              </StateCell>
            ))}
          </StateGrid>
        </ShowcaseSection>

        <ShowcaseSection
          description="Тонкие индикаторы используют accentLine: затемнённый лайм в светлой теме."
          title="Switch и Checkbox"
        >
          <StateGrid>
            {DEMO_STATES.map((state) => (
              <StateCell key={`switch-${state}`} state={state}>
                <Label className="flex items-center gap-3">
                  <Switch
                    aria-invalid={state === "error" || undefined}
                    checked={state === "default" ? switchOn : state !== "disabled"}
                    disabled={state === "disabled"}
                    onCheckedChange={setSwitchOn}
                  />
                  Канал включён
                </Label>
              </StateCell>
            ))}
          </StateGrid>
          <StateGrid>
            {DEMO_STATES.map((state) => (
              <StateCell key={`checkbox-${state}`} state={state}>
                <Label className="flex items-center gap-3">
                  <Checkbox
                    aria-invalid={state === "error" || undefined}
                    checked={state === "default" ? checked : state !== "disabled"}
                    disabled={state === "disabled"}
                    onCheckedChange={(value) => setChecked(value === true)}
                  />
                  Сохранять историю
                </Label>
              </StateCell>
            ))}
          </StateGrid>
        </ShowcaseSection>

        <ShowcaseSection title="Select">
          <StateGrid>
            {DEMO_STATES.map((state) => (
              <StateCell key={state} state={state}>
                <Label htmlFor={`select-${state}`}>Статус</Label>
                <Select
                  disabled={state === "disabled"}
                  id={`select-${state}`}
                  onValueChange={setSelectValue}
                  value={selectValue}
                >
                  <SelectOption value="active">Активен</SelectOption>
                  <SelectOption value="paused">На паузе</SelectOption>
                  <SelectOption value="draft">Черновик</SelectOption>
                </Select>
              </StateCell>
            ))}
          </StateGrid>
        </ShowcaseSection>

        <ShowcaseSection
          description="Карточка использует уровень 3; строки плотного списка остаются плоскими до взаимодействия."
          title="Card, Badge и ListItem"
        >
          <div className="grid gap-8 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Рабочая область</CardTitle>
                <CardDescription>
                  Мягкая поверхность без обводки, 12px/24px глубина.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-3">
                <Badge>По умолчанию</Badge>
                <Badge tone="success">Работает</Badge>
                <Badge tone="warning">Внимание</Badge>
                <Badge tone="destructive">Ошибка</Badge>
                <Badge tone="secondary">Архив</Badge>
                <Badge tone="outline">Inset</Badge>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Плотный список</CardTitle>
                <CardDescription>Без постоянной тени на каждой строке.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-1">
                <ListItem>Обычная строка</ListItem>
                <div data-demo-state="hover">
                  <ListItem>Наведение</ListItem>
                </div>
                <ListItem active>Активная строка</ListItem>
                <div data-demo-state="focus">
                  <ListItem>Клавиатурный фокус</ListItem>
                </div>
                <ListItem disabled>Недоступная строка</ListItem>
              </CardContent>
            </Card>
          </div>
        </ShowcaseSection>

        <ShowcaseSection title="Dialog и ConfirmDialog">
          <div className="flex flex-wrap gap-4">
            <Dialog>
              <DialogTrigger asChild>
                <Button outlined>Открыть диалог</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Новая автоматизация</DialogTitle>
                  <DialogDescription>
                    Диалог использует ту же поверхность и максимальную глубину.
                  </DialogDescription>
                </DialogHeader>
                <div className="p-5">
                  <Input placeholder="Название" />
                </div>
                <DialogFooter>
                  <Button outlined>Отмена</Button>
                  <Button>Создать</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <Button destructive onClick={() => setConfirmOpen(true)}>
              Открыть подтверждение
            </Button>
            <ConfirmDialog
              description="Действие нельзя отменить."
              destructive
              onCancel={() => setConfirmOpen(false)}
              onConfirm={() => setConfirmOpen(false)}
              open={confirmOpen}
              title="Удалить подключение?"
            />
          </div>
          <div className="neo-dialog relative grid max-w-lg gap-0">
            <div className="neo-dialog-header p-5">
              <h3 className="font-semibold">Статичный вид для сверки</h3>
              <p className="neo-dialog-description text-sm">
                Полный интерактивный вариант открывается кнопкой выше.
              </p>
            </div>
            <div className="flex justify-end gap-3 p-4">
              <Button outlined>Отмена</Button>
              <Button>Продолжить</Button>
            </div>
          </div>
        </ShowcaseSection>

        <ShowcaseSection title="Toast, Tabs и Tooltip">
          <div className="flex flex-wrap gap-4">
            <Button
              outlined
              onClick={() =>
                setToast({ message: "Настройки сохранены", type: "success" })
              }
            >
              Успешный toast
            </Button>
            <Button
              outlined
              onClick={() =>
                setToast({ message: "Не удалось сохранить", type: "error" })
              }
            >
              Toast с ошибкой
            </Button>
            <Button ghost onClick={() => setToast(null)}>
              Скрыть toast
            </Button>
            <Toast toast={toast} />
          </div>

          <Tabs defaultValue="general">
            {(active, setActive) => (
              <TabsList aria-label="Раздел настроек">
                <TabsTrigger
                  active={active === "general"}
                  onClick={() => setActive("general")}
                  value="general"
                >
                  Основное
                </TabsTrigger>
                <TabsTrigger
                  active={active === "access"}
                  onClick={() => setActive("access")}
                  value="access"
                >
                  Доступ
                </TabsTrigger>
                <TabsTrigger active={false} disabled value="archive">
                  Архив
                </TabsTrigger>
              </TabsList>
            )}
          </Tabs>

          <StateGrid>
            {DEMO_STATES.map((state) => (
              <StateCell key={`tab-${state}`} state={state}>
                <div className="neo-tabs-list inline-flex h-10 items-center p-1">
                  <TabsTrigger
                    active={state === "pressed"}
                    disabled={state === "disabled"}
                    value={state}
                  >
                    Вкладка
                  </TabsTrigger>
                </div>
              </StateCell>
            ))}
          </StateGrid>

          <TooltipProvider>
            <Tooltip defaultOpen>
              <TooltipTrigger asChild>
                <Button outlined>Наведите для подсказки</Button>
              </TooltipTrigger>
              <TooltipContent>Пояснение без отдельной рамки.</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </ShowcaseSection>

        <ShowcaseSection title="Spinner, Skeleton, Separator и Label">
          <div className="flex items-center gap-3">
            <Spinner aria-label="Загрузка" role="status" />
            <span className="text-[var(--neo-text-secondary)]">
              Короткое действие
            </span>
          </div>
          <Card>
            <CardContent className="space-y-4">
              <Skeleton className="h-6 max-w-xs" />
              <Skeleton className="h-24" />
              <Separator />
              <Label>Загрузка блока обозначается скелетоном</Label>
            </CardContent>
          </Card>
        </ShowcaseSection>
      </div>
    </div>
  );
}

function ShowcaseSection({
  children,
  description,
  title,
}: {
  children: ReactNode;
  description?: string;
  title: string;
}) {
  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold">{title}</h2>
        {description && (
          <p className="mt-1 max-w-3xl text-sm text-[var(--neo-text-secondary)]">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

function StateGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      {children}
    </div>
  );
}

function StateCell({
  children,
  state,
}: {
  children: ReactNode;
  state: DemoState;
}) {
  return (
    <div
      className="flex min-h-28 flex-col justify-center gap-3"
      data-demo-state={state === "default" ? undefined : state}
    >
      <span className="text-xs text-[var(--neo-text-secondary)]">{state}</span>
      {children}
    </div>
  );
}
