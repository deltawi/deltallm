import { useCallback, useEffect, useRef, useState } from 'react';

import {
  normalizeOrganizationCapabilities,
  organizationRecordsApi,
  type OrganizationRecord,
} from './api/organizations';
import type { OrganizationLifecycleTransition } from './organizationLifecycle';

export type { OrganizationLifecycleTransition } from './organizationLifecycle';

export function reconcileOrganizationLifecycleTransition(
  organization: OrganizationRecord,
  transition: OrganizationLifecycleTransition,
): OrganizationRecord {
  return {
    ...organization,
    lifecycle_state: transition.lifecycleState,
    deletion_requested_at: transition.lifecycleState === 'active'
      ? null
      : organization.deletion_requested_at,
    deletion_not_before_at: transition.lifecycleState === 'active'
      ? null
      : transition.deletionNotBeforeAt ?? organization.deletion_not_before_at,
    capabilities: normalizeOrganizationCapabilities(
      transition.lifecycleState === 'active' ? null : organization.capabilities,
      transition.lifecycleState,
    ),
  };
}

export function useOrganizationResource(organizationId: string | undefined) {
  const [data, setData] = useState<OrganizationRecord | null>(null);
  const [initialError, setInitialError] = useState<unknown>(null);
  const [refreshError, setRefreshError] = useState<unknown>(null);
  const [initialLoading, setInitialLoading] = useState(Boolean(organizationId));
  const [refreshing, setRefreshing] = useState(false);
  const dataRef = useRef<OrganizationRecord | null>(null);
  const requestIdRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, []);

  const load = useCallback(async (): Promise<OrganizationRecord> => {
    if (!organizationId) throw new Error('Organization id is required');
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestId = ++requestIdRef.current;
    const isInitial = dataRef.current === null;
    if (isInitial) {
      setInitialLoading(true);
      setInitialError(null);
    } else {
      setRefreshing(true);
      setRefreshError(null);
    }

    try {
      const next = await organizationRecordsApi.get(organizationId, controller.signal);
      if (requestId !== requestIdRef.current || !mountedRef.current) return next;
      dataRef.current = next;
      setData(next);
      setInitialError(null);
      setRefreshError(null);
      return next;
    } catch (error: unknown) {
      if (controller.signal.aborted || requestId !== requestIdRef.current) throw error;
      if (mountedRef.current) {
        if (dataRef.current === null) setInitialError(error);
        else setRefreshError(error);
      }
      throw error;
    } finally {
      if (requestId === requestIdRef.current && mountedRef.current) {
        setInitialLoading(false);
        setRefreshing(false);
      }
    }
  }, [organizationId]);

  useEffect(() => {
    dataRef.current = null;
    setData(null);
    setRefreshError(null);
    if (!organizationId) {
      setInitialLoading(false);
      setInitialError(new Error('Organization id is required'));
      return undefined;
    }
    void load().catch(() => undefined);
    return () => controllerRef.current?.abort();
  }, [load, organizationId]);

  const applyLifecycleTransition = useCallback((transition: OrganizationLifecycleTransition) => {
    setData((current) => {
      if (current === null) return current;
      const next = reconcileOrganizationLifecycleTransition(current, transition);
      dataRef.current = next;
      return next;
    });
  }, []);

  return {
    data,
    initialError,
    initialLoading,
    refreshing,
    refreshError,
    refresh: load,
    applyLifecycleTransition,
  };
}
