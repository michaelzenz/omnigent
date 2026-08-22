import { useMemo, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon, SearchIcon } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import {
  useToolPreferences,
  useUpdateToolPreferences,
  type ToolEntry,
  type ToolGroup,
} from "@/hooks/useToolPreferences";
import { cn } from "@/lib/utils";

type CheckedState = boolean | "indeterminate";

function groupCheckedState(tools: ToolEntry[]): CheckedState {
  const enabledCount = tools.filter((t) => t.enabled).length;
  if (enabledCount === 0) return false;
  if (enabledCount === tools.length) return true;
  return "indeterminate";
}

function ToolsTabInner({ data }: { data: NonNullable<ReturnType<typeof useToolPreferences>["data"]> }) {
  const update = useUpdateToolPreferences();
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toolsByGroup = useMemo(() => {
    const map = new Map<string, ToolEntry[]>();
    for (const tool of data.tools) {
      const list = map.get(tool.group) ?? [];
      list.push(tool);
      map.set(tool.group, list);
    }
    return map;
  }, [data.tools]);

  const filteredGroups = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return data.groups;
    return data.groups.filter((group) => {
      const tools = toolsByGroup.get(group.id) ?? [];
      return tools.some(
        (tool) =>
          tool.title.toLowerCase().includes(query) ||
          tool.name.toLowerCase().includes(query) ||
          tool.description.toLowerCase().includes(query),
      );
    });
  }, [search, data.groups, toolsByGroup]);

  function toggleGroup(groupId: string, enable: boolean) {
    const groupTools = toolsByGroup.get(groupId) ?? [];
    const names = new Set(groupTools.map((t) => t.name));
    const currentDisabled = new Set(data.disabledTools);
    if (enable) {
      for (const name of names) currentDisabled.delete(name);
    } else {
      for (const name of names) currentDisabled.add(name);
    }
    update.mutate([...currentDisabled]);
  }

  function toggleTool(toolName: string, enable: boolean) {
    const currentDisabled = new Set(data.disabledTools);
    if (enable) {
      currentDisabled.delete(toolName);
    } else {
      currentDisabled.add(toolName);
    }
    update.mutate([...currentDisabled]);
  }

  function toggleExpand(groupId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }

  function setAllExpanded(expand: boolean) {
    if (expand) {
      setExpanded(new Set(data.groups.map((g) => g.id)));
    } else {
      setExpanded(new Set());
    }
  }

  const query = search.trim().toLowerCase();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Control which tools agents may use. Changes apply to all agents and sessions in this
          deployment, taking effect on the next agent turn.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search tools by name or description"
            className="pl-9"
          />
        </div>
        <Button variant="ghost" size="sm" onClick={() => setAllExpanded(expanded.size === 0)}>
          {expanded.size === 0 ? "Expand all" : "Collapse all"}
        </Button>
      </div>
      {update.isError && (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-sm text-destructive">
          Failed to save tool preferences. {update.error instanceof Error ? update.error.message : ""}
        </p>
      )}
      <div className="rounded-lg border border-border divide-y divide-border">
        {filteredGroups.map((group) => {
          const groupTools = toolsByGroup.get(group.id) ?? [];
          const enabledCount = groupTools.filter((t) => t.enabled).length;
          const isChecked = groupCheckedState(groupTools);
          const isOpen = expanded.has(group.id) || (query.length > 0 && groupTools.some(
            (t) => t.title.toLowerCase().includes(query) || t.name.toLowerCase().includes(query) || t.description.toLowerCase().includes(query),
          ));

          return (
            <div key={group.id}>
              <div className="flex items-center gap-2 px-3 py-2.5">
                <button
                  type="button"
                  onClick={() => toggleExpand(group.id)}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label={isOpen ? "Collapse" : "Expand"}
                >
                  {isOpen ? <ChevronDownIcon className="size-4" /> : <ChevronRightIcon className="size-4" />}
                </button>
                <Checkbox
                  checked={isChecked}
                  onCheckedChange={(value) => toggleGroup(group.id, value === true)}
                  aria-label={`Toggle all ${group.title}`}
                />
                <button
                  type="button"
                  onClick={() => toggleExpand(group.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <span className="text-sm font-medium">{group.title}</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {enabledCount} of {groupTools.length} enabled
                  </span>
                </button>
              </div>
              {isOpen && (
                <div className="border-t border-border bg-muted/20">
                  {groupTools.map((tool) => (
                    <div
                      key={tool.name}
                      className="flex items-center gap-3 px-3 py-2 pl-10"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{tool.title}</span>
                          <code className="text-xs text-muted-foreground">{tool.name}</code>
                        </div>
                        <p className="truncate text-xs text-muted-foreground">{tool.description}</p>
                      </div>
                      <Switch
                        checked={tool.enabled}
                        onCheckedChange={(checked) => toggleTool(tool.name, checked === true)}
                        aria-label={`Toggle ${tool.title}`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {filteredGroups.length === 0 && (
          <p className="p-6 text-center text-sm text-muted-foreground">No tools match your search.</p>
        )}
      </div>
    </div>
  );
}

export function ToolsTab() {
  const prefs = useToolPreferences();

  if (prefs.isLoading) {
    return (
      <div className="grid min-h-[20rem] place-items-center text-sm text-muted-foreground">
        Loading tool preferences…
      </div>
    );
  }
  if (prefs.isError || !prefs.data) {
    return (
      <div className="grid min-h-[20rem] place-items-center text-sm text-muted-foreground">
        Failed to load tool preferences.{" "}
        {prefs.error instanceof Error ? prefs.error.message : ""}
      </div>
    );
  }
  return <ToolsTabInner data={prefs.data} />;
}
