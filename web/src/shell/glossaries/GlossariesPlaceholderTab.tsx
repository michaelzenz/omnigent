interface GlossariesPlaceholderTabProps {
  title: string;
}

export function GlossariesPlaceholderTab({ title }: GlossariesPlaceholderTabProps) {
  return (
    <div
      className="flex h-48 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground"
      data-testid={`glossaries-tab-${title.toLowerCase()}`}
    >
      {title} — coming soon
    </div>
  );
}
