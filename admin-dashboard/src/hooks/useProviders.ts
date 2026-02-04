import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import type { CreateProviderRequest, UpdateProviderRequest } from '@/types';

export function useProviders(params?: {
  org_id?: string;
  provider_type?: string;
  is_active?: boolean;
}) {
  return useQuery({
    queryKey: ['providers', params],
    queryFn: () => api.getProviders(params),
  });
}

export function useProvider(id: string) {
  return useQuery({
    queryKey: ['provider', id],
    queryFn: () => api.getProvider(id),
    enabled: !!id,
  });
}

export function useCreateProvider() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateProviderRequest) => api.createProvider(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });
}

export function useUpdateProvider(id: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: UpdateProviderRequest) => api.updateProvider(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['provider', id] });
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });
}

export function useDeleteProvider() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      api.deleteProvider(id, force),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] });
    },
  });
}

export function useTestProviderConnectivity() {
  return useMutation({
    mutationFn: (id: string) => api.testProviderConnectivity(id),
  });
}

export function useProviderHealth(id: string) {
  return useQuery({
    queryKey: ['provider-health', id],
    queryFn: () => api.getProviderHealth(id),
    enabled: !!id,
    refetchInterval: 30000, // Refresh every 30 seconds
  });
}
