import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface PromptProfile {
  id: string;
  name: string;
  description: string | null;
  instructions: string;
  enabled: boolean;
  archived: boolean;
  created_at: number;
  updated_at: number | null;
}

interface PromptProfilesListWire {
  data: PromptProfile[];
}

export interface CreatePromptProfileInput {
  name: string;
  description?: string | null;
  instructions: string;
  enabled?: boolean;
}

async function profileApiError(response: Response, fallback: string): Promise<Error> {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
    error?: { message?: string };
  } | null;
  return new Error(body?.error?.message ?? body?.detail ?? `${fallback} (${response.status})`);
}

async function fetchPromptProfiles(enabledOnly: boolean): Promise<PromptProfile[]> {
  const url = enabledOnly ? "/v1/prompt-profiles?enabled_only=true" : "/v1/prompt-profiles";
  const response = await authenticatedFetch(url);
  if (!response.ok) throw await profileApiError(response, "Couldn't load profiles");
  const body = (await response.json()) as PromptProfilesListWire | PromptProfile[];
  return Array.isArray(body) ? body : body.data;
}

async function createPromptProfile(input: CreatePromptProfileInput): Promise<PromptProfile> {
  const response = await authenticatedFetch("/v1/prompt-profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw await profileApiError(response, "Couldn't create profile");
  return (await response.json()) as PromptProfile;
}

async function updatePromptProfile(
  input: { id: string } & Partial<CreatePromptProfileInput>,
): Promise<PromptProfile> {
  const { id, ...updates } = input;
  const response = await authenticatedFetch(`/v1/prompt-profiles/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) throw await profileApiError(response, "Couldn't update profile");
  return (await response.json()) as PromptProfile;
}

async function archivePromptProfile(id: string): Promise<void> {
  const response = await authenticatedFetch(`/v1/prompt-profiles/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await profileApiError(response, "Couldn't delete profile");
}

export function usePromptProfiles(options: { enabled?: boolean; enabledOnly?: boolean } = {}) {
  const enabledOnly = options.enabledOnly ?? false;
  return useQuery({
    queryKey: ["prompt-profiles", enabledOnly ? "enabled" : "active"],
    queryFn: () => fetchPromptProfiles(enabledOnly),
    staleTime: 30_000,
    enabled: options.enabled ?? true,
  });
}

function useInvalidatePromptProfiles() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["prompt-profiles"] });
}

export function useCreatePromptProfile() {
  const invalidate = useInvalidatePromptProfiles();
  return useMutation({
    mutationFn: createPromptProfile,
    onSuccess: invalidate,
  });
}

export function useUpdatePromptProfile() {
  const invalidate = useInvalidatePromptProfiles();
  return useMutation({
    mutationFn: updatePromptProfile,
    onSuccess: invalidate,
  });
}

export function useArchivePromptProfile() {
  const invalidate = useInvalidatePromptProfiles();
  return useMutation({
    mutationFn: archivePromptProfile,
    onSuccess: invalidate,
  });
}
