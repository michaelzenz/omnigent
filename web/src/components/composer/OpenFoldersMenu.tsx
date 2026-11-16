import { FolderOpenIcon } from "lucide-react";
import { CursorIcon } from "@/components/icons/CursorIcon";
import { VSCodeIcon } from "@/components/icons/VSCodeIcon";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useHostSshTarget } from "@/hooks/useHostSshTarget";
import { useInstalledEditors } from "@/hooks/useInstalledEditors";
import { useOpenFolders } from "@/hooks/useOpenFolders";
import { CODE_EDITORS, type CodeEditor, editorLink, launchEditorLink } from "@/lib/editorLinks";
import type { OpenFolder } from "@/lib/events";
import { isAndroidShell, isIOSShell } from "@/lib/nativeBridge";
import { cn } from "@/lib/utils";
import { useDesktopHostId } from "@/shell/RevealInFileManager";

const EDITOR_LABELS = Object.fromEntries(
  CODE_EDITORS.map(({ id, label }) => [id, label]),
) as Record<CodeEditor, string>;

function EditorIcon({ editor }: { editor: CodeEditor }) {
  return (
    <span aria-hidden="true" className="inline-flex size-4 shrink-0">
      {editor === "cursor" ? <CursorIcon size={16} /> : <VSCodeIcon size={16} />}
    </span>
  );
}

/**
 * The Open button for the folders that the session's agent stamped. It shows only
 * where a folder can open: this desktop owns the session's host, or the host takes SSH.
 */
export function OpenFoldersMenu({
  conversationId,
  hostId,
  className,
}: {
  conversationId: string | null;
  /** The host where the session's agent runs, which holds the stamped folders. */
  hostId: string | null;
  className?: string;
}) {
  const folders = useOpenFolders(conversationId);
  if (folders.length === 0) return null;
  return (
    <OpenFoldersButton
      folders={folders}
      conversationId={conversationId}
      hostId={hostId}
      className={className}
    />
  );
}

function OpenFoldersButton({
  folders,
  conversationId,
  hostId,
  className,
}: {
  folders: OpenFolder[];
  conversationId: string | null;
  hostId: string | null;
  className?: string;
}) {
  const editors = useInstalledEditors();
  const desktopHostId = useDesktopHostId();
  const mobile = isAndroidShell() || isIOSShell();
  const local = hostId !== null && hostId === desktopHostId;
  const sshTarget = useHostSshTarget(
    !mobile && !local ? hostId : null,
    conversationId ?? undefined,
  );
  if (mobile || editors.length === 0 || (!local && !sshTarget)) return null;
  const open = (editor: CodeEditor, path: string) => {
    const url = editorLink(editor, path, local ? null : sshTarget, true);
    if (url) launchEditorLink(url);
  };
  const onlyEditor = editors.length === 1 ? editors[0] : null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          data-testid="composer-open-folders"
          className={cn(
            "flex shrink-0 items-center gap-1 rounded text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
            className,
          )}
        >
          <FolderOpenIcon className="size-3.5 shrink-0" aria-hidden />
          <span>Open</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="min-w-44 max-w-80">
        {folders.map((folder) =>
          onlyEditor ? (
            <DropdownMenuItem
              key={folder.path}
              title={folder.path}
              onSelect={() => open(onlyEditor, folder.path)}
            >
              <EditorIcon editor={onlyEditor} />
              <span className="truncate">{folder.title}</span>
            </DropdownMenuItem>
          ) : (
            <DropdownMenuSub key={folder.path}>
              <DropdownMenuSubTrigger title={folder.path}>
                <FolderOpenIcon className="size-4" aria-hidden />
                <span className="truncate">{folder.title}</span>
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {editors.map((editor) => (
                  <DropdownMenuItem key={editor} onSelect={() => open(editor, folder.path)}>
                    <EditorIcon editor={editor} />
                    {EDITOR_LABELS[editor]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          ),
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
