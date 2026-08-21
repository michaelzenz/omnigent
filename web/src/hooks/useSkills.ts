import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface SkillOccurrence {
  name: string;
  description: string;
  hostId: string;
  hostName: string;
  harness: string;
  relHomePath: string;
  contentSha256: string;
  online: boolean;
}

export interface SkillVariant {
  contentSha256: string;
  activeCount: number;
  occurrences: SkillOccurrence[];
}

export interface SkillTreeFile {
  path: string;
  content: string;
  binary: boolean;
}

export interface SkillHost {
  hostId: string;
  hostName: string;
  online: boolean;
  reported: boolean;
  harnesses: SkillHarnessState[];
}

export interface SkillHarnessState {
  harness: string;
  installed: boolean;
  enabled: boolean | null;
  state:
    | "present"
    | "missing"
    | "unavailable"
    | "offline"
    | "not_reported"
    | "ignored"
    | "ignored_variant";
  occurrence: SkillOccurrence | null;
}

export interface AggregatedSkill {
  name: string;
  description: string;
  synced: boolean;
  syncStatus: "synced" | "partial" | "not_synced";
  variants: SkillVariant[];
  hosts: SkillHost[];
}

export interface HostSkillRoots {
  hostId: string;
  hostName: string;
  online: boolean;
  roots: { harness: string; relHomePath: string }[];
  syncHarnesses: Record<string, boolean> | null;
  installedHarnesses: Record<string, boolean>;
  error: string | null;
}

interface WireOccurrence {
  name: string;
  description: string;
  host_id: string;
  host_name: string;
  harness: string;
  rel_home_path: string;
  content_sha256: string;
  online: boolean;
}

const SKILLS_KEY = ["skills"];

function mapOccurrence(raw: WireOccurrence): SkillOccurrence {
  return {
    name: raw.name,
    description: raw.description,
    hostId: raw.host_id,
    hostName: raw.host_name,
    harness: raw.harness,
    relHomePath: raw.rel_home_path,
    contentSha256: raw.content_sha256,
    online: raw.online,
  };
}

async function fetchSkills(): Promise<AggregatedSkill[]> {
  const response = await authenticatedFetch("/v1/skills");
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const body = (await response.json()) as {
    data: {
      name: string;
      description: string;
      synced: boolean;
      sync_status: "synced" | "partial" | "not_synced";
      variants: {
        content_sha256: string;
        active_count: number;
        occurrences: WireOccurrence[];
      }[];
      hosts: {
        host_id: string;
        host_name: string;
        online: boolean;
        reported: boolean;
        harnesses: {
          harness: string;
          installed: boolean;
          enabled: boolean | null;
          state:
            | "present"
            | "missing"
            | "unavailable"
            | "offline"
            | "not_reported"
            | "ignored"
            | "ignored_variant";
          occurrence: WireOccurrence | null;
        }[];
      }[];
    }[];
  };
  return body.data.map((skill) => ({
    name: skill.name,
    description: skill.description,
    synced: skill.synced,
    syncStatus: skill.sync_status,
    variants: skill.variants.map((variant) => ({
      contentSha256: variant.content_sha256,
      activeCount: variant.active_count,
      occurrences: variant.occurrences.map(mapOccurrence),
    })),
    hosts: skill.hosts.map((host) => ({
      hostId: host.host_id,
      hostName: host.host_name,
      online: host.online,
      reported: host.reported,
      harnesses: host.harnesses.map((harness) => ({
        harness: harness.harness,
        installed: harness.installed,
        enabled: harness.enabled,
        state: harness.state,
        occurrence: harness.occurrence ? mapOccurrence(harness.occurrence) : null,
      })),
    })),
  }));
}

async function fetchSkillRoots(): Promise<HostSkillRoots[]> {
  const response = await authenticatedFetch("/v1/skills/roots");
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const body = (await response.json()) as {
    data: {
      host_id: string;
      host_name: string;
      online: boolean;
      roots: { harness: string; rel_home_path: string }[];
      sync_harnesses: Record<string, boolean> | null;
      installed_harnesses: Record<string, boolean>;
      error: string | null;
    }[];
  };
  return body.data.map((host) => ({
    hostId: host.host_id,
    hostName: host.host_name,
    online: host.online,
    roots: host.roots.map((root) => ({
      harness: root.harness,
      relHomePath: root.rel_home_path,
    })),
    syncHarnesses: host.sync_harnesses,
    installedHarnesses: host.installed_harnesses,
    error: host.error,
  }));
}

async function refreshSkillInventories(): Promise<void> {
  const response = await authenticatedFetch("/v1/skills/refresh", { method: "POST" });
  if (!response.ok) throw new Error((await response.text()) || `${response.status}`);
  const body = (await response.json()) as { failed?: number };
  if ((body.failed ?? 0) > 0) {
    throw new Error(
      `${body.failed} connected host${body.failed === 1 ? "" : "s"} failed to refresh skills.`,
    );
  }
}

async function fetchSkillContent(name: string, hostId: string, harness: string): Promise<string> {
  const params = new URLSearchParams({ host_id: hostId, harness });
  const response = await authenticatedFetch(
    `/v1/skills/${encodeURIComponent(name)}/content?${params}`,
  );
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return ((await response.json()) as { content: string }).content;
}

