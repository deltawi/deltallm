import { Plus, Trash2 } from 'lucide-react';
import Card from '../Card';
import DeploymentSearchSelect from '../DeploymentSearchSelect';
import type { RouteGroupMember } from '../../lib/api';

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
  members: RouteGroupMember[];
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
  const selectedCandidate = candidateDeployments.find((candidate) => candidate.deployment_id === memberForm.deployment_id);

  return (
    <Card title="2. Members">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Start with the deployments that should serve this group</h4>
          <p className="mt-1 text-xs text-slate-500">Selecting a deployment is the only required action here. Weight, priority, and manual entry are for advanced routing setups.</p>
        </div>
        <div className="grid grid-cols-1 gap-3">
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
          <details className="rounded-xl border border-slate-200 px-3 py-3">
            <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900">Advanced member options</summary>
            <div className="mt-4 space-y-3">
              <button type="button" onClick={onToggleManualEntry} className="text-xs text-gray-500 hover:text-gray-700 w-fit">
                {manualMemberEntry ? 'Hide manual deployment ID entry' : 'Use manual deployment ID entry'}
              </button>
              {manualMemberEntry && (
                <label className="space-y-1 block">
                  <span className="text-xs font-medium text-gray-600">Manual Deployment ID</span>
                  <input
                    value={memberForm.deployment_id}
                    onChange={(event) => onMemberFormChange({ ...memberForm, deployment_id: event.target.value })}
                    placeholder="deployment_id"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
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
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <span className="block text-[11px] text-gray-500">Used mainly for weighted traffic splits.</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-gray-600">Priority</span>
                  <input
                    value={memberForm.priority}
                    onChange={(event) => onMemberFormChange({ ...memberForm, priority: event.target.value })}
                    placeholder="e.g. 1"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <span className="block text-[11px] text-gray-500">Used mainly for fallback order.</span>
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
                  <span className="block text-xs text-gray-500">Turn this off to keep the member attached but temporarily out of selection.</span>
                </span>
              </label>
            </div>
          </details>
          <button
            type="button"
            onClick={onAddMember}
            disabled={addingMember}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            <Plus className="w-4 h-4" />
            {addingMember ? 'Adding...' : 'Add Member'}
          </button>
        </div>
        <div className="space-y-2">
          {members.length === 0 && (
            <div className="rounded-xl border border-dashed border-slate-300 px-3 py-4 text-sm text-slate-500">
              No members yet. Add at least one healthy deployment before enabling live traffic.
            </div>
          )}
          {members.map((member) => (
            <div key={member.membership_id} className="flex items-center justify-between gap-2 text-sm border border-gray-100 rounded-xl px-3 py-3">
              <div>
                <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{member.deployment_id}</code>
                <div className="text-xs text-gray-500 mt-1">
                  Weight: {member.weight ?? 'default'} · Priority: {member.priority ?? 'default'}
                </div>
                <div className="text-xs mt-1">
                  <span className={`px-2 py-0.5 rounded ${member.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>
                    {member.enabled ? 'Eligible' : 'Paused'}
                  </span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => onRequestRemoveMember(member.deployment_id)}
                className="p-1.5 hover:bg-red-50 rounded-lg"
                aria-label={`Remove ${member.deployment_id}`}
              >
                <Trash2 className="w-4 h-4 text-red-500" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
