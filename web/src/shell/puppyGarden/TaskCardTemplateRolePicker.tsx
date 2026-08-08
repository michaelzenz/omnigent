import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
    <div className="space-y-1" data-testid={testId}>
      <span className="text-xs text-muted-foreground">{label}</span>
      <Select value={roleKey} onValueChange={onChange} disabled={isPending || options.length === 0}>
        <SelectTrigger className="h-8 w-full max-w-xs text-xs">
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
