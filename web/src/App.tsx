import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type FocusEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  Routes,
  Route,
  NavLink,
  Navigate,
  useLocation,
  useNavigate,
} from "react-router";
import {
  Activity,
  BarChart3,
  BookOpen,
  ChevronDown,
  Clock,
  Code,
  Cpu,
  Database,
  Download,
  Eye,
  FolderOpen,
  FileText,
  Globe,
  Heart,
  KeyRound,
  Menu,
  MessageSquare,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Puzzle,
  Radio,
  RefreshCw,
  RotateCw,
  Settings,
  Shield,
  ShieldCheck,
  Sparkles,
  Star,
  Terminal,
  Users,
  Webhook,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { SelectionSwitcher } from "@nous-research/ui/ui/components/selection-switcher";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { KorraLoader } from "@/components/KorraLoader";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { cn } from "@/lib/utils";
import { SidebarFooter } from "@/components/SidebarFooter";
import { SidebarStatusStrip, gatewayLine } from "@/components/SidebarStatusStrip";
import { useBelowBreakpoint } from "@nous-research/ui/hooks/use-below-breakpoint";
import { useSidebarStatus } from "@/hooks/useSidebarStatus";
import { AuthWidget } from "@/components/AuthWidget";
import { PageHeaderProvider } from "@/contexts/PageHeaderProvider";
import { ProfileProvider } from "@/contexts/ProfileProvider";
import { useProfileScope } from "@/contexts/useProfileScope";
import { MemoryPressureBanner } from "@/components/MemoryPressureBanner";
import { useSystemActions } from "@/contexts/useSystemActions";
import type { SystemAction } from "@/contexts/system-actions-context";
// Route pages are lazy-loaded so the initial dashboard shell does not pay for
// every admin surface (and heavy deps like xterm) up front.
const ConfigPage = lazy(() => import("@/pages/ConfigPage"));
const DocsPage = lazy(() => import("@/pages/DocsPage"));
const ClientHelpPage = lazy(() => import("@/pages/ClientHelpPage"));
const EnvPage = lazy(() => import("@/pages/EnvPage"));
const FilesPage = lazy(() => import("@/pages/FilesPage"));
const SessionsPage = lazy(() => import("@/pages/SessionsPage"));
const LogsPage = lazy(() => import("@/pages/LogsPage"));
const AnalyticsPage = lazy(() => import("@/pages/AnalyticsPage"));
const ModelsPage = lazy(() => import("@/pages/ModelsPage"));
const CronPage = lazy(() => import("@/pages/CronPage"));
const ProfilesPage = lazy(() => import("@/pages/ProfilesPage"));
const ProfileBuilderPage = lazy(() => import("@/pages/ProfileBuilderPage"));
const SkillsPage = lazy(() => import("@/pages/SkillsPage"));
const PluginsPage = lazy(() => import("@/pages/PluginsPage"));
const McpPage = lazy(() => import("@/pages/McpPage"));
const PairingPage = lazy(() => import("@/pages/PairingPage"));
const ChannelsPage = lazy(() => import("@/pages/ChannelsPage"));
const WebhooksPage = lazy(() => import("@/pages/WebhooksPage"));
const SystemPage = lazy(() => import("@/pages/SystemPage"));
const ChatPage = lazy(() => import("@/pages/ChatPage"));
const BubbleChatPage = lazy(() => import("@/pages/BubbleChatPage"));
const AgentWorkbenchPage = lazy(() => import("@/pages/AgentWorkbenchPage"));
const UpdatesPage = lazy(() => import("@/pages/UpdatesPage"));
const UiKitPage = lazy(() => import("@/pages/UiKitPage"));
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { KorraBrand } from "@/components/KorraBrand";
import { useI18n } from "@/i18n";
import type { Translations } from "@/i18n/types";
import { PluginPage, PluginSlot, usePlugins } from "@/plugins";
import type { PluginManifest } from "@/plugins";
import { useTheme } from "@/themes";
import { EveningThemePrompt } from "@/components/EveningThemePrompt";
import {
  isDashboardBubbleChatEnabled,
  isDashboardEmbeddedChatEnabled,
  isProductUiMode,
  productUiMode,
} from "@/lib/dashboard-flags";
import {
  SHIPPED_PLUGIN_LABELS,
  productHomePath,
  resolveOpenGroup,
  selectProductNav,
  selectProductSidebar,
  type ProductSidebarGroups,
  type SidebarGroupChoice,
  type SidebarGroupKey,
} from "@/lib/product-nav";
import { latchChatActivation } from "@/lib/chat-activation";
import { isStaleBuild, loadedBuild } from "@/lib/build-version";
import { api } from "@/lib/api";
import type { StatusResponse, UpdateCheckResponse } from "@/lib/api";
import { russianInterfaceLabel } from "@/lib/russian-interface-text";

function RouteFallback({ label = "Загрузка…" }: { label?: string }) {
  return (
    <div
      className="flex min-h-[70vh] flex-1 items-center justify-center"
      aria-busy="true"
      aria-live="polite"
    >
      <KorraLoader label={label} />
    </div>
  );
}

function StaleBuildNotice({ build }: { build?: string | null }) {
  const [hidden, setHidden] = useState(false);
  if (hidden || !isStaleBuild(loadedBuild(), build)) return null;
  return (
    <div
      className="mb-3 flex flex-wrap items-center gap-3 rounded-xl border border-primary/25 bg-primary/[0.06] px-4 py-3 text-sm"
      role="status"
    >
      <span className="min-w-0 flex-1">
        Интерфейс обновился. Перезагрузите страницу, чтобы увидеть новую версию.
      </span>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-primary bg-primary px-4 py-2 font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
      >
        <RefreshCw className="size-4" aria-hidden />
        Обновить
      </button>
      <button
        type="button"
        onClick={() => setHidden(true)}
        className="min-h-11 rounded-lg px-3 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
      >
        Позже
      </button>
    </div>
  );
}

function RootRedirect() {
  return <Navigate to={productHomePath(productUiMode())} replace />;
}

function UnknownRouteFallback({ pluginsLoading }: { pluginsLoading: boolean }) {
  if (pluginsLoading) {
    // Render nothing during the plugin-load window — a spinner here would just flash.
    return null;
  }
  return (
    <div className="mx-auto flex max-w-lg flex-col gap-4 py-16">
      <h2 className="text-xl font-semibold">Такого раздела нет</h2>
      <p className="text-muted-foreground">Возможно, ссылка устарела. Откройте нужный раздел в меню или вернитесь к агентам.</p>
      <NavLink to={productHomePath(productUiMode())} className="neo-button w-fit px-5 py-3">К агентам</NavLink>
    </div>
  );
}

const CHAT_NAV_ITEM: NavItem = {
  path: "/chat",
  labelKey: "chat",
  label: "Chat",
  icon: Terminal,
};

/**
 * Built-in routes except /chat.  Chat is rendered persistently (outside
 * <Routes>) when embedded — see the persistent chat host block rendered
 * inline near the bottom of this file — so the PTY child, WebSocket,
 * and xterm instance survive when the user visits another tab and comes
 * back.  A `display:none` toggle hides the terminal without unmounting.
 * The host itself is still deferred until the first /chat visit so the
 * xterm chunk is not downloaded on unrelated pages.  Routing still owns
 * the URL so /chat deep-links, browser back/forward, and nav highlight
 * keep working.
 */
const BUILTIN_ROUTES_CORE: Record<string, ComponentType> = {
  "/": RootRedirect,
  "/agents": AgentsRouteSink,
  "/sessions": SessionsPage,
  "/files": FilesPage,
  "/analytics": AnalyticsPage,
  "/models": ModelsPage,
  "/logs": LogsPage,
  "/cron": CronPage,
  "/skills": SkillsPage,
  "/plugins": PluginsPage,
  "/mcp": McpPage,
  "/pairing": PairingPage,
  "/channels": ChannelsPage,
  "/webhooks": WebhooksPage,
  "/system": SystemPage,
  "/profiles": ProfilesPage,
  "/profiles/new": ProfileBuilderPage,
  "/config": ConfigPage,
  "/env": EnvPage,
  "/docs": DocsPage,
  "/help": ClientHelpPage,
  "/help/:article": ClientHelpPage,
  "/updates": UpdatesPage,
  "/ui-kit": UiKitPage,
};

// Route placeholder for /chat.  The persistent ChatPage host (rendered
// outside <Routes> when embedded chat is on) paints on top; this empty
// element just claims the path so the `*` catch-all redirect doesn't
// fire when the user navigates to /chat.
function ChatRouteSink() {
  return null;
}

// The workbench owns several live chat streams and therefore stays mounted
// outside ProfileKeyedRoutes; this placeholder only claims the URL.
function AgentsRouteSink() {
  return null;
}

const BUILTIN_NAV_REST: NavItem[] = [
  { path: "/agents", label: "Агенты", icon: Users },
  {
    path: "/sessions",
    labelKey: "sessions",
    label: "История",
    icon: MessageSquare,
  },
  { path: "/files", label: "Файлы", icon: FolderOpen },
  {
    path: "/analytics",
    labelKey: "analytics",
    label: "Аналитика",
    icon: BarChart3,
  },
  {
    path: "/models",
    labelKey: "models",
    label: "Модели",
    icon: Cpu,
  },
  { path: "/logs", labelKey: "logs", label: "Журналы", icon: FileText },
  { path: "/cron", labelKey: "cron", label: "Расписание", icon: Clock },
  { path: "/help", label: "Помощь", icon: BookOpen },
  { path: "/updates", label: "Обновления", icon: Download },
  { path: "/skills", labelKey: "skills", label: "Навыки", icon: Package },
  { path: "/plugins", labelKey: "plugins", label: "Плагины", icon: Puzzle },
  { path: "/mcp", label: "MCP", icon: Plug },
  { path: "/channels", label: "Каналы", icon: Radio },
  { path: "/webhooks", label: "Вебхуки", icon: Webhook },
  { path: "/pairing", label: "Подключения", icon: ShieldCheck },
  { path: "/profiles", labelKey: "profiles", label: "Профили", icon: Users },
  { path: "/config", labelKey: "config", label: "Конфигурация", icon: Settings },
  { path: "/env", labelKey: "keys", label: "Ключи", icon: KeyRound },
  { path: "/system", label: "Система", icon: Wrench },
  {
    path: "/docs",
    labelKey: "documentation",
    label: "Документация",
    icon: BookOpen,
  },
];

const ICON_MAP: Record<string, ComponentType<{ className?: string }>> = {
  Activity,
  BarChart3,
  Clock,
  Cpu,
  FileText,
  FolderOpen,
  KeyRound,
  MessageSquare,
  Package,
  Settings,
  Puzzle,
  Sparkles,
  Terminal,
  Globe,
  Database,
  Shield,
  Users,
  Wrench,
  Zap,
  Heart,
  Star,
  Code,
  Eye,
};

function resolveIcon(name: string): ComponentType<{ className?: string }> {
  return ICON_MAP[name] ?? Puzzle;
}

function buildNavItems(
  builtIn: NavItem[],
  manifests: PluginManifest[],
): NavItem[] {
  const items = [...builtIn];

  for (const manifest of manifests) {
    if (manifest.tab.override) continue;
    if (manifest.tab.hidden) continue;

    const pluginItem: NavItem = {
      path: manifest.tab.path,
      label:
        SHIPPED_PLUGIN_LABELS[manifest.tab.path] ??
        russianInterfaceLabel(manifest.label, manifest.name),
      icon: resolveIcon(manifest.icon),
    };

    const pos = manifest.tab.position ?? "end";
    if (pos === "end") {
      items.push(pluginItem);
    } else if (pos.startsWith("after:")) {
      const target = "/" + pos.slice(6);
      const idx = items.findIndex((i) => i.path === target);
      items.splice(idx >= 0 ? idx + 1 : items.length, 0, pluginItem);
    } else if (pos.startsWith("before:")) {
      const target = "/" + pos.slice(7);
      const idx = items.findIndex((i) => i.path === target);
      items.splice(idx >= 0 ? idx : items.length, 0, pluginItem);
    } else {
      items.push(pluginItem);
    }
  }

  return items;
}

/** Split merged nav into built-in sidebar entries vs plugin tabs, preserving plugin order hints. */
function partitionSidebarNav(
  builtIn: NavItem[],
  manifests: PluginManifest[],
): { coreItems: NavItem[]; pluginItems: NavItem[] } {
  const merged = buildNavItems(builtIn, manifests);
  const builtinPaths = new Set(builtIn.map((i) => i.path));
  const coreItems: NavItem[] = [];
  const pluginItems: NavItem[] = [];
  for (const item of merged) {
    if (builtinPaths.has(item.path)) coreItems.push(item);
    else pluginItems.push(item);
  }
  return { coreItems, pluginItems };
}

function buildRoutes(
  builtinRoutes: Record<string, ComponentType>,
  manifests: PluginManifest[],
): Array<{
  key: string;
  path: string;
  element: ReactNode;
}> {
  const byOverride = new Map<string, PluginManifest>();
  const addons: PluginManifest[] = [];

  for (const m of manifests) {
    if (m.tab.override) {
      byOverride.set(m.tab.override, m);
    } else {
      addons.push(m);
    }
  }

  const routes: Array<{
    key: string;
    path: string;
    element: ReactNode;
  }> = [];

  for (const [path, Component] of Object.entries(builtinRoutes)) {
    const om = byOverride.get(path);
    if (om) {
      routes.push({
        key: `override:${om.name}`,
        path,
        element: <PluginPage name={om.name} />,
      });
    } else {
      routes.push({ key: `builtin:${path}`, path, element: <Component /> });
    }
  }

  for (const m of addons) {
    if (m.tab.hidden) continue;
    if (m.tab.path === "/plugins") continue;
    if (builtinRoutes[m.tab.path]) continue;
    routes.push({
      key: `plugin:${m.name}`,
      path: m.tab.path,
      element: <PluginPage name={m.name} />,
    });
  }

  for (const m of manifests) {
    if (!m.tab.hidden) continue;
    if (m.tab.path === "/plugins") continue;
    if (builtinRoutes[m.tab.path] || m.tab.override) continue;
    routes.push({
      key: `plugin:hidden:${m.name}`,
      path: m.tab.path,
      element: <PluginPage name={m.name} />,
    });
  }

  return routes;
}

const SIDEBAR_COLLAPSED_KEY = "hermes-sidebar-collapsed";

export default function App() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const { manifests, loading: pluginsLoading } = usePlugins();
  const { theme } = useTheme();
  const { isBusy: systemBusy } = useSystemActions();
  const [mobileOpen, setMobileOpen] = useState(false);
  const closeMobile = useCallback(() => setMobileOpen(false), []);

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
    } catch {
      return false;
    }
  });
  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch { /* localStorage may be unavailable in private browsing */ }
      return next;
    });
  }, []);
  const isMobile = useBelowBreakpoint(1024);
  const isDesktopCollapsed = collapsed && !isMobile;
  const tooltipWarmRef = useRef(0);
  const { status: sidebarStatus, reachable: sidebarReachable } =
    useSidebarStatus();
  const isDocsRoute = pathname === "/docs" || pathname === "/docs/";
  const normalizedPath = pathname.replace(/\/$/, "") || "/";
  const uiMode = productUiMode();
  const isFleetMode = uiMode === "fleet";
  const isChatRoute = normalizedPath === "/chat";
  const isAgentsRoute = normalizedPath === "/agents";
  const isFullHeightRoute =
    isChatRoute || isAgentsRoute || normalizedPath === "/ui-kit";
  const embeddedChat = isDashboardEmbeddedChatEnabled();
  const bubbleChat = isDashboardBubbleChatEnabled();
  // Defer mounting the persistent chat host (and its xterm chunk) until the
  // user has actually opened /chat at least once. Sticky after that so the
  // PTY survives later tab switches.
  const [chatHostMounted, setChatHostMounted] = useState(isChatRoute);
  useEffect(() => {
    setChatHostMounted((prev) => latchChatActivation(prev, isChatRoute));
  }, [isChatRoute]);

  const [agentsHostMounted, setAgentsHostMounted] = useState(isAgentsRoute);
  useEffect(() => {
    setAgentsHostMounted((prev) => latchChatActivation(prev, isAgentsRoute));
  }, [isAgentsRoute]);

  // `dashboard.show_token_analytics` gates the Analytics nav item.  The
  // page itself remains reachable by URL (it renders an explanation when
  // the flag is off — see AnalyticsPage), but hiding the nav entry avoids
  // surfacing misleading token/cost numbers in the sidebar.  Default off.
  const [showTokenAnalytics, setShowTokenAnalytics] = useState(false);
  useEffect(() => {
    if (isProductUiMode()) return;
    api
      .getConfig()
      .then((cfg) => {
        const dash = (cfg?.dashboard ?? {}) as {
          show_token_analytics?: unknown;
        };
        setShowTokenAnalytics(dash.show_token_analytics === true);
      })
      .catch(() => setShowTokenAnalytics(false));
  }, []);

  // A plugin can replace the built-in /chat page via `tab.override: "/chat"`
  // in its manifest.  When one does, `buildRoutes` already swaps the route
  // element for <PluginPage /> — but we also have to suppress the
  // persistent ChatPage host below, or the plugin's page and the built-in
  // terminal would paint on top of each other.  The override is niche
  // (nothing ships overriding /chat today) but it's an advertised
  // extension point, so preserve the pre-persistence contract: when a
  // plugin owns /chat, the built-in chat UI is entirely absent.
  //
  // Waiting on `pluginsLoading` is load-bearing: manifests arrive
  // asynchronously from /api/dashboard/plugins, so on initial render
  // `chatOverriddenByPlugin` is always false.  Without the loading
  // gate, the persistent host would mount, spawn a PTY, and THEN get
  // yanked out from under the user when the plugin's manifest resolves
  // — killing the session mid-paint.  Delaying host mount by the
  // plugin-load window (typically <50ms, worst case 2s safety timeout)
  // is the cheaper trade-off.
  const chatOverriddenByPlugin = useMemo(
    () => manifests.some((m) => m.tab.override === "/chat"),
    [manifests],
  );

  const builtinRoutes = useMemo(
    () => ({
      ...BUILTIN_ROUTES_CORE,
      ...(!isFleetMode && bubbleChat
        ? { "/chat": BubbleChatPage }
        : !isFleetMode && embeddedChat
          ? { "/chat": ChatRouteSink }
          : {}),
    }),
    [bubbleChat, embeddedChat, isFleetMode],
  );

  const builtinNav = useMemo(() => {
    const base = embeddedChat
      ? [CHAT_NAV_ITEM, ...BUILTIN_NAV_REST]
      : BUILTIN_NAV_REST;
    const withAnalytics = showTokenAnalytics
      ? base
      : base.filter((n) => n.path !== "/analytics");
    return uiMode ? selectProductNav(withAnalytics, uiMode) : withAnalytics;
  }, [embeddedChat, showTokenAnalytics, uiMode]);

  const sidebarNav = useMemo(
    () => partitionSidebarNav(builtinNav, manifests),
    [builtinNav, manifests],
  );
  // Продуктовый сайдбар собирается одним решением: главный список, «Настройки»
  // и «Служебное» приходят из product-nav готовыми, поэтому пункт плагина не
  // может остаться в двух группах сразу или потеряться между ними.
  const productSidebar = useMemo<ProductSidebarGroups<NavItem> | null>(() => {
    if (!uiMode) return null;
    const source = embeddedChat || bubbleChat
      ? [CHAT_NAV_ITEM, ...BUILTIN_NAV_REST]
      : BUILTIN_NAV_REST;
    const { pluginItems } = partitionSidebarNav(source, manifests);
    return selectProductSidebar(source, pluginItems, uiMode);
  }, [bubbleChat, embeddedChat, manifests, uiMode]);
  const mainNav = productSidebar?.main ?? sidebarNav.coreItems;
  const productSettingsNav = productSidebar?.settings ?? [];
  const productServiceNav = productSidebar?.service ?? [];
  // Группы сайдбара — аккордеон (решение владельца 03.09): открыта одна,
  // клик по другой переключает, клик вне сайдбара закрывает. Группа текущего
  // экрана раскрывается сама (решение владельца 15.09), поэтому состояние
  // здесь — только выбор пользователя для конкретного маршрута, а не сама
  // открытая группа: иначе ручное сворачивание тут же отменялось бы.
  const [groupChoice, setGroupChoice] = useState<SidebarGroupChoice | null>(null);
  const openGroup = resolveOpenGroup(normalizedPath, productSidebar, groupChoice);
  const settingsOpen = openGroup === "settings";
  const serviceOpen = openGroup === "service";
  // Без useCallback: обработчики групп и так создаются на месте, а ручная
  // мемоизация с двумя зависимостями мешает React Compiler.
  const toggleGroup = (group: SidebarGroupKey) =>
    setGroupChoice({
      path: normalizedPath,
      group: openGroup === group ? null : group,
    });
  useEffect(() => {
    if (!openGroup) return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest?.("aside")) {
        setGroupChoice({ path: normalizedPath, group: null });
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePress);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress);
  }, [normalizedPath, openGroup]);
  const routes = useMemo(() => {
    const built = buildRoutes(builtinRoutes, manifests);
    if (!isFleetMode) return built;

    // /chat is deliberately unavailable in fleet, including when a plugin
    // attempts to add or override it. Keep an explicit redirect so deep links
    // and old bookmarks converge on the agents workbench.
    return [
      ...built.filter((route) => route.path !== "/chat"),
      {
        key: "fleet:/chat",
        path: "/chat",
        element: <RootRedirect />,
      },
    ];
  }, [builtinRoutes, isFleetMode, manifests]);
  const pluginTabMeta = useMemo(
    () =>
      manifests
        .filter((m) => !m.tab.hidden)
        .map((m) => {
          const path = m.tab.override ?? m.tab.path;
          return {
            path,
            label:
              SHIPPED_PLUGIN_LABELS[path] ??
              russianInterfaceLabel(m.label, m.name),
          };
        }),
    [manifests],
  );

  const layoutVariant = theme.layoutVariant ?? "standard";

  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [mobileOpen]);

  useEffect(() => {
    const mql = window.matchMedia("(min-width: 1024px)");
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setMobileOpen(false);
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return (
    <ProfileProvider>
    <div
      data-layout-variant={layoutVariant}
      data-client-ui={isProductUiMode() ? "true" : undefined}
      className="flex h-dvh max-h-dvh min-h-0 flex-col overflow-hidden bg-background-base text-text-primary antialiased"
    >
      <a
        href="#main-content"
        className="fixed left-3 top-3 z-[200] inline-flex min-h-[44px] -translate-y-20 items-center rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-lg transition-transform focus:translate-y-0"
      >
        К основному содержанию
      </a>
      <SelectionSwitcher />

      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 z-0"
      >
        <PluginSlot name="backdrop" />
      </div>

      <header
        className={cn(
          "lg:hidden fixed top-0 left-0 right-0 z-40 min-h-14",
          "flex items-center gap-2 px-4 py-2",

          "bg-background-base",
        )}
        style={{
          background: "var(--component-header-background)",
          borderImage: "var(--component-header-border-image)",
          clipPath: "var(--component-header-clip-path)",
        }}
      >
        <Button
          ghost
          size="icon"
          onClick={() => setMobileOpen(true)}
          aria-label={t.app.openNavigation}
          aria-expanded={mobileOpen}
          aria-controls="app-sidebar"
          className="text-text-secondary hover:text-midground"
        >
          <Menu />
        </Button>

        <KorraBrand themeName={theme.name} className="h-[18px]" />
      </header>

      {mobileOpen && (
        <Button
          ghost
          aria-label={t.app.closeNavigation}
          onClick={closeMobile}
          className={cn(
            "lg:hidden fixed inset-0 z-40 p-0 block",
            "bg-black/70",
          )}
        />
      )}

      {/* Single mobile header clearance for the banner stack + content. The
          fixed lg:hidden header is h-14/z-40; previously each banner carried
          its own mt-14 AND the content kept pt-14, so two visible banners
          stacked three offsets (NS-656 review P3). One spacer, applied once. */}
      <div aria-hidden className="h-14 shrink-0 lg:hidden" />
      <PluginSlot name="header-banner" />
      <MemoryPressureBanner status={sidebarStatus} />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex min-h-0 min-w-0 flex-1">
          <aside
            id="app-sidebar"
            aria-label={t.app.navigation}
            className={cn(
              "fixed top-0 left-0 z-50 flex h-dvh max-h-dvh w-64 min-h-0 flex-col font-sans",

              "bg-background-base",
              "transition-[transform] duration-200 ease-[cubic-bezier(0.23,1,0.32,1)]",
              mobileOpen ? "translate-x-0" : "-translate-x-full",
              "lg:sticky lg:top-0 lg:translate-x-0 lg:shrink-0 lg:overflow-hidden",
              "lg:transition-[width] lg:duration-300 lg:ease-[cubic-bezier(0.23,1,0.32,1)]",
              collapsed && "lg:w-14",
            )}
            style={{
              background: "var(--component-sidebar-background)",
              clipPath: "var(--component-sidebar-clip-path)",
              borderImage: "var(--component-sidebar-border-image)",
            }}
          >
            <div
              className={cn(
                "flex h-14 shrink-0 items-center gap-2",

                collapsed ? "lg:justify-center lg:px-0" : "px-4 justify-between",
              )}
            >
              <div
                className={cn(
                  "flex items-center gap-2",
                  collapsed && "lg:hidden",
                )}
              >
                <PluginSlot name="header-left" />

                <KorraBrand themeName={theme.name} />
              </div>

              <Button
                ghost
                size="icon"
                onClick={closeMobile}
                aria-label={t.app.closeNavigation}
                className="lg:hidden text-text-secondary hover:text-midground"
              >
                <X />
              </Button>

              <Button
                ghost
                size="icon"
                onClick={toggleCollapsed}
                aria-label={
                  collapsed ? t.common.expand : t.common.collapse
                }
                className="hidden lg:flex text-text-secondary hover:text-midground"
              >
                {collapsed ? (
                  <PanelLeftOpen className="h-4 w-4" />
                ) : (
                  <PanelLeftClose className="h-4 w-4" />
                )}
              </Button>
            </div>

            <nav
              className="min-h-0 w-full flex-1 overflow-y-auto overflow-x-hidden py-2"
              aria-label={t.app.navigation}
            >
              <ul className="flex flex-col">
                {mainNav.map((item) => (
                  <SidebarNavLink
                    closeMobile={closeMobile}
                    collapsed={isDesktopCollapsed}
                    item={item}
                    key={item.path}
                    t={t}
                    tooltipWarmRef={tooltipWarmRef}
                  />
                ))}
              </ul>

              {!isProductUiMode() && sidebarNav.pluginItems.length > 0 && (
                <div
                  aria-labelledby="hermes-sidebar-plugin-nav-heading"
                  className="flex flex-col pb-2"
                  role="group"
                >
                  <span
                    className={cn(
                      "px-5 pt-2.5 pb-1",
                      "font-sans text-display text-xs tracking-[0.12em] text-text-tertiary",
                      isDesktopCollapsed && "lg:hidden",
                    )}
                    id="hermes-sidebar-plugin-nav-heading"
                  >
                    {t.app.pluginNavSection}
                  </span>

                  <ul className="flex flex-col">
                    {sidebarNav.pluginItems.map((item) => (
                      <SidebarNavLink
                        closeMobile={closeMobile}
                        collapsed={isDesktopCollapsed}
                        item={item}
                        key={item.path}
                        t={t}
                        tooltipWarmRef={tooltipWarmRef}
                      />
                    ))}
                  </ul>
                </div>
              )}

              {productSettingsNav.length > 0 && (
                <div className="flex flex-col pb-2" role="group">
                  <button
                    type="button"
                    onClick={() => toggleGroup("settings")}
                    aria-label="Настройки"
                    title={isDesktopCollapsed ? "Настройки" : undefined}
                    aria-expanded={settingsOpen}
                    className={cn(
                      "flex items-center gap-2 px-5 pt-2.5 pb-1 text-left",
                      "font-sans text-display text-xs tracking-[0.12em] text-text-tertiary",
                      "hover:text-midground transition-colors",
                      isDesktopCollapsed && "lg:justify-center lg:px-0",
                    )}
                  >
                    <Settings className="h-4 w-4 shrink-0" aria-hidden />
                    <span className={cn(isDesktopCollapsed && "lg:hidden")}>Настройки</span>
                    <ChevronDown
                      aria-hidden
                      className={cn(
                        "h-3 w-3 shrink-0 transition-transform",
                        settingsOpen && "rotate-180",
                        isDesktopCollapsed && "lg:hidden",
                      )}
                    />
                  </button>
                  {settingsOpen && (
                    <ul className="flex flex-col">
                      {productSettingsNav.map((item) => (
                        <SidebarNavLink
                          closeMobile={closeMobile}
                          collapsed={isDesktopCollapsed}
                          item={item}
                          key={item.path}
                          t={t}
                          tooltipWarmRef={tooltipWarmRef}
                        />
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {productServiceNav.length > 0 && (
                <div className="flex flex-col pb-2" role="group">
                  <button
                    type="button"
                    onClick={() => toggleGroup("service")}
                    aria-label="Служебное"
                    title={isDesktopCollapsed ? "Служебное" : undefined}
                    aria-expanded={serviceOpen}
                    className={cn(
                      "flex items-center gap-2 px-5 pt-2.5 pb-1 text-left",
                      "font-sans text-display text-xs tracking-[0.12em] text-text-tertiary",
                      "hover:text-midground transition-colors",
                      isDesktopCollapsed && "lg:justify-center lg:px-0",
                    )}
                  >
                    <Wrench className="h-4 w-4 shrink-0" aria-hidden />
                    <span className={cn(isDesktopCollapsed && "lg:hidden")}>Служебное</span>
                    <ChevronDown
                      aria-hidden
                      className={cn(
                        "h-3 w-3 shrink-0 transition-transform",
                        serviceOpen && "rotate-180",
                        isDesktopCollapsed && "lg:hidden",
                      )}
                    />
                  </button>
                  {serviceOpen && (
                    <ul className="flex flex-col">
                      {productServiceNav.map((item) => (
                        <SidebarNavLink
                          closeMobile={closeMobile}
                          collapsed={isDesktopCollapsed}
                          item={item}
                          key={item.path}
                          t={t}
                          tooltipWarmRef={tooltipWarmRef}
                        />
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </nav>

            <SidebarSystemActions
              collapsed={isDesktopCollapsed}
              onNavigate={closeMobile}
              reachable={sidebarReachable}
              status={sidebarStatus}
              tooltipWarmRef={tooltipWarmRef}
            />

            <div
              className={cn(
                "flex shrink-0 items-center gap-2",
                "px-3 py-2",

                isDesktopCollapsed
                  ? "lg:flex-col lg:items-start lg:gap-3 lg:py-3"
                  : "justify-between",
              )}
            >
              <div
                className={cn(
                  "flex min-w-0 items-center gap-2",
                  isDesktopCollapsed && "lg:flex-col lg:items-start",
                )}
              >
                <PluginSlot name="header-right" />

                <SidebarIconWithTooltip
                  collapsed={isDesktopCollapsed}
                  label={t.theme?.switchTheme ?? "Сменить тему"}
                  tooltipWarmRef={tooltipWarmRef}
                >
                  <ThemeSwitcher collapsed={isDesktopCollapsed} dropUp />
                </SidebarIconWithTooltip>

              </div>
            </div>

            <AuthWidget collapsed={isDesktopCollapsed} />
            <div
              className={cn(
                "flex shrink-0 flex-col",
                isDesktopCollapsed && "lg:hidden",
              )}
            >
              <SidebarFooter status={sidebarStatus} />
            </div>
          </aside>

          <PageHeaderProvider pluginTabs={pluginTabMeta}>
            <div
              id="main-content"
              className={cn(
                "relative z-2 flex min-w-0 min-h-0 flex-1 flex-col",
                "px-3 sm:px-6",
                isFullHeightRoute
                  ? "pb-0 pt-1 sm:pt-2 lg:pt-4"
                  : "pt-2 sm:pt-4 lg:pt-6",
                isDocsRoute && "min-h-0 flex-1",
              )}
            >
              <PluginSlot name="pre-main" />
              <StaleBuildNotice build={sidebarStatus?.build} />
              <div
                className={cn(
                  "w-full min-w-0",
                  !isFullHeightRoute &&
                    "pb-[calc(2rem+env(safe-area-inset-bottom,0px))] lg:pb-8",
                  (isDocsRoute || isFullHeightRoute) &&
                    "min-h-0 flex flex-1 flex-col",
                )}
              >
                <ProfileKeyedRoutes>
                  <Suspense fallback={<RouteFallback />}>
                    <Routes>
                      {routes.map(({ key, path, element }) => (
                        <Route key={key} path={path} element={element} />
                      ))}
                      <Route
                        path="*"
                        element={
                          <UnknownRouteFallback pluginsLoading={pluginsLoading} />
                        }
                      />
                    </Routes>
                  </Suspense>
                </ProfileKeyedRoutes>

                {embeddedChat &&
                  !bubbleChat &&
                  !chatOverriddenByPlugin &&
                  (pluginsLoading ? (
                    isChatRoute ? (
                      <RouteFallback label="Загрузка чата…" />
                    ) : null
                  ) : chatHostMounted ? (
                    <div
                      data-chat-active={isChatRoute ? "true" : "false"}
                      className={cn(
                        "min-h-0 min-w-0",
                        isChatRoute ? "flex flex-1 flex-col" : "hidden",
                      )}
                      aria-hidden={!isChatRoute}
                    >
                      <Suspense
                        fallback={
                          isChatRoute ? (
                            <RouteFallback label="Загрузка чата…" />
                          ) : null
                        }
                      >
                        <ChatPage isActive={isChatRoute} />
                      </Suspense>
                    </div>
                  ) : isChatRoute ? (
                    <RouteFallback label="Загрузка чата…" />
                  ) : null)}

                {agentsHostMounted && (
                  <div
                    data-agents-active={isAgentsRoute ? "true" : "false"}
                    className={cn(
                      "min-h-0 min-w-0",
                      isAgentsRoute ? "flex flex-1 flex-col" : "hidden",
                    )}
                    aria-hidden={!isAgentsRoute}
                  >
                    <Suspense
                      fallback={
                        isAgentsRoute ? (
                          <RouteFallback label="Загрузка агентов…" />
                        ) : null
                      }
                    >
                      <AgentWorkbenchPage />
                    </Suspense>
                  </div>
                )}
              </div>
              <PluginSlot name="post-main" />
            </div>
          </PageHeaderProvider>
        </div>
      </div>

      <PluginSlot name="overlay" />
      <EveningThemePrompt blocked={systemBusy || sidebarReachable !== true || mobileOpen} />
    </div>
    </ProfileProvider>
  );
}

/**
 * Remounts the entire routed page tree when the global management profile
 * changes. Pages load their data on mount; without this, a page opened
 * under profile A would keep showing A's state while writes (via the
 * fetchJSON ?profile= injection) silently targeted the newly selected
 * profile B — the exact stale-target footgun the switcher exists to kill.
 * Keying by profile resets every page's local state so it refetches under
 * the new scope. The persistent ChatPage host below handles its own
 * remount (channel keyed on scopedProfile).
 */
function ProfileKeyedRoutes({ children }: { children: ReactNode }) {
  const { profile } = useProfileScope();
  return <div key={profile || "__own__"} className="contents">{children}</div>;
}

function SidebarNavLink({
  closeMobile,
  collapsed,
  item,
  tooltipWarmRef,
  t,
}: SidebarNavLinkProps) {
  const { path, label, labelKey, icon: Icon } = item;
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);

  const navLabel = labelKey
    ? ((t.app.nav as Record<string, string>)[labelKey] ?? label)
    : label;
  const showTooltip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <li
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      <NavLink
        to={path}
        end={path === "/sessions"}
        onClick={closeMobile}
        aria-label={collapsed ? navLabel : undefined}
        onFocus={collapsed ? showTooltip : undefined}
        onBlur={collapsed ? hideTooltip : undefined}
        className={({ isActive }) =>
          cn(
            // Пункты сайдбара — как список чатов (владелец 03.09): активный
            // выпуклый, наведение вдавленное, без полосок и подсветок.
            "group/nav relative mx-3 my-0.5 flex items-center gap-3",
            "rounded-[var(--neo-radius-control)] px-4 py-3 text-[15px]",
            "font-sans normal-case tracking-normal whitespace-nowrap cursor-pointer",
            "transition-[box-shadow,color] duration-200 focus-visible:outline-none",
            collapsed && "lg:mx-2 lg:justify-center lg:px-0",
            isActive
              ? "bg-[var(--neo-surface)] text-[var(--neo-text-primary)] shadow-[var(--neo-depth-1)]"
              : "text-[var(--neo-text-secondary)] hover:text-[var(--neo-text-primary)] hover:shadow-[var(--neo-inset-compact)]",
          )
        }
      >
        {() => (
          <>
            <Icon className="h-4 w-4 shrink-0" />

            <span
              className={cn(
                "truncate transition-opacity duration-300",
                collapsed ? "lg:hidden" : "lg:opacity-100",
              )}
            >
              {navLabel}
            </span>
          </>
        )}
      </NavLink>

      {collapsed && hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={navLabel} warmRef={tooltipWarmRef} />
      )}
    </li>
  );
}

function SidebarSystemActions({
  collapsed,
  onNavigate,
  reachable,
  status,
  tooltipWarmRef,
}: SidebarSystemActionsProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { activeAction, isBusy, isRunning, pendingAction, runAction } =
    useSystemActions();
  const canUpdateHermes = status?.can_update_hermes === true;
  const [restartConfirmOpen, setRestartConfirmOpen] = useState(false);
  const [updateConfirmOpen, setUpdateConfirmOpen] = useState(false);
  const [updateConfirmInfo, setUpdateConfirmInfo] =
    useState<UpdateCheckResponse | null>(null);
  const [updateConfirmChecking, setUpdateConfirmChecking] = useState(false);

  useEffect(() => {
    if (!updateConfirmOpen) {
      setUpdateConfirmInfo(null);
      return;
    }
    let cancelled = false;
    setUpdateConfirmChecking(true);
    api
      .checkHermesUpdate(false)
      .then((info) => {
        if (!cancelled) setUpdateConfirmInfo(info);
      })
      .catch(() => {
        if (!cancelled) setUpdateConfirmInfo(null);
      })
      .finally(() => {
        if (!cancelled) setUpdateConfirmChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [updateConfirmOpen]);

  const updateConfirmDescription = useMemo(() => {
    if (updateConfirmInfo?.behind && updateConfirmInfo.behind > 0) {
      const cmd = updateConfirmInfo.update_command;
      const n = updateConfirmInfo.behind;
      return `Будет выполнена команда korra update (${cmd}) и загружено новых коммитов: ${n}. После обновления шлюз перезапустится; текущая сессия до этого сохранит кэш промпта.`;
    }
    const cmd = updateConfirmInfo?.update_command ?? "korra update";
    return (
      t.status.updateHermesConfirmMessage ??
      `Будет выполнена команда korra update (${cmd}), затем шлюз перезапустится.`
    );
  }, [t.status.updateHermesConfirmMessage, updateConfirmInfo]);

  const items: SystemActionItem[] = isProductUiMode()
    ? []
    : [
        {
          action: "restart",
          icon: RotateCw,
          label: t.status.restartGateway,
          runningLabel: t.status.restartingGateway,
          spin: true,
        },
      ];
  if (!isProductUiMode() && canUpdateHermes) {
    items.push({
      action: "update",
      icon: Download,
      label: t.status.updateHermes,
      runningLabel: t.status.updatingHermes,
      spin: false,
    });
  }

  const handleClick = (action: SystemAction) => {
    if (isBusy) return;
    if (action === "restart") {
      setRestartConfirmOpen(true);
      return;
    }
    if (action === "update") {
      setUpdateConfirmOpen(true);
      return;
    }
    void runAction(action);
    navigate("/sessions");
    onNavigate();
  };

  const confirmRestart = () => {
    setRestartConfirmOpen(false);
    void runAction("restart");
    navigate("/sessions");
    onNavigate();
  };

  const confirmUpdate = () => {
    setUpdateConfirmOpen(false);
    void runAction("update");
    navigate("/sessions");
    onNavigate();
  };

  return (
    <>
    <div
      className={cn(
        "shrink-0 flex flex-col",

        "py-1",
      )}
    >
      <span
        className={cn(
          "px-5 pt-0.5 pb-0.5",
          "font-sans text-display text-xs tracking-[0.12em] text-text-tertiary",
          collapsed && "lg:hidden",
        )}
      >
        {t.app.system}
      </span>

      <div className={cn(collapsed && "lg:hidden")}>
        <SidebarStatusStrip reachable={reachable} status={status} />
      </div>

      <GatewayDot
        collapsed={collapsed}
        reachable={reachable}
        status={status}
        tooltipWarmRef={tooltipWarmRef}
      />

      <ul className="flex flex-col">
        {items.map((item) => (
          <SystemActionButton
            key={item.action}
            collapsed={collapsed}
            disabled={isBusy && !(pendingAction === item.action || (activeAction === item.action && isRunning))}
            tooltipWarmRef={tooltipWarmRef}
            isPending={pendingAction === item.action}
            isRunning={activeAction === item.action && isRunning && pendingAction !== item.action}
            item={item}
            onClick={() => handleClick(item.action)}
          />
        ))}
      </ul>
    </div>

    <ConfirmDialog
      cancelLabel={t.common.cancel}
      confirmLabel={t.status.restartGateway}
      description={
        t.status.restartGatewayConfirmMessage ??
        "Процесс шлюза Korra будет перезапущен. Каналы и активные сессии подключатся заново."
      }
      loading={pendingAction === "restart"}
      onCancel={() => setRestartConfirmOpen(false)}
      onConfirm={confirmRestart}
      open={restartConfirmOpen}
      title={
        t.status.restartGatewayConfirmTitle ?? `${t.status.restartGateway}?`
      }
    />

    <ConfirmDialog
      cancelLabel={t.common.cancel}
      confirmLabel={t.status.updateHermesConfirmNow ?? "Обновить сейчас"}
      description={
        updateConfirmChecking ? t.common.loading : updateConfirmDescription
      }
      loading={pendingAction === "update" || updateConfirmChecking}
      onCancel={() => setUpdateConfirmOpen(false)}
      onConfirm={confirmUpdate}
      open={updateConfirmOpen}
      title={t.status.updateHermesConfirmTitle ?? `${t.status.updateHermes}?`}
    />
    </>
  );
}

function SystemActionButton({
  collapsed,
  disabled,
  isPending,
  isRunning: isActionRunning,
  item,
  onClick,
  tooltipWarmRef,
}: SystemActionButtonProps) {
  const { icon: Icon, label, runningLabel, spin } = item;
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const busy = isPending || isActionRunning;
  const displayLabel = isActionRunning ? runningLabel : label;
  const showTooltip = (event: MouseEvent<HTMLElement> | FocusEvent<HTMLElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <li
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      <button
        onClick={onClick}
        disabled={disabled}
        aria-busy={busy}
        aria-label={collapsed ? displayLabel : undefined}
        onFocus={collapsed ? showTooltip : undefined}
        onBlur={collapsed ? hideTooltip : undefined}
        type="button"
        className={cn(
          "group/action relative flex w-full items-center gap-3",
          "px-5 py-2.5",
          "font-sans text-display text-xs tracking-[0.1em]",
          "whitespace-nowrap transition-colors cursor-pointer",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground",
          busy
            ? "text-midground"
            : "text-text-secondary hover:text-midground",
          "disabled:text-text-disabled disabled:cursor-not-allowed",
        )}
      >
        {isPending ? (
          <Spinner className="shrink-0 text-[0.875rem]" />
        ) : isActionRunning && spin ? (
          <Spinner className="shrink-0 text-[0.875rem]" />
        ) : (
          <Icon
            className={cn(
              "h-3.5 w-3.5 shrink-0",
              isActionRunning && !spin && "animate-pulse",
            )}
          />
        )}

        <span className={cn(
          "truncate transition-opacity duration-300",
          collapsed ? "lg:opacity-0" : "lg:opacity-100",
        )}>
          {displayLabel}
        </span>

        <span
          aria-hidden
          className="absolute inset-y-0.5 left-1.5 right-1.5 bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover/action:opacity-5"
        />

        {busy && (
          <span
            aria-hidden
            className="absolute left-0 top-0 bottom-0 w-px bg-midground"
          />
        )}
      </button>

      {collapsed && hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={displayLabel} warmRef={tooltipWarmRef} />
      )}
    </li>
  );
}

function SidebarIconWithTooltip({
  children,
  collapsed,
  label,
  tooltipWarmRef,
}: SidebarIconWithTooltipProps) {
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);
  const showTooltip = (event: MouseEvent<HTMLDivElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <div
      className={cn(
        "relative w-fit",
        collapsed && "group/icon",
      )}
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
    >
      {children}

      {collapsed && (
        <span
          aria-hidden
          className="absolute inset-y-0 inset-x-[-0.375rem] bg-midground opacity-0 pointer-events-none transition-opacity duration-200 group-hover/icon:opacity-5 hidden lg:block"
        />
      )}

      {collapsed && hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={label} warmRef={tooltipWarmRef} />
      )}
    </div>
  );
}

function GatewayDot({ collapsed, reachable, status, tooltipWarmRef }: GatewayDotProps) {
  const { t } = useI18n();
  const [hovered, setHovered] = useState(false);
  const [tooltipAnchor, setTooltipAnchor] = useState<HTMLElement | null>(null);

  const toneToColor: Record<string, string> = {
    "text-success": "bg-success",
    "text-warning": "bg-warning",
    "text-destructive": "bg-destructive",
    "text-muted-foreground": "bg-muted-foreground",
  };

  let color: string;
  let label: string;

  // Свёрнутая панель показывает только точку — при обрыве она обязана
  // покраснеть вместе с подписью, а не держать последний удачный ответ.
  if (reachable === false) {
    color = "bg-destructive";
    label = "Панель недоступна";
  } else if (!status) {
    color = "bg-midground/20";
    label = t.status.gateway;
  } else {
    const gw = gatewayLine(status, t);
    color = toneToColor[gw.tone] ?? "bg-muted-foreground";
    label = `${t.status.gateway} ${gw.label}`;
  }
  const showTooltip = (event: MouseEvent<HTMLDivElement> | FocusEvent<HTMLDivElement>) => {
    setHovered(true);
    setTooltipAnchor(event.currentTarget);
  };
  const hideTooltip = () => {
    setHovered(false);
    setTooltipAnchor(null);
  };

  return (
    <div
      className={cn(
        "hidden lg:flex py-3 pl-[1.625rem] transition-opacity duration-300",
        collapsed ? "lg:opacity-100" : "lg:opacity-0 lg:h-0 lg:py-0 lg:overflow-hidden",
      )}
      role="status"
      aria-label={label}
      tabIndex={collapsed ? 0 : -1}
      onMouseEnter={collapsed ? showTooltip : undefined}
      onMouseLeave={collapsed ? hideTooltip : undefined}
      onFocus={collapsed ? showTooltip : undefined}
      onBlur={collapsed ? hideTooltip : undefined}
    >
      <span
        aria-hidden
        className={cn("h-1.5 w-1.5 rounded-full", color)}
      />

      {hovered && tooltipAnchor && (
        <SidebarTooltip anchor={tooltipAnchor} label={label} warmRef={tooltipWarmRef} />
      )}
    </div>
  );
}

function SidebarTooltip({ anchor, label, warmRef }: SidebarTooltipProps) {
  const rect = anchor.getBoundingClientRect();
  const sidebar = document.getElementById("app-sidebar");
  const sidebarRight = sidebar?.getBoundingClientRect().right ?? rect.right;
  const [isWarm, setIsWarm] = useState(false);

  useEffect(() => {
    if (!warmRef) {
      setIsWarm(false);
      return;
    }
    const now = Date.now();
    setIsWarm(now - warmRef.current < 300);
    warmRef.current = now;
    return () => {
      if (warmRef) warmRef.current = Date.now();
    };
  }, [warmRef]);

  return createPortal(
    <span
      className={cn(
        "neo-tooltip fixed z-[100] pointer-events-none",
        "px-2 py-1",
        "font-sans text-display text-xs tracking-[0.1em] text-midground uppercase",
      )}
      style={{
        top: rect.top + rect.height / 2,
        left: sidebarRight + 8,
        transform: "translateY(-50%)",
        opacity: isWarm ? 1 : undefined,
        animation: isWarm ? "none" : "sidebar-tooltip-in 120ms ease-out",
      }}
    >
      {label}
    </span>,
    document.body,
  );
}

type TooltipWarmRef = React.RefObject<number>;

interface GatewayDotProps {
  collapsed: boolean;
  reachable: boolean | null;
  status: StatusResponse | null;
  tooltipWarmRef: TooltipWarmRef;
}

interface NavItem {
  icon: ComponentType<{ className?: string }>;
  label: string;
  labelKey?: string;
  path: string;
}

interface SidebarIconWithTooltipProps {
  children: ReactNode;
  collapsed: boolean;
  label: string;
  tooltipWarmRef: TooltipWarmRef;
}

interface SidebarNavLinkProps {
  closeMobile: () => void;
  collapsed: boolean;
  item: NavItem;
  t: Translations;
  tooltipWarmRef: TooltipWarmRef;
}

interface SidebarSystemActionsProps {
  collapsed: boolean;
  onNavigate: () => void;
  reachable: boolean | null;
  status: StatusResponse | null;
  tooltipWarmRef: TooltipWarmRef;
}

interface SidebarTooltipProps {
  anchor: HTMLElement;
  label: string;
  warmRef?: TooltipWarmRef;
}

interface SystemActionButtonProps {
  collapsed: boolean;
  disabled: boolean;
  isPending: boolean;
  isRunning: boolean;
  item: SystemActionItem;
  onClick: () => void;
  tooltipWarmRef: TooltipWarmRef;
}

interface SystemActionItem {
  action: SystemAction;
  icon: ComponentType<{ className?: string }>;
  label: string;
  runningLabel: string;
  spin: boolean;
}
