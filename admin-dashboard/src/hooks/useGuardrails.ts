import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { ContentCheckRequest } from '@/types';

export function useGuardrailPolicies() {
  return useQuery({
    queryKey: ['guardrail-policies'],
    queryFn: () => api.getGuardrailPolicies(),
  });
}

export function useGuardrailPolicy(policyId: string) {
  return useQuery({
    queryKey: ['guardrail-policy', policyId],
    queryFn: () => api.getGuardrailPolicy(policyId),
    enabled: !!policyId,
  });
}

export function useGuardrailsStatus(orgId?: string) {
  return useQuery({
    queryKey: ['guardrails-status', orgId],
    queryFn: () => api.getGuardrailsStatus(orgId),
  });
}

export function useCheckContent() {
  return useMutation({
    mutationFn: (data: ContentCheckRequest) => api.checkContent(data),
  });
}

export function useSetOrgPolicy() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ orgId, policyId }: { orgId: string; policyId: string }) =>
      api.setOrgPolicy(orgId, policyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['guardrails-status'] });
    },
  });
}
