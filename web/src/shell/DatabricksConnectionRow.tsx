import { useEffect, useRef, useState } from "react";
import { AlertTriangleIcon, Loader2Icon, ExternalLinkIcon, CheckCircle2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  useDatabricksStatus,
  useDatabricksLogin,
  useDatabricksLoginPoll,
} from "@/hooks/useDatabricksStatus";
import { cn } from "@/lib/utils";
import { SIDEBAR_ROW } from "./sidebarStyles";

type DialogStep = "input" | "auth-link" | "polling" | "done";

/**
 * Yellow "No Databricks Connection" row pinned above the server picker at
 * the sidebar's bottom.  Clicking opens a centered modal that guides the
 * user through the Databricks OAuth login flow.
 *
 * Two entry points depending on what the status probe found:
 * - **Host known** (profile in .databrickscfg, token expired): skips URL
 *   input, goes straight to "click to re-login".
 * - **Host unknown** (no profile or no host in .databrickscfg): shows a
 *   workspace URL input first.
 *
 * Hidden when Databricks is connected (or while the status probe is still
 * loading for the first time).
 */
export function DatabricksConnectionRow() {
  const { data: status, isLoading } = useDatabricksStatus();
  const [dialogOpen, setDialogOpen] = useState(false);

  // Don't render until the first probe resolves, and hide when connected.
  if (isLoading || !status || status.connected) return null;

  return (
    <>
      <div className="shrink-0 px-2 pt-1" data-testid="databricks-connection-row">
        <Button
          type="button"
          variant="ghost"
          onClick={() => setDialogOpen(true)}
          className={cn(
            SIDEBAR_ROW,
            "w-full justify-start border-0 font-normal",
            "text-amber-600 dark:text-amber-500",
            "hover:bg-amber-50 dark:hover:bg-amber-950/30",
          )}
          aria-label="No Databricks connection. Click to login."
          data-testid="databricks-connection-button"
        >
          <AlertTriangleIcon className="ui-icon shrink-0" />
          <span className="truncate">No Databricks Connection</span>
        </Button>
      </div>

      <DatabricksLoginDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        knownHost={status.host}
      />
    </>
  );
}

interface DatabricksLoginDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Non-null when .databrickscfg already has a host for the profile. */
  knownHost: string | null;
}

function DatabricksLoginDialog({ open, onOpenChange, knownHost }: DatabricksLoginDialogProps) {
  // When host is known, skip the URL input and start at auth-link.
  const [step, setStep] = useState<DialogStep>("input");
  const [host, setHost] = useState("");
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const login = useDatabricksLogin();
  const poll = useDatabricksLoginPoll();
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Reset state when dialog opens — skip URL input if host is already known.
  useEffect(() => {
    if (open) {
      setErrorMsg(null);
      setAuthUrl(null);
      if (knownHost) {
        // Host is in .databrickscfg — no URL needed, go straight to auth-link.
        setStep("auth-link");
        // Kick off login immediately (no --host flag).
        login.mutate(undefined, {
          onSuccess: (result) => {
            if (result.auth_url) {
              setAuthUrl(result.auth_url);
              startPolling();
            } else {
              setErrorMsg(result.error ?? "Could not start login.");
              setStep("input");
            }
          },
          onError: (err) => {
            setErrorMsg(err instanceof Error ? err.message : "Login failed.");
            setStep("input");
          },
        });
      } else {
        setStep("input");
        setHost("");
      }
    } else {
      // Stop polling when dialog closes.
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, knownHost]);

  // Cleanup polling on unmount.
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, []);

  const startPolling = () => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    setStep("polling");
    pollTimerRef.current = setInterval(() => {
      poll.mutate(undefined, {
        onSuccess: (data) => {
          if (data.completed) {
            if (pollTimerRef.current) {
              clearInterval(pollTimerRef.current);
              pollTimerRef.current = null;
            }
            if (data.success) {
              setStep("done");
            } else {
              setErrorMsg(data.error ?? "Login failed.");
              setStep("input");
            }
          }
        },
      });
    }, 2000);
  };

  const handleStartLogin = () => {
    setErrorMsg(null);
    login.mutate(host, {
      onSuccess: (result) => {
        if (result.auth_url) {
          setAuthUrl(result.auth_url);
          setStep("auth-link");
          // Start polling immediately — the user may complete login quickly.
          startPolling();
        } else {
          setErrorMsg(result.error ?? "Could not start login.");
        }
      },
      onError: (err) => {
        setErrorMsg(err instanceof Error ? err.message : "Login failed.");
      },
    });
  };

  const handleOpenAuthUrl = () => {
    if (authUrl) window.open(authUrl, "_blank");
  };

  const handleDone = () => {
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangleIcon className="size-5 text-amber-500" />
            Login to Databricks
          </DialogTitle>
          <DialogDescription>
            Login to Databricks to unlock full features — policy-guarded tool calls and model
            routing require a valid Databricks session.
          </DialogDescription>
        </DialogHeader>

        {/* Step 1: Enter workspace URL (only when host is not known) */}
        {step === "input" && (
          <div className="space-y-3 py-2">
            <label htmlFor="databricks-host" className="text-sm font-medium">
              Databricks Workspace URL
            </label>
            <Input
              id="databricks-host"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder="https://dbc-xxxx.cloud.databricks.com"
              disabled={login.isPending}
              data-testid="databricks-host-input"
            />
            {login.isPending && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                Starting login…
              </p>
            )}
            {errorMsg && (
              <p className="text-sm text-destructive" data-testid="databricks-login-error">
                {errorMsg}
              </p>
            )}
          </div>
        )}

        {/* Step 2: Auth link + polling */}
        {(step === "auth-link" || step === "polling") && (
          <div className="space-y-3 py-2">
            <p className="text-sm text-muted-foreground">
              Click the button below to open the Databricks login page in your browser. After you
              complete login, this dialog will close automatically.
            </p>
            <Button
              onClick={handleOpenAuthUrl}
              variant="outline"
              className="w-full"
              disabled={!authUrl}
              data-testid="databricks-open-auth-url"
            >
              <ExternalLinkIcon className="size-4" />
              Open Databricks Login Page
            </Button>
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" />
              Waiting for login to complete…
            </p>
            {errorMsg && (
              <p className="text-sm text-destructive" data-testid="databricks-login-error">
                {errorMsg}
              </p>
            )}
          </div>
        )}

        {/* Step 3: Done */}
        {step === "done" && (
          <div className="space-y-3 py-2">
            <p className="flex items-center gap-2 text-sm text-green-600 dark:text-green-500">
              <CheckCircle2Icon className="size-4" />
              Successfully connected to Databricks.
            </p>
          </div>
        )}

        <DialogFooter>
          {step === "input" && (
            <>
              <Button
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={login.isPending}
              >
                Cancel
              </Button>
              <Button
                onClick={handleStartLogin}
                disabled={login.isPending || !host.trim()}
                data-testid="databricks-login-button"
              >
                {login.isPending ? (
                  <>
                    <Loader2Icon className="size-4 animate-spin" />
                    Starting…
                  </>
                ) : (
                  "Login to Databricks"
                )}
              </Button>
            </>
          )}
          {(step === "auth-link" || step === "polling") && (
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
          )}
          {step === "done" && (
            <Button onClick={handleDone} data-testid="databricks-done-button">
              Done
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
