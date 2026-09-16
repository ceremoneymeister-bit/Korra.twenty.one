import { createRoot } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";
import { useEffect } from "react";
import App from "@/App";
import { I18nProvider } from "@/i18n";
import { ThemeProvider } from "@/themes";
import { SystemActionsProvider } from "@/contexts/SystemActions";
import "@/index.css";

const scenario = new URLSearchParams(location.search).get("scenario") || "short";
function DemoNavigation() {
  const navigate = useNavigate();
  useEffect(() => {
    const click = (event: MouseEvent) => {
      const anchor = (event.target as Element).closest("a[href]");
      if (!anchor || event.defaultPrevented || event.ctrlKey || event.metaKey) return;
      const url = new URL(anchor.getAttribute("href")!, location.href);
      const base = window.__HERMES_BASE_PATH__ || "";
      if (url.origin === location.origin && url.pathname.startsWith(base + "/files")) {
        event.preventDefault(); navigate(url.pathname.slice(base.length) + url.search);
      }
    };
    document.addEventListener("click", click);
    return () => document.removeEventListener("click", click);
  }, [navigate]);
  return null;
}
createRoot(document.getElementById("root")!).render(
  <MemoryRouter initialEntries={[`/agents?agent=default&resume=demo-${scenario}`]}>
    <DemoNavigation /><I18nProvider><ThemeProvider><SystemActionsProvider><App /></SystemActionsProvider></ThemeProvider></I18nProvider>
  </MemoryRouter>,
);
