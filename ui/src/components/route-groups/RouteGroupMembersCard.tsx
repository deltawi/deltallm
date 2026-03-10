import { Activity, Plus, ShieldAlert, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import Card from '../Card';
import DataTable from '../DataTable';
import DeploymentSearchSelect from '../DeploymentSearchSelect';
import ProviderBadge from '../ProviderBadge';
import type { RouteGroupMemberDetail } from '../../lib/api';
import { modelDetailPath } from '../../lib/modelRoutes';

interface MemberFormValues {
  deployment_id: string;
  weight: string;
  priority: string;
  enabled: boolean;
}

interface CandidateDeployment {
  deployment_id: string;
  model_name?: string | null;
  provider?: string | null;
  mode?: string | null;
  healthy?: boolean;
}

interface RouteGroupMembersCardProps {
  mode: string;
  memberForm: MemberFormValues;
  manualMemberEntry: boolean;
  memberSearchInput: string;
  candidateDeployments: CandidateDeployment[];
  loadingCandidates: boolean;
  hasCandidateError: boolean;
  addingMember: boolean;
  members: RouteGroupMemberDetail[];
  onMemberFormChange: (next: MemberFormValues) => void;
  onToggleManualEntry: () => void;
  onMemberSearchChange: (value: string) => void;
  onAddMember: () => void;
  onRequestRemoveMember: (deploymentId: string) => void;
}

export default function RouteGroupMembersCard({
  mode,
  memberForm,
  manualMemberEntry,
  memberSearchInput,
  candidateDeployments,
  loadingCandidates,
  hasCandidateError,
  addingMember,
  members,
  onMemberFormChange,
  onToggleManualEntry,
  onMemberSearchChange,
  onAddMember,
  onRequestRemoveMember,
}: RouteGroupMembersCardProps) {
  const navigate = useNavigate();
  const selectedCandidate = candidateDeployments.find((candidate) => candidate.deployment_id === memberForm.deployment_id);
  const healthyMembers = members.filter((member) => member.healthy === true).length;
  const eligibleMembers = members.filter((member) => member.enabled).length;
  const unavailableMembers = members.filter((member) => member.healthy == null).length;
  const columns = [
    {
      key: 'model',
      header: 'Model',
      render: (member: RouteGroupMemberDetail) => (
        <div className="min-w-0">
          {member.model_name ? (
            <Link to={modelDetailPath(member.deployment_id)} className="font-medium text-slate-900 hover:text-blue-700">
              {member.model_name}
            </Link>
          ) : (
            <span className="font-medium text-slate-900">{member.deployment_id}</span>
          )}
          <div className="mt-1">
            <ProviderBadge provider={member.provider} />
          </div>
        </div>
      ),
    },
    {
      key: 'deployment_id',
      header: 'Deployment ID',
      render: (member: RouteGroupMemberDetail) => (
        <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">{member.deployment_id}</code>
      ),
    },
    {
      key: 'health',
      header: 'Health',
      render: (member: RouteGroupMemberDetail) => (
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
            member.healthy === false
              ? 'bg-red-50 text-red-700'
              : member.healthy === null
                ? 'bg-amber-50 text-amber-700'
                : 'bg-green-50 text-green-700'
          }`}
        >
          {member.healthy === false ? (
            <>
              <ShieldAlert className="h-3.5 w-3.5" />
              Unhealthy
            </>
          ) : member.healthy === null ? (
            <>
              <ShieldAlert className="h-3.5 w-3.5" />
              Missing
            </>
          ) : (
            <>
              <Activity className="h-3.5 w-3.5" />
              Healthy
            </>
          )}
        </span>
      ),
    },
    {
      key: 'routing',
      header: 'Routing',
      render: (member: RouteGroupMemberDetail) => (
        <div className="flex flex-wrap gap-2">
          <span className={`rounded-full px-2 py-0.5 text-xs ${member.enabled ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-700'}`}>
            {member.enabled ? 'Eligible' : 'Paused'}
          </span>
          {member.weight !== null ? <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700">Weight {member.weight}</span> : null}
          {member.priority !== null ? <span className="rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700">Priority {member.priority}</span> : null}
        </div>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (member: RouteGroupMemberDetail) => (
        <div className="flex justify-end" onClick={(event) => event.stopPropagation()}>
          <button
            type="button"
            onClick={() => onRequestRemoveMember(member.deployment_id)}
            className="rounded-lg p-1.5 hover:bg-red-50"
            aria-label={`Remove ${member.deployment_id}`}
          >
            <Trash2 className="h-4 w-4 text-red-500" />
          </button>
        </div>
      ),
      className: 'w-px',
    },
  ];

  return (
    <Card title="Models">
      <div className="space-y-5">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">This group serves these deployments</h4>
          <p className="mt-1 text-xs text-slate-500">Check the current model inventory first. Add or tune members only when this group needs different capacity or routing behavior.</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Models</div>
            <div className="mt-1 text-lg font-semibold text-slate-900">{members.length}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Healthy</div>
            <div className="mt-1 text-lg font-semibold text-slate-900">{healthyMembers}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Eligible</div>
            <div className="mt-1 text-lg font-semibold text-slate-900">{eligibleMembers}</div>
          </div>
        </div>

        <div className="overflow-hidden rounded-xl border border-slate-200">
          <DataTable
            columns={columns}
            data={members}
            emptyMessage="No models are attached yet. Add at least one healthy deployment before enabling live traffic."
            onRowClick={(member) => navigate(modelDetailPath(member.deployment_id))}
          />
        </div>

        <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
          <div className="flex flex-col gap-1">
            <h5 className="text-sm font-semibold text-slate-900">Add a model</h5>
            <p className="text-xs text-slate-500">Only attach deployments compatible with this group&apos;s {mode} traffic. Weight and priority are optional routing controls.</p>
            {unavailableMembers > 0 ? (
              <div className="mt-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                {unavailableMembers === 1
                  ? 'One attached deployment is no longer present in the active registry.'
                  : `${unavailableMembers} attached deployments are no longer present in the active registry.`}
              </div>
            ) : null}
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3">
            <DeploymentSearchSelect
              search={memberSearchInput}
              onSearchChange={onMemberSearchChange}
              options={candidateDeployments}
              loading={loadingCandidates}
              selectedDeploymentId={memberForm.deployment_id}
              onSelect={(option) => onMemberFormChange({ ...memberForm, deployment_id: option.deployment_id })}
              searchPlaceholder="Search by deployment, model, or provider..."
              helperText={`Showing deployments compatible with "${mode}" traffic.`}
              emptyText={hasCandidateError ? 'Failed to load candidates. Enter deployment ID manually.' : 'No compatible deployments found.'}
            />
            {selectedCandidate && (
              <div className="rounded-xl border border-blue-100 bg-blue-50 px-3 py-3 text-sm text-blue-900">
                <div className="font-medium">{selectedCandidate.model_name || selectedCandidate.deployment_id}</div>
                <div className="mt-1 text-xs text-blue-800">
                  {selectedCandidate.deployment_id}
                  {selectedCandidate.provider ? ` · ${selectedCandidate.provider}` : ''}
                  {selectedCandidate.mode ? ` · ${selectedCandidate.mode}` : ''}
                </div>
              </div>
            )}
            <details className="rounded-xl border border-slate-200 bg-white px-3 py-3">
              <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900">Advanced member options</summary>
              <div className="mt-4 space-y-3">
                <button type="button" onClick={onToggleManualEntry} className="w-fit text-xs text-gray-500 hover:text-gray-700">
                  {manualMemberEntry ? 'Hide manual deployment ID entry' : 'Use manual deployment ID entry'}
                </button>
                {manualMemberEntry && (
                  <label className="block space-y-1">
                    <span className="text-xs font-medium text-gray-600">Manual Deployment ID</span>
                    <input
                      value={memberForm.deployment_id}
                      onChange={(event) => onMemberFormChange({ ...memberForm, deployment_id: event.target.value })}
                      placeholder="deployment_id"
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </label>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-gray-600">Weight</span>
                    <input
                      value={memberForm.weight}
                      onChange={(event) => onMemberFormChange({ ...memberForm, weight: event.target.value })}
                      placeholder="e.g. 10"
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <span className="block text-[11px] text-gray-500">Use this for weighted traffic splits.</span>
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs font-medium text-gray-600">Priority</span>
                    <input
                      value={memberForm.priority}
                      onChange={(event) => onMemberFormChange({ ...memberForm, priority: event.target.value })}
                      placeholder="e.g. 1"
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <span className="block text-[11px] text-gray-500">Use this for ordered fallback behavior.</span>
                  </label>
                </div>
                <label className="flex items-start gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={memberForm.enabled}
                    onChange={(event) => onMemberFormChange({ ...memberForm, enabled: event.target.checked })}
                    className="mt-0.5 rounded border-gray-300"
                  />
                  <span>
                    Eligible for routing
                    <span className="block text-xs text-gray-500">Turn this off to keep the model attached but temporarily out of selection.</span>
                  </span>
                </label>
              </div>
            </details>
            <button
              type="button"
              onClick={onAddMember}
              disabled={addingMember}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              <Plus className="w-4 h-4" />
              {addingMember ? 'Adding...' : 'Add Model'}
            </button>
          </div>
        </div>
      </div>
    </Card>
  );
}
