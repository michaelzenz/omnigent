import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { authenticatedFetch } from "@/lib/identity";

export interface WorktreeSize {
  path: string;
  branch: string | null;
  is_main: boolean;
  size_bytes: number;
  error: string | null;
}

export interface WorktreeSizesResponse {
  data: WorktreeSize[];
  total_bytes: number;
  calculated_at: number; // 0 = not yet calculated
  error: string | null;
}

async function fetchWorktreeSizes(
  hostId: string,
  repoPath: string,
  force = false,
): Promise<WorktreeSizesResponse> {
  const params = new URLSearchParams({ path: repoPath });
  if (force) params.set("force", "true");
  const res = await authenticatedFetch(
    `/v1/hosts/${encodeURIComponent(hostId)}/worktree-sizes?${params}`,
  );
  if (res.status === 400 || res.status === 404) {
    return { data: [], total_bytes: 0, calculated_at: 0, error: "not a git repository" };
  }
  if (!res.ok) throw new Error(`worktree sizes fetch failed: HTTP ${res.status}`);
  const body = await res.json();
  return {
    data: (body.data ?? []) as WorktreeSize[],
    total_bytes: body.total_bytes ?? 0,
    calculated_at: body.calculated_at ?? 0,
    error: (body.error as string | null) ?? null,
  };
}

export function useWorktreeSizes(hostId: string | null, repoPath: string | null) {
  return useQuery({
    queryKey: ["worktree-sizes", hostId, repoPath],
    queryFn: () => fetchWorktreeSizes(hostId as string, repoPath as string),
    enabled: hostId !== null && repoPath !== null && repoPath !== "",
    staleTime: 60_000,
    refetchInterval: 600_000, // poll every 10 min to pick up background-refreshed data
  });
}

export function useRefreshWorktreeSizes(hostId: string | null, repoPath: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => fetchWorktreeSizes(hostId as string, repoPath as string, true),
    onSuccess: (data) => {
      queryClient.setQueryData(["worktree-sizes", hostId, repoPath], data);
    },
  });
}
