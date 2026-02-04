import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/services/api';

export function useOrgBudget(orgId: string) {
  return useQuery({
    queryKey: ['org-budget', orgId],
    queryFn: () => api.getOrgBudget(orgId),
    enabled: !!orgId,
  });
}

export function useOrgBudgetFull(orgId: string) {
  return useQuery({
    queryKey: ['org-budget-full', orgId],
    queryFn: () => api.getOrgBudgetFull(orgId),
    enabled: !!orgId,
  });
}

export function useSetOrgBudget(orgId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (maxBudget: number) => api.setOrgBudget(orgId, maxBudget),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-budget', orgId] });
      queryClient.invalidateQueries({ queryKey: ['org-budget-full', orgId] });
    },
  });
}

export function useTeamBudget(teamId: string) {
  return useQuery({
    queryKey: ['team-budget', teamId],
    queryFn: () => api.getTeamBudget(teamId),
    enabled: !!teamId,
  });
}

export function useSetTeamBudget(teamId: string) {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (maxBudget: number) => api.setTeamBudget(teamId, maxBudget),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-budget', teamId] });
    },
  });
}

export function useSpendLogs(params?: {
  org_id?: string;
  team_id?: string;
  days?: number;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: ['spend-logs', params],
    queryFn: () => api.getSpendLogs(params),
  });
}

export function useSpendSummary(params?: {
  org_id?: string;
  team_id?: string;
  days?: number;
}) {
  return useQuery({
    queryKey: ['spend-summary', params],
    queryFn: () => api.getSpendSummary(params),
  });
}
