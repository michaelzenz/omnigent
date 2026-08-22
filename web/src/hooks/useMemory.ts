import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface MemoryCategory {
  id: string;
  name: string;
  display_order: number;
  content: string;
  token_count: number;
  created_at: number;
  updated_at: number | null;
}

export interface MemoryResponse {
  categories: MemoryCategory[];
  used_tokens: number;
  max_tokens: number;
  provider: MemoryProvider;
  usage_percent: number;
  over_limit: boolean;
}

const MEMORY_KEY = ["memory"] as const;
const MEMORY_FILES_KEY = ["memory-files"] as const;

export type MemoryProvider = "omniharness" | "claude" | "agents";

export interface MemoryFileHost {
  host_id: string;
  host_name: string;
  online: boolean;
  status: "present" | "missing" | "unknown";
  content_sha256: string | null;
  error: string | null;
}

export interface MemoryFileVariant {
  content_sha256: string;
  content: string;
  token_count: number;
  active_count: number;
  hosts: MemoryFileHost[];
}

export interface MemoryFilesResponse {
  provider: Exclude<MemoryProvider, "omniharness">;
  rel_home_path: string;
  variants: MemoryFileVariant[];
  hosts: MemoryFileHost[];
  sync_results?: { host_id: string; status: "updated" | "unchanged" | "offline" }[];
}

async function memoryRequest(path: string, init?: RequestInit): Promise<MemoryResponse> {
  const response = await authenticatedFetch(path, init);
  if (!response.ok) {
    throw new Error((await response.text()) || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as MemoryResponse;
}

export function useMemory() {
  return useQuery({
    queryKey: MEMORY_KEY,
    queryFn: () => memoryRequest("/v1/memory"),
  });
}

function useMemoryMutation<TVariables>(
  mutationFn: (variables: TVariables) => Promise<MemoryResponse>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (memory) => {
      queryClient.setQueryData(MEMORY_KEY, memory);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: MEMORY_KEY });
    },
  });
}

export function useCreateMemoryCategory() {
  return useMemoryMutation(({ name, content = "" }: { name: string; content?: string }) =>
    memoryRequest("/v1/memory/categories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, content }),
    }),
  );
}

export function useUpdateMemoryCategory() {
  return useMemoryMutation(
    ({ id, ...updates }: { id: string; name?: string; content?: string; display_order?: number }) =>
      memoryRequest(`/v1/memory/categories/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      }),
  );
}

export function useDeleteMemoryCategory() {
  return useMemoryMutation((id: string) =>
    memoryRequest(`/v1/memory/categories/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  );
}

export function useReorderMemoryCategories() {
  return useMemoryMutation((orderedIds: string[]) =>
    memoryRequest("/v1/memory/order", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ordered_ids: orderedIds }),
    }),
  );
}

export function useUpdateMemorySettings() {
  return useMemoryMutation(
    (
      settings:
        | number
        | {
            max_tokens?: number;
            provider?: MemoryProvider;
          },
    ) =>
      memoryRequest("/v1/memory/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(typeof settings === "number" ? { max_tokens: settings } : settings),
      }),
  );
}

async function memoryFilesRequest(
  provider: Exclude<MemoryProvider, "omniharness">,
  init?: RequestInit,
): Promise<MemoryFilesResponse> {
  const response = await authenticatedFetch(`/v1/memory/files/${provider}`, init);
  if (!response.ok) {
    throw new Error((await response.text()) || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as MemoryFilesResponse;
}

export function useMemoryFileVariants(provider: Exclude<MemoryProvider, "omniharness"> | null) {
  return useQuery({
    queryKey: [...MEMORY_FILES_KEY, provider],
    queryFn: () => memoryFilesRequest(provider!),
    enabled: provider !== null,
  });
}

function useMemoryFilesMutation<
  TVariables extends { provider: Exclude<MemoryProvider, "omniharness"> },
>(mutationFn: (variables: TVariables) => Promise<MemoryFilesResponse>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (response) => {
      queryClient.setQueryData([...MEMORY_FILES_KEY, response.provider], response);
    },
    onSettled: (_data, _error, variables) => {
      void queryClient.invalidateQueries({
        queryKey: [...MEMORY_FILES_KEY, variables.provider],
      });
    },
  });
}

export function useUpdateMemoryFileVariant() {
  return useMemoryFilesMutation(
    ({
      provider,
      contentSha256,
      content,
    }: {
      provider: Exclude<MemoryProvider, "omniharness">;
      contentSha256: string;
      content: string;
    }) =>
      authenticatedFetch(
        `/v1/memory/files/${provider}/variants/${encodeURIComponent(contentSha256)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        },
      ).then(async (response) => {
        if (!response.ok) {
          throw new Error((await response.text()) || `${response.status} ${response.statusText}`);
        }
        return (await response.json()) as MemoryFilesResponse;
      }),
  );
}

export function useSyncMemoryFileVariant() {
  return useMemoryFilesMutation(
    ({
      provider,
      sourceSha256,
    }: {
      provider: Exclude<MemoryProvider, "omniharness">;
      sourceSha256: string;
    }) =>
      authenticatedFetch(`/v1/memory/files/${provider}/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_sha256: sourceSha256 }),
      }).then(async (response) => {
        if (!response.ok) {
          throw new Error((await response.text()) || `${response.status} ${response.statusText}`);
        }
        return (await response.json()) as MemoryFilesResponse;
      }),
  );
}
