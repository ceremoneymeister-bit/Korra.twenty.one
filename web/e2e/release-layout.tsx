// Real panel shell, routes and chat; synthetic HTTP, no backend or model.
import { installDemo } from "../prototypes/interface/demo-api";

installDemo();
window.__HERMES_BASE_PATH__ = "";
const demoFetch = window.fetch;
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.href);
  const data = url.pathname === "/api/updates/state" ? {
    installed: new URLSearchParams(location.search).has("seen") ? null
      : { release_id: "layout-fixture", title: "Браузер, файлы и удобная работа с агентами", sections: [] },
  } : ["/api/chat/decisions", "/api/chat/approvals"].includes(url.pathname) ? { data: [{
    request_id: "layout-decision", decision_kind: "outbound_message", effect_status: "pending",
    profile: "bitrix", source_session_id: "bitrix-0", choices: ["once", "deny"],
    command: "Кому: k21b-Локальная проверка · отправка отключена\nТестовый текст для проверки мобильной карточки.",
    description: "Проверьте адресата, текст и вложения перед внешней отправкой.",
    timestamp: Date.now() / 1000 - 16 * 86400,
  }] } : undefined;
  return data === undefined ? demoFetch(input, init)
    : new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
};
void import("../prototypes/interface/render");
