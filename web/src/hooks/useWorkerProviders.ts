import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createWorkerProvider,
  deleteWorkerProvider,
  fetchWorkerProviders,
  updateWorkerProvider,
  type CreateWorkerProviderRequest,
  type UpdateWorkerProviderRequest,
} from "@/lib/workerProvidersApi";

export const workerProvidersQueryKey = ["worker-providers"] as const;

export function useWorkerProviders() {
  return useQuery({ queryKey: workerProvidersQueryKey, queryFn: fetchWorkerProviders });
}

export function useCreateWorkerProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateWorkerProviderRequest) => createWorkerProvider(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workerProvidersQueryKey }),
  });
}

export function useUpdateWorkerProvider(providerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateWorkerProviderRequest) => updateWorkerProvider(providerId, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workerProvidersQueryKey }),
  });
}

export function useDeleteWorkerProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteWorkerProvider,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: workerProvidersQueryKey }),
  });
}
