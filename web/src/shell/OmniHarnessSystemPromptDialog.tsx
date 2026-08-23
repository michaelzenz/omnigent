import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useOmniHarnessSettings, useUpdateOmniHarnessSettings } from "@/hooks/useModelSettings";

export function OmniHarnessSystemPromptEditor() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        type="button"
        variant="outline"
        className="w-full"
        onClick={() => setOpen(true)}
        data-testid="omniharness-system-prompt-edit"
      >
        Edit
      </Button>
      {open && <SystemPromptDialog open={open} onOpenChange={setOpen} />}
    </>
  );
}

function SystemPromptDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [draft, setDraft] = useState("");
  const settings = useOmniHarnessSettings(open);
  const update = useUpdateOmniHarnessSettings();

  useEffect(() => {
    if (open && settings.data) setDraft(settings.data.systemPrompt);
  }, [open, settings.data]);

  const save = async () => {
    await update.mutateAsync({ systemPrompt: draft });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-2xl"
        data-testid="omniharness-system-prompt-dialog"
      >
        <DialogHeader className="shrink-0">
          <DialogTitle>OmniHarness system prompt</DialogTitle>
          <DialogDescription>
            Applied globally before the selected Prompt Profile and memory on every OmniHarness
            turn. Leave empty to use the built-in default.
          </DialogDescription>
        </DialogHeader>
        {settings.isError ? (
          <p className="text-sm text-destructive">Couldn&apos;t load the system prompt.</p>
        ) : (
          <Textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="You are a helpful assistant."
            className="h-72 min-h-0 max-h-72 resize-none overflow-y-auto font-mono text-sm"
            disabled={settings.isLoading}
            aria-label="OmniHarness system prompt"
            data-testid="omniharness-system-prompt-input"
          />
        )}
        {update.isError && (
          <p className="text-sm text-destructive">Couldn&apos;t save the system prompt.</p>
        )}
        <DialogFooter className="shrink-0">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={() => void save()}
            disabled={settings.isLoading || settings.isError || update.isPending}
            data-testid="omniharness-system-prompt-save"
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
