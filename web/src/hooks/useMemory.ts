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
  usage_percent: number;
  over_limit: boolean;
}

const MEMORY_KEY = ["memory"] as const;

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
  return useMemoryMutation((maxTokens: number) =>
    memoryRequest("/v1/memory/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_tokens: maxTokens }),
    }),
  );
}
