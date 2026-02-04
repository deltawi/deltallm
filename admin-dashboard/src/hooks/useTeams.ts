import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';
import { useAuth } from '@/contexts/AuthContext';
import type { CreateTeamRequest, UpdateTeamRequest, AddMemberRequest } from '@/types';

export function useTeams(orgId?: string) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();

  return useQuery({
    queryKey: ['teams', orgId],
    queryFn: () => api.getTeams(orgId),
    enabled: isAuthenticated && !isAuthLoading,
    staleTime: 0,
    retry: 3,
    refetchOnWindowFocus: false,
  });
}

export function useTeam(id: string) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  const enabled = !!id && isAuthenticated && !isAuthLoading;
  
  console.log('[useTeam] Hook called - id:', id, 'enabled:', enabled, 'isAuthenticated:', isAuthenticated, 'isAuthLoading:', isAuthLoading);

  const query = useQuery({
    queryKey: ['team', id],
    queryFn: async () => {
      console.log('[useTeam] Fetching team:', id);
      const result = await api.getTeam(id);
      console.log('[useTeam] Team data received:', result);
      return result;
    },
    enabled: enabled,
    staleTime: 0,
    retry: 3,
  });
  
  console.log('[useTeam] Query state - isLoading:', query.isLoading, 'isSuccess:', query.isSuccess, 'data:', query.data);
  
  return query;
}

export function useCreateTeam() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: CreateTeamRequest) => api.createTeam(data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['teams', variables.org_id] });
    },
  });
}

export function useUpdateTeam(id: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: UpdateTeamRequest) => api.updateTeam(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', id] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });
    },
  });
}

export function useDeleteTeam() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (id: string) => api.deleteTeam(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
    },
  });
}

export function useTeamMembers(teamId: string) {
  return useQuery({
    queryKey: ['team-members', teamId],
    queryFn: () => api.getTeamMembers(teamId),
    enabled: !!teamId,
  });
}

export function useAddTeamMember(teamId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (data: AddMemberRequest) => api.addTeamMember(teamId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-members', teamId] });
    },
  });
}

export function useUpdateTeamMemberRole(teamId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) => 
      api.updateTeamMemberRole(teamId, userId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-members', teamId] });
    },
  });
}

export function useRemoveTeamMember(teamId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (userId: string) => api.removeTeamMember(teamId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-members', teamId] });
    },
  });
}
