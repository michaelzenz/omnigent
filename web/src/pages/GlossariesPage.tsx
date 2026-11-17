import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MemoryTab } from "@/glossaries/MemoryTab";
import { RolesTab } from "@/glossaries/RolesTab";
import { SkillsTab } from "@/glossaries/SkillsTab";

/**
 * What the user's Global Hub agents work with, one tab each: roles, skills and
 * memory. The memory tab stays mounted after its first visit, so unsaved text
 * survives a tab switch.
 */
export function GlossariesPage() {
  const [memoryOpened, setMemoryOpened] = useState(false);
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="glossaries-page">
      <div className="border-b px-4 py-3">
        <h1 className="text-lg font-semibold">Glossaries</h1>
        <p className="text-sm text-muted-foreground">
          How your Global Hub roles and watchers behave, and the skills and AGENTS.md on your hosts.
        </p>
      </div>
      <Tabs
        defaultValue="roles"
        onValueChange={(value) => {
          if (value === "memory") setMemoryOpened(true);
        }}
        className="min-h-0 flex-1 gap-0"
      >
        <div className="shrink-0 border-b px-4 py-2">
          <TabsList>
            <TabsTrigger value="roles">Roles</TabsTrigger>
            <TabsTrigger value="skills">Skills</TabsTrigger>
            <TabsTrigger value="memory">Memory</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="roles" className="min-h-0 overflow-auto p-4">
          <RolesTab />
        </TabsContent>
        <TabsContent value="skills" className="min-h-0 overflow-auto p-4">
          <SkillsTab />
        </TabsContent>
        {memoryOpened && (
          <TabsContent
            value="memory"
            forceMount
            className="min-h-0 overflow-auto p-4 data-[state=inactive]:hidden"
          >
            <MemoryTab />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
