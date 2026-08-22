import { authenticatedFetch } from "@/lib/identity";

export type WorkerProviderKind = "internal" | "external";

export interface InternalWorkerProviderConfiguration {
  agent_id: string | null;
  host_id: string | null;
  workspace: string | null;
  harness: string | null;
  model: string | null;
}

export interface WorkerProvider {
  id: string;
  name: string;
  description: string | null;
  kind: WorkerProviderKind;
  configuration: InternalWorkerProviderConfiguration | Record<string, unknown>;
  built_in: boolean;
  available: boolean;
  unavailable_reason: string | null;
  capabilities: string[];
  created_at: number;
  updated_at: number | null;
}

export interface CreateWorkerProviderRequest {
  name: string;
  description?: string | null;
  kind?: WorkerProviderKind;
  configuration: InternalWorkerProviderConfiguration | Record<string, unknown>;
}

export interface UpdateWorkerProviderRequest {
  name?: string;
  description?: string | null;
  configuration?: InternalWorkerProviderConfiguration | Record<string, unknown>;
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      // Keep the HTTP status when the response has no JSON error body.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function fetchWorkerProviders(): Promise<WorkerProvider[]> {
  const response = await authenticatedFetch("/v1/worker-providers");
  const body = await readJson<{ data: WorkerProvider[] }>(response);
  return body.data;
}

export async function createWorkerProvider(
  body: CreateWorkerProviderRequest,
): Promise<WorkerProvider> {
  const response = await authenticatedFetch("/v1/worker-providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return readJson<WorkerProvider>(response);
}

export async function updateWorkerProvider(
  providerId: string,
  body: UpdateWorkerProviderRequest,
): Promise<WorkerProvider> {
  const response = await authenticatedFetch(
    `/v1/worker-providers/${encodeURIComponent(providerId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return readJson<WorkerProvider>(response);
}

export async function deleteWorkerProvider(providerId: string): Promise<void> {
  const response = await authenticatedFetch(
    `/v1/worker-providers/${encodeURIComponent(providerId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) await readJson<never>(response);
}
