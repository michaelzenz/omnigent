import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchFileContent } from "@/hooks/useFileContent";
import { useHosts } from "@/hooks/useHosts";
import { inferHostSshTarget, parseArcaTypeLabel } from "@/lib/editorLinks";
import { isTempConvId } from "@/lib/tempConversationId";

const DISCOVERY_STALE_MS = 5 * 60_000;

async function readMetadataText(conversationId: string, path: string): Promise<string | null> {
  const file = await fetchFileContent(conversationId, path);
  return file.object === "session.environment.filesystem.file_content" &&
    file.encoding === "utf-8" &&
    !file.truncated &&
    typeof file.content === "string"
    ? file.content
    : null;
}

async function discoverHostSshTarget(conversationId: string): Promise<string | null> {
  const labels = await readMetadataText(conversationId, "/etc/metric_proxy/labels/default");
  const type = labels === null ? null : parseArcaTypeLabel(labels);
  if (type === "arca") return "arca.ssh";
  if (type !== "arclet") return null;
  const name = await readMetadataText(conversationId, "/opt/arclet/arclet-name");
  return inferHostSshTarget({ name: name?.trim() ?? "", sandbox_provider: "arclet" });
}

function sshTargetKey(conversationId: string | undefined, hostId: string | null) {
  return ["hostSshTarget", conversationId, hostId] as const;
}

/** Resolve standard aliases before the click, using metadata only for user-connected hosts. */
export function useHostSshTarget(
  hostId: string | null,
  conversationId: string | undefined,
): string | null {
  const { data: hosts } = useHosts({ includeSandbox: true, enabled: hostId !== null });
  const host = hosts?.find((candidate) => candidate.host_id === hostId);
  const online = host?.status === "online";
  const providerTarget = inferHostSshTarget(host);
  const discover =
    online && host.sandbox_provider == null && !!conversationId && !isTempConvId(conversationId);
  const { data: discoveredTarget, isError } = useQuery({
    queryKey: sshTargetKey(conversationId, hostId),
    enabled: discover,
    staleTime: DISCOVERY_STALE_MS,
    retry: false,
    queryFn: () => discoverHostSshTarget(conversationId!),
  });
  if (!online) return null;
  return providerTarget ?? (discover && !isError ? (discoveredTarget ?? null) : null);
}

/**
 * A host's SSH target, or why there is none: the host is offline, no address is known, or
 * the check through the session failed, e.g. while its runner starts.
 */
export type HostSshTargetResolution =
  { target: string } | { unreachable: "offline" | "no_address" | "check_failed" };

/**
 * Return a function that resolves the host's SSH target when the user acts. A check that
 * failed before, e.g. while the session's runner was down, runs again.
 */
export function useResolveHostSshTarget(
  hostId: string | null,
  conversationId: string | undefined,
): () => Promise<HostSshTargetResolution> {
  const queryClient = useQueryClient();
  const { data: hosts } = useHosts({ includeSandbox: true, enabled: hostId !== null });
  return async () => {
    const host = hosts?.find((candidate) => candidate.host_id === hostId);
    if (host?.status !== "online") return { unreachable: "offline" };
    const providerTarget = inferHostSshTarget(host);
    if (providerTarget) return { target: providerTarget };
    if (host.sandbox_provider != null || !conversationId || isTempConvId(conversationId)) {
      return { unreachable: "no_address" };
    }
    try {
      const target = await queryClient.fetchQuery({
        queryKey: sshTargetKey(conversationId, hostId),
        staleTime: DISCOVERY_STALE_MS,
        queryFn: () => discoverHostSshTarget(conversationId),
      });
      return target ? { target } : { unreachable: "no_address" };
    } catch {
      return { unreachable: "check_failed" };
    }
  };
}
