import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import {
  Activity,
  Brain,
  Check,
  Clock,
  Copy,
  Cpu,
  Database,
  Download,
  Globe,
  HardDrive,
  KeyRound,
  Link2,
  Play,
  Plus,
  Power,
  RotateCw,
  Server,
  Share2,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Terminal,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Select, SelectOption } from "@nous-research/ui/ui/components/select";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { useModalBehavior } from "@/hooks/useModalBehavior";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { HermesConsoleModal } from "@/components/HermesConsoleModal";
import { cn, themedBody } from "@/lib/utils";
import { api } from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";
import { ownerFacingError } from "@/lib/owner-facing-error";
import { russianInterfaceText } from "@/lib/russian-interface-text";
import type {
  StatusResponse,
  MemoryStatus,
  MemoryProviderInfo,
  CredentialPoolProvider,
  CheckpointsResponse,
  HooksResponse,
  HookEntry,
  SystemStats,
  UpdateCheckResponse,
  CuratorStatus,
  PortalStatus,
  DebugShareResponse,
} from "@/lib/api";
import { useI18n } from "@/i18n";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDuration(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

type BackupImportTarget =
  | { kind: "upload"; file: File }
  | { kind: "path"; path: string };

function backupImportLabel(target: BackupImportTarget | null, fallback: string): string {
  if (!target) return fallback;
  return target.kind === "upload" ? target.file.name : target.path;
}

function backupFileName(path: string | null, fallback: string): string {
  if (!path) return fallback;
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

/**
 * Live action-log viewer for the spawn-based admin actions (doctor, audit,
 * backup, import, skills update, checkpoints prune, gateway start/stop).
 * Polls /api/actions/<name>/status until the process exits.
 */
function ActionLogViewer({
  action,
  onClose,
  onComplete,
}: {
  action: string;
  onClose: () => void;
  onComplete?: (action: string, exitCode: number | null) => void;
}) {
  const { tr } = useI18n();
  const [lines, setLines] = useState<string[]>([]);
  const [running, setRunning] = useState(true);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const completeRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    completeRef.current = false;
    const poll = async () => {
      try {
        const st = await api.getActionStatus(action, 400);
        if (cancelled) return;
        setLines(st.lines);
        setRunning(st.running);
        setExitCode(st.exit_code);
        if (!st.running && !completeRef.current) {
          completeRef.current = true;
          onComplete?.(action, st.exit_code);
        }
        if (st.running) timer.current = setTimeout(poll, 1200);
      } catch {
        if (!cancelled) setRunning(false);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [action, onComplete]);

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <span className="font-mono text-sm">{action}</span>
            {running ? (
              <Badge tone="warning">{tr("running")}</Badge>
            ) : (
              <Badge tone={exitCode === 0 ? "success" : "destructive"}>
                {exitCode === 0 ? tr("done") : tr("exit {code}", { code: exitCode ?? "?" })}
              </Badge>
            )}
          </div>
          <Button ghost size="icon" onClick={onClose} aria-label={tr("Close log")}>
            <X />
          </Button>
        </div>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words bg-background/50 border border-border p-3 text-xs font-mono text-muted-foreground">
          {lines.length ? lines.join("\n") : tr("Starting…")}
        </pre>
      </CardContent>
    </Card>
  );
}

const HOOK_EVENTS_FALLBACK = [
  "pre_tool_call",
  "post_tool_call",
  "pre_llm_call",
  "post_llm_call",
  "on_session_start",
  "on_session_end",
];

const MEMORY_STATUS_LABEL: Record<MemoryProviderInfo["status"], string> = {
  ready: "ready",
  needs_config: "needs setup",
  unavailable: "unavailable",
  missing: "missing",
};

const MEMORY_STATUS_TONE: Record<
  MemoryProviderInfo["status"],
  "success" | "warning" | "destructive" | "secondary"
> = {
  ready: "success",
  needs_config: "warning",
  unavailable: "destructive",
  missing: "destructive",
};

export default function SystemPage() {
  const { toast, showToast } = useToast();
  const { t, tr } = useI18n();

  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [memory, setMemory] = useState<MemoryStatus | null>(null);
  const [pool, setPool] = useState<CredentialPoolProvider[]>([]);
  const [checkpoints, setCheckpoints] = useState<CheckpointsResponse | null>(
    null,
  );
  const [hooks, setHooks] = useState<HooksResponse | null>(null);
  const [curator, setCurator] = useState<CuratorStatus | null>(null);
  const [portal, setPortal] = useState<PortalStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(false);

  // Add-credential form.
  const [credProvider, setCredProvider] = useState("openrouter");
  const [credKey, setCredKey] = useState("");
  const [credLabel, setCredLabel] = useState("");
  const [addingCred, setAddingCred] = useState(false);

  const [pendingBackupArchive, setPendingBackupArchive] = useState<string | null>(
    null,
  );
  const [downloadableBackupArchive, setDownloadableBackupArchive] = useState<
    string | null
  >(null);
  const [downloadingBackup, setDownloadingBackup] = useState(false);
  const importUploadInputRef = useRef<HTMLInputElement | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPath, setImportPath] = useState("");
  // Restore-from-backup is destructive (overwrites the live config) and the
  // spawned `hermes import` runs non-interactively (stdin is /dev/null), so
  // its CLI "Continue? [y/N]" prompt would auto-abort. The dashboard owns the
  // consent: confirm here, then call the endpoint with force=true.
  const [importingBackup, setImportingBackup] = useState(false);
  const [importConfirmTarget, setImportConfirmTarget] =
    useState<BackupImportTarget | null>(null);

  // Create-hook modal.
  const [hookModalOpen, setHookModalOpen] = useState(false);
  const closeHookModal = useCallback(() => setHookModalOpen(false), []);
  const hookModalRef = useModalBehavior({
    open: hookModalOpen,
    onClose: closeHookModal,
  });
  const [hookEvent, setHookEvent] = useState("pre_tool_call");
  const [hookCommand, setHookCommand] = useState("");
  const [hookMatcher, setHookMatcher] = useState("");
  const [hookTimeout, setHookTimeout] = useState("");
  const [hookApprove, setHookApprove] = useState(true);
  const [creatingHook, setCreatingHook] = useState(false);

  // ── Update check ───────────────────────────────────────────────────
  const [updateInfo, setUpdateInfo] = useState<UpdateCheckResponse | null>(
    null,
  );
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [updateConfirmOpen, setUpdateConfirmOpen] = useState(false);

  const loadAll = useCallback(() => {
    Promise.allSettled([
      api.getStatus(),
      api.getSystemStats(),
      api.getMemory(),
      api.getCredentialPool(),
      api.getCheckpoints(),
      api.getHooks(),
      api.getCurator(),
      api.getPortal(),
      // Cached (non-forced) check so the version row shows update status on
      // load without a separate effect / a forced network round-trip.
      api.checkHermesUpdate(false),
    ])
      .then(([s, st, m, p, c, h, cur, prt, upd]) => {
        if (s.status === "fulfilled") setStatus(s.value);
        if (st.status === "fulfilled") setStats(st.value);
        if (m.status === "fulfilled") setMemory(m.value);
        if (p.status === "fulfilled") setPool(p.value.providers);
        if (c.status === "fulfilled") setCheckpoints(c.value);
        if (h.status === "fulfilled") setHooks(h.value);
        if (cur.status === "fulfilled") setCurator(cur.value);
        if (prt.status === "fulfilled") setPortal(prt.value);
        if (upd.status === "fulfilled") setUpdateInfo(upd.value);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // ── Gateway lifecycle ──────────────────────────────────────────────
  const runGateway = async (verb: "start" | "stop" | "restart") => {
    try {
      if (verb === "start") {
        await api.startGateway();
        setActiveAction("gateway-start");
      } else if (verb === "stop") {
        await api.stopGateway();
        setActiveAction("gateway-stop");
      } else {
        await api.restartGateway();
        setActiveAction("gateway-restart");
      }
      showToast(tr("Gateway {verb} started", { verb: tr(verb) }), "success");
      setTimeout(loadAll, 3000);
    } catch (e) {
      showToast(tr("Gateway {verb} failed: {error}", { verb: tr(verb), error: ownerFacingError(e, "подробности недоступны") }), "error");
    }
  };

  // ── Curator ────────────────────────────────────────────────────────
  const toggleCuratorPaused = async () => {
    if (!curator) return;
    try {
      await api.setCuratorPaused(!curator.paused);
      showToast(curator.paused ? tr("Curator resumed") : tr("Curator paused"), "success");
      loadAll();
    } catch (e) {
      showToast(tr("Curator toggle failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    }
  };

  // ── Memory ─────────────────────────────────────────────────────────
  // Memory provider selection lives on the /plugins page now (see the
  // read-only display + link below); the dropdown was intentionally
  // dropped from this card during the admin-panel refresh.
  const memoryReset = useConfirmDelete({
    onDelete: useCallback(
      async (target: string) => {
        try {
          const res = await api.resetMemory(
            target as "all" | "memory" | "user",
          );
          showToast(tr("Reset: {items}", { items: res.deleted.join(", ") || tr("nothing") }), "success");
          loadAll();
        } catch (e) {
          showToast(tr("Reset failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
          throw e;
        }
      },
      [loadAll, showToast, tr],
    ),
  });

  // ── Credential pool ────────────────────────────────────────────────
  const addCredential = async () => {
    if (!credProvider.trim() || !credKey.trim()) {
      showToast(tr("Provider and API key required"), "error");
      return;
    }
    setAddingCred(true);
    try {
      await api.addCredentialPoolEntry(
        credProvider.trim(),
        credKey.trim(),
        credLabel.trim() || undefined,
      );
      showToast(tr("Credential added"), "success");
      setCredKey("");
      setCredLabel("");
      loadAll();
    } catch (e) {
      showToast(tr("Failed to add credential: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    } finally {
      setAddingCred(false);
    }
  };

  const credDelete = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const [provider, idxStr] = key.split("|");
        try {
          await api.removeCredentialPoolEntry(provider, Number(idxStr));
          showToast(tr("Credential removed"), "success");
          loadAll();
        } catch (e) {
          showToast(tr("Failed to remove: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
          throw e;
        }
      },
      [loadAll, showToast, tr],
    ),
  });

  // ── Operations ─────────────────────────────────────────────────────
  const runOp = async (fn: () => Promise<{ name: string }>, label: string) => {
    try {
      const res = await fn();
      setActiveAction(res.name);
      showToast(tr("{label} started", { label }), "success");
    } catch (e) {
      showToast(tr("{label} failed: {error}", { label, error: ownerFacingError(e, "подробности недоступны") }), "error");
    }
  };

  const runDashboardBackup = async () => {
    try {
      const res = await api.runBackup();
      setActiveAction(res.name);
      setPendingBackupArchive(res.archive ?? null);
      setDownloadableBackupArchive(null);
      showToast(tr("Backup started"), "success");
    } catch (e) {
      showToast(tr("Backup failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    }
  };

  const handleActionComplete = useCallback(
    (action: string, exitCode: number | null) => {
      if (action === "backup" && pendingBackupArchive) {
        if (exitCode === 0) {
          setDownloadableBackupArchive(pendingBackupArchive);
          showToast(tr("Backup ready to download"), "success");
        } else {
          setPendingBackupArchive(null);
        }
      }
    },
    [pendingBackupArchive, showToast, tr],
  );

  const downloadBackup = async () => {
    const archive = downloadableBackupArchive;
    if (!archive) return;
    setDownloadingBackup(true);
    try {
      const res = await api.downloadBackup(archive);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = backupFileName(archive, tr("Backup archive"));
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      showToast(tr("Download failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    } finally {
      setDownloadingBackup(false);
    }
  };

  const clearImportFile = () => {
    setImportFile(null);
    if (importUploadInputRef.current) importUploadInputRef.current.value = "";
  };

  const runBackupImport = async (target: BackupImportTarget) => {
    setImportingBackup(true);
    try {
      const res =
        target.kind === "upload"
          ? await api.runImportUpload(target.file, true)
          : await api.runImport(target.path, true);
      setActiveAction(res.name);
      showToast(tr("Import started"), "success");
      if (target.kind === "upload") clearImportFile();
    } catch (e) {
      showToast(tr("Import failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    } finally {
      setImportingBackup(false);
    }
  };

  // ── Debug share ────────────────────────────────────────────────────
  // Unlike the fire-and-forget ops above, `debug share` produces shareable
  // paste URLs that are the whole point — so we surface them as real,
  // copyable links rather than a log tail.
  const [shareRedact, setShareRedact] = useState(true);
  const [sharing, setSharing] = useState(false);
  const [shareResult, setShareResult] = useState<DebugShareResponse | null>(
    null,
  );
  const [copiedLabel, setCopiedLabel] = useState<string | null>(null);

  const copyToClipboard = useCallback(
    async (text: string, label: string) => {
      if (await copyTextToClipboard(text)) {
        setCopiedLabel(label);
        setTimeout(
          () => setCopiedLabel((cur) => (cur === label ? null : cur)),
          1500,
        );
      } else {
        showToast(tr("Couldn't copy to clipboard"), "error");
      }
    },
    [showToast, tr],
  );

  const runDebugShare = useCallback(async () => {
    setSharing(true);
    setShareResult(null);
    try {
      const res = await api.runDebugShare({ redact: shareRedact });
      setShareResult(res);
      const n = Object.keys(res.urls).length;
      showToast(
        tr(n === 1 ? "Uploaded {count} paste{redacted}" : "Uploaded {count} pastes{redacted}", {
          count: n,
          redacted: res.redacted ? tr(" (redacted)") : "",
        }),
        "success",
      );
    } catch (e) {
      showToast(tr("Debug share failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    } finally {
      setSharing(false);
    }
  }, [shareRedact, showToast, tr]);


  // ── Update check / apply ───────────────────────────────────────────
  const checkForUpdate = useCallback(
    async (force = false) => {
      if (status?.can_update_hermes === false) return;
      setCheckingUpdate(true);
      try {
        const info = await api.checkHermesUpdate(force);
        setUpdateInfo(info);
        if (force) {
          if (info.update_available) {
            showToast(
              info.behind && info.behind > 0
                ? tr(info.behind === 1 ? "Update available — {count} commit behind" : "Update available — {count} commits behind", { count: info.behind })
                : tr("Update available"),
              "success",
            );
          } else if (info.behind === 0) {
            showToast(tr("You're on the latest version"), "success");
          } else if (info.message) {
            showToast(
              ownerFacingError(info.message, "Не удалось проверить обновление."),
              "error",
            );
          }
        }
      } catch (e) {
        showToast(tr("Update check failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
      } finally {
        setCheckingUpdate(false);
      }
    },
    [showToast, status?.can_update_hermes, tr],
  );

  // Auto-check (cached) runs inside loadAll on mount; this is the
  // user-triggered forced re-check from the "Check for updates" button.
  const applyUpdate = async () => {
    setUpdateConfirmOpen(false);
    if (status?.can_update_hermes === false) {
      showToast(
        tr("Korra updates are managed outside this dashboard."),
        "success",
      );
      return;
    }
    try {
      const resp = await api.updateHermes();
      if (!resp.ok) {
        showToast(
          russianInterfaceText(
            resp.message,
            tr("Updates don't apply from this dashboard."),
          ),
          "success",
        );
        return;
      }
      setActiveAction(resp.name ?? "hermes-update");
      showToast(tr("Update started"), "success");
    } catch (e) {
      showToast(tr("Update failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    }
  };

  const checkpointsPrune = useConfirmDelete({
    onDelete: useCallback(async () => {
      try {
        const res = await api.pruneCheckpoints();
        setActiveAction(res.name);
        showToast(tr("Checkpoint prune started"), "success");
      } catch (e) {
        showToast(tr("Prune failed: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
        throw e;
      }
    }, [showToast, tr]),
  });

  // ── Hooks ──────────────────────────────────────────────────────────
  const createHook = async () => {
    if (!hookCommand.trim()) {
      showToast(tr("Command is required"), "error");
      return;
    }
    setCreatingHook(true);
    try {
      await api.createHook({
        event: hookEvent,
        command: hookCommand.trim(),
        matcher: hookMatcher.trim() || undefined,
        timeout: hookTimeout.trim() ? Number(hookTimeout) : undefined,
        approve: hookApprove,
      });
      showToast(tr("Hook created"), "success");
      setHookCommand("");
      setHookMatcher("");
      setHookTimeout("");
      setHookModalOpen(false);
      loadAll();
    } catch (e) {
      showToast(tr("Failed to create hook: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    } finally {
      setCreatingHook(false);
    }
  };

  const hookDelete = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const sep = key.indexOf("|");
        const event = key.slice(0, sep);
        const command = key.slice(sep + 1);
        try {
          await api.deleteHook(event, command);
          showToast(tr("Hook removed"), "success");
          loadAll();
        } catch (e) {
          showToast(tr("Failed to remove hook: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
          throw e;
        }
      },
      [loadAll, showToast, tr],
    ),
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const gatewayRunning = status?.gateway_running;
  const canUpdateHermes = status?.can_update_hermes !== false;
  const activeMemoryProvider = memory?.active
    ? memory.providers.find((provider) => provider.name === memory.active)
    : null;
  const validEvents = hooks?.valid_events?.length
    ? hooks.valid_events
    : HOOK_EVENTS_FALLBACK;

  return (
    <div className="flex flex-col gap-8">
      <Toast toast={toast} />
      <input
        ref={importUploadInputRef}
        type="file"
        accept=".zip,application/zip,application/x-zip-compressed"
        className="hidden"
        onChange={(event) => {
          setImportFile(event.currentTarget.files?.[0] ?? null);
        }}
      />

      <ConfirmDialog
        open={canUpdateHermes && updateConfirmOpen}
        onCancel={() => setUpdateConfirmOpen(false)}
        onConfirm={() => void applyUpdate()}
        title={tr("Update Korra?")}
        description={
          updateInfo && updateInfo.behind && updateInfo.behind > 0
            ? tr("This will run 'hermes update' ({command}) and pull {count} new commits. The gateway restarts when the update finishes; the current session keeps its prompt cache until then.", { command: updateInfo.update_command, count: updateInfo.behind })
            : tr("This will run 'hermes update' ({command}) and restart the gateway when it finishes.", { command: updateInfo?.update_command ?? "hermes update" })
        }
        confirmLabel={tr("Update now")}
        cancelLabel={t.common.cancel}
      />

      <DeleteConfirmDialog
        open={memoryReset.isOpen}
        onCancel={memoryReset.cancel}
        onConfirm={memoryReset.confirm}
        title={tr("Reset memory")}
        description={tr("This permanently erases the selected built-in memory files. This cannot be undone.")}
        loading={memoryReset.isDeleting}
      />
      <DeleteConfirmDialog
        open={credDelete.isOpen}
        onCancel={credDelete.cancel}
        onConfirm={credDelete.confirm}
        title={tr("Remove credential")}
        description={tr("Remove this pooled API key? The agent will no longer rotate through it.")}
        loading={credDelete.isDeleting}
      />
      <DeleteConfirmDialog
        open={checkpointsPrune.isOpen}
        onCancel={checkpointsPrune.cancel}
        onConfirm={checkpointsPrune.confirm}
        title={tr("Prune checkpoints")}
        description={tr("Delete the rollback checkpoint shadow store? Existing /rollback points will be lost.")}
        loading={checkpointsPrune.isDeleting}
      />
      <DeleteConfirmDialog
        open={hookDelete.isOpen}
        onCancel={hookDelete.cancel}
        onConfirm={hookDelete.confirm}
        title={tr("Remove shell hook")}
        description={tr("Remove this hook from config and revoke its consent? It stops firing on the next restart.")}
        loading={hookDelete.isDeleting}
      />
      <HermesConsoleModal
        open={consoleOpen}
        onClose={() => setConsoleOpen(false)}
      />

      {/* Create-hook modal */}
      {hookModalOpen && (
        <div
          ref={hookModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={(e) => e.target === e.currentTarget && setHookModalOpen(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className={cn(themedBody, "relative w-full max-w-lg border border-border bg-card shadow-2xl flex flex-col")}>
            <Button
              ghost
              size="icon"
              onClick={() => setHookModalOpen(false)}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              aria-label={t.common.close}
            >
              <X />
            </Button>
            <header className="p-5 pb-3 border-b border-border">
              <h2 className="font-mondwest text-display text-base tracking-wider">
                {tr("New shell hook")}
              </h2>
            </header>
            <div className="p-5 grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="hook-event">{tr("Event")}</Label>
                <Select
                  id="hook-event"
                  value={hookEvent}
                  onValueChange={(v) => setHookEvent(v)}
                >
                  {validEvents.map((ev) => (
                    <SelectOption key={ev} value={ev}>
                      {ev}
                    </SelectOption>
                  ))}
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="hook-command">{tr("Command (absolute path)")}</Label>
                <Input
                  id="hook-command"
                  autoFocus
                  placeholder="/usr/local/bin/my-hook.sh"
                  value={hookCommand}
                  onChange={(e) => setHookCommand(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label htmlFor="hook-matcher">{tr("Matcher (optional)")}</Label>
                  <Input
                    id="hook-matcher"
                    placeholder={tr("e.g. terminal")}
                    value={hookMatcher}
                    onChange={(e) => setHookMatcher(e.target.value)}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="hook-timeout">{tr("Timeout (s)")}</Label>
                  <Input
                    id="hook-timeout"
                    placeholder="10"
                    value={hookTimeout}
                    onChange={(e) => setHookTimeout(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex items-center gap-2.5">
                <Checkbox
                  checked={hookApprove}
                  id="hook-approve"
                  onCheckedChange={(checked) => setHookApprove(checked === true)}
                />

                <Label
                  className="cursor-pointer text-sm font-normal normal-case tracking-normal text-muted-foreground"
                  htmlFor="hook-approve"
                >
                  {tr("Approve now (grant consent so it fires; otherwise it stays configured but inactive)")}
                </Label>
              </div>
              <p className="text-xs text-warning">
                {tr("Shell hooks run arbitrary commands on this host. Only add scripts you trust. Takes effect on the next gateway/session restart.")}
              </p>
              <div className="flex justify-end">
                <Button
                  className="uppercase"
                  size="sm"
                  onClick={createHook}
                  disabled={creatingHook}
                  prefix={creatingHook ? <Spinner /> : undefined}
                >
                  {creatingHook ? tr("Creating") : tr("Create hook")}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Live action log */}
      {activeAction && (
        <ActionLogViewer
          action={activeAction}
          onComplete={handleActionComplete}
          onClose={() => setActiveAction(null)}
        />
      )}

      {/* ── Host / system stats ───────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Server className="h-4 w-4" /> {tr("Host")}
        </H2>
        <Card>
          <CardContent className="py-4">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-y-3 gap-x-6 text-sm">
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">OS</div>
                <div>{stats?.os} {stats?.os_release}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">{tr("Arch")}</div>
                <div>{stats?.arch}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">{tr("Host")}</div>
                <div className="truncate">{stats?.hostname}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">Python</div>
                <div>{stats?.python_impl} {stats?.python_version}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">Korra</div>
                <div className="flex items-center gap-2">
                  <span>v{stats?.hermes_version}</span>
                  {canUpdateHermes &&
                    updateInfo &&
                    (updateInfo.update_available ? (
                      <Badge tone="warning">
                        {updateInfo.behind && updateInfo.behind > 0
                          ? tr("{count} behind", { count: updateInfo.behind })
                          : tr("update available")}
                      </Badge>
                    ) : updateInfo.behind === 0 ? (
                      <Badge tone="success">{tr("latest")}</Badge>
                    ) : null)}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                  <Cpu className="h-3 w-3" /> CPU
                </div>
                <div>
                  {tr("{count} cores", { count: stats?.cpu_count ?? "—" })}
                  {typeof stats?.cpu_percent === "number"
                    ? ` · ${stats.cpu_percent.toFixed(0)}%`
                    : ""}
                </div>
              </div>
              {stats?.memory && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground">{tr("Memory")}</div>
                  <div>
                    {formatBytes(stats.memory.used)} / {formatBytes(stats.memory.total)} ({stats.memory.percent}%)
                  </div>
                </div>
              )}
              {stats?.disk && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                    <HardDrive className="h-3 w-3" /> {tr("Disk")}
                  </div>
                  <div>
                    {formatBytes(stats.disk.used)} / {formatBytes(stats.disk.total)} ({stats.disk.percent}%)
                  </div>
                </div>
              )}
              {typeof stats?.uptime_seconds === "number" && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground">{tr("Uptime")}</div>
                  <div>{formatDuration(stats.uptime_seconds)}</div>
                </div>
              )}
              {stats?.load_avg && stats.load_avg.length >= 3 && (
                <div>
                  <div className="text-xs uppercase tracking-wider text-muted-foreground">{tr("Load avg")}</div>
                  <div>{stats.load_avg.map((n) => n.toFixed(2)).join(" / ")}</div>
                </div>
              )}
            </div>
            {stats && !stats.psutil && (
              <p className="mt-3 text-xs text-muted-foreground">
                {tr("Install the")} <span className="font-mono">psutil</span> {tr("extra for CPU / memory / disk metrics.")}
              </p>
            )}
            {canUpdateHermes && (
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button
                  size="sm"
                  ghost
                  disabled={checkingUpdate}
                  prefix={
                    checkingUpdate ? (
                      <Spinner className="h-3.5 w-3.5" />
                    ) : (
                      <RotateCw className="h-3.5 w-3.5" />
                    )
                  }
                  onClick={() => void checkForUpdate(true)}
                >
                  {tr("Check for updates")}
                </Button>
                {updateInfo?.update_available && updateInfo.can_apply && (
                  <Button
                    size="sm"
                    prefix={<Download className="h-3.5 w-3.5" />}
                    onClick={() => setUpdateConfirmOpen(true)}
                  >
                    {tr("Update now")}
                  </Button>
                )}
                {updateInfo &&
                  !updateInfo.can_apply &&
                  updateInfo.update_available && (
                    <span className="text-xs text-muted-foreground">
                      {tr("Update with")}{" "}
                      <span className="font-mono">{updateInfo.update_command}</span>
                    </span>
                  )}
                {updateInfo?.message && !updateInfo.update_available && (
                  <span className="text-xs text-muted-foreground">
                    {russianInterfaceText(
                      updateInfo.message,
                      "Сведения об обновлении сейчас недоступны.",
                    )}
                  </span>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Portal ────────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Globe className="h-4 w-4" /> Nous Portal
        </H2>
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex items-center gap-3">
              <Badge tone={portal?.logged_in ? "success" : "secondary"}>
                {portal?.logged_in ? tr("logged in") : tr("not logged in")}
              </Badge>
              {portal?.provider && (
                <span className="text-sm text-muted-foreground">
                  {tr("inference provider:")} {portal.provider}
                </span>
              )}
              <a
                href={portal?.subscription_url || "https://portal.nousresearch.com/manage-subscription"}
                target="_blank"
                rel="noreferrer"
                className="ml-auto text-xs text-primary underline"
              >
                {tr("Manage subscription")}
              </a>
            </div>
            {portal?.features && portal.features.length > 0 && (
              <div className="flex flex-col gap-1 border-t border-border pt-3">
                <span className="text-xs uppercase tracking-wider text-muted-foreground">
                  {tr("Tool Gateway routing")}
                </span>
                {portal.features.map((f) => (
                  <div key={f.label} className="flex items-center justify-between text-sm">
                    <span>{({
                      "Web tools": "Интернет-инструменты",
                      "Image generation": "Создание изображений",
                      "Video generation": "Создание видео",
                      "OpenAI TTS": "Синтез речи OpenAI",
                      "Speech-to-text": "Распознавание речи",
                      "Browser automation": "Автоматизация браузера",
                      "Modal execution": "Выполнение в Modal",
                    } as Record<string, string>)[f.label] ?? russianInterfaceText(f.label, "Возможность Nous Portal")}</span>
                    <span className="text-muted-foreground">{({
                      "via Nous Portal": "Через Nous Portal",
                      active: "Активно",
                      "not configured": "Не настроено",
                    } as Record<string, string>)[f.state] ?? "Подключено"}</span>
                  </div>
                ))}
              </div>
            )}
            {!portal?.logged_in && (
              <p className="text-xs text-muted-foreground">
                {tr("Log in with")} <span className="font-mono">hermes portal</span>.
              </p>
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Curator ───────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Sparkles className="h-4 w-4" /> {tr("Skill curator")}
        </H2>
        <Card>
          <CardContent className="flex items-center justify-between py-4">
            <div className="flex items-center gap-3">
              <Badge tone={curator?.paused ? "warning" : curator?.enabled ? "success" : "secondary"}>
                {curator?.paused ? tr("paused") : curator?.enabled ? tr("active") : tr("disabled")}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {curator?.interval_hours ? tr("every {hours}h", { hours: curator.interval_hours }) : ""}
                {curator?.last_run_at ? tr(" · last run {time}", { time: new Date(curator.last_run_at).toLocaleString() }) : tr(" · never run")}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" ghost onClick={toggleCuratorPaused}>
                {curator?.paused ? tr("Resume") : tr("Pause")}
              </Button>
              <Button
                size="sm"
                ghost
                prefix={<Play className="h-3.5 w-3.5" />}
                onClick={() => runOp(api.runCurator, tr("Curator review"))}
              >
                {tr("Run now")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── Gateway ───────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Power className="h-4 w-4" /> {tr("Gateway")}
        </H2>
        <Card>
          <CardContent className="flex items-center justify-between py-4">
            <div className="flex items-center gap-3">
              <Badge tone={gatewayRunning ? "success" : "secondary"}>
                {gatewayRunning ? tr("running") : tr("stopped")}
              </Badge>
              <span className="text-sm text-muted-foreground">
                {status?.gateway_state ?? "—"}
                {status?.gateway_pid ? ` · pid ${status.gateway_pid}` : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                className="uppercase"
                onClick={() => runGateway("start")}
                disabled={gatewayRunning}
                prefix={<Play className="h-3.5 w-3.5" />}
              >
                {tr("Start")}
              </Button>
              <Button
                size="sm"
                className="uppercase"
                onClick={() => runGateway("restart")}
                prefix={<RotateCw className="h-3.5 w-3.5" />}
              >
                {tr("Restart")}
              </Button>
              <Button
                size="sm"
                className="uppercase text-warning"
                ghost
                onClick={() => runGateway("stop")}
                disabled={!gatewayRunning}
                prefix={<Power className="h-3.5 w-3.5" />}
              >
                {tr("Stop")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── Memory ────────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Brain className="h-4 w-4" /> {tr("Memory")}
        </H2>
        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <span>
                {tr("External provider:")}{" "}
                <span className="font-mono text-foreground">
                  {memory?.active || tr("built-in only")}
                </span>
              </span>
              {activeMemoryProvider && (
                <Badge tone={MEMORY_STATUS_TONE[activeMemoryProvider.status]}>
                  {tr(MEMORY_STATUS_LABEL[activeMemoryProvider.status])}
                </Badge>
              )}
              <Link to="/plugins" className="underline">
                {tr("Change in Plugins →")}
              </Link>
              <span className="ml-auto">
                {tr("Provider setup:")}{" "}
                <Link to="/plugins" className="underline">
                  {tr("configure in Plugins")}
                </Link>
              </span>
            </div>

            {activeMemoryProvider?.status === "missing" && (
              <p className="border border-destructive/50 px-3 py-2 text-xs text-destructive">
                {tr("The configured provider is no longer installed. Switch to built-in memory or configure another provider in Plugins.")}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
              <span className="text-xs text-muted-foreground">
                {tr("Built-in files — MEMORY.md:")}{" "}
                {formatBytes(memory?.builtin_files.memory ?? 0)} {tr("· USER.md:")}{" "}
                {formatBytes(memory?.builtin_files.user ?? 0)}
              </span>
              <div className="flex items-center gap-2 ml-auto">
                <Button size="sm" ghost className="text-destructive" onClick={() => memoryReset.requestDelete("memory")}>
                  {tr("Reset MEMORY.md")}
                </Button>
                <Button size="sm" ghost className="text-destructive" onClick={() => memoryReset.requestDelete("user")}>
                  {tr("Reset USER.md")}
                </Button>
                <Button size="sm" ghost className="text-destructive" onClick={() => memoryReset.requestDelete("all")}>
                  {tr("Reset all")}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ── Credential pool ───────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <KeyRound className="h-4 w-4" /> {tr("Credential pool")}
        </H2>
        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end">
              <div className="grid gap-2">
                <Label htmlFor="cred-provider">{tr("Provider")}</Label>
                <Input id="cred-provider" value={credProvider} onChange={(e) => setCredProvider(e.target.value)} placeholder="openrouter" />
              </div>
              <div className="grid gap-2 sm:col-span-2">
                <Label htmlFor="cred-key">{tr("API key")}</Label>
                <Input id="cred-key" type="password" value={credKey} onChange={(e) => setCredKey(e.target.value)} placeholder="sk-…" />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="cred-label">{tr("Label")}</Label>
                <Input id="cred-label" value={credLabel} onChange={(e) => setCredLabel(e.target.value)} placeholder={tr("optional")} />
              </div>
            </div>
            <div className="flex justify-end">
              <Button size="sm" className="uppercase" onClick={addCredential} disabled={addingCred} prefix={addingCred ? <Spinner /> : undefined}>
                {tr("Add key")}
              </Button>
            </div>
            {pool.length === 0 && (
              <p className="text-sm text-muted-foreground">
                {tr("No pooled credentials. Add one above to enable key rotation.")}
              </p>
            )}
            {pool.map((prov) => (
              <div key={prov.provider} className="flex flex-col gap-2">
                <span className="text-xs uppercase tracking-wider text-muted-foreground">
                  {prov.provider}
                </span>
                {prov.entries.map((entry) => (
                  <div key={`${prov.provider}-${entry.index}`} className="flex items-center gap-3 border border-border bg-background/40 px-3 py-2">
                    <span className="text-sm font-medium">
                      {russianInterfaceText(entry.label, `Ключ ${entry.index}`)}
                    </span>
                    <span className="font-mono text-xs text-muted-foreground">{entry.token_preview}</span>
                    <Badge tone="outline">{entry.auth_type}</Badge>
                    {entry.last_status && (
                      <Badge tone="secondary">
                        {({
                          ok: "Работает",
                          exhausted: "Лимит исчерпан",
                          cooldown: "Пауза",
                          error: "Ошибка",
                        } as Record<string, string>)[entry.last_status] ?? "Статус обновлён"}
                      </Badge>
                    )}
                    <Button ghost size="icon" className="ml-auto text-destructive" aria-label={tr("Remove credential")} onClick={() => credDelete.requestDelete(`${prov.provider}|${entry.index}`)}>
                      <Trash2 />
                    </Button>
                  </div>
                ))}
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      {/* ── Operations ────────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Activity className="h-4 w-4" /> {tr("Operations")}
        </H2>
        <Card>
          <CardContent className="flex flex-wrap gap-2 py-4">
            <Button size="sm" ghost prefix={<Terminal className="h-3.5 w-3.5" />} onClick={() => setConsoleOpen(true)}>
              {tr("Open console")}
            </Button>
            <Button size="sm" ghost prefix={<Stethoscope className="h-3.5 w-3.5" />} onClick={() => runOp(api.runDoctor, tr("Doctor"))}>
              {tr("Run doctor")}
            </Button>
            <Button size="sm" ghost prefix={<ShieldCheck className="h-3.5 w-3.5" />} onClick={() => runOp(api.runSecurityAudit, tr("Security audit"))}>
              {tr("Security audit")}
            </Button>
            <Button size="sm" ghost prefix={<RotateCw className="h-3.5 w-3.5" />} onClick={() => runOp(api.updateSkillsFromHub, tr("Skills update"))}>
              {tr("Update skills")}
            </Button>
            <Button size="sm" ghost prefix={<Activity className="h-3.5 w-3.5" />} onClick={() => runOp(api.runPromptSize, tr("Prompt size"))}>
              {tr("Prompt size")}
            </Button>
            <Button size="sm" ghost prefix={<Database className="h-3.5 w-3.5" />} onClick={() => runOp(api.runDump, tr("Support dump"))}>
              {tr("Support dump")}
            </Button>
            <Button size="sm" ghost prefix={<RotateCw className="h-3.5 w-3.5" />} onClick={() => runOp(api.runConfigMigrate, tr("Config migrate"))}>
              {tr("Migrate config")}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
              <div className="grid min-w-0 flex-1 gap-2">
                <Label>{tr("Full backup")}</Label>
                <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
                  <Button
                    size="sm"
                    ghost
                    prefix={<Database className="h-3.5 w-3.5" />}
                    onClick={() => void runDashboardBackup()}
                  >
                    {tr("Create backup")}
                  </Button>
                  <Button
                    size="sm"
                    ghost
                    disabled={!downloadableBackupArchive || downloadingBackup}
                    prefix={
                      downloadingBackup ? (
                        <Spinner className="h-3.5 w-3.5" />
                      ) : (
                        <Download className="h-3.5 w-3.5" />
                      )
                    }
                    onClick={() => void downloadBackup()}
                  >
                    {tr("Download backup")}
                  </Button>
                  <span
                    className="min-w-0 truncate text-xs text-muted-foreground"
                    title={pendingBackupArchive ?? tr("No backup created yet")}
                  >
                    {backupFileName(pendingBackupArchive, tr("No backup created yet"))}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-end">
              <div className="grid min-w-0 flex-1 gap-2">
                <Label>{tr("Restore from backup upload")}</Label>
                <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
                  <Button
                    type="button"
                    size="sm"
                    ghost
                    disabled={importingBackup}
                    prefix={<Upload className="h-3.5 w-3.5" />}
                    onClick={() => importUploadInputRef.current?.click()}
                  >
                    {tr("Choose restore zip")}
                  </Button>
                  <span
                    className="min-w-0 truncate text-xs text-muted-foreground"
                    title={importFile?.name ?? tr("No backup archive selected")}
                  >
                    {importFile?.name ?? tr("No backup archive selected")}
                  </span>
                </div>
              </div>
              <Button
                size="sm"
                ghost
                disabled={!importFile || importingBackup}
                prefix={importingBackup ? <Spinner /> : undefined}
                onClick={() => {
                  if (!importFile) return;
                  setImportConfirmTarget({ kind: "upload", file: importFile });
                }}
              >
                {tr("Restore upload")}
              </Button>
            </div>

            <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-end">
              <div className="grid min-w-0 flex-1 gap-2">
                <Label htmlFor="import-path">{tr("Restore from backups path")}</Label>
                <Input
                  id="import-path"
                  value={importPath}
                  onChange={(e) => setImportPath(e.target.value)}
                  placeholder="$HERMES_HOME/backups/hermes-backup.zip"
                />
              </div>
              <Button
                size="sm"
                ghost
                disabled={!importPath.trim() || importingBackup}
                prefix={importingBackup ? <Spinner /> : undefined}
                onClick={() => {
                  const path = importPath.trim();
                  if (!path) return;
                  setImportConfirmTarget({ kind: "path", path });
                }}
              >
                {tr("Restore path")}
              </Button>
            </div>
            <ConfirmDialog
              open={!!importConfirmTarget}
              title={tr("Restore full Korra backup?")}
              description={tr("This will overwrite your current Korra configuration, skills, sessions, and data with the contents of {archive}. This cannot be undone.", { archive: backupImportLabel(importConfirmTarget, tr("the archive")) })}
              destructive
              confirmLabel={tr("Restore")}
              cancelLabel={t.common.cancel}
              onCancel={() => setImportConfirmTarget(null)}
              onConfirm={() => {
                const target = importConfirmTarget;
                setImportConfirmTarget(null);
                if (target) void runBackupImport(target);
              }}
            />
          </CardContent>
        </Card>

        {/* Debug share — uploads a redacted report + logs, returns shareable
            links. Separated from the buttons above because its output is
            persistent, copyable URLs, not a fire-and-forget log tail. */}
        <Card>
          <CardContent className="flex flex-col gap-3 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-start gap-2">
                <Share2 className="h-4 w-4 mt-0.5 text-muted-foreground" />
                <div className="flex flex-col">
                  <span className="text-sm font-medium">{tr("Share debug report")}</span>
                  <span className="text-xs text-muted-foreground max-w-prose">
                    {tr("Uploads system info + logs to a public paste service and returns links to send the Korra team. Pastes auto-delete after 6 hours.")}
                  </span>
                </div>
              </div>
              <Button
                size="sm"
                disabled={sharing}
                prefix={
                  sharing ? (
                    <Spinner className="h-3.5 w-3.5" />
                  ) : (
                    <Share2 className="h-3.5 w-3.5" />
                  )
                }
                onClick={() => void runDebugShare()}
              >
                {sharing ? tr("Uploading…") : tr("Generate share link")}
              </Button>
            </div>

            <div className="flex items-center gap-2.5">
              <Checkbox
                checked={shareRedact}
                disabled={sharing}
                id="share-redact"
                onCheckedChange={(checked) => setShareRedact(checked === true)}
              />

              <Label
                className="cursor-pointer select-none text-xs font-normal normal-case tracking-normal text-muted-foreground"
                htmlFor="share-redact"
              >
                {tr("Redact credential-shaped tokens before upload (recommended)")}
              </Label>
            </div>

            {shareResult && (
              <div className="flex flex-col gap-2 border-t border-border pt-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Badge tone="success">{tr("uploaded")}</Badge>
                    {shareResult.redacted ? (
                      <Badge tone="outline">{tr("redacted")}</Badge>
                    ) : (
                      <Badge tone="warning">{tr("not redacted")}</Badge>
                    )}
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      {tr("auto-deletes in {hours}h", { hours: Math.round(shareResult.auto_delete_seconds / 3600) })}
                    </span>
                  </div>
                  {Object.keys(shareResult.urls).length > 1 && (
                    <Button
                      size="sm"
                      ghost
                      prefix={
                        copiedLabel === "__all__" ? (
                          <Check className="h-3.5 w-3.5" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )
                      }
                      onClick={() =>
                        void copyToClipboard(
                          Object.entries(shareResult.urls)
                            .map(([label, url]) => `${label}: ${url}`)
                            .join("\n"),
                          "__all__",
                        )
                      }
                    >
                      {tr("Copy all")}
                    </Button>
                  )}
                </div>

                {Object.entries(shareResult.urls).map(([label, url]) => (
                  <div
                    key={label}
                    className="flex items-center gap-2 bg-background/50 border border-border px-3 py-2"
                  >
                    <Link2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="font-mono text-xs shrink-0 w-24 truncate text-muted-foreground">
                      {label}
                    </span>
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-xs truncate flex-1 text-primary hover:underline"
                    >
                      {url}
                    </a>
                    <Button
                      ghost
                      size="icon"
                      aria-label={tr("Copy {label} link", { label })}
                      onClick={() => void copyToClipboard(url, label)}
                    >
                      {copiedLabel === label ? <Check /> : <Copy />}
                    </Button>
                  </div>
                ))}

                {shareResult.failures.length > 0 && (
                  <span className="text-xs text-destructive">
                    {tr("Some logs failed to upload: {errors}", { errors: shareResult.failures.join("; ") })}
                  </span>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </section>

      {/* ── Checkpoints ───────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Database className="h-4 w-4" /> {tr("Checkpoints")}
        </H2>
        <Card>
          <CardContent className="flex items-center justify-between py-4">
            <span className="text-sm text-muted-foreground">
              {tr("{count} sessions · {size}", { count: checkpoints?.sessions.length ?? 0, size: formatBytes(checkpoints?.total_bytes ?? 0) })}
            </span>
            <Button size="sm" ghost className="text-destructive" disabled={!checkpoints?.sessions.length} prefix={<Trash2 className="h-3.5 w-3.5" />} onClick={() => checkpointsPrune.requestDelete("all")}>
              {tr("Prune")}
            </Button>
          </CardContent>
        </Card>
      </section>

      {/* ── Shell hooks ───────────────────────────────────────────── */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
            <Terminal className="h-4 w-4" /> {tr("Shell hooks")}
          </H2>
          <Button size="sm" className="uppercase" prefix={<Plus className="h-3.5 w-3.5" />} onClick={() => setHookModalOpen(true)}>
            {tr("New hook")}
          </Button>
        </div>
        {(!hooks || hooks.hooks.length === 0) && (
          <Card>
            <CardContent className="py-6 text-center text-sm text-muted-foreground">
              {tr("No shell hooks configured.")}
            </CardContent>
          </Card>
        )}
        {hooks?.hooks.map((h: HookEntry, i) => (
          <Card key={`${h.event}-${i}`}>
            <CardContent className="flex items-center gap-3 py-3">
              <Badge tone="outline">{h.event}</Badge>
              {h.matcher && (
                <span className="text-xs text-muted-foreground">{tr("matcher:")} {h.matcher}</span>
              )}
              <span className="font-mono text-xs truncate flex-1">{h.command}</span>
              {h.executable === false && (
                <Badge tone="destructive">{tr("not executable")}</Badge>
              )}
              <Badge tone={h.allowed ? "success" : "warning"}>
                {h.allowed ? tr("allowed") : tr("not approved")}
              </Badge>
              <Button
                ghost
                size="icon"
                className="text-destructive"
                aria-label={tr("Remove hook")}
                onClick={() =>
                  hookDelete.requestDelete(`${h.event}|${h.command ?? ""}`)
                }
              >
                <Trash2 />
              </Button>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
