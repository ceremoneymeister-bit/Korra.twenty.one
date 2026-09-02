import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { Check, ShieldCheck, Trash2, Users, X } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type { PairingResponse, PairingUser } from "@/lib/api";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { useConfirmDelete } from "@nous-research/ui/hooks/use-confirm-delete";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useI18n } from "@/i18n";
import { ownerFacingError } from "@/lib/owner-facing-error";

function getUserKey(user: PairingUser): string {
  return `${user.platform}:${user.user_id}`;
}

function splitUserKey(key: string): { platform: string; user_id: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { platform: "", user_id: key };
  return { platform: key.slice(0, idx), user_id: key.slice(idx + 1) };
}

function getUserLabel(user: PairingUser): string {
  return user.user_name || user.user_id;
}

export default function PairingPage() {
  const [pending, setPending] = useState<PairingUser[]>([]);
  const [approved, setApproved] = useState<PairingUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [approving, setApproving] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const { toast, showToast } = useToast();
  const { setEnd } = usePageHeader();
  const { tr } = useI18n();

  const loadPairing = useCallback(() => {
    api
      .getPairing()
      .then((res: PairingResponse) => {
        setPending(res.pending);
        setApproved(res.approved);
      })
      .catch(() => showToast(tr("Failed to load pairing requests"), "error"))
      .finally(() => setLoading(false));
  }, [showToast, tr]);

  useEffect(() => {
    loadPairing();
  }, [loadPairing]);

  const handleApprove = async (user: PairingUser) => {
    if (!user.request_id) {
      showToast(tr("Missing pairing request"), "error");
      return;
    }
    const key = getUserKey(user);
    setApproving(key);
    try {
      await api.approvePairing(user.platform, user.request_id);
      showToast(tr('Approved: "{name}"', { name: getUserLabel(user) }), "success");
      loadPairing();
    } catch (e) {
      showToast(tr("Error: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    } finally {
      setApproving(null);
    }
  };

  const handleClearPending = async () => {
    if (!window.confirm(tr("Clear all pending pairing requests?"))) return;
    setClearing(true);
    try {
      const res = await api.clearPendingPairing();
      showToast(tr("Cleared {count} pending requests", { count: res.cleared }), "success");
      loadPairing();
    } catch (e) {
      showToast(tr("Error: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
    } finally {
      setClearing(false);
    }
  };

  const userRevoke = useConfirmDelete({
    onDelete: useCallback(
      async (key: string) => {
        const { platform, user_id } = splitUserKey(key);
        const user = approved.find((u) => getUserKey(u) === key);
        try {
          await api.revokePairing(platform, user_id);
          showToast(
            tr('Revoked: "{name}"', { name: user ? getUserLabel(user) : user_id }),
            "success",
          );
          loadPairing();
        } catch (e) {
          showToast(tr("Error: {error}", { error: ownerFacingError(e, "подробности недоступны") }), "error");
          throw e;
        }
      },
      [approved, loadPairing, showToast, tr],
    ),
  });

  // Put "Clear pending" button in page header
  useLayoutEffect(() => {
    setEnd(
      <Button
        className="uppercase"
        size="sm"
        onClick={handleClearPending}
        disabled={clearing}
        prefix={clearing ? <Spinner /> : <Trash2 className="h-4 w-4" />}
      >
        {tr("Clear pending")}
      </Button>,
    );
    return () => {
      setEnd(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setEnd, clearing, tr]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  const pendingRevokeUser = userRevoke.pendingId
    ? approved.find((u) => getUserKey(u) === userRevoke.pendingId)
    : null;

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={userRevoke.isOpen}
        onCancel={userRevoke.cancel}
        onConfirm={userRevoke.confirm}
        title={tr("Revoke access")}
        description={
          pendingRevokeUser
            ? tr('"{name}" will lose access. This cannot be undone.', { name: getUserLabel(pendingRevokeUser) })
            : tr("This user will lose access. This cannot be undone.")
        }
        confirmLabel={tr("Revoke")}
        loading={userRevoke.isDeleting}
      />

      {/* Pending requests */}
      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <Users className="h-4 w-4" />
          {tr("Pending requests ({count})", { count: pending.length })}
        </H2>

        {pending.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              {tr("No pending pairing requests")}
            </CardContent>
          </Card>
        )}

        {pending.map((user) => {
          const key = getUserKey(user);
          return (
            <Card key={key}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge tone="outline">{user.platform}</Badge>
                    <span className="font-medium text-sm truncate">
                      {getUserLabel(user)}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 text-xs text-muted-foreground">
                    <span className="truncate">{user.user_id}</span>
                    {typeof user.age_minutes === "number" && (
                      <span>{tr("{minutes}m ago", { minutes: user.age_minutes })}</span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    size="sm"
                    className="uppercase"
                    onClick={() => handleApprove(user)}
                    disabled={approving === key || !user.request_id}
                    prefix={
                      approving === key ? (
                        <Spinner />
                      ) : (
                        <Check className="h-4 w-4" />
                      )
                    }
                  >
                    {tr("Approve")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Approved users */}
      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <ShieldCheck className="h-4 w-4" />
          {tr("Approved users ({count})", { count: approved.length })}
        </H2>

        {approved.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              {tr("No approved users")}
            </CardContent>
          </Card>
        )}

        {approved.map((user) => {
          const key = getUserKey(user);
          return (
            <Card key={key}>
              <CardContent className="flex items-start gap-4 py-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge tone="outline">{user.platform}</Badge>
                    <span className="font-medium text-sm truncate">
                      {user.user_id}
                    </span>
                  </div>
                  {user.user_name && (
                    <div className="text-xs text-muted-foreground truncate">
                      {user.user_name}
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    ghost
                    size="icon"
                    title={tr("Revoke")}
                    aria-label={tr("Revoke")}
                    className="text-destructive"
                    onClick={() => userRevoke.requestDelete(key)}
                  >
                    <X />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
