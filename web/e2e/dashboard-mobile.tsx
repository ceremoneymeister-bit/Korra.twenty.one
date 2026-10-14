// Real product shell and dashboard; all sources are synthetic and local.
import { installDemo } from "../prototypes/interface/demo-api";
import { dashboardStateFixture, FIXTURE_NOW } from "../src/components/dashboard/dashboard-state.fixture";
import { DASHBOARD_CATALOG } from "../src/components/dashboard/widget-catalog";
import { defaultLayout } from "../src/lib/dashboard-layout";

installDemo();
window.__HERMES_BASE_PATH__ = "";
const demoFetch = window.fetch;
const state = dashboardStateFixture();
state.quota.windows![0].remaining_percent = 71;
state.quota.windows![0].used_percent = 29;
state.quota.windows![0].forecast = {
  pace: 1.7,
  exhausts_at: FIXTURE_NOW + 86400 * 3,
  exhausts_before_reset: true,
};
state.quota.windows![0].resets_at = FIXTURE_NOW + 86400 * 6;
const layout = defaultLayout(DASHBOARD_CATALOG);
layout.order = ["attention", "codex-quota", "calendar", "agents", ...layout.order.filter(id => !["attention", "codex-quota", "calendar", "agents"].includes(id))];
layout.sizes.calendar = "s";
layout.sizes.agents = "s";
const calendar = {
  timezone: state.timezone,
  now: "2026-09-23T13:00:00+03:00",
  window: { start: "2026-09-23T00:00:00+03:00", end: "2026-09-30T00:00:00+03:00", days: 7 },
  google: { state: "connected", source_profile: "default", action: null, account: "owner@example.com", fetched_at: "2026-09-23T10:00:00Z", stale: false, error: null },
  schedule: { state: "ok" },
  items: [{ kind: "event", id: "synthetic-meeting", title: "Созвон с поставщиком", start: "2026-09-23T14:00:00+03:00", end: "2026-09-23T15:00:00+03:00", all_day: false }],
};
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.href);
  const path = url.pathname;
  const data = path === "/api/dashboard/state" ? state
    : path === "/api/dashboard/layout" ? { ...layout, revision: 1, scope: "synthetic-mobile" }
    : path === "/api/dashboard/calendar" ? calendar
    : undefined;
  return data === undefined ? demoFetch(input, init)
    : new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
};
void import("../prototypes/interface/render");
