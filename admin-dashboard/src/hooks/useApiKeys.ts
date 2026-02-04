import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { CreateApiKeyRequest } from '@/types';

export function useApiKeys(orgId?: string, teamId?: string) {
  return useQuery({
    queryKey: ['api-keys', orgId, teamId],
    queryFn: () => api.getApiKeys({ org_id: orgId, team_id: teamId }),
  });
}

export function useApiKey(keyHash: string) {
  return useQuery({
    queryKey: ['api-key', keyHash],
    queryFn: () => api.getApiKey(keyHash),
    enabled: !!keyHash,
  });
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateApiKeyRequest) => api.createApiKey(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
    },
  });
}

export function useDeleteApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (keyHash: string) => api.deleteApiKey(keyHash),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
    },
  });
}

export function useKeySpendLogs(keyId?: string, days?: number) {
  return useQuery({
    queryKey: ['key-spend-logs', keyId, days],
    queryFn: () => api.getSpendLogs({ api_key_id: keyId, days, limit: 50 }),
    enabled: !!keyId,
  });
}
