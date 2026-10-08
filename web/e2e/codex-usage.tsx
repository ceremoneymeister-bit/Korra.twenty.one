// Synthetic dashboard transport in check_codex_usage.py; no live backend.
import "../src/index.css";
import "../src/components/dashboard/dashboard-grid.css";
import "../src/components/dashboard/dashboard-widgets.css";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { DashboardWidgetCard } from "../src/components/dashboard/DashboardWidgetCard";
import { CODEX_QUOTA_WIDGET } from "../src/components/dashboard/widgets/CodexQuotaWidget";

const Body = CODEX_QUOTA_WIDGET.Body;
createRoot(document.getElementById("root")!).render(
  <MemoryRouter>
    <main className="korra-dashboard" style={{ padding: 16 }}>
      <ul className="korra-dashboard__grid">
        <li className="korra-dashboard__tile" data-size="l">
          <DashboardWidgetCard title="Лимит Codex" purpose={CODEX_QUOTA_WIDGET.purpose} widgetId="codex-quota" size="l">
            <Body size="l" />
          </DashboardWidgetCard>
        </li>
      </ul>
    </main>
  </MemoryRouter>,
);
