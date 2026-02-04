import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAuth } from '@/contexts/AuthContext';
import type { CreateOrganizationRequest, UpdateOrganizationRequest, AddMemberRequest } from '@/types';

export function useOrganizations() {
  const { isAuthenticated, isLoading: isAuthLoading, user } = useAuth();

  console.log('[useOrganizations] Auth state:', { isAuthenticated, isAuthLoading, userId: user?.id });

  const query = useQuery({
    queryKey: ['organizations'],
    queryFn: async () => {
      console.log('[useOrganizations] Fetching organizations...');
      try {
        const result = await api.getOrganizations();
        console.log('[useOrganizations] Fetched successfully:', result);
        return result;
      } catch (err) {
        console.error('[useOrganizations] Fetch error:', err);
        throw err;
      }
    },
    enabled: isAuthenticated && !isAuthLoading,
    staleTime: 0,
    retry: 3,
    refetchOnWindowFocus: false,
  });

  console.log('[useOrganizations] Query state:', { 
    isLoading: query.isLoading, 
    isFetching: query.isFetching,
    isPending: query.isPending,
    isError: query.isError,
    error: query.error,
    data: query.data,
    enabled: isAuthenticated && !isAuthLoading
  });

  return query;
}

export function useOrganization(id: string) {
  return useQuery({
    queryKey: ['organization', id],
    queryFn: () => api.getOrganization(id),
    enabled: !!id,
  });
}

export function useCreateOrganization() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: CreateOrganizationRequest) => api.createOrganization(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['organizations'] });
    },
  });
}

export function useUpdateOrganization(id: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: UpdateOrganizationRequest) => api.updateOrganization(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['organization', id] });
      queryClient.invalidateQueries({ queryKey: ['organizations'] });
    },
  });
}

export function useDeleteOrganization() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (id: string) => api.deleteOrganization(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['organizations'] });
    },
  });
}

export function useOrgMembers(orgId: string) {
  return useQuery({
    queryKey: ['org-members', orgId],
    queryFn: () => api.getOrgMembers(orgId),
    enabled: !!orgId,
  });
}

export function useAddOrgMember(orgId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: AddMemberRequest) => api.addOrgMember(orgId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-members', orgId] });
    },
  });
}

export function useUpdateOrgMemberRole(orgId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) => 
      api.updateOrgMemberRole(orgId, userId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-members', orgId] });
    },
  });
}

export function useRemoveOrgMember(orgId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (userId: string) => api.removeOrgMember(orgId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-members', orgId] });
    },
  });
}
