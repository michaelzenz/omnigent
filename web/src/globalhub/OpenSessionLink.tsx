import { ArrowUpRightIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Link } from "@/lib/routing";

/** An arrow button that opens a session's page. */
export function OpenSessionLink({ sessionId, label }: { sessionId: string; label: string }) {
  return (
    <Button asChild variant="outline" size="icon-sm" className="text-muted-foreground">
      <Link to={`/c/${encodeURIComponent(sessionId)}`} aria-label={label} title="Open session">
        <ArrowUpRightIcon aria-hidden />
      </Link>
    </Button>
  );
}
