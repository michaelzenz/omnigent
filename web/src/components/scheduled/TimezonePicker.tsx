// Searchable IANA timezone picker shared by the automation schedule and other daily schedules.
import { useMemo, useState } from "react";
import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { commonTimezones } from "@/lib/timezones";
import { cn } from "@/lib/utils";

export function TimezonePicker({
  timezone,
  onChange,
  onOpenChange,
  className,
  testId = "task-timezone-trigger",
}: {
  timezone: string;
  onChange: (timezone: string) => void;
  /** Hears the list open and close, e.g. so a dialog leaves Escape to the list. */
  onOpenChange?: (open: boolean) => void;
  /** Classes for the trigger button. */
  className?: string;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const zones = useMemo(() => commonTimezones(timezone), [timezone]);

  function changeOpen(next: boolean) {
    setOpen(next);
    onOpenChange?.(next);
  }

  return (
    <Popover modal open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          className={className}
          role="combobox"
          aria-label="Timezone"
          aria-expanded={open}
          data-testid={testId}
        >
          <span className="truncate">{timezone.replaceAll("_", " ")}</span>
          <ChevronDownIcon className="size-3.5 shrink-0" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="menu-surface w-[min(22rem,calc(100vw-2rem))] p-0">
        <Command label="Search timezones">
          <CommandInput placeholder="Search timezones…" aria-label="Search timezones" />
          <CommandList>
            <CommandEmpty>No timezone found.</CommandEmpty>
            {zones.map((zone) => (
              <CommandItem
                key={zone}
                value={zone}
                keywords={[zone.replaceAll("_", " ")]}
                onSelect={() => {
                  onChange(zone);
                  changeOpen(false);
                }}
              >
                {zone.replaceAll("_", " ")}
                <CheckIcon className={cn("ml-auto size-4", zone !== timezone && "invisible")} />
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
