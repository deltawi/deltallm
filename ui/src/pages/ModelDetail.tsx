import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Box, Code2, DollarSign, Gauge, Plug, RefreshCw, Settings } from 'lucide-react';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import { ApiError, models } from '../lib/api';
import { modelEditPath } from '../lib/modelRoutes';
import { sharingSummary } from '../lib/routeGroupSummary';
import { MODE_OPTIONS } from '../components/modelFormShared';
import { EntityAvatar, IconTabs, PageShell } from '../components/admin/shells';
import { navTrail } from '../lib/navigation';
import Button from '../components/Button';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { Skeleton } from '../components/usage/ReportBits';
import { mutationOutcome } from '../lib/mutationOutcome';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';
import ModelConnectionTab from '../components/models/detail/ModelConnectionTab';
import ModelDetailMeta from '../components/models/detail/ModelDetailMeta';
import ModelLimitsTab from '../components/models/detail/ModelLimitsTab';
import ModelPricingTab from '../components/models/detail/ModelPricingTab';
import ModelSettingsTab from '../components/models/detail/ModelSettingsTab';
import UseModelDrawer from '../components/models/detail/UseModelDrawer';
import type { ModelDetailTabId } from '../components/models/detail/modelDetailFormat';

const TABS = [
  { id: 'connection', label: 'Connection', icon: Plug },
  { id: 'limits', label: 'Limits', icon: Gauge },
  { id: 'pricing', label: 'Pricing', icon: DollarSign },
  { id: 'settings', label: 'Settings', icon: Settings },
] as const;

const TABS_ID = 'model-tabs';

function loadErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return 'You do not have permission to view this model.';
  if (error instanceof ApiError && error.status === 404) return 'Model not found.';
  return 'Failed to load model details.';
}

