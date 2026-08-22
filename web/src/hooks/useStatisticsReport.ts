import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearModelPricing,
  fetchStatisticsReport,
  updateModelPricing,
  type ModelPricingInput,
  type StatisticsReport,
} from "@/lib/statisticsApi";

export function useStatisticsReport(month: string) {
  return useQuery<StatisticsReport>({
    queryKey: ["statistics", month],
    queryFn: () => fetchStatisticsReport(month),
    staleTime: 60_000,
  });
}

export function useUpdateModelPricing(month: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ model, pricing }: { model: string; pricing: ModelPricingInput }) =>
      updateModelPricing(model, pricing),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["statistics", month] }),
  });
}

export function useClearModelPricing(month: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: clearModelPricing,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["statistics", month] }),
  });
}
