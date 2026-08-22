import { useState } from "react";
import { Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  useCreateTemplateRoleProfile,
  useDeleteRoleProfile,
  useRoleProfiles,
} from "@/hooks/useRoleProfiles";
import { RoleDefinitionCard } from "./RoleDefinitionCard";
import { RoleDefaultsForm } from "./RoleDefaultsForm";
import { RoleDescriptionField } from "./RoleHeaderControls";

interface TemplateRolesSectionProps {
  rolePrefix: string;
  sectionTitle: string;
  definition: string;
  addButtonLabel: string;
  testId: string;
}

export function TemplateRolesSection({
  rolePrefix,
  sectionTitle,
  definition,
  addButtonLabel,
  testId,
}: TemplateRolesSectionProps) {
  const { data: profiles = [], isLoading, isError, error } = useRoleProfiles(rolePrefix);
  const createRole = useCreateTemplateRoleProfile(rolePrefix);
  const deleteRole = useDeleteRoleProfile();
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [slugDraft, setSlugDraft] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const templateProfiles = profiles.filter((profile) => profile.role.startsWith(rolePrefix));
  const activeRole = selectedRole ?? templateProfiles[0]?.role ?? null;

  async function handleCreate() {
    setCreateError(null);
    try {
      const created = await createRole.mutateAsync({ slug: slugDraft });
      setSlugDraft("");
      setSelectedRole(created.role);
    } catch (err) {
      setCreateError(
        err instanceof Error ? err.message : `Failed to create ${sectionTitle.toLowerCase()}`,
      );
    }
  }

  return (
    <section className="space-y-3" data-testid={testId}>
      <div className="flex flex-col items-end gap-1 sm:flex-row sm:items-center sm:justify-between">
        <h3 className="text-sm font-semibold self-start sm:self-center">{sectionTitle}</h3>
        <div className="flex w-full flex-col gap-1 sm:w-auto sm:items-end">
          <div className="flex items-center gap-2">
            <Input
              className="h-8 w-full min-w-40 text-xs sm:w-40"
              placeholder="slug (e.g. research)"
              value={slugDraft}
              onChange={(e) => setSlugDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && slugDraft.trim() && !createRole.isPending) {
                  void handleCreate();
                }
              }}
              data-testid={`${testId}-slug-input`}
            />
            <Button
              type="button"
              size="sm"
              className="h-8 shrink-0"
              disabled={!slugDraft.trim() || createRole.isPending}
              onClick={() => void handleCreate()}
              data-testid={`${testId}-add-button`}
            >
              {addButtonLabel}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Enter a slug to enable {addButtonLabel.toLowerCase()}.
          </p>
        </div>
      </div>
      {createError ? <p className="text-xs text-destructive">{createError}</p> : null}
      {isError ? (
        <p className="text-sm text-destructive">
          {error instanceof Error ? error.message : `Failed to load ${sectionTitle.toLowerCase()}`}
        </p>
      ) : null}
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading {sectionTitle.toLowerCase()}…</p>
      ) : (
        templateProfiles.map((profile) => (
          <RoleDefinitionCard
            key={profile.role}
            entry={{
              id: profile.role,
              title: profile.title ?? profile.role,
              definition,
              defaultsKind: "per_user",
              profileRoleId: profile.role,
            }}
            selected={activeRole === profile.role}
            onSelect={() => setSelectedRole(profile.role)}
            headerActions={
              <div className="flex items-center gap-1.5">
                {profile.deletable ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7 text-muted-foreground hover:text-destructive"
                    disabled={deleteRole.isPending}
                    aria-label={`Delete ${profile.title ?? profile.role}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      void deleteRole.mutateAsync(profile.role);
                    }}
                    data-testid={`${testId}-delete-${profile.role}`}
                  >
                    <Trash2Icon className="size-4" />
                  </Button>
                ) : null}
              </div>
            }
            descriptionSlot={<RoleDescriptionField roleId={profile.role} />}
          >
            <RoleDefaultsForm roleId={profile.role} />
          </RoleDefinitionCard>
        ))
      )}
    </section>
  );
}
