import { useEffect, useMemo, useState } from "react";
import { DiffEditor, type DiffEditorProps } from "@monaco-editor/react";
import { Button } from "@/components/ui/button";
import {
  codeFontFamilyForEditor,
  readCodeFontFamily,
  readCodeFontSizePx,
} from "@/lib/codeFontPreferences";
import { detectLang } from "../codeViewerHelpers";
import { ensureLanguage, ensureMonacoReady, monacoLanguageId } from "../monacoSetup";
import "../monacoCodeEditor.css";

interface SkillVariantDiffProps {
  original: string;
  modified: string;
  originalLabel: string;
  modifiedLabel: string;
}

export function SkillVariantDiff({
  original,
  modified,
  originalLabel,
  modifiedLabel,
}: SkillVariantDiffProps) {
  const [layout, setLayout] = useState<"split" | "unified">("split");
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const language = detectLang("SKILL.md");

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    setLoadError(false);
    void Promise.all([ensureMonacoReady(), ensureLanguage(language)]).then(
      () => {
        if (!cancelled) setReady(true);
      },
      () => {
        if (!cancelled) setLoadError(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [language]);

  const options = useMemo<DiffEditorProps["options"]>(
    () => ({
      readOnly: true,
      originalEditable: false,
      renderSideBySide: layout === "split",
      minimap: { enabled: false },
      automaticLayout: true,
      scrollBeyondLastLine: false,
      renderOverviewRuler: false,
      ignoreTrimWhitespace: false,
      diffWordWrap: "on",
      fontSize: readCodeFontSizePx(),
      fontFamily: codeFontFamilyForEditor(readCodeFontFamily()),
      hideUnchangedRegions: { enabled: true, contextLineCount: 3 },
    }),
    [layout],
  );

  return (
    <div className="flex h-[32rem] flex-none flex-col bg-white text-slate-950">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-3 py-2 text-xs">
        <span className="font-medium">
          {originalLabel} → {modifiedLabel}
        </span>
        <span className="flex items-center gap-1 text-muted-foreground">
          <span className="size-2 rounded-sm bg-red-500/70" /> Removed
        </span>
        <span className="flex items-center gap-1 text-muted-foreground">
          <span className="size-2 rounded-sm bg-green-500/70" /> Added
        </span>
        <span className="text-muted-foreground">Darker marks show changed words</span>
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="xs"
            variant={layout === "split" ? "secondary" : "ghost"}
            onClick={() => setLayout("split")}
          >
            Split
          </Button>
          <Button
            size="xs"
            variant={layout === "unified" ? "secondary" : "ghost"}
            onClick={() => setLayout("unified")}
          >
            Unified
          </Button>
        </div>
      </div>
      <div className="relative min-h-0 flex-1">
        {loadError && (
          <div className="grid h-full place-items-center text-sm text-destructive">
            Failed to load highlighted differences.
          </div>
        )}
        {!loadError && !ready && (
          <div className="grid h-full place-items-center text-sm text-muted-foreground">
            Loading differences…
          </div>
        )}
        {!loadError && ready && (
          <DiffEditor
            height="100%"
            original={original}
            modified={modified}
            language={monacoLanguageId(language)}
            theme="github-light"
            options={options}
          />
        )}
      </div>
    </div>
  );
}
