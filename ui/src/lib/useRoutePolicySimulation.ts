import { useCallback } from 'react';
import { routeGroups, type RoutePolicySimulationRequest } from './api';
import { useExplicitReport } from './useExplicitReport';

export function useRoutePolicySimulation(routeGroupId: string, fingerprint: string) {
  const load = useCallback((input: RoutePolicySimulationRequest, signal: AbortSignal) =>
    routeGroups.simulatePolicy(routeGroupId, input, signal), [routeGroupId]);
  return useExplicitReport(routeGroupId, fingerprint, load);
}
