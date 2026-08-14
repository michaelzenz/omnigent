import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useRoleProfiles } from "@/hooks/useRoleProfiles";

interface TaskCardTemplateRolePickerProps {
  taskId: string;
  rolePrefix: string;
  roleKey: string;
  label: string;
  editable: boolean;
  onChange: (roleKey: string) => void;
  isPending?: boolean;
  error?: string | null;
  compact?: boolean;
}

export function TaskCardTemplateRolePicker({
  taskId,
  rolePrefix,
  roleKey,
  label,
  editable,
  onChange,
  isPending = false,
  error = null,
  compact = false,
}: TaskCardTemplateRolePickerProps) {
  const { data: profiles = [] } = useRoleProfiles(rolePrefix);
  const options = profiles.filter((profile) => profile.role.startsWith(rolePrefix));
  const current = options.find((profile) => profile.role === roleKey);
  const testId = `task-${rolePrefix.replace(":", "")}-role-${taskId}`;

  if (!editable) {
    return (
      <p className="text-xs text-muted-foreground" data-testid={testId}>
        {label}: {current?.title ?? roleKey}
      </p>
    );
  }

  return (
    <div className={cn(compact ? "min-w-0 flex-1" : "space-y-1")} data-testid={testId}>
      {!compact ? <span className="text-xs text-muted-foreground">{label}</span> : null}
      <Select value={roleKey} onValueChange={onChange} disabled={isPending || options.length === 0}>
        <SelectTrigger
          className={cn("h-8 text-xs", compact ? "w-full" : "w-full max-w-xs")}
          aria-label={compact ? label : undefined}
        >
          <SelectValue placeholder={`Select ${label.toLowerCase()}`} />
        </SelectTrigger>
        <SelectContent>
          {options.map((profile) => (
            <SelectItem key={profile.role} value={profile.role}>
              {profile.title ?? profile.role}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
