import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAuth } from '@/contexts/AuthContext';
import type { CreateDeploymentRequest, UpdateDeploymentRequest } from '@/types';

export function useDeployments(params?: {
  model_name?: string;
  provider_id?: string;
  org_id?: string;
  is_active?: boolean;
  page?: number;
  page_size?: number;
}) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  return useQuery({
    queryKey: ['deployments', params],
    queryFn: () => api.getDeployments(params),
    enabled: isAuthenticated && !isAuthLoading,
    staleTime: 0,
    retry: 3,
  });
}

export function useDeployment(id: string) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  return useQuery({
    queryKey: ['deployment', id],
    queryFn: () => api.getDeployment(id),
    enabled: !!id && isAuthenticated && !isAuthLoading,
    staleTime: 0,
    retry: 3,
  });
}

export function useCreateDeployment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateDeploymentRequest) => api.createDeployment(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['deployments'] });
    },
  });
}

export function useUpdateDeployment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateDeploymentRequest }) =>
      api.updateDeployment(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['deployments'] });
      queryClient.invalidateQueries({ queryKey: ['deployment', variables.id] });
    },
  });
}

export function useDeleteDeployment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteDeployment(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['deployments'] });
    },
  });
}

export function useEnableDeployment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.enableDeployment(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['deployments'] });
      queryClient.invalidateQueries({ queryKey: ['deployment', id] });
    },
  });
}

export function useDisableDeployment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.disableDeployment(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['deployments'] });
      queryClient.invalidateQueries({ queryKey: ['deployment', id] });
    },
  });
}

export function useDeploymentsForModel(modelName: string, onlyActive?: boolean) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  return useQuery({
    queryKey: ['deployments', 'model', modelName, onlyActive],
    queryFn: () => api.getDeploymentsForModel(modelName, onlyActive),
    enabled: !!modelName && isAuthenticated && !isAuthLoading,
    staleTime: 0,
    retry: 3,
  });
}
