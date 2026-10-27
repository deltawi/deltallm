import { useState } from 'react';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import ManagedAssetAccessPanel from '../ManagedAssetAccessPanel';
import ToggleSwitch from '../ToggleSwitch';
import { SettingsRow } from '../admin/shells';
import { useToast } from '../ToastProvider';
import { routeGroups, type RouteGroup } from '../../lib/api';
import { ROUTE_GROUP_MODE_OPTIONS, routeGroupMutationOutcome } from '../../lib/routeGroups';
import { workloadLabel } from '../../lib/routeGroupSummary';
import type { ManagedAssetAudienceOption } from '../../lib/useManagedAssetAudienceOptions';
import type { useRouteGroupMutationScope } from '../../lib/useRouteGroupMutationScope';

type MutationScope = ReturnType<typeof useRouteGroupMutationScope>;

export interface AudienceOptions {
  teamOptions: ManagedAssetAudienceOption[];
  organizationOptions: ManagedAssetAudienceOption[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

const fieldClass = 'control-field w-full rounded-lg border border-gray-200 bg-white px-3 focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-50 disabled:text-gray-500';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

interface RouteGroupSettingsTabProps {
  group: RouteGroup;
  memberCount: number;
  sharing: string | null;
  canWrite: boolean;
  canDelete: boolean;
  isPlatformAdmin: boolean;
  audiences: AudienceOptions;
  mutations: MutationScope;
  onSaved: () => void;
  onDeleted: () => void;
}

export default function RouteGroupSettingsTab({
  group, memberCount, sharing, canWrite, canDelete, isPlatformAdmin, audiences, mutations, onSaved, onDeleted,
}: RouteGroupSettingsTabProps) {
  const { pushToast } = useToast();
  const initial = { name: group.name ?? '', mode: group.mode };
  const [form, setForm] = useState(initial);
  const [syncedGroup, setSyncedGroup] = useState(group);
  const [busy, setBusy] = useState<'general' | 'enabled' | 'delete' | null>(null);
  const [confirm, setConfirm] = useState<'disable' | 'delete' | null>(null);
  const [showAccess, setShowAccess] = useState(false);
  const dirty = form.name.trim() !== (syncedGroup.name ?? '') || form.mode !== syncedGroup.mode;
  if (syncedGroup !== group) {
    // A refresh keeps unsaved edits; otherwise the form follows the saved group.
    setSyncedGroup(group);
    if (!dirty) setForm(initial);
  }

  const update = async (kind: 'general' | 'enabled', payload: { name?: string | null; mode?: string; enabled?: boolean }, success: string) => {
    if (!canWrite) return;
    const operation = mutations.begin();
    if (!operation) return;
    setBusy(kind);
    try {
      const result = await routeGroups.update(group.route_group_id, payload, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const outcome = routeGroupMutationOutcome(success, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Group updated', message: outcome.message });
      setConfirm(null);
      onSaved();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Update failed', message: errorMessage(error, 'Failed to update the group.') });
    } finally {
      if (mutations.finish(operation)) setBusy(null);
    }
  };

  const deleteGroup = async () => {
    if (!canDelete) return;
    const operation = mutations.begin();
    if (!operation) return;
    setBusy('delete');
    try {
      const result = await routeGroups.delete(group.route_group_id, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const outcome = routeGroupMutationOutcome(`"${group.group_key}" was deleted.`, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Group deleted', message: outcome.message });
      onDeleted();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Delete failed', message: errorMessage(error, 'Failed to delete the group.') });
      setBusy(null);
    } finally {
      mutations.finish(operation);
    }
  };

  const access = group.access;
  const owner = access?.effective_role === 'owner' ? ' You are the owner.' : '';
  // Dialogs sit outside the spaced column: its margins would move their fixed layer.
  return (
    <>
      <div className="max-w-4xl space-y-3">
        <section aria-labelledby="route-group-general-title" className="rounded-xl border border-gray-200 bg-white shadow-sm">
          <header className="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
            <h2 id="route-group-general-title" className="text-sm font-semibold text-gray-900">General</h2>
            {canWrite ? (
              <Button size="sm" disabled={!dirty} loading={busy === 'general'}
                onClick={() => void update('general', { name: form.name.trim() || null, mode: form.mode }, 'The group settings were saved.')}>Save</Button>
            ) : null}
          </header>
          <div className="grid gap-3 px-4 py-3 sm:grid-cols-2">
            <label className="block space-y-1 text-xs font-medium text-gray-700">
              <span>Name</span>
              <input className={fieldClass} value={form.name} disabled={!canWrite} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="e.g. Production chat" />
            </label>
            <label className="block space-y-1 text-xs font-medium text-gray-700">
              <span>Group key</span>
              <span className="flex overflow-hidden rounded-lg border border-gray-200 bg-gray-50">
                <input className="control-field-inset min-w-0 flex-1 bg-transparent px-3 font-mono text-gray-700 focus:outline-none" value={group.group_key} readOnly aria-describedby="route-group-key-fixed" />
                <span id="route-group-key-fixed" className="border-l border-gray-200 px-3 py-2 text-xs font-normal text-gray-500">Fixed</span>
              </span>
            </label>
            <label className="block space-y-1 text-xs font-medium text-gray-700">
              <span>Workload</span>
              <select className={fieldClass} value={form.mode} disabled={!canWrite || memberCount > 0} onChange={(event) => setForm({ ...form, mode: event.target.value })} aria-describedby="route-group-workload-help">
                {ROUTE_GROUP_MODE_OPTIONS.map((mode) => <option key={mode} value={mode}>{workloadLabel(mode)}</option>)}
              </select>
              <span id="route-group-workload-help" className="block font-normal text-gray-500">
                {memberCount > 0 ? 'Fixed while the group has models.' : 'Only models with this workload can join the group.'}
              </span>
            </label>
          </div>
        </section>

        <SettingsRow
          title="Accept requests"
          description={<>When off, requests to <code className="font-mono">{group.group_key}</code> are rejected. Routing and models are kept.</>}
          action={(
            <ToggleSwitch checked={group.enabled} disabled={!canWrite || busy !== null} activeColor="rgb(var(--brand-primary))" aria-label="Accept requests"
              onCheckedChange={(enabled) => (enabled
                ? void update('enabled', { enabled: true }, 'The group accepts requests again.')
                : setConfirm('disable'))} />
          )}
        />

        {access ? (
          <SettingsRow
            title="Sharing"
            description={`${sharing ?? 'Private'}.${owner}`}
            action={access.capabilities.manage_access || isPlatformAdmin
              ? <Button size="sm" variant="outline" aria-expanded={showAccess} onClick={() => setShowAccess((open) => !open)}>{showAccess ? 'Close' : 'Manage access'}</Button>
              : undefined}
          >
            {showAccess ? (
              <div className="border-t border-gray-100 px-4 py-3">
                <ManagedAssetAccessPanel
                  access={access}
                  assetLabel="Model group"
                  teamOptions={audiences.teamOptions}
                  organizationOptions={audiences.organizationOptions}
                  allowPublic={isPlatformAdmin}
                  audiencesLoading={audiences.loading}
                  audiencesError={audiences.error}
                  onRetryAudiences={audiences.refetch}
                  onSaved={onSaved}
                />
              </div>
            ) : null}
          </SettingsRow>
        ) : null}

        {canDelete ? (
          <SettingsRow
            tone="danger"
            title="Delete group"
            description={<>Removes the group, its routing history and prompt link. Clients calling <code className="font-mono">{group.group_key}</code> will get errors.</>}
            action={<Button size="sm" variant="danger-outline" onClick={() => setConfirm('delete')}>Delete group</Button>}
          />
        ) : null}
      </div>

      <ConfirmDialog
        open={confirm === 'disable'}
        title="Stop accepting requests"
        description={`Requests to ${group.group_key} will be rejected until you turn this on again. Routing and models are kept.`}
        confirmLabel="Stop requests"
        destructive
        confirming={busy === 'enabled'}
        onConfirm={() => void update('enabled', { enabled: false }, 'The group no longer accepts requests.')}
        onClose={() => { if (busy !== 'enabled') setConfirm(null); }}
      />
      <ConfirmDialog
        open={confirm === 'delete'}
        title="Delete model group"
        description={`Delete "${group.group_key}"? This removes all members, policy history, and prompt bindings for this group.`}
        confirmLabel="Delete Group"
        destructive
        confirming={busy === 'delete'}
        onConfirm={() => void deleteGroup()}
        onClose={() => { if (busy !== 'delete') setConfirm(null); }}
      />
    </>
  );
}
