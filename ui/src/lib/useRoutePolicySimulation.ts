import { useCallback, useEffect, useRef, useState } from 'react';
import {
  routeGroups,
  type RoutePolicySimulationRequest,
  type RoutePolicySimulationResponse,
} from './api';

interface SimulationIdentity {
  routeGroupId: string;
  fingerprint: string;
}

interface SimulationResult extends SimulationIdentity {
  data: RoutePolicySimulationResponse;
}

function sameIdentity(
  identity: SimulationIdentity | null,
  routeGroupId: string,
  fingerprint: string,
): boolean {
  return identity?.routeGroupId === routeGroupId && identity.fingerprint === fingerprint;
}

export function useRoutePolicySimulation(routeGroupId: string, fingerprint: string) {
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState<SimulationIdentity | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestIdRef.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, []);

  useEffect(() => () => {
    requestIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, [routeGroupId, fingerprint]);

  const run = useCallback(async (request: RoutePolicySimulationRequest) => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestId = ++requestIdRef.current;
    const identity = { routeGroupId, fingerprint };
    setPending(identity);
    setError(null);

    try {
      const data = await routeGroups.simulatePolicy(routeGroupId, request, controller.signal);
      if (
        requestId !== requestIdRef.current
        || !mountedRef.current
        || controller.signal.aborted
      ) return null;
      setResult({ ...identity, data });
      return data;
    } catch (caught: unknown) {
      if (
        requestId === requestIdRef.current
        && mountedRef.current
        && !controller.signal.aborted
      ) setError(caught);
      return null;
    } finally {
      if (requestId === requestIdRef.current && mountedRef.current) setPending(null);
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, [fingerprint, routeGroupId]);

  const reset = useCallback(() => {
    requestIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setPending(null);
    setResult(null);
    setError(null);
  }, []);

  return {
    data: result?.data ?? null,
    error,
    loading: sameIdentity(pending, routeGroupId, fingerprint),
    stale: result !== null && !sameIdentity(result, routeGroupId, fingerprint),
    run,
    reset,
  };
}
