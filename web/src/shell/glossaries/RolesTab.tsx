import { useState } from "react";
import { STATIC_ROLE_CATALOG, MANAGER_ROLE_DEFINITION } from "./rolesCatalog";
import { RoleDefinitionCard } from "./RoleDefinitionCard";
import { RoleDefaultsForm } from "./RoleDefaultsForm";
import { WorkerProvidersSection } from "./WorkerProvidersSection";
import { TemplateRolesSection } from "./TemplateRolesSection";
import { MANAGER_ROLE_PREFIX } from "@/lib/agentTasksApi";

export function RolesTab() {
  const [selectedRoleId, setSelectedRoleId] = useState<string>(
    STATIC_ROLE_CATALOG[0]?.id ?? "broker",
  );

  return (
    <div
      className="mx-auto flex w-full max-w-4xl flex-col gap-4"
      data-testid="glossaries-roles-tab"
    >
      {STATIC_ROLE_CATALOG.map((entry) => {
        const hasProfile = entry.defaultsKind === "per_user" && entry.profileRoleId;
        return (
          <RoleDefinitionCard
            key={entry.id}
            entry={entry}
            selected={selectedRoleId === entry.id}
            onSelect={() => setSelectedRoleId(entry.id)}
          >
            {hasProfile ? <RoleDefaultsForm roleId={entry.profileRoleId!} /> : null}
          </RoleDefinitionCard>
        );
      })}
      <TemplateRolesSection
        rolePrefix={MANAGER_ROLE_PREFIX}
        sectionTitle="Task managers"
        definition={MANAGER_ROLE_DEFINITION}
        addButtonLabel="Add manager"
        testId="glossary-manager-roles-section"
      />
      <WorkerProvidersSection />
    </div>
  );
}
