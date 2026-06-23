import { ArrowLeft, BadgeDollarSign, Edit3, Layers, Shield, Trash2 } from 'lucide-react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import StatusBadge from '../components/StatusBadge';
import { DetailMetricCard, EntityDetailShell, TextTabs } from '../components/admin/shells';
import TierCapacityPoolEditor from '../components/tiers/TierCapacityPoolEditor';
import TierFormDrawer from '../components/tiers/TierFormDrawer';
import TierModelPolicyGrid from '../components/tiers/TierModelPolicyGrid';
import TierVersionOverview from '../components/tiers/TierVersionOverview';
import { useToast } from '../components/ToastProvider';
import {
  callableTargets,
  tiers,
  type TierCapacityPool,
  type TierModelPolicy,
  type TierVersion,
  type TierVersionDetail,
} from '../lib/api';
import { useApi } from '../lib/hooks';
import {
  capacityPoolsToPayload,
  errorMessage,
  formatDateTime,
  modelPoliciesToPayload,
  pickEditableVersion,
  poolOptionsForCallable,
  tierToForm,
  versionLabel,
} from '../lib/tiers';

type TabId = 'overview' | 'editor';
type TierRouteRequest = {
  tierId: string;
  routeEpoch: number;
  requestId: number;
};

export default function TierDetail() {
  const { tierId } = useParams<{ tierId: string }>();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const [tab, setTab] = useState<TabId>('overview');
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [form, setForm] = useState(tierToForm());
  const [savingTier, setSavingTier] = useState(false);
  const [tierError, setTierError] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [poolError, setPoolError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const tierIdRef = useRef(tierId);
  const routeEpochRef = useRef(0);
  const mutationRequestRef = useRef(0);

  const { data: detail, loading: detailLoading, error: detailError, refetch: refetchDetail } = useApi(
    () => tiers.get(tierId!),
    [tierId],
  );

  const currentDetail = detail?.tier?.tier_id === tierId ? detail : null;
  const routeDetailPending = Boolean(tierId && detail && detail.tier?.tier_id !== tierId);
  const versions = currentDetail?.versions || [];
  const selectedVersion = versions.find((version) => version.tier_version_id === selectedVersionId) || null;
  const activeVersion = versions.find((version) => version.status === 'active') || null;
  const canEditVersion = selectedVersion?.status === 'draft';

  useLayoutEffect(() => {
    if (tierIdRef.current !== tierId) {
      tierIdRef.current = tierId;
      routeEpochRef.current += 1;
      mutationRequestRef.current += 1;
    }
    setSelectedVersionId(null);
    setDrawerOpen(false);
    setForm(tierToForm());
    setSavingTier(false);
    setTierError(null);
    setPolicyError(null);
    setPoolError(null);
    setBusyAction(null);
    setTab('overview');
  }, [tierId]);

  useEffect(() => {
    if (!currentDetail) return;
    if (selectedVersionId && currentDetail.versions.some((version) => version.tier_version_id === selectedVersionId)) return;
    const initial = pickEditableVersion(currentDetail.versions);
    setSelectedVersionId(initial?.tier_version_id ?? null);
  }, [currentDetail, selectedVersionId]);

  const { data: versionDetail, loading: versionLoading, error: versionError, refetch: refetchVersion } = useApi<TierVersionDetail | null>(
    () => selectedVersionId ? tiers.getVersion(tierId!, selectedVersionId) : Promise.resolve(null),
    [tierId, selectedVersionId],
  );

  const { data: callablePage } = useApi(
    () => callableTargets.listAll({ target_type: 'model' }),
    [],
  );
  const callableOptions = (callablePage || []).map((item) => item.callable_key);
  const currentVersionDetail = (
    versionDetail?.tier_version?.tier_version_id === selectedVersionId
    && versionDetail.tier_version?.tier_id === tierId
  ) ? versionDetail : null;
  const versionDetailMismatch = Boolean(
    selectedVersionId
      && versionDetail
      && (
        versionDetail.tier_version?.tier_version_id !== selectedVersionId
        || versionDetail.tier_version?.tier_id !== tierId
      ),
  );
  const versionDetailPending = Boolean(
    selectedVersionId
      && (versionLoading || versionDetailMismatch || (!versionDetail && !versionError)),
  );
  const poolOptions = useMemo(
    () => poolOptionsForCallable(currentVersionDetail?.capacity_pools || []),
    [currentVersionDetail],
  );
  const isMutating = savingTier || busyAction !== null;
  const overviewBusyAction = isMutating ? busyAction || 'tier-update' : null;

  const beginTierRouteRequest = (): TierRouteRequest | null => {
    if (!tierId) return null;
    const requestId = mutationRequestRef.current + 1;
    mutationRequestRef.current = requestId;
    return {
      tierId,
      routeEpoch: routeEpochRef.current,
      requestId,
    };
  };

  const isCurrentTierRouteRequest = (request: TierRouteRequest): boolean => (
    tierIdRef.current === request.tierId
    && routeEpochRef.current === request.routeEpoch
    && mutationRequestRef.current === request.requestId
  );

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
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      setTierError(errorMessage(err, 'Failed to update tier.'));
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setSavingTier(false);
      }
    }
  };

  const handleDeleteTier = async () => {
    if (isMutating || !currentDetail?.tier) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    if (!confirm(`Delete tier ${currentDetail.tier.name}?`)) return;
    setBusyAction('delete');
    try {
      await tiers.delete(request.tierId);
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'success', title: 'Tier deleted', message: 'Tier was removed.' });
      navigate('/tiers');
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Delete failed', message: errorMessage(err, 'Failed to delete tier.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setBusyAction(null);
      }
    }
  };

  const handleCreateDraft = async () => {
    if (isMutating) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    setBusyAction('create-draft');
    try {
      const created = await tiers.createVersion(request.tierId);
      if (!isCurrentTierRouteRequest(request)) return;
      setSelectedVersionId(created.tier_version_id);
      refetchDetail();
      pushToast({ tone: 'success', title: 'Draft created', message: `Version ${created.version_number} is ready.` });
      setTab('editor');
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Draft failed', message: errorMessage(err, 'Failed to create draft.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setBusyAction(null);
      }
    }
  };

  const handleCloneActive = async () => {
    if (isMutating || !activeVersion) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    const sourceVersionId = activeVersion.tier_version_id;
    setBusyAction('clone-active');
    try {
      const created = await tiers.cloneVersion(request.tierId, sourceVersionId);
      if (!isCurrentTierRouteRequest(request)) return;
      setSelectedVersionId(created.tier_version_id);
      refetchDetail();
      refetchVersion();
      pushToast({ tone: 'success', title: 'Draft cloned', message: `Version ${created.version_number} copied active policy.` });
      setTab('editor');
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Clone failed', message: errorMessage(err, 'Failed to clone active version.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setBusyAction(null);
      }
    }
  };

  const handlePublish = async () => {
    if (isMutating || !selectedVersion) return;
    if (!currentVersionDetail) {
      pushToast({ tone: 'error', title: 'Version still loading', message: 'Wait for the selected version details before publishing.' });
      return;
    }
    if ((currentVersionDetail.model_policies || []).length === 0 && !confirm('Publish this tier version with no model policies?')) return;
    const request = beginTierRouteRequest();
    if (!request) return;
    const versionId = selectedVersion.tier_version_id;
    setBusyAction('publish');
    try {
      const published = await tiers.publishVersion(request.tierId, versionId);
      if (!isCurrentTierRouteRequest(request)) return;
      refetchDetail();
      refetchVersion();
      pushToast({ tone: 'success', title: 'Tier published', message: `Version ${published.version_number} is active.` });
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Publish failed', message: errorMessage(err, 'Failed to publish version.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setBusyAction(null);
      }
    }
  };

  const handleArchive = async (version: TierVersion) => {
    if (isMutating) return;
    const request = beginTierRouteRequest();
    if (!request || !confirm(`Archive version ${version.version_number}?`)) return;
    const versionId = version.tier_version_id;
    setBusyAction(`archive-${version.tier_version_id}`);
    try {
      await tiers.archiveVersion(request.tierId, versionId);
      if (!isCurrentTierRouteRequest(request)) return;
      refetchDetail();
      refetchVersion();
      pushToast({ tone: 'success', title: 'Version archived', message: `Version ${version.version_number} was archived.` });
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      pushToast({ tone: 'error', title: 'Archive failed', message: errorMessage(err, 'Failed to archive version.') });
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setBusyAction(null);
      }
    }
  };

  const saveModelPolicies = async (policiesToSave: TierModelPolicy[]) => {
    if (isMutating || !selectedVersionId) {
      throw new Error('Select a tier version before saving policies.');
    }
    if (!currentVersionDetail) {
      throw new Error('Wait for the selected version details before saving policies.');
    }
    const request = beginTierRouteRequest();
    if (!request) {
      throw new Error('Select a tier before saving policies.');
    }
    const versionId = selectedVersionId;
    setPolicyError(null);
    setBusyAction('save-policies');
    try {
      await tiers.replaceModelPolicies(request.tierId, versionId, modelPoliciesToPayload(policiesToSave));
      if (!isCurrentTierRouteRequest(request)) return;
      refetchVersion();
      refetchDetail();
      pushToast({ tone: 'success', title: 'Policies saved', message: 'Model policy set was replaced.' });
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      setPolicyError(errorMessage(err, 'Failed to save model policies.'));
      throw err;
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setBusyAction(null);
      }
    }
  };

  const saveCapacityPools = async (poolsToSave: TierCapacityPool[]) => {
    if (isMutating || !selectedVersionId) {
      throw new Error('Select a tier version before saving capacity pools.');
    }
    if (!currentVersionDetail) {
      throw new Error('Wait for the selected version details before saving capacity pools.');
    }
    const request = beginTierRouteRequest();
    if (!request) {
      throw new Error('Select a tier before saving capacity pools.');
    }
    const versionId = selectedVersionId;
    setPoolError(null);
    setBusyAction('save-pools');
    try {
      await tiers.replaceCapacityPools(request.tierId, versionId, capacityPoolsToPayload(poolsToSave));
      if (!isCurrentTierRouteRequest(request)) return;
      refetchVersion();
      refetchDetail();
      pushToast({ tone: 'success', title: 'Pools saved', message: 'Capacity pool set was replaced.' });
    } catch (err: unknown) {
      if (!isCurrentTierRouteRequest(request)) return;
      setPoolError(errorMessage(err, 'Failed to save capacity pools.'));
      throw err;
    } finally {
      if (isCurrentTierRouteRequest(request)) {
        setBusyAction(null);
      }
    }
  };

  if (detailLoading || routeDetailPending || (!detail && !detailError)) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!currentDetail) {
    return (
      <div className="p-6">
        <p className="text-gray-500">{errorMessage(detailError, 'Tier not found.')}</p>
        <button type="button" onClick={() => navigate('/tiers')} className="mt-2 text-sm text-blue-600">Back to Tiers</button>
      </div>
    );
  }

  const tier = currentDetail.tier;

  return (
    <EntityDetailShell
      breadcrumbs={[
        { label: 'Tiers', onClick: () => navigate('/tiers'), icon: ArrowLeft },
        { label: tier.name },
      ]}
      avatar={(
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-blue-600 shadow-sm">
          <BadgeDollarSign className="h-6 w-6 text-white" />
        </div>
      )}
      title={tier.name}
      badges={(
        <div className="flex items-center gap-2">
          <StatusBadge status={tier.enabled ? 'enabled' : 'disabled'} label={tier.enabled ? 'Enabled' : 'Disabled'} />
          {activeVersion ? <StatusBadge status="active" label={`Active v${activeVersion.version_number}`} /> : null}
        </div>
      )}
      meta={(
        <div className="flex flex-wrap items-center gap-3">
          <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-500">{tier.tier_key}</code>
          <span className="text-xs text-gray-400">Updated {formatDateTime(tier.updated_at)}</span>
        </div>
      )}
      action={(
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={openEditTier} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
            <Edit3 className="h-3.5 w-3.5" />
            Edit
          </button>
          <button type="button" onClick={handleDeleteTier} disabled={isMutating} className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50">
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </button>
        </div>
      )}
      metrics={(
        <>
          <DetailMetricCard icon={Layers} label="Versions" value={String(versions.length)} sub={`${versions.filter((v) => v.status === 'draft').length} draft`} tone="blue" />
          <DetailMetricCard icon={Shield} label="Model policies" value={String(selectedVersion?.model_policy_count || currentVersionDetail?.model_policies?.length || 0)} sub={versionLabel(selectedVersion)} tone="violet" />
          <DetailMetricCard icon={BadgeDollarSign} label="Capacity pools" value={String(selectedVersion?.capacity_pool_count || currentVersionDetail?.capacity_pools?.length || 0)} sub="selected version" tone="indigo" />
          <DetailMetricCard icon={Shield} label="Assignments" value={String(tier.assignment_count || 0)} sub="active and historical" tone="green" />
        </>
      )}
      tabs={<TextTabs active={tab} onChange={setTab} items={[{ id: 'overview', label: 'Overview' }, { id: 'editor', label: 'Policy Editor' }]} />}
      notice={tier.description ? (
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-sm text-blue-800">{tier.description}</div>
      ) : undefined}
    >
      {tab === 'overview' ? (
        <TierVersionOverview
          versions={versions}
          selectedVersion={selectedVersion}
          activeVersion={activeVersion}
          versionDetail={currentVersionDetail}
          versionPending={versionDetailPending}
          busyAction={overviewBusyAction}
          onCreateDraft={handleCreateDraft}
          onCloneActive={handleCloneActive}
          onSelectEditor={(versionId) => {
            if (isMutating) return;
            setSelectedVersionId(versionId);
            setTab('editor');
          }}
          onArchive={handleArchive}
          onPublish={handlePublish}
        />
      ) : (
        <div className="space-y-5">
          <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-gray-900">Selected version</p>
                <p className="text-xs text-gray-500">{versionLabel(selectedVersion)} · {canEditVersion ? 'draft editable' : 'read-only'}</p>
              </div>
              <select
                value={selectedVersionId || ''}
                onChange={(event) => setSelectedVersionId(event.target.value || null)}
                disabled={isMutating}
                className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {versions.map((version) => (
                  <option key={version.tier_version_id} value={version.tier_version_id}>
                    v{version.version_number} {version.status}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {versionDetailPending ? (
            <div className="rounded-xl border border-gray-200 bg-white p-8">
              <div className="mx-auto h-6 w-6 animate-spin rounded-full border-b-2 border-blue-600" />
            </div>
          ) : versionError ? (
            <div className="rounded-xl border border-red-100 bg-red-50 p-4 text-sm text-red-700">
              {errorMessage(versionError, 'Failed to load selected version.')}
            </div>
          ) : !currentVersionDetail ? (
            <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-400">Create or select a version to edit policy.</div>
          ) : (
            <>
              <TierCapacityPoolEditor
                key={`pools:${selectedVersionId || 'none'}:${canEditVersion ? 'editable' : 'readonly'}`}
                pools={currentVersionDetail.capacity_pools}
                callableOptions={callableOptions}
                readOnly={!canEditVersion}
                saving={isMutating}
                error={poolError}
                onSave={saveCapacityPools}
              />
              <TierModelPolicyGrid
                key={`policies:${selectedVersionId || 'none'}:${canEditVersion ? 'editable' : 'readonly'}`}
                policies={currentVersionDetail.model_policies}
                poolOptions={poolOptions}
                callableOptions={callableOptions}
                readOnly={!canEditVersion}
                saving={isMutating}
                error={policyError}
                onSave={saveModelPolicies}
              />
            </>
          )}
        </div>
      )}

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
    </EntityDetailShell>
  );
}
