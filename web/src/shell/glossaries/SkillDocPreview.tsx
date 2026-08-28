import { useEffect, useMemo, useRef, useState } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import { StarterKit } from "@tiptap/starter-kit";
import { Table, TableRow, TableCell, TableHeader } from "@tiptap/extension-table";
import { ListItem, TaskItem, TaskList } from "@tiptap/extension-list";
import { Link } from "@tiptap/extension-link";
import { Markdown } from "@tiptap/markdown";
import { GitHubAlertBlockquote } from "@/shell/TipTapGitHubAlert";
import { HtmlPassthrough } from "@/shell/TipTapHtmlPassthrough";
import {
  installMarkdownParserPatch,
  installMarkdownSerializerPatch,
} from "@/shell/tiptapMarkdownPatches";
import { ToolbarPlugin } from "@/shell/MarkdownEditorToolbar";

installMarkdownSerializerPatch();
installMarkdownParserPatch();

// See MarkdownRichTextViewer: @tiptap/markdown can build list items with
// non-paragraph first children that crash ProseMirror on the first transaction.
const SafeListItem = ListItem.extend({ content: "block+" });

interface FrontmatterEntry {
  key: string;
  value: string;
}

function splitFrontmatter(content: string): { entries: FrontmatterEntry[]; body: string; prefix: string } {
  if (!content.startsWith("---")) return { entries: [], body: content, prefix: "" };
  const end = content.indexOf("\n---", 3);
  if (end === -1) return { entries: [], body: content, prefix: "" };
  const raw = content.slice(3, end).trim();
  const prefix = content.slice(0, end + 4) + "\n";
  const body = content.slice(end + 4).replace(/^\n/, "");
  const entries: FrontmatterEntry[] = [];
  for (const line of raw.split("\n")) {
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const key = line.slice(0, colon).trim();
    let value = line.slice(colon + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    entries.push({ key, value });
  }
  return { entries, body, prefix };
}

interface SkillDocPreviewProps {
  content: string;
  onChange: (content: string) => void;
}

export function SkillDocPreview({ content, onChange }: SkillDocPreviewProps) {
  const { entries, body, prefix } = useMemo(() => splitFrontmatter(content), [content]);
  const prefixRef = useRef(prefix);
  prefixRef.current = prefix;

  const [isDirty, setIsDirty] = useState(false);
  const hasUserEditedRef = useRef(false);
  const baselineRef = useRef<string | null>(null);

  const extensions = useMemo(
    () => [
      StarterKit.configure({ link: false, blockquote: false, listItem: false }),
      SafeListItem,
      TaskList,
      TaskItem.configure({ nested: true }),
      Table.configure({ resizable: true }),
      TableRow,
      TableCell,
      TableHeader,
      Link.configure({ openOnClick: false, autolink: false }),
      GitHubAlertBlockquote,
      HtmlPassthrough,
      Markdown,
    ],
    [],
  );

  const editor = useEditor({
    extensions,
    content: body,
    contentType: "markdown",
    editable: true,
    onUpdate: ({ editor: ed }) => {
      const markdown = ed.getMarkdown();
      if (baselineRef.current === null || !ed.isFocused) {
        baselineRef.current = markdown;
        setIsDirty(false);
        return;
      }
      hasUserEditedRef.current = true;
      setIsDirty(markdown !== baselineRef.current);
      onChange(prefixRef.current + markdown);
    },
    onCreate: ({ editor: ed }) => {
      baselineRef.current = ed.getMarkdown();
    },
  });

  // Re-sync when external content changes (e.g. switching skills/variants).
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    const currentBody = editor.getMarkdown();
    if (currentBody === body) return;
    hasUserEditedRef.current = false;
    baselineRef.current = null;
    editor.commands.setContent(body, { emitUpdate: false, contentType: "markdown" });
    baselineRef.current = editor.getMarkdown();
    setIsDirty(false);
  }, [editor, body]);

  useEffect(() => () => editor?.destroy(), [editor]);

  return (
    <div className="flex min-h-[32rem] flex-col">
      {entries.length > 0 && (
        <dl className="flex flex-wrap gap-x-6 gap-y-1.5 border-b border-border bg-muted/30 px-4 py-3">
          {entries.map((entry) => (
            <div key={entry.key} className="flex items-baseline gap-1.5">
              <dt className="text-xs font-medium text-muted-foreground">{entry.key}</dt>
              <dd className="text-xs text-foreground">{entry.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {editor && (
        <ToolbarPlugin
          editor={editor}
          onSave={() => onChange(prefixRef.current + editor.getMarkdown())}
          isSaving={false}
          isDirty={isDirty}
          saveError={false}
          saveDisabled={false}
          hasExternalUpdate={false}
        />
      )}
      <div className="flex-1 overflow-auto px-6 py-4">
        <EditorContent
          editor={editor}
          className="prose prose-sm dark:prose-invert max-w-none outline-none tiptap-md-content [&_*::selection]:bg-blue-300/40"
        />
      </div>
    </div>
  );
}
