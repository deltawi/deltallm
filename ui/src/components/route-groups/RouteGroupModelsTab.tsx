import { useMemo, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import DeploymentSearchSelect from '../DeploymentSearchSelect';
import { useToast } from '../ToastProvider';
import { routeGroups, type RouteGroup, type RouteGroupMemberDetail, type RoutePolicy } from '../../lib/api';
import { modelDetailPath } from '../../lib/modelRoutes';
import { routeGroupMutationOutcome } from '../../lib/routeGroups';
import { memberLabels, memberTrafficShares, type TrafficShare } from '../../lib/routeGroupRouting';
import { tokenPricePerMillion } from '../../lib/routeGroupSnippets';
import { useDeploymentCandidates } from '../../lib/useDeploymentCandidates';
import type { useRouteGroupMutationScope } from '../../lib/useRouteGroupMutationScope';
import { HealthText, ModelCell } from './RouteGroupModelCell';

type MutationScope = ReturnType<typeof useRouteGroupMutationScope>;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function TrafficCell({ share }: { share: TrafficShare | undefined }) {
  if (!share || share.kind === 'varies') return <span className="text-xs text-gray-500">Set in advanced routing</span>;
  if (share.kind === 'rank') {
    return <span className="text-xs font-medium text-gray-700">{share.rank === 0 ? 'Primary' : `Backup ${share.rank}`}</span>;
  }
  if (share.kind === 'off') {
    const text = share.reason === 'paused' ? 'Paused' : share.reason === 'unused' ? 'Not used' : '0%';
    return <span className="text-xs text-gray-500">{text}{share.reason === 'unhealthy' ? ' while unhealthy' : ''}</span>;
  }
  return (
    <span className="flex items-center gap-2">
      <span className="w-9 text-xs font-medium tabular-nums text-gray-700">{share.value}%</span>
      <span aria-hidden="true" className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-100">
        <span className="block h-full rounded-full bg-brand-primary" style={{ width: `${share.value}%` }} />
      </span>
    </span>
  );
}

interface RouteGroupModelsTabProps {
  group: RouteGroup;
  members: RouteGroupMemberDetail[];
  policy: RoutePolicy | null;
  canWrite: boolean;
  mutations: MutationScope;
  /** Changes each time another tab asks to add a model; the add form then opens. */
  addRequest?: number;
  onChanged: () => void;
  onOpenRouting: () => void;
}

export default function RouteGroupModelsTab({
  group, members, policy, canWrite, mutations, addRequest = 0, onChanged, onOpenRouting,
}: RouteGroupModelsTabProps) {
  const { pushToast } = useToast();
  const [adding, setAdding] = useState(addRequest > 0 && canWrite);
  const [syncedRequest, setSyncedRequest] = useState(addRequest);
  if (syncedRequest !== addRequest) {
    setSyncedRequest(addRequest);
    if (canWrite) setAdding(true);
  }
  const [selectedId, setSelectedId] = useState('');
  const [saving, setSaving] = useState(false);
  const [removeId, setRemoveId] = useState<string | null>(null);
  const memberIds = useMemo(() => members.map((member) => member.deployment_id), [members]);
  const candidates = useDeploymentCandidates(group.mode, adding, memberIds);
  const labels = memberLabels(members);
  const shares = memberTrafficShares(policy?.policy_json ?? null, group.routing_strategy, members);
  const listsMembers = Array.isArray(policy?.policy_json?.members);
  const hasUnused = Object.values(shares).some((share) => share.kind === 'off' && share.reason === 'unused');
  const selected = candidates.options.find((option) => option.deployment_id === selectedId) ?? null;

  const closeAdd = () => { setAdding(false); setSelectedId(''); candidates.reset(); };

  const addMember = async () => {
    if (!canWrite || !selectedId) return;
    const operation = mutations.begin();
    if (!operation) return;
    setSaving(true);
    try {
      const result = await routeGroups.upsertMember(group.route_group_id, { deployment_id: selectedId, enabled: true }, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const name = selected?.model_name || selectedId;
      const outcome = routeGroupMutationOutcome(
        listsMembers ? `${name} was added. Open Routing to send traffic to it.` : `${name} was added and now gets traffic.`,
        result.warnings,
      );
      pushToast({ tone: outcome.tone, title: 'Model added', message: outcome.message });
      closeAdd();
      onChanged();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Could not add the model', message: errorMessage(error, 'Failed to add the model to this group.') });
    } finally {
      if (mutations.finish(operation)) setSaving(false);
    }
  };

  const removeMember = async () => {
    if (!canWrite || !removeId) return;
    const operation = mutations.begin();
    if (!operation) return;
    setSaving(true);
    try {
      const result = await routeGroups.removeMember(group.route_group_id, removeId, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      const outcome = routeGroupMutationOutcome(`${labels[removeId] ?? removeId} was removed from the group.`, result.warnings);
      pushToast({ tone: outcome.tone, title: 'Model removed', message: outcome.message });
      setRemoveId(null);
      onChanged();
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({ tone: 'error', title: 'Could not remove the model', message: errorMessage(error, 'Failed to remove the model.') });
    } finally {
      if (mutations.finish(operation)) setSaving(false);
    }
  };

  return (
    <section aria-labelledby="route-group-models-title" className="max-w-4xl overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div>
          <h2 id="route-group-models-title" className="text-sm font-semibold text-gray-900">Models in this group</h2>
          <p className="mt-0.5 text-xs text-gray-500">Requests to <code className="font-mono">{group.group_key}</code> are answered by these models.</p>
        </div>
        {canWrite && !adding ? <Button size="sm" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" aria-hidden="true" />Add model</Button> : null}
      </header>

      {adding ? (
        <div className="space-y-3 border-b border-gray-100 bg-gray-50 px-4 py-3">
          <DeploymentSearchSelect
            search={candidates.search}
            onSearchChange={candidates.setSearch}
            options={candidates.options}
            loading={candidates.loading}
            selectedDeploymentId={selectedId}
            onSelect={(option) => setSelectedId(option.deployment_id)}
            searchPlaceholder="Search models by name, provider or ID"
            helperText={`Only models that support ${group.mode.replace(/_/g, ' ')} are shown.`}
            emptyText={candidates.error ? 'Models could not be loaded. Try the search again.' : 'No more models match.'}
          />
          {selected ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2">
              <ModelCell name={selected.model_name || selected.deployment_id} provider={selected.provider} deploymentId={selected.deployment_id} />
              <span className="flex items-center gap-3 text-xs text-gray-500">
                {selected.healthy != null ? <HealthText healthy={selected.healthy} /> : null}
                {tokenPricePerMillion(selected.model_info) ? <span>{tokenPricePerMillion(selected.model_info)} per 1M tokens</span> : null}
              </span>
            </div>
          ) : null}
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={!selectedId || saving} loading={saving} onClick={() => void addMember()}>Add to group</Button>
            <Button size="sm" variant="ghost" disabled={saving} onClick={closeAdd}>Cancel</Button>
          </div>
        </div>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead>
            <tr className="border-b border-gray-100 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              <th scope="col" className="px-4 py-2">Model</th>
              <th scope="col" className="px-4 py-2">Health</th>
              <th scope="col" className="px-4 py-2">Traffic</th>
              <th scope="col" className="w-12 px-4 py-2"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {members.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-10 text-center text-sm text-gray-500">
                  No models yet. {canWrite ? 'Add a model so this group can answer requests.' : 'Ask the owner to add a model.'}
                </td>
              </tr>
            ) : members.map((member) => (
              <tr key={member.deployment_id} className="border-b border-gray-100 last:border-0">
                <td className="px-4 py-2.5">
                  <Link to={modelDetailPath(member.deployment_id)} className="block rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary">
                    <ModelCell name={member.model_name || member.deployment_id} provider={member.provider} deploymentId={member.deployment_id} muted={!member.enabled} />
                  </Link>
                </td>
                <td className="px-4 py-2.5"><HealthText healthy={member.healthy} enabled={member.enabled} /></td>
                <td className="px-4 py-2.5"><TrafficCell share={shares[member.deployment_id]} /></td>
                <td className="px-4 py-2.5 text-right">
                  {canWrite ? (
                    <button
                      type="button"
                      aria-label={`Remove ${labels[member.deployment_id]}`}
                      title="Remove from group"
                      onClick={() => setRemoveId(member.deployment_id)}
                      className="rounded-md border border-gray-200 p-1 text-gray-500 hover:border-red-200 hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {members.length > 0 ? (
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 bg-gray-50 px-4 py-2.5 text-xs text-gray-500">
          <span>{hasUnused ? 'Models marked “Not used” get no traffic until you add them in Routing.' : 'Traffic follows the Routing tab.'}</span>
          <Button size="sm" variant="link" onClick={onOpenRouting}>Change routing</Button>
        </footer>
      ) : null}

      <ConfirmDialog
        open={removeId !== null}
        title="Remove model from group"
        description={removeId ? `Remove ${labels[removeId] ?? removeId} from ${group.group_key}? Requests stop going to it. The model itself is not deleted.` : ''}
        confirmLabel="Remove model"
        destructive
        confirming={saving}
        onConfirm={() => void removeMember()}
        onClose={() => { if (!saving) setRemoveId(null); }}
      />
    </section>
  );
}
