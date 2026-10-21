// Synthetic preview of the «Ваши ответы» tray: real component and CSS, fake rows.
import "../src/index.css";
import { createRoot } from "react-dom/client";
import { DecisionTray } from "../src/components/chat/DecisionTray";

const noop = () => {};
createRoot(document.getElementById("root")!).render(
  <div style={{ maxWidth: Number(new URLSearchParams(location.search).get("w")) || undefined }}>
    <DecisionTray onChange={noop} onRemove={noop} onSend={noop} drafts={[
      { kind: "approve", name: "Коммерческое предложение 1024 Пример.docx", path: "/w/1/КП.docx" },
      { kind: "change", name: "Калькуляция.xlsx", path: "/w/1/К.xlsx", note: "срок 30 дней" },
      { kind: "defer", name: "Отчёт.html", path: "/w/1/О.html" },
    ]} />
  </div>,
);