async function fetchSkillTree(
  name: string,
  hostId: string,
  harness: string,
): Promise<SkillTreeFile[]> {
  const params = new URLSearchParams({ host_id: hostId, harness });
  const response = await authenticatedFetch(
    `/v1/skills/${encodeURIComponent(name)}/tree?${params}`,
  );
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return ((await response.json()) as { files: SkillTreeFile[] }).files;
}

async function saveSkillVariantFiles(
  name: string,
  contentSha256: string,
  files: Record<string, string>,
): Promise<{ contentSha256: string | null }> {
  const response = await authenticatedFetch(
    `/v1/skills/${encodeURIComponent(name)}/variants/${encodeURIComponent(contentSha256)}/files`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files }),
    },
  );
  if (!response.ok) throw new Error((await response.text()) || `${response.status}`);
  const body = (await response.json()) as {
    content_sha256: string | null;
    results: { status: string; error?: string }[];
  };
  const failed = body.results.filter((result) => result.status === "failed");
  if (failed.length > 0) {
    throw new Error(failed[0]?.error || `${failed.length} skill occurrence(s) failed to save.`);
  }
  return { contentSha256: body.content_sha256 };
}

async function syncSkill(name: string, sourceHostId: string, sourceHarness: string): Promise<void> {
  const response = await authenticatedFetch(`/v1/skills/${encodeURIComponent(name)}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_host_id: sourceHostId, source_harness: sourceHarness }),
  });
  if (!response.ok) throw new Error((await response.text()) || `${response.status}`);
}

async function createSkill(
  name: string,
  files: Record<string, string>,
): Promise<void> {
  const response = await authenticatedFetch(`/v1/skills/${encodeURIComponent(name)}/files`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files }),
  });
  if (!response.ok) throw new Error((await response.text()) || `${response.status}`);
  const body = (await response.json()) as {
    results: { status: string; error?: string }[];
  };
  const failed = body.results.filter((result) => result.status === "failed");
  if (failed.length > 0) {
    throw new Error(failed[0]?.error || `${failed.length} skill location(s) failed to create.`);
  }
}

async function deleteSkillEverywhere(name: string): Promise<void> {
  const response = await authenticatedFetch(`/v1/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error((await response.text()) || `${response.status}`);
}

export function useSyncedSkills() {
  return useQuery({
    queryKey: SKILLS_KEY,
    queryFn: fetchSkills,
    staleTime: 30_000,
  });
}

export function useRefreshSkills() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: refreshSkillInventories,
    onSettled: () => queryClient.invalidateQueries({ queryKey: SKILLS_KEY }),
  });
}

export function useSkillRoots(enabled: boolean) {
  return useQuery({
    queryKey: [...SKILLS_KEY, "roots"],
    queryFn: fetchSkillRoots,
    enabled,
  });
}

export function useUpdateSkillHarnessSetting() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ harness, enabled }: { harness: string; enabled: boolean }) => {
      const response = await authenticatedFetch(`/v1/skills/roots/${encodeURIComponent(harness)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!response.ok) throw new Error((await response.text()) || `${response.status}`);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}

export function useSkillContent(
  name: string | null,
  hostId: string | null,
  harness: string | null,
) {
  return useQuery({
    queryKey: [...SKILLS_KEY, "content", name, hostId, harness],
    queryFn: () => fetchSkillContent(name as string, hostId as string, harness as string),
    enabled: Boolean(name && hostId && harness),
  });
}

export function useSkillTree(name: string | null, hostId: string | null, harness: string | null) {
  return useQuery({
    queryKey: [...SKILLS_KEY, "tree", name, hostId, harness],
    queryFn: () => fetchSkillTree(name as string, hostId as string, harness as string),
    enabled: Boolean(name && hostId && harness),
  });
}

export function useSyncSkills() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      sourceHostId,
      sourceHarness,
    }: {
      name: string;
      sourceHostId: string;
      sourceHarness: string;
    }) => syncSkill(name, sourceHostId, sourceHarness),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}

export function useSaveSkillVariantFiles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      contentSha256,
      files,
    }: {
      name: string;
      contentSha256: string;
      files: Record<string, string>;
    }) => saveSkillVariantFiles(name, contentSha256, files),
    onSuccess: (result, variables) => {
      const nextHash = result.contentSha256;
      if (nextHash) {
        queryClient.setQueryData<AggregatedSkill[]>(SKILLS_KEY, (current) =>
          current?.map((skill) => {
            if (skill.name !== variables.name) return skill;
            const variants = skill.variants.map((variant) =>
              variant.contentSha256 === variables.contentSha256
                ? {
                    ...variant,
                    contentSha256: nextHash,
                    occurrences: variant.occurrences.map((occurrence) => ({
                      ...occurrence,
                      contentSha256: nextHash,
                    })),
                  }
                : variant,
            );
            return { ...skill, variants };
          }),
        );
      }
      queryClient.setQueriesData<SkillTreeFile[]>(
        { queryKey: [...SKILLS_KEY, "tree", variables.name] },
        (current) =>
          current?.map((file) =>
            Object.hasOwn(variables.files, file.path)
              ? { ...file, content: variables.files[file.path] as string }
              : file,
          ),
      );
    },
  });
}

export function useDeleteSkillEverywhere() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteSkillEverywhere,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}

export function useCreateSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, files }: { name: string; files: Record<string, string> }) =>
      createSkill(name, files),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SKILLS_KEY });
    },
  });
}
