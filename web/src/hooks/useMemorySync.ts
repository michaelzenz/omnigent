import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";
import { apiErrorFromResponse } from "@/lib/sessionsApi";

export type MemoryHostState = "present" | "missing" | "offline" | "needs_update" | "error";

/** The hosts whose ``~/AGENTS.md`` has the same content. */
export interface MemoryVariant {
  contentSha256: string;
  content: string;
  hostIds: string[];
}

/** One host's ``~/AGENTS.md``; ``error`` says why its file is unknown. */
export interface MemoryHost {
  hostId: string;
  hostName: string;
  online: boolean;
  state: MemoryHostState;
  contentSha256: string | null;
  error: string | null;
}

export interface MemorySyncState {
  file: string;
  variants: MemoryVariant[];
  hosts: MemoryHost[];
}

/** A host to write; ``expectedSha256`` is the hash read from it, ``null`` for no file. */
export interface MemoryWriteTarget {
  hostId: string;
  expectedSha256: string | null;
}

export interface MemoryWriteHostResult {
  hostId: string;
  status: "done" | "conflict" | "failed";
  contentSha256: string | null;
  error: string | null;
}

export const MEMORY_SYNC_KEY = ["memory-sync"] as const;

interface WireMemoryState {
  file: string;
  variants: { content_sha256: string; content: string; host_ids: string[] }[];
  hosts: {
    host_id: string;
    host_name: string;
    online: boolean;
    state: MemoryHostState;
    content_sha256: string | null;
    error: string | null;
  }[];
}

interface WireWriteResult {
  results: {
    host_id: string;
    status: MemoryWriteHostResult["status"];
    content_sha256?: string | null;
    error?: string | null;
  }[];
}

async function fetchMemory(): Promise<MemorySyncState> {
  const response = await authenticatedFetch("/v1/memory-sync");
  if (!response.ok) throw await apiErrorFromResponse(response);
  const body = (await response.json()) as WireMemoryState;
  return {
    file: body.file,
    variants: body.variants.map((variant) => ({
      contentSha256: variant.content_sha256,
      content: variant.content,
      hostIds: variant.host_ids,
    })),
    hosts: body.hosts.map((host) => ({
      hostId: host.host_id,
      hostName: host.host_name,
      online: host.online,
      state: host.state,
      contentSha256: host.content_sha256,
      error: host.error,
    })),
  };
}

async function writeMemory(
  content: string,
  targets: MemoryWriteTarget[],
): Promise<MemoryWriteHostResult[]> {
  const response = await authenticatedFetch("/v1/memory-sync/write", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content,
      targets: targets.map((target) => ({
        host_id: target.hostId,
        expected_sha256: target.expectedSha256,
      })),
    }),
  });
  if (!response.ok) throw await apiErrorFromResponse(response);
  const body = (await response.json()) as WireWriteResult;
  return body.results.map((result) => ({
    hostId: result.host_id,
    status: result.status,
    contentSha256: result.content_sha256 ?? null,
    error: result.error ?? null,
  }));
}

/** ``~/AGENTS.md`` on every online host; each fetch asks the hosts again. */
export function useMemorySync() {
  return useQuery({
    queryKey: MEMORY_SYNC_KEY,
    queryFn: fetchMemory,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}

/** Write new ``~/AGENTS.md`` text to the given hosts, then read every host again. */
export function useWriteMemory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ content, targets }: { content: string; targets: MemoryWriteTarget[] }) =>
      writeMemory(content, targets),
    onSettled: () => queryClient.invalidateQueries({ queryKey: MEMORY_SYNC_KEY }),
  });
}
