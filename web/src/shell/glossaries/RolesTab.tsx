import { useState } from "react";
import {
  STATIC_ROLE_CATALOG,
  MANAGER_ROLE_DEFINITION,
  WORKER_ROLE_DEFINITION,
} from "./rolesCatalog";
import { RoleDefinitionCard } from "./RoleDefinitionCard";
import { RoleDefaultsForm } from "./RoleDefaultsForm";
import { TemplateRolesSection } from "./TemplateRolesSection";
import { MANAGER_ROLE_PREFIX, WORKER_ROLE_PREFIX } from "@/lib/agentTasksApi";

export function RolesTab() {
  const [selectedRoleId, setSelectedRoleId] = useState<string>(
    STATIC_ROLE_CATALOG[0]?.id ?? "broker",
  );

  return (
    <div
      className="mx-auto flex w-full max-w-4xl flex-col gap-4"
      data-testid="glossaries-roles-tab"
    >
      {STATIC_ROLE_CATALOG.map((entry) => (
        <RoleDefinitionCard
          key={entry.id}
          entry={entry}
          selected={selectedRoleId === entry.id}
          onSelect={() => setSelectedRoleId(entry.id)}
        >
          {entry.defaultsKind === "per_user" && entry.profileRoleId ? (
            <RoleDefaultsForm roleId={entry.profileRoleId} />
          ) : null}
        </RoleDefinitionCard>
      ))}
      <TemplateRolesSection
        rolePrefix={MANAGER_ROLE_PREFIX}
        sectionTitle="Task managers"
        definition={MANAGER_ROLE_DEFINITION}
        addButtonLabel="Add manager"
        testId="glossary-manager-roles-section"
      />
      <TemplateRolesSection
        rolePrefix={WORKER_ROLE_PREFIX}
        sectionTitle="Task workers"
        definition={WORKER_ROLE_DEFINITION}
        addButtonLabel="Add worker"
        testId="glossary-worker-roles-section"
      />
    </div>
  );
}
