import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { PricingCreateRequest } from '@/types';

export function useDeploymentPricing(deploymentId: string | undefined) {
  return useQuery({
    queryKey: ['deployment-pricing', deploymentId],
    queryFn: () => api.getDeploymentPricing(deploymentId!),
    enabled: !!deploymentId,
    retry: false,
  });
}

export function useSetDeploymentPricing() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      deploymentId,
      data,
    }: {
      deploymentId: string;
      data: PricingCreateRequest;
    }) => api.setDeploymentPricing(deploymentId, data),
    onSuccess: (_, variables) => {
      // Invalidate specific deployment pricing query
      queryClient.invalidateQueries({ queryKey: ['deployment-pricing', variables.deploymentId] });
    },
  });
}

export function useDeleteDeploymentPricing() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ deploymentId }: { deploymentId: string }) =>
      api.deleteDeploymentPricing(deploymentId),
    onSuccess: (_, variables) => {
      // Invalidate specific deployment pricing query
      queryClient.invalidateQueries({ queryKey: ['deployment-pricing', variables.deploymentId] });
    },
  });
}
