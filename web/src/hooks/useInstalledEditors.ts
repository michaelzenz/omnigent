import { useQuery } from "@tanstack/react-query";
import { CODE_EDITORS, type CodeEditor } from "@/lib/editorLinks";
import { getInstalledEditors } from "@/lib/nativeBridge";

const ALL_EDITORS: readonly CodeEditor[] = CODE_EDITORS.map((editor) => editor.id);

/**
 * The editors whose links can open here. The desktop shell asks the OS; a browser
 * or an older shell cannot tell, so every editor stays available there.
 */
export function useInstalledEditors(): readonly CodeEditor[] {
  const { data } = useQuery({
    queryKey: ["installed-editors"],
    queryFn: getInstalledEditors,
    staleTime: 5 * 60_000,
    retry: false,
  });
  if (!data) return ALL_EDITORS;
  return ALL_EDITORS.filter((editor) => data[editor]);
}
