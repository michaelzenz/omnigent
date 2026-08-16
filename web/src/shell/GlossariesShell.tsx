import { useMemo } from "react";
import { useSearchParams } from "@/lib/routing";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { GlossariesPlaceholderTab } from "./glossaries/GlossariesPlaceholderTab";
import { RolesTab } from "./glossaries/RolesTab";
import { ScriptPluginsBoard } from "./glossaries/ScriptPluginsBoard";

const TAB_IDS = ["roles", "pollers", "timers", "skills"] as const;
type GlossariesTabId = (typeof TAB_IDS)[number];

function parseTab(raw: string | null): GlossariesTabId {
  if (raw && TAB_IDS.includes(raw as GlossariesTabId)) {
    return raw as GlossariesTabId;
  }
  return "roles";
}

export function GlossariesShell() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = useMemo(() => parseTab(searchParams.get("tab")), [searchParams]);

  function setTab(tab: GlossariesTabId) {
    const next = new URLSearchParams(searchParams);
    next.set("tab", tab);
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="glossaries-page">
      <header className="border-b border-border px-4 py-3">
        <h1 className="text-lg font-semibold">Glossaries</h1>
        <p className="text-sm text-muted-foreground">
          Role definitions, defaults, and shared automation inventory.
        </p>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <Tabs value={activeTab} onValueChange={(v) => setTab(parseTab(v))} className="w-full">
          <TabsList>
            <TabsTrigger value="roles">Roles</TabsTrigger>
            <TabsTrigger value="pollers">Pollers</TabsTrigger>
            <TabsTrigger value="timers">Timers</TabsTrigger>
            <TabsTrigger value="skills">Skills</TabsTrigger>
          </TabsList>
          <TabsContent value="roles" className="mt-4">
            <RolesTab />
          </TabsContent>
          <TabsContent value="pollers" className="mt-4">
            <ScriptPluginsBoard kind="poll" testId="glossaries-tab-pollers" />
          </TabsContent>
          <TabsContent value="timers" className="mt-4">
            <ScriptPluginsBoard kind="timer" testId="glossaries-tab-timers" />
          </TabsContent>
          <TabsContent value="skills" className="mt-4">
            <GlossariesPlaceholderTab title="Skills" />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
