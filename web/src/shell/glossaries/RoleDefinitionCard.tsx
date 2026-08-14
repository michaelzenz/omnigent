import type { ReactNode } from "react";
import type { RoleCatalogEntry } from "./rolesCatalog";

interface RoleDefinitionCardProps {
  entry: RoleCatalogEntry;
  selected: boolean;
  onSelect: () => void;
  headerActions?: ReactNode;
  children?: ReactNode;
}

/** Glossary card shell: definition text plus an optional defaults slot. */
export function RoleDefinitionCard({
  entry,
  selected,
  onSelect,
  headerActions,
  children,
}: RoleDefinitionCardProps) {
  return (
    <article
      className="rounded-lg border border-border bg-card p-4 shadow-sm"
      data-testid={`glossary-role-card-${entry.id}`}
      data-selected={selected ? "true" : "false"}
    >
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          className="min-w-0 flex-1 text-left"
          onClick={onSelect}
          aria-pressed={selected}
        >
          <h3 className="text-sm font-semibold">{entry.title}</h3>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{entry.definition}</p>
        </button>
        {headerActions ? <div className="shrink-0">{headerActions}</div> : null}
      </div>
      {children ? <div className="mt-4 border-t border-border pt-4">{children}</div> : null}
    </article>
  );
}
