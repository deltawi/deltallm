import {
  ArrowLeft,
  BadgeDollarSign,
  Edit3,
  FilePlus2,
  Layers,
  RotateCcw,
  Send,
  Shield,
  Trash2,
} from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import StatusBadge from '../components/StatusBadge';
import { DetailMetricCard, EntityDetailShell, TextTabs } from '../components/admin/shells';
import TierActivationDialog from '../components/tiers/TierActivationDialog';
import TierCapacityPoolEditor from '../components/tiers/TierCapacityPoolEditor';
import TierFormDrawer from '../components/tiers/TierFormDrawer';
import TierModelPolicyGrid from '../components/tiers/TierModelPolicyGrid';
import TierVersionBadge from '../components/tiers/TierVersionBadge';
import TierVersionRail from '../components/tiers/TierVersionRail';
import { useToast } from '../components/ToastProvider';
import {
  callableTargets,
  structuredApiErrorDetail,
  tiers,
  type Pagination,
  type TierActivationPreview,
  type TierCapacityPool,
  type TierCapacityPoolPayload,
  type TierConfigurationPage,
  type TierModelPolicy,
  type TierModelPolicyPayload,
  type TierVersion,
} from '../lib/api';
import { useApi } from '../lib/hooks';
import { clampTierPaginationOffset } from '../lib/tierPagination';
import {
  errorMessage,
  formatDateTime,
  poolOptionsForCallable,
  tierToForm,
  versionLabel,
} from '../lib/tiers';

type WorkspaceTab = 'models' | 'pricing' | 'pools';
type EnabledFilter = 'all' | 'enabled' | 'disabled';
type AccessFilter = 'all' | 'allow' | 'deny';
type StrategyFilter = 'all' | 'hard_cap' | 'weighted_fair' | 'reserved_burst';
type TierRouteRequest = { tierId: string; routeEpoch: number; requestId: number };

const EMPTY_PAGINATION: Pagination = { total: 0, limit: 10, offset: 0, has_more: false };