export default function ModelDetail() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { session, authMode } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const audiences = useManagedAssetAudienceOptions(session, isPlatformAdmin);

  const detail = useApi((signal) => models.get(deploymentId!, signal), [deploymentId]);
  const model = detail.data;

  const [activeTab, setActiveTab] = useState<ModelDetailTabId>('connection');
  // A tab renders when it is first opened, then stays mounted so an open access editor survives a tab switch.
  const [visited, setVisited] = useState<ReadonlySet<ModelDetailTabId>>(() => new Set(['connection']));
  const [using, setUsing] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [healthActionMessage, setHealthActionMessage] = useState<string | null>(null);
  const [healthActionError, setHealthActionError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [revokeCredentialOpen, setRevokeCredentialOpen] = useState(false);
  const [revokingCredential, setRevokingCredential] = useState(false);

  const canEdit = isPlatformAdmin || Boolean(model?.access?.capabilities.write);
  const canDelete = isPlatformAdmin || Boolean(model?.access?.capabilities.delete);
  const canRevokeCredential = Boolean(
    model?.credential_binding?.state === 'active'
    && model.credential_binding.can_revoke,
  );

  const openTab = (tab: ModelDetailTabId) => {
    setActiveTab(tab);
    setVisited((current) => (current.has(tab) ? current : new Set([...current, tab])));
  };
  const openEditor = () => navigate(modelEditPath(deploymentId!));

  const handleDelete = async () => {
    setDeleting(true);
    try {
      const result = await models.delete(deploymentId!);
      const outcome = mutationOutcome('Model deployment was deleted.', result.warnings);
      pushToast({
        tone: outcome.tone,
        title: outcome.tone === 'info' ? 'Model deleted with warning' : 'Model deleted',
        message: outcome.message,
      });
      navigate('/models');
    } catch (err: unknown) {
      pushToast({
        tone: 'error',
        title: 'Delete failed',
        message: err instanceof Error ? err.message : 'Failed to delete model',
      });
      setDeleting(false);
    }
  };

  const handleCheckHealth = async () => {
    if (!canEdit) return;
    setCheckingHealth(true);
    setHealthActionMessage(null);
    setHealthActionError(null);
    try {
      const result = await models.checkHealth(deploymentId!);
      setHealthActionMessage(result.message);
      detail.refetch();
    } catch (err: unknown) {
      setHealthActionError(err instanceof ApiError ? err.message : 'Failed to run health check');
    } finally {
      setCheckingHealth(false);
    }
  };

  const handleRevokeCredential = async () => {
    setRevokingCredential(true);
    try {
      const result = await models.revokeCredentialBinding(deploymentId!);
      const outcome = mutationOutcome(
        'Credential access was revoked. This deployment will not route until an editor chooses a replacement.',
        result.warnings,
      );
      pushToast({
        tone: outcome.tone,
        title: outcome.tone === 'info' ? 'Credential revoked with warning' : 'Credential revoked',
        message: outcome.message,
      });
      setRevokeCredentialOpen(false);
      detail.refetch();
    } catch (err: unknown) {
      pushToast({
        tone: 'error',
        title: 'Credential revoke failed',
        message: err instanceof Error ? err.message : 'Failed to revoke credential access',
      });
    } finally {
      setRevokingCredential(false);
    }
  };

  const breadcrumbs = navTrail('/models');
  // A refetch keeps the last loaded model on screen; only the first load shows the skeleton.
  if (!model) {
    const failed = !!detail.error && !detail.loading;
    return (
      <PageShell breadcrumbs={breadcrumbs} avatar={<EntityAvatar icon={Box} />} title="Model">
        {failed ? (
          <div className="space-y-3">
            <div role="alert" className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700">{loadErrorMessage(detail.error)}</div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={detail.refetch}>Retry</Button>
              <Button size="sm" variant="ghost" onClick={() => navigate('/models')}>Back to Models</Button>
            </div>
          </div>
        ) : (
          <div role="status" aria-label="Loading model" className="max-w-4xl space-y-3 rounded-xl border border-gray-200 bg-white px-4 py-4 shadow-sm">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-3 w-80" />
            <Skeleton className="h-24" />
          </div>
        )}
      </PageShell>
    );
  }

  const mi = model.model_info || {};
  const mode = mi.mode || model.mode || 'chat';
  const modeLabel = MODE_OPTIONS.find((option) => option.value === mode)?.label || mode;
  const modelName = model.api_model_id || model.model_name;
  const title = model.display_name || model.model_name;
  const access = model.access;
  const sharing = access?.governance_source === 'platform'
    ? 'Tier managed'
    : access?.governance_source === 'creator'
      ? sharingSummary(access, audiences.teamOptions, audiences.organizationOptions)
      : null;
  const defaults = mi.default_params && typeof mi.default_params === 'object' ? mi.default_params : {};

  function renderTab(id: ModelDetailTabId) {
    switch (id) {
      case 'connection':
        return <ModelConnectionTab model={model!} canEdit={canEdit} onEdit={openEditor} />;
      case 'limits':
        return <ModelLimitsTab model={model!} mode={mode} />;
      case 'pricing':
        return <ModelPricingTab model={model!} mode={mode} canEdit={canEdit} onEdit={openEditor} />;
      case 'settings':
        return (
          <ModelSettingsTab model={model!} modeLabel={modeLabel} sharing={sharing} canEdit={canEdit} canDelete={canDelete}
            canRevokeCredential={canRevokeCredential} isPlatformAdmin={isPlatformAdmin} audiences={audiences}
            onEdit={openEditor} onRevokeCredential={() => setRevokeCredentialOpen(true)} onDelete={() => setDeleteOpen(true)}
            onAccessSaved={detail.refetch} />
        );
    }
  }

  return (
    <>
      <PageShell
        breadcrumbs={[...breadcrumbs, { label: title }]}
        avatar={<EntityAvatar icon={Box} />}
        title={title}
        badges={<span className="rounded-md bg-brand-primary-soft px-1.5 py-0.5 text-[11px] font-semibold text-brand-primary-ink">{modeLabel}</span>}
        meta={<ModelDetailMeta model={model} mode={mode} sharing={sharing} onOpenTab={openTab} />}
        actions={(
          <>
            {canEdit ? (
              <Button size="sm" variant="outline" onClick={handleCheckHealth} disabled={checkingHealth}>
                <RefreshCw className={`h-4 w-4 ${checkingHealth ? 'animate-spin' : ''}`} aria-hidden="true" />
                Check health
              </Button>
            ) : null}
            <Button size="sm" onClick={() => setUsing(true)}><Code2 className="h-4 w-4" aria-hidden="true" />Use this model</Button>
          </>
        )}
        tabs={<IconTabs id={TABS_ID} label="Model sections" variant="header" active={activeTab} onChange={openTab} items={[...TABS]} />}
        notice={healthActionMessage || healthActionError ? (
          <div role="status" className={`rounded-xl border px-4 py-3 text-sm ${healthActionError ? 'border-red-200 bg-red-50 text-red-800' : 'border-green-200 bg-green-50 text-green-800'}`}>
            {healthActionError || healthActionMessage}
          </div>
        ) : undefined}
      >
        {TABS.map(({ id }) => (
          <div key={id} role="tabpanel" id={`${TABS_ID}-panel-${id}`} aria-labelledby={`${TABS_ID}-tab-${id}`} hidden={activeTab !== id}>
            {visited.has(id) ? renderTab(id) : null}
          </div>
        ))}
      </PageShell>
      {using ? <UseModelDrawer modelName={modelName} mode={mode} defaults={defaults} healthy={model.healthy} onClose={() => setUsing(false)} /> : null}
      <ConfirmDialog
        open={revokeCredentialOpen}
        title="Revoke credential from this model"
        description={`Stop "${title}" from using this credential? The model will remain visible, but requests will stop until an editor selects a replacement credential.`}
        confirmLabel="Revoke Credential"
        destructive
        confirming={revokingCredential}
        onConfirm={handleRevokeCredential}
        onClose={() => { if (!revokingCredential) setRevokeCredentialOpen(false); }}
      />
      <ConfirmDialog
        open={deleteOpen}
        title="Delete model deployment"
        description={`Delete "${title}"? This cannot be undone.`}
        confirmLabel="Delete Model"
        destructive
        confirming={deleting}
        onConfirm={handleDelete}
        onClose={() => { if (!deleting) setDeleteOpen(false); }}
      />
    </>
  );
}
