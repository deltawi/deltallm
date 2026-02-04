import { useQuery } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAuth } from '@/contexts/AuthContext';

export function useModels(orgId?: string) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  return useQuery({
    queryKey: ['models', orgId],
    queryFn: () => api.getModels(orgId),
    enabled: isAuthenticated && !isAuthLoading,
    staleTime: 0,
    retry: 3,
  });
}

export function useModel(modelId: string) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  return useQuery({
    queryKey: ['model', modelId],
    queryFn: () => api.getModel(modelId),
    enabled: !!modelId && isAuthenticated && !isAuthLoading,
    staleTime: 0,
    retry: 3,
  });
}