export default function TierDetail() {
  const { tierId } = useParams<{ tierId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { pushToast } = useToast();
  const tab = routeTab(searchParams.get('tab'));
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [optimisticVersion, setOptimisticVersion] = useState<TierVersion | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [form, setForm] = useState(tierToForm());
  const [savingTier, setSavingTier] = useState(false);
  const [tierError, setTierError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [workspaceRevision, setWorkspaceRevision] = useState<number | null>(null);
  const [configurationLocked, setConfigurationLocked] = useState(false);

  const [policySearchInput, setPolicySearchInput] = useState('');
  const [policySearch, setPolicySearch] = useState('');
  const [policyEnabled, setPolicyEnabled] = useState<EnabledFilter>('all');
  const [policyAccess, setPolicyAccess] = useState<AccessFilter>('all');
  const [policyOffset, setPolicyOffset] = useState(0);
  const [policyPageSize, setPolicyPageSize] = useState(10);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [policyConflict, setPolicyConflict] = useState<string | null>(null);

  const [poolSearchInput, setPoolSearchInput] = useState('');
  const [poolSearch, setPoolSearch] = useState('');
  const [poolStrategy, setPoolStrategy] = useState<StrategyFilter>('all');
  const [poolOffset, setPoolOffset] = useState(0);
  const [poolPageSize, setPoolPageSize] = useState(10);
  const [poolError, setPoolError] = useState<string | null>(null);
  const [poolConflict, setPoolConflict] = useState<string | null>(null);

  const [archiveOffset, setArchiveOffset] = useState(0);
  const [archivedVersions, setArchivedVersions] = useState<TierVersion[]>([]);
  const [archivedPagination, setArchivedPagination] = useState<Pagination | null>(null);

  const [activationOpen, setActivationOpen] = useState(false);
  const [activationPreview, setActivationPreview] = useState<TierActivationPreview | null>(null);
  const [activationLoading, setActivationLoading] = useState(false);
  const [activationError, setActivationError] = useState<string | null>(null);
  const [activating, setActivating] = useState(false);
  const activationRequestRef = useRef(0);

  const tierIdRef = useRef(tierId);
  const routeEpochRef = useRef(0);
  const mutationRequestRef = useRef(0);

  const {
    data: detail,
    loading: detailLoading,
    error: detailError,
    refetch: refetchDetail,
  } = useApi(
    () => tiers.get(tierId!, { include_versions: false }),
    [tierId],
  );
  const currentDetail = detail?.tier?.tier_id === tierId ? detail : null;
  const routeDetailPending = Boolean(tierId && detail && detail.tier?.tier_id !== tierId);

  const {
    data: currentVersionPage,
    loading: versionsLoading,
    error: versionsError,
    refetch: refetchCurrentVersions,
  } = useApi(
    () => tiers.listVersions(tierId!, { status: ['active', 'draft'], limit: 100, offset: 0 }),
    [tierId],
  );
  const {
    data: archivedPage,
    loading: archivesLoading,
    error: archivesError,
    refetch: refetchArchivedPage,
  } = useApi(
    () => tiers.listVersions(tierId!, { status: 'archived', limit: 10, offset: archiveOffset }),
    [tierId, archiveOffset],
  );

  const currentVersions = useMemo(
    () => dedupeVersions([
      ...(optimisticVersion ? [optimisticVersion] : []),
      ...(currentVersionPage?.data || []),
    ]),
    [currentVersionPage, optimisticVersion],
  );
  const allVisibleVersions = useMemo(
    () => [...currentVersions, ...archivedVersions],
    [currentVersions, archivedVersions],
  );
  const selectedVersion = useMemo(
    () => allVisibleVersions.find((version) => version.tier_version_id === selectedVersionId) || null,
    [allVisibleVersions, selectedVersionId],
  );
  const activeVersion = useMemo(
    () => currentVersions.find((version) => version.status === 'active') || null,
    [currentVersions],
  );
  const draftVersions = useMemo(
    () => currentVersions.filter((version) => version.status === 'draft'),
    [currentVersions],
  );
  const canEditVersion = selectedVersion?.status === 'draft' && !configurationLocked;

  useLayoutEffect(() => {
    if (tierIdRef.current !== tierId) {
      tierIdRef.current = tierId;
      routeEpochRef.current += 1;
      mutationRequestRef.current += 1;
    }
    activationRequestRef.current += 1;
    setSelectedVersionId(null);
    setOptimisticVersion(null);
    setDrawerOpen(false);
    setForm(tierToForm());
    setSavingTier(false);
    setTierError(null);
    setBusyAction(null);
    setWorkspaceRevision(null);
    setConfigurationLocked(false);
    setPolicySearchInput('');
    setPolicySearch('');
    setPolicyEnabled('all');
    setPolicyAccess('all');
    setPolicyOffset(0);
    setPolicyError(null);
    setPolicyConflict(null);
    setPoolSearchInput('');
    setPoolSearch('');
    setPoolStrategy('all');
    setPoolOffset(0);
    setPoolError(null);
    setPoolConflict(null);
    setArchiveOffset(0);
    setArchivedVersions([]);
    setArchivedPagination(null);
    setActivationOpen(false);
    setActivationPreview(null);
    setActivationError(null);
    setActivating(false);
  }, [tierId]);

  useEffect(() => {
    if (!optimisticVersion || !currentVersionPage?.data.some(
      (version) => version.tier_version_id === optimisticVersion.tier_version_id,
    )) return;
    const timer = window.setTimeout(() => setOptimisticVersion(null), 0);
    return () => window.clearTimeout(timer);
  }, [currentVersionPage, optimisticVersion]);

  useEffect(() => {
    if (!archivedPage) return;
    setArchivedVersions((current) => {
      const combined = archiveOffset === 0 ? archivedPage.data : [...current, ...archivedPage.data];
      return dedupeVersions(combined);
    });
    setArchivedPagination(archivedPage.pagination);
  }, [archivedPage, archiveOffset]);

  useEffect(() => {
    if (versionsLoading || archivesLoading) return;
    if (selectedVersionId && allVisibleVersions.some((version) => version.tier_version_id === selectedVersionId)) {
      return;
    }
    const requestedId = searchParams.get('version');
    const requested = requestedId
      ? allVisibleVersions.find((version) => version.tier_version_id === requestedId)
      : null;
    const initial = requested
      || (draftVersions.length === 1 ? draftVersions[0] : null)
      || activeVersion
      || (draftVersions.length === 0 ? archivedVersions[0] : null)
      || null;
    setSelectedVersionId(initial?.tier_version_id || null);
  }, [
    activeVersion,
    allVisibleVersions,
    archivedVersions,
    archivesLoading,
    draftVersions,
    searchParams,
    selectedVersionId,
    versionsLoading,
  ]);

  useEffect(() => {
    setWorkspaceRevision(selectedVersion?.configuration_revision ?? null);
    setConfigurationLocked(false);
    setPolicyOffset(0);
    setPoolOffset(0);
    setPolicyError(null);
    setPoolError(null);
    setPolicyConflict(null);
    setPoolConflict(null);
    // A server revision refresh is handled by the paginated policy/pool responses;
    // this reset is intentionally scoped to selecting a different version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVersionId]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPolicySearch(policySearchInput.trim());
      setPolicyOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [policySearchInput]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPoolSearch(poolSearchInput.trim());
      setPoolOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [poolSearchInput]);

  const policyEnabledParam = policyEnabled === 'all' ? undefined : policyEnabled === 'enabled';
  const policyAccessParam = policyAccess === 'all' ? undefined : policyAccess;
  const {
    data: policyPage,
    loading: policyLoading,
    error: policyLoadError,
    refetch: refetchPolicies,
  } = useApi<TierConfigurationPage<TierModelPolicy> | null>(
    () => selectedVersionId
      ? tiers.listModelPolicies(tierId!, selectedVersionId, {
          search: policySearch || undefined,
          enabled: policyEnabledParam,
          access_mode: policyAccessParam,
          sort: 'callable_key',
          order: 'asc',
          limit: policyPageSize,
          offset: policyOffset,
        })
      : Promise.resolve(null),
    [
      tierId,
      selectedVersionId,
      policySearch,
      policyEnabledParam,
      policyAccessParam,
      policyPageSize,
      policyOffset,
    ],
  );
  const {
    data: poolPage,
    loading: poolLoading,
    error: poolLoadError,
    refetch: refetchPools,
  } = useApi<TierConfigurationPage<TierCapacityPool> | null>(
    () => selectedVersionId
      ? tiers.listCapacityPools(tierId!, selectedVersionId, {
          search: poolSearch || undefined,
          strategy: poolStrategy === 'all' ? undefined : poolStrategy,
          sort: 'pool_key',
          order: 'asc',
          limit: poolPageSize,
          offset: poolOffset,
        })
      : Promise.resolve(null),
    [tierId, selectedVersionId, poolSearch, poolStrategy, poolPageSize, poolOffset],
  );

  useEffect(() => {
    if (!policyPage) return;
    setWorkspaceRevision((current) => Math.max(current ?? 0, policyPage.configuration_revision));
  }, [policyPage]);
  useEffect(() => {
    if (!poolPage) return;
    setWorkspaceRevision((current) => Math.max(current ?? 0, poolPage.configuration_revision));
  }, [poolPage]);
  useEffect(() => {
    if (!policyPage) return;
    const nextOffset = clampTierPaginationOffset(
      policyPage.pagination.total,
      policyPageSize,
      policyOffset,
    );
    if (nextOffset !== policyOffset) setPolicyOffset(nextOffset);
  }, [policyOffset, policyPage, policyPageSize]);
  useEffect(() => {
    if (!poolPage) return;
    const nextOffset = clampTierPaginationOffset(
      poolPage.pagination.total,
      poolPageSize,
      poolOffset,
    );
    if (nextOffset !== poolOffset) setPoolOffset(nextOffset);
  }, [poolOffset, poolPage, poolPageSize]);

  const { data: callableCatalog } = useApi(() => callableTargets.listAll(), []);
  const callableOptions = useMemo(
    () => (callableCatalog || []).map((item) => item.callable_key),
    [callableCatalog],
  );
  const callableModes = useMemo(() => Object.fromEntries(
    (callableCatalog || [])
      .filter((item) => item.mode)
      .map((item) => [item.callable_key, String(item.mode)]),
  ), [callableCatalog]);
  const callableModeConflicts = useMemo(() => Object.fromEntries(
    (callableCatalog || []).map((item) => [item.callable_key, Boolean(item.mode_conflict)]),
  ), [callableCatalog]);
  const poolOptions = useMemo(
    () => poolOptionsForCallable(poolPage?.data || []),
    [poolPage],
  );
  const loadPoolOptions = useCallback(async (callableKey: string, search: string) => {
    if (!tierId || !selectedVersionId) return { options: [], hasMore: false };
    const response = await tiers.listCapacityPools(tierId, selectedVersionId, {
      callable_key: callableKey,
      search: search || undefined,
      sort: 'pool_key',
      order: 'asc',
      limit: 25,
      offset: 0,
    });
    return {
      options: poolOptionsForCallable(response.data, callableKey),
      hasMore: response.pagination.has_more,
    };
  }, [selectedVersionId, tierId]);

  const isMutating = savingTier || busyAction !== null || activating;
  const requiresDraftChoice = draftVersions.length > 1 && selectedVersion?.status !== 'draft';

  const beginTierRouteRequest = (): TierRouteRequest | null => {
    if (!tierId) return null;
    const requestId = mutationRequestRef.current + 1;
    mutationRequestRef.current = requestId;
    return { tierId, routeEpoch: routeEpochRef.current, requestId };
  };
  const isCurrentTierRouteRequest = (request: TierRouteRequest): boolean => (
    tierIdRef.current === request.tierId
    && routeEpochRef.current === request.routeEpoch
    && mutationRequestRef.current === request.requestId
  );

  const updateRouteState = (versionId: string | null, nextTab: WorkspaceTab = tab) => {
    const next = new URLSearchParams(searchParams);
    if (versionId) next.set('version', versionId);
    else next.delete('version');
    next.set('tab', nextTab);
    setSearchParams(next, { replace: true });
  };
  const selectVersion = (versionId: string) => {
    if (isMutating) return;
    setSelectedVersionId(versionId);
    updateRouteState(versionId);
  };
  const selectTab = (nextTab: WorkspaceTab) => {
    updateRouteState(selectedVersionId, nextTab);
  };

  const openEditTier = () => {
    if (isMutating || !currentDetail?.tier) return;
    setForm(tierToForm(currentDetail.tier));
    setTierError(null);
    setDrawerOpen(true);
  };
  const handleUpdateTier = async () => {
    if (isMutating) return;
    const tierKey = form.tier_key.trim();
    const name = form.name.trim();
    if (!tierKey || !name) {
      setTierError('Tier key and display name are required.');
      return;
    }
    const request = beginTierRouteRequest();
    if (!request) return;
    setSavingTier(true);
    setTierError(null);
    try {
      await tiers.update(request.tierId, {
        tier_key: tierKey,
        name,
        description: form.description.trim() || null,
        enabled: form.enabled,
      });
      if (!isCurrentTierRouteRequest(request)) return;
      setDrawerOpen(false);
      refetchDetail();
      pushToast({ tone: 'success', title: 'Tier updated', message: 'Tier metadata was saved.' });
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      setTierError(errorMessage(error, 'Failed to update tier.'));
    } finally {
      if (isCurrentTierRouteRequest(request)) setSavingTier(false);
    }
  };
  const handleDeleteTier = async () => {
    if (isMutating || !currentDetail?.tier) return;
    if (!confirm(`Delete tier ${currentDetail.tier.name}?`)) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction('delete-tier');
    try {
      await tiers.delete(request.tierId);
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'success', title: 'Tier deleted', message: 'Tier was removed.' });
      navigate('/tiers');
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Delete failed', message: errorMessage(error, 'Failed to delete tier.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };

  const refreshArchives = () => {
    setArchivedVersions([]);
    setArchivedPagination(null);
    if (archiveOffset === 0) refetchArchivedPage();
    else setArchiveOffset(0);
  };
  const refreshVersions = () => {
    refetchCurrentVersions();
    refreshArchives();
    refetchDetail();
  };

  const handleCreateDraft = async () => {
    if (isMutating) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction('create-draft');
    try {
      const created = await tiers.createVersion(request.tierId);
      if (!isCurrentTierRouteRequest(request)) return;
      setOptimisticVersion(created);
      setSelectedVersionId(created.tier_version_id);
      updateRouteState(created.tier_version_id, 'models');
      refreshVersions();
      pushToast({ tone: 'success', title: 'Draft created', message: `Draft v${created.version_number} is ready.` });
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Draft failed', message: errorMessage(error, 'Failed to create draft.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };

  const handleCloneVersion = async (source: TierVersion) => {
    if (isMutating) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction(`clone-${source.tier_version_id}`);
    try {
      const created = await tiers.cloneVersion(request.tierId, source.tier_version_id);
      if (!isCurrentTierRouteRequest(request)) return;
      setOptimisticVersion(created);
      setSelectedVersionId(created.tier_version_id);
      updateRouteState(created.tier_version_id, 'models');
      refreshVersions();
      pushToast({
        tone: 'success',
        title: source.status === 'archived' ? 'Version restored' : 'Draft cloned',
        message: `Draft v${created.version_number} was created from v${source.version_number}.`,
      });
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Clone failed', message: errorMessage(error, 'Failed to create draft from this version.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };

  const handleArchive = async (version: TierVersion) => {
    if (isMutating || !confirm(`Archive version ${version.version_number}? Its configuration will remain available as immutable history.`)) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction(`archive-${version.tier_version_id}`);
    try {
      await tiers.archiveVersion(request.tierId, version.tier_version_id);
      if (!isCurrentTierRouteRequest(request)) return;
      if (optimisticVersion?.tier_version_id === version.tier_version_id) {
        setOptimisticVersion(null);
      }
      refreshVersions();
      pushToast({ tone: 'success', title: 'Version archived', message: `Version ${version.version_number} remains available to restore as a draft.` });
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Archive failed', message: errorMessage(error, 'Failed to archive version.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };

  const mutationRevision = (): number => {
    if (workspaceRevision == null) throw new Error('Wait for the selected version revision before saving.');
    return workspaceRevision;
  };
  const handleConfigurationError = (
    error: unknown,
    setError: (message: string | null) => void,
    setConflict: (message: string | null) => void,
  ) => {
    const detail = structuredApiErrorDetail(error);
    const message = errorMessage(error, 'Failed to update this draft.');
    if (detail?.code === 'tier_configuration_stale') {
      setError(null);
      setConflict(message);
    } else {
      setError(message);
    }
    if (detail?.code === 'tier_version_not_draft') {
      setConfigurationLocked(true);
      refreshVersions();
    }
  };
  const configurationMutationSucceeded = (
    revision: number,
    message: string,
  ) => {
    setWorkspaceRevision(revision);
    setPolicyError(null);
    setPoolError(null);
    setPolicyConflict(null);
    setPoolConflict(null);
    refetchPolicies();
    refetchPools();
    refetchCurrentVersions();
    refetchDetail();
    pushToast({ tone: 'success', title: 'Draft saved', message });
  };

  const createPolicy = async (payload: TierModelPolicyPayload) => {
    if (!selectedVersionId) throw new Error('Select a version before adding a policy.');
    const versionId = selectedVersionId;
    const expectedRevision = mutationRevision();
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction('create-policy');
    setPolicyError(null);
    try {
      const result = await tiers.createModelPolicy(request.tierId, versionId, {
        ...payload,
        expected_revision: expectedRevision,
      });
      if (!isCurrentTierRouteRequest(request)) return;
      configurationMutationSucceeded(result.configuration_revision, `Added ${result.data.callable_key}.`);
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      handleConfigurationError(error, setPolicyError, setPolicyConflict);
      throw error;
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };
  const updatePolicy = async (existing: TierModelPolicy, payload: TierModelPolicyPayload) => {
    if (!selectedVersionId || !existing.tier_model_policy_id) throw new Error('This policy does not have a server ID.');
    const versionId = selectedVersionId;
    const expectedRevision = mutationRevision();
    const request = beginTierRouteRequest();
    if (!request) return;
    const { callable_key, ...editable } = payload;
    if (callable_key !== existing.callable_key) throw new Error('Callable identity cannot be changed.');
    setBusyAction(`update-policy-${existing.tier_model_policy_id}`);
    setPolicyError(null);
    try {
      const result = await tiers.updateModelPolicy(request.tierId, versionId, existing.tier_model_policy_id, {
        ...editable,
        expected_revision: expectedRevision,
      });
      if (!isCurrentTierRouteRequest(request)) return;
      configurationMutationSucceeded(result.configuration_revision, `Updated ${result.data.callable_key}.`);
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      handleConfigurationError(error, setPolicyError, setPolicyConflict);
      throw error;
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };
  const deletePolicy = async (policy: TierModelPolicy) => {
    if (!selectedVersionId || !policy.tier_model_policy_id) throw new Error('This policy does not have a server ID.');
    const versionId = selectedVersionId;
    const expectedRevision = mutationRevision();
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction(`delete-policy-${policy.tier_model_policy_id}`);
    setPolicyError(null);
    try {
      const result = await tiers.deleteModelPolicy(
        request.tierId,
        versionId,
        policy.tier_model_policy_id,
        expectedRevision,
      );
      if (!isCurrentTierRouteRequest(request)) return;
      if (policyPage && policyPage.pagination.total - 1 <= policyOffset && policyOffset > 0) {
        setPolicyOffset(Math.max(0, policyOffset - policyPageSize));
      }
      configurationMutationSucceeded(result.configuration_revision, `Removed ${policy.callable_key}.`);
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      handleConfigurationError(error, setPolicyError, setPolicyConflict);
      throw error;
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };
  const bulkUpdateLimits = async (limits: { rpm_limit?: number; tpm_limit?: number }) => {
    if (!selectedVersionId) throw new Error('Select a version before updating limits.');
    const versionId = selectedVersionId;
    const expectedRevision = mutationRevision();
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction('bulk-policy-limits');
    setPolicyError(null);
    try {
      const result = await tiers.bulkUpdateModelPolicyLimits(request.tierId, versionId, {
        ...limits,
        expected_revision: expectedRevision,
        all_filtered: true,
        search: policySearch || undefined,
        enabled: policyEnabledParam,
        access_mode: policyAccessParam,
      });
      if (!isCurrentTierRouteRequest(request)) return;
      configurationMutationSucceeded(result.configuration_revision, `Updated limits for ${result.affected_count} filtered policies.`);
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      handleConfigurationError(error, setPolicyError, setPolicyConflict);
      throw error;
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };

  const createPool = async (payload: TierCapacityPoolPayload) => {
    if (!selectedVersionId) throw new Error('Select a version before adding a pool.');
    const versionId = selectedVersionId;
    const expectedRevision = mutationRevision();
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction('create-pool');
    setPoolError(null);
    try {
      const result = await tiers.createCapacityPool(request.tierId, versionId, {
        ...payload,
        expected_revision: expectedRevision,
      });
      if (!isCurrentTierRouteRequest(request)) return;
      configurationMutationSucceeded(result.configuration_revision, `Added capacity pool ${result.data.pool_key}.`);
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      handleConfigurationError(error, setPoolError, setPoolConflict);
      throw error;
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };
  const updatePool = async (existing: TierCapacityPool, payload: TierCapacityPoolPayload) => {
    if (!selectedVersionId || !existing.tier_capacity_pool_id) throw new Error('This pool does not have a server ID.');
    const versionId = selectedVersionId;
    const expectedRevision = mutationRevision();
    const request = beginTierRouteRequest();
    if (!request) return;
    const { pool_key, callable_key, ...editable } = payload;
    if (pool_key !== existing.pool_key || callable_key !== existing.callable_key) {
      throw new Error('Pool and callable identities cannot be changed.');
    }
    setBusyAction(`update-pool-${existing.tier_capacity_pool_id}`);
    setPoolError(null);
    try {
      const result = await tiers.updateCapacityPool(request.tierId, versionId, existing.tier_capacity_pool_id, {
        ...editable,
        expected_revision: expectedRevision,
      });
      if (!isCurrentTierRouteRequest(request)) return;
      configurationMutationSucceeded(result.configuration_revision, `Updated capacity pool ${result.data.pool_key}.`);
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      handleConfigurationError(error, setPoolError, setPoolConflict);
      throw error;
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };
  const deletePool = async (pool: TierCapacityPool) => {
    if (!selectedVersionId || !pool.tier_capacity_pool_id) throw new Error('This pool does not have a server ID.');
    const versionId = selectedVersionId;
    const expectedRevision = mutationRevision();
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction(`delete-pool-${pool.tier_capacity_pool_id}`);
    setPoolError(null);
    try {
      const result = await tiers.deleteCapacityPool(
        request.tierId,
        versionId,
        pool.tier_capacity_pool_id,
        expectedRevision,
      );
      if (!isCurrentTierRouteRequest(request)) return;
      if (poolPage && poolPage.pagination.total - 1 <= poolOffset && poolOffset > 0) {
        setPoolOffset(Math.max(0, poolOffset - poolPageSize));
      }
      configurationMutationSucceeded(result.configuration_revision, `Removed capacity pool ${pool.pool_key}.`);
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      const detail = structuredApiErrorDetail(error);
      if (detail?.code === 'tier_pool_in_use') {
        setPoolError(`${errorMessage(error, 'This pool is in use.')} Open Models & limits and filter by ${pool.pool_key} to rebind those policies.`);
      } else {
        handleConfigurationError(error, setPoolError, setPoolConflict);
      }
      throw error;
    } finally {
      if (isCurrentTierRouteRequest(request)) setBusyAction(null);
    }
  };

  const loadActivationPreview = async (version: TierVersion, refreshedMessage?: string) => {
    const requestId = activationRequestRef.current + 1;
    activationRequestRef.current = requestId;
    setActivationLoading(true);
    setActivationError(null);
    try {
      const preview = await tiers.activationPreview(tierId!, version.tier_version_id);
      if (activationRequestRef.current !== requestId) return;
      setActivationPreview(preview);
      setActivationError(refreshedMessage || null);
    } catch (error: unknown) {
      if (activationRequestRef.current !== requestId) return;
      setActivationPreview(null);
      setActivationError(errorMessage(error, 'Failed to build activation preview.'));
    } finally {
      if (activationRequestRef.current === requestId) setActivationLoading(false);
    }
  };
  const openActivation = () => {
    if (!selectedVersion || selectedVersion.status !== 'draft' || isMutating) return;
    setActivationOpen(true);
    void loadActivationPreview(selectedVersion);
  };
  const closeActivation = () => {
    if (activating) return;
    activationRequestRef.current += 1;
    setActivationOpen(false);
    setActivationPreview(null);
    setActivationError(null);
  };
  const activateSelectedVersion = async () => {
    if (!selectedVersion || !activationPreview || activating) return;
    const version = selectedVersion;
    const preview = activationPreview;
    const request = beginTierRouteRequest();
    if (!request) return;
    setActivating(true);
    setActivationError(null);
    try {
      const activated = await tiers.activateVersion(request.tierId, version.tier_version_id, {
        expected_revision: preview.draft_configuration_revision,
        expected_active_version_id: preview.expected_active_version_id,
      });
      if (!isCurrentTierRouteRequest(request)) return;
      setActivationOpen(false);
      setActivationPreview(null);
      setOptimisticVersion(null);
      setWorkspaceRevision(activated.configuration_revision);
      refreshVersions();
      refetchPolicies();
      refetchPools();
      pushToast({ tone: 'success', title: 'Tier activated', message: `Version ${activated.version_number} is now live.` });
    } catch (error: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      const detail = structuredApiErrorDetail(error);
      const message = errorMessage(error, 'Failed to activate this draft.');
      if (detail?.code === 'tier_configuration_stale' || detail?.code === 'tier_activation_active_changed') {
        await loadActivationPreview(version, `${message} The preview was refreshed; review it before confirming again.`);
      } else {
        setActivationError(message);
      }
    } finally {
      if (isCurrentTierRouteRequest(request)) setActivating(false);
    }
  };

  if (detailLoading || routeDetailPending || (!detail && !detailError)) {
    return <FullPageSpinner />;
  }
  if (!currentDetail) {
    return (
      <div className="p-6">
        <p className="text-gray-500">{errorMessage(detailError, 'Tier not found.')}</p>
        <button type="button" onClick={() => navigate('/tiers')} className="mt-2 text-sm text-brand-primary-ink">Back to Tiers</button>
      </div>
    );
  }

  const tier = currentDetail.tier;
  const policyData = policyPage?.data || [];
  const poolData = poolPage?.data || [];
  const selectedReadOnly = !canEditVersion;
  const versionFetchError = versionsError || archivesError;

  return (
    <EntityDetailShell
      breadcrumbs={[
        { label: 'Tiers', onClick: () => navigate('/tiers'), icon: ArrowLeft },
        { label: tier.name },
      ]}
      avatar={(
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-brand-primary shadow-sm">
          <BadgeDollarSign aria-hidden="true" className="h-6 w-6 text-brand-on-primary" />
        </div>
      )}
      title={tier.name}
      badges={(
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge status={tier.enabled ? 'enabled' : 'disabled'} label={tier.enabled ? 'Enabled' : 'Disabled'} />
          {activeVersion ? <TierVersionBadge status="active" versionNumber={activeVersion.version_number} /> : null}
          {draftVersions[0] ? <TierVersionBadge status="draft" versionNumber={draftVersions[0].version_number} /> : null}
          {draftVersions.length > 1 ? <span className="text-[11px] text-gray-500">+{draftVersions.length - 1} drafts</span> : null}
        </div>
      )}
      meta={(
        <div className="flex flex-wrap items-center gap-3">
          <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-500">{tier.tier_key}</code>
          <span className="text-xs text-gray-400">Updated {formatDateTime(tier.last_activity_at || tier.updated_at)}</span>
        </div>
      )}
      action={(
        <div className="flex flex-wrap justify-end gap-2">
          {requiresDraftChoice ? (
            <button
              type="button"
              onClick={() => {
                document.getElementById('tier-version-rail')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                window.setTimeout(() => document.querySelector<HTMLElement>('[data-tier-draft-version="true"]')?.focus(), 250);
              }}
              disabled={isMutating}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
            >
              <FilePlus2 aria-hidden="true" className="h-3.5 w-3.5" />
              Choose a draft
            </button>
          ) : selectedVersion?.status === 'draft' ? (
            <button type="button" onClick={openActivation} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50">
              <Send aria-hidden="true" className="h-3.5 w-3.5" />
              Review & activate
            </button>
          ) : selectedVersion?.status === 'archived' ? (
            <button type="button" onClick={() => handleCloneVersion(selectedVersion)} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50">
              <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
              Restore as draft
            </button>
          ) : selectedVersion?.status === 'active' ? (
            <button type="button" onClick={() => handleCloneVersion(selectedVersion)} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50">
              <FilePlus2 aria-hidden="true" className="h-3.5 w-3.5" />
              Edit live configuration
            </button>
          ) : (
            <button type="button" onClick={handleCreateDraft} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50">
              <FilePlus2 aria-hidden="true" className="h-3.5 w-3.5" />
              Create draft
            </button>
          )}
          <button type="button" onClick={openEditTier} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
            <Edit3 aria-hidden="true" className="h-3.5 w-3.5" />
            Edit tier
          </button>
          <button type="button" onClick={handleDeleteTier} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50">
            <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
            Delete
          </button>
        </div>
      )}
      metrics={(
        <>
          <DetailMetricCard icon={Layers} label="Versions" value={String(tier.version_count || allVisibleVersions.length)} sub={`${draftVersions.length} draft${draftVersions.length === 1 ? '' : 's'}`} tone="blue" />
          <DetailMetricCard icon={Shield} label="Model policies" value={String(selectedVersion?.model_policy_count || 0)} sub={versionLabel(selectedVersion)} tone="violet" />
          <DetailMetricCard icon={BadgeDollarSign} label="Capacity pools" value={String(selectedVersion?.capacity_pool_count || 0)} sub="selected version" tone="indigo" />
          <DetailMetricCard icon={Shield} label="Organizations" value={String(tier.organization_count || 0)} sub="live or scheduled" tone="green" />
        </>
      )}
      tabs={(
        <TextTabs
          active={tab}
          onChange={selectTab}
          items={[
            { id: 'models', label: 'Models & limits' },
            { id: 'pricing', label: 'Pricing' },
            { id: 'pools', label: 'Capacity pools' },
          ]}
        />
      )}
      notice={tier.description ? (
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-800">{tier.description}</div>
      ) : undefined}
    >
      {versionFetchError ? (
        <div className="mb-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage(versionFetchError, 'Failed to load version history.')}
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
        <TierVersionRail
          currentVersions={currentVersions}
          archivedVersions={archivedVersions}
          archivedPagination={archivedPagination}
          selectedVersionId={selectedVersionId}
          busy={isMutating || versionsLoading || archivesLoading}
          onSelect={selectVersion}
          onCreateDraft={handleCreateDraft}
          onClone={handleCloneVersion}
          onArchive={handleArchive}
          onLoadMoreArchived={() => {
            if (archivedPagination?.has_more) setArchiveOffset(archivedPagination.offset + archivedPagination.limit);
          }}
        />

        <main className="min-w-0 space-y-4">
          {selectedVersion ? (
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold text-gray-900">Version {selectedVersion.version_number}</h2>
                    <TierVersionBadge status={selectedVersion.status} versionNumber={selectedVersion.version_number} />
                  </div>
                  <p className="mt-1 text-xs text-gray-500">
                    {canEditVersion ? 'Editable draft · changes are not live until activation' : 'Read-only immutable configuration'}
                  </p>
                </div>
                <div className="text-right text-xs text-gray-500">
                  <p>Revision {workspaceRevision ?? selectedVersion.configuration_revision}</p>
                  <p>Updated {formatDateTime(selectedVersion.updated_at)}</p>
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-xl border border-gray-200 bg-white p-10 text-center">
              <p className="text-sm font-semibold text-gray-700">Choose a version to inspect or edit</p>
              <p className="mt-1 text-xs text-gray-500">When multiple drafts exist, selecting one explicitly prevents you from opening another admin’s work by accident.</p>
            </div>
          )}

          {selectedVersion && (tab === 'models' || tab === 'pricing') ? (
            policyLoading && !policyPage ? <PanelSpinner label="Loading model policies…" /> : policyLoadError ? (
              <PanelError error={policyLoadError} fallback="Failed to load model policies." />
            ) : (
              <TierModelPolicyGrid
                key={`policies:${selectedVersionId}:${tab}`}
                view={tab === 'pricing' ? 'pricing' : 'limits'}
                policies={policyData}
                pagination={policyPage?.pagination || EMPTY_PAGINATION}
                pageSize={policyPageSize}
                searchInput={policySearchInput}
                enabledFilter={policyEnabled}
                accessFilter={policyAccess}
                poolOptions={poolOptions}
                callableOptions={callableOptions}
                callableModes={callableModes}
                callableModeConflicts={callableModeConflicts}
                readOnly={selectedReadOnly}
                saving={isMutating}
                error={policyError}
                conflict={policyConflict}
                onSearchInputChange={setPolicySearchInput}
                onEnabledFilterChange={(value) => { setPolicyEnabled(value); setPolicyOffset(0); }}
                onAccessFilterChange={(value) => { setPolicyAccess(value); setPolicyOffset(0); }}
                onPageChange={setPolicyOffset}
                onPageSizeChange={(value) => { setPolicyPageSize(value); setPolicyOffset(0); }}
                onCreate={createPolicy}
                onUpdate={updatePolicy}
                onDelete={deletePolicy}
                onBulkLimits={bulkUpdateLimits}
                onLoadPoolOptions={loadPoolOptions}
                onReviewLatest={() => {
                  setPolicyConflict('Loading the latest server values now. Compare them with your open form before saving again.');
                  refetchPolicies();
                  refetchPools();
                }}
                onDiscardConflict={() => {
                  setPolicyConflict(null);
                  setPolicyError(null);
                  refetchPolicies();
                }}
              />
            )
          ) : null}

          {selectedVersion && tab === 'pools' ? (
            poolLoading && !poolPage ? <PanelSpinner label="Loading capacity pools…" /> : poolLoadError ? (
              <PanelError error={poolLoadError} fallback="Failed to load capacity pools." />
            ) : (
              <TierCapacityPoolEditor
                key={`pools:${selectedVersionId}`}
                pools={poolData}
                pagination={poolPage?.pagination || EMPTY_PAGINATION}
                pageSize={poolPageSize}
                searchInput={poolSearchInput}
                strategyFilter={poolStrategy}
                callableOptions={callableOptions}
                readOnly={selectedReadOnly}
                saving={isMutating}
                error={poolError}
                conflict={poolConflict}
                onSearchInputChange={setPoolSearchInput}
                onStrategyFilterChange={(value) => { setPoolStrategy(value); setPoolOffset(0); }}
                onPageChange={setPoolOffset}
                onPageSizeChange={(value) => { setPoolPageSize(value); setPoolOffset(0); }}
                onCreate={createPool}
                onUpdate={updatePool}
                onDelete={deletePool}
                onReviewLatest={() => {
                  setPoolConflict('Loading the latest server values now. Compare them with your open form before saving again.');
                  refetchPools();
                  refetchPolicies();
                }}
                onDiscardConflict={() => {
                  setPoolConflict(null);
                  setPoolError(null);
                  refetchPools();
                }}
              />
            )
          ) : null}
        </main>
      </div>

      <TierFormDrawer
        open={drawerOpen}
        title="Edit Tier"
        values={form}
        saving={savingTier}
        error={tierError}
        submitLabel="Save tier"
        onChange={setForm}
        onClose={() => setDrawerOpen(false)}
        onSubmit={handleUpdateTier}
      />
      <TierActivationDialog
        open={activationOpen}
        preview={activationPreview}
        loading={activationLoading}
        activating={activating}
        error={activationError}
        onClose={closeActivation}
        onRefresh={() => selectedVersion && void loadActivationPreview(selectedVersion)}
        onActivate={activateSelectedVersion}
      />
    </EntityDetailShell>
  );
}

function routeTab(value: string | null): WorkspaceTab {
  return value === 'pricing' || value === 'pools' ? value : 'models';
}

function dedupeVersions(versions: TierVersion[]): TierVersion[] {
  return [...new Map(versions.map((version) => [version.tier_version_id, version])).values()]
    .sort((left, right) => right.version_number - left.version_number);
}

function FullPageSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50">
      <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-brand-primary" />
    </div>
  );
}

function PanelSpinner({ label }: { label: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-10 text-center">
      <div className="mx-auto h-6 w-6 animate-spin rounded-full border-b-2 border-brand-primary" />
      <p className="mt-2 text-xs text-gray-500">{label}</p>
    </div>
  );
}

function PanelError({ error, fallback }: { error: unknown; fallback: string }) {
  return (
    <div className="rounded-xl border border-red-100 bg-red-50 p-4 text-sm text-red-700">
      {errorMessage(error, fallback)}
    </div>
  );
}
