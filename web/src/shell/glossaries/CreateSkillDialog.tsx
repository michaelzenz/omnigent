import { useEffect, useState } from "react";
import { PlusIcon } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useCreateSkill } from "@/hooks/useSkills";

// Mirrors the backend ``_SKILL_NAME_RE``: ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$
const SKILL_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/**
 * Dialog for creating a new skill from the glossaries Skills tab.
 *
 * Collects a name, description, and optional instructions, builds a
 * ``SKILL.md`` with the matching frontmatter, and asks the server to write
 * it to every detected harness root plus ``~/.omnigent/skills``.
 */
export function CreateSkillDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (name: string) => void;
}) {
  const create = useCreateSkill();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setDescription("");
    setContent("");
    setError(null);
  }, [open]);

  const trimmedName = name.trim();
  const nameValid = SKILL_NAME_RE.test(trimmedName);
  const descriptionValid = description.trim().length > 0;
  const canSubmit = nameValid && descriptionValid && !create.isPending;

  function buildSkillMd(): string {
    return `---\nname: ${trimmedName}\ndescription: ${description.trim()}\n---\n\n${content}\n`;
  }

  async function handleSubmit() {
    if (!canSubmit) return;
    setError(null);
    try {
      await create.mutateAsync({ name: trimmedName, files: { "SKILL.md": buildSkillMd() } });
      onCreated(trimmedName);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create skill");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] w-[calc(100vw-2rem)] flex-col gap-4 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create skill</DialogTitle>
          <DialogDescription>
            Saves the new skill to every detected harness and to ~/.omnigent/skills.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="create-skill-name"
              className="text-sm font-medium text-muted-foreground"
            >
              Name <span className="text-destructive">*</span>
            </label>
            <Input
              id="create-skill-name"
              data-testid="create-skill-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="my-skill"
              autoFocus
            />
            {name && !nameValid && (
              <p className="text-xs text-destructive">
                Use letters, digits, &ldquo;.&rdquo;, &ldquo;_&rdquo; or &ldquo;-&rdquo; (max 128
                chars).
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="create-skill-description"
              className="text-sm font-medium text-muted-foreground"
            >
              Description <span className="text-destructive">*</span>
            </label>
            <Input
              id="create-skill-description"
              data-testid="create-skill-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="A short summary of what this skill does"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="create-skill-content"
              className="text-sm font-medium text-muted-foreground"
            >
              Instructions
            </label>
            <Textarea
              id="create-skill-content"
              data-testid="create-skill-content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="Write the skill instructions here…"
              className="min-h-[16rem] font-mono text-sm"
              spellCheck={false}
            />
          </div>

          {error && (
            <p className="text-sm text-destructive" data-testid="create-skill-error">
              {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={create.isPending}>
            Cancel
          </Button>
          <Button data-testid="create-skill-submit" onClick={handleSubmit} disabled={!canSubmit}>
            <PlusIcon /> Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
