import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RolesTab } from "@/glossaries/RolesTab";
import { SkillsTab } from "@/glossaries/SkillsTab";

/** What the user's Global Hub agents work with, one tab each: their roles and their skills. */
export function GlossariesPage() {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="glossaries-page">
      <div className="border-b px-4 py-3">
        <h1 className="text-lg font-semibold">Glossaries</h1>
        <p className="text-sm text-muted-foreground">
          How your Global Hub roles and watchers behave, and the skills on your hosts.
        </p>
      </div>
      <Tabs defaultValue="roles" className="min-h-0 flex-1 gap-0">
        <div className="shrink-0 border-b px-4 py-2">
          <TabsList>
            <TabsTrigger value="roles">Roles</TabsTrigger>
            <TabsTrigger value="skills">Skills</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="roles" className="min-h-0 overflow-auto p-4">
          <RolesTab />
        </TabsContent>
        <TabsContent value="skills" className="min-h-0 overflow-auto p-4">
          <SkillsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
