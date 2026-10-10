import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layers, Plus, Trash2 } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import CreateDrawer from '../components/route-groups/CreateRouteGroupDrawer';
import type { Column } from '../components/DataTable';
import AssetList from '../components/admin/lists/AssetList';
import { assetMetadataColumns } from '../components/admin/lists/assetMetadataColumns';
import { ListIdentity } from '../components/admin/lists/AssetListCells';
import StatusBadge from '../components/StatusBadge';
import ModelTypePill from '../components/models/ModelTypePill';
import { routeGroupStrategyLabel } from '../lib/routeGroups';
import { useAssetListSort } from '../lib/useAssetListSort';
import IndexShell from '../components/admin/shells/IndexShell';
import { managedAssetAccessInput, routeGroups } from '../lib/api';
import type { ManagedAssetGrantInput, RouteGroup } from '../lib/api';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import { groupKeySuffixFromName, routeGroupMutationOutcome } from '../lib/routeGroups';
import { useApi } from '../lib/hooks';
import { routeGroupDetailPath } from '../lib/routeGroupRoutes';
import { useRouteGroupMutationScope } from '../lib/useRouteGroupMutationScope';
import { useToast } from '../components/ToastProvider';
import { useBranding } from '../lib/brandingContext';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';

function mutationErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/* ─── Page ───────────────────────────────────────────────────────────────── */
export default function RouteGroups() {
  const mutations = useRouteGroupMutationScope();
  const { branding } = useBranding();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { session, authMode } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const { teamOptions, organizationOptions, loading: audiencesLoading, error: audiencesError, refetch: refetchAudiences } = useManagedAssetAudienceOptions(
    session,
    isPlatformAdmin,
  );

  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RouteGroup | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [form, setForm] = useState({ group_key: '', name: '', mode: 'chat' });
  const [grants, setGrants] = useState<ManagedAssetGrantInput[]>([]);

  const pageSize = 20;
  const listSort = useAssetListSort(['name', 'routing', 'members', 'health', 'created_by', 'updated_at', 'visibility'] as const, () => setPageOffset(0));
  const { data: result, loading, error, refetch } = useApi(
    (signal) => routeGroups.list({ search, limit: pageSize, offset: pageOffset, sort_by: listSort.sortBy, sort_direction: listSort.sortDirection }, signal),
    [search, pageOffset, listSort.sortBy, listSort.sortDirection],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPageOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const resetForm = () => {
    setForm({ group_key: '', name: '', mode: 'chat' });
    setGrants([]);
  };

  const handleCreate = async () => {
    const name = form.name.trim();
    const groupKey = isPlatformAdmin
      ? form.group_key.trim()
      : groupKeySuffixFromName(name);
    if (isPlatformAdmin && !groupKey) {
      setFormError('Group key is required.');
      return;
    }
    if (!isPlatformAdmin && !name) {
      setFormError('Group name is required.');
      return;
    }
    if (!isPlatformAdmin && !groupKey) {
      setFormError('Group name must contain at least one letter or number.');
      return;
    }
    setFormError(null);
    const operation = mutations.begin();
    if (!operation) return;
    setCreating(true);
    try {
      const created = await routeGroups.create({
        group_key: groupKey,
        name: name || null,
        mode: form.mode,
        access: managedAssetAccessInput(grants),
      }, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      setCreateOpen(false);
      resetForm();
      const outcome = routeGroupMutationOutcome(
        `"${created.group_key}" is ready for configuration.`,
        created.warnings,
      );
      pushToast({ tone: outcome.tone, title: 'Model group created', message: outcome.message });
      navigate(routeGroupDetailPath(created.route_group_id));
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Create failed', message: mutationErrorMessage(error, 'Failed to create model group.') });
    } finally {
      if (mutations.finish(operation)) setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    const operation = mutations.begin();
    if (!operation) return;
    setDeletingKey(deleteTarget.route_group_id);
    try {
      const result = await routeGroups.delete(deleteTarget.route_group_id, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const outcome = routeGroupMutationOutcome(`"${deleteTarget.group_key}" was deleted.`, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Model group deleted', message: outcome.message });
      setDeleteTarget(null);
      refetch();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Delete failed', message: mutationErrorMessage(error, 'Failed to delete model group.') });
    } finally {
      if (mutations.finish(operation)) setDeletingKey(null);
    }
  };

  const groups: RouteGroup[] = result?.data || [];
  const pagination = result?.pagination;

  const columns: Column<RouteGroup>[] = [
    { key: 'name', header: 'Group', sortKey: 'name', render: (row) => <ListIdentity name={row.name || row.group_key} identifier={row.group_key} onOpen={() => navigate(routeGroupDetailPath(row.route_group_id))} secondary={<ModelTypePill mode={row.mode} />} actions={(!row.access || row.access.capabilities.delete) ? <button type="button" aria-label={`Delete ${row.name || row.group_key}`} onClick={() => setDeleteTarget(row)} disabled={deletingKey === row.route_group_id} className="rounded-lg p-2 md:p-1.5 hover:bg-red-50 disabled:opacity-50"><Trash2 className="h-4 w-4 text-red-500" /></button> : null} /> },
    { key: 'routing', header: 'Routing', sortKey: 'routing', render: (row) => <div className="text-xs">{routeGroupStrategyLabel(row.routing_strategy || 'simple-shuffle')}<span className="mt-1 block text-gray-400">Traffic {row.enabled ? 'live' : 'off'}</span></div> },
    { key: 'member_count', header: 'Members', sortKey: 'members', defaultDirection: 'desc' },
    { key: 'health', header: 'Health', sortKey: 'health', render: (row) => <div><StatusBadge status={row.health_status || 'unknown'} label={row.health_status === 'empty' ? 'No active members' : undefined} /><span className="mt-1 block text-xs text-gray-400">{row.health_status === 'unknown' || !row.health_status ? 'Health unavailable' : row.health_status === 'paused' ? 'Traffic off' : row.health_status === 'empty' ? 'No active deployments' : row.healthy_member_count == null || row.active_member_count == null ? 'Health unavailable' : `${row.healthy_member_count} of ${row.active_member_count} available`}</span></div> },
    ...assetMetadataColumns<RouteGroup>(),
  ];

  return (
    <IndexShell
      title="Model Groups"
      titleIcon={Layers}
      count={pagination?.total ?? null}
      description={`Group deployments behind a single key — clients call the group, ${branding.instance_name} routes traffic.`}
      action={(
        <button
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-2 rounded-xl bg-brand-primary px-4 py-2.5 text-sm font-medium text-brand-on-primary shadow-sm hover:bg-brand-primary-hover"
        >
          <Plus className="h-4 w-4" /> Create Group
        </button>
      )}

    >
      <CreateDrawer
        open={createOpen}
        onClose={() => { if (!creating) setCreateOpen(false); }}
        form={form}
        setForm={setForm}
        formError={formError}
        setFormError={setFormError}
        creating={creating}
        onCreate={handleCreate}
        grants={grants}
        teamOptions={teamOptions}
        organizationOptions={organizationOptions}
        audiencesLoading={audiencesLoading}
        audiencesError={audiencesError}
        onRetryAudiences={refetchAudiences}
        allowPublic={isPlatformAdmin}
        useGeneratedPrefix={!isPlatformAdmin}
        onGrantsChange={(nextGrants) => { setGrants(nextGrants); if (formError) setFormError(null); }}
      />
      <AssetList columns={columns} data={groups} rowKey={(row) => row.route_group_id} loading={loading} error={error} onRetry={refetch} emptyMessage={search ? `No model groups matching "${search}"` : 'No model groups yet'} search={searchInput} searchLabel="Search model groups" onSearchChange={setSearchInput} sort={listSort.sort} onSortChange={listSort.onSortChange} pagination={pagination} onPageChange={setPageOffset} onRowClick={(row) => navigate(routeGroupDetailPath(row.route_group_id))} />
      <details className="mt-4 text-xs text-gray-500"><summary className="cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary">Group setup</summary><p className="mt-2">Create the group shell, add members, then use default shuffle. Add a policy when you need more control.</p></details>

      {/* Delete confirmation */}
      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete model group"
        description={deleteTarget ? `Delete "${deleteTarget.group_key}"? This removes all members and policy history references for this group.` : ''}
        confirmLabel="Delete Group"
        destructive
        confirming={!!deletingKey}
        onConfirm={handleDelete}
        onClose={() => { if (!deletingKey) setDeleteTarget(null); }}
      />
    </IndexShell>
  );
}
