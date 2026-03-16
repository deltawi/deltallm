import { Activity, ChevronDown, ChevronUp, Plus, ShieldAlert, Trash2, XCircle, CheckCircle2 } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import ProviderBadge from '../ProviderBadge';
import DeploymentSearchSelect from '../DeploymentSearchSelect';
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
  const [showAddForm, setShowAddForm] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const healthyCount = members.filter((m) => m.healthy === true).length;
  const missingCount = members.filter((m) => m.healthy == null).length;
  const totalWeight = members.reduce((s, m) => s + (m.weight ?? 0), 0);
  const selectedCandidate = candidateDeployments.find((c) => c.deployment_id === memberForm.deployment_id);

  return (
    <div className="space-y-4">
      {/* Summary row + Add button */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          {members.length} {members.length === 1 ? 'deployment' : 'deployments'}&nbsp;·&nbsp;
          <span className={healthyCount === members.length && members.length > 0 ? 'text-emerald-600 font-medium' : 'text-gray-500'}>
            {healthyCount} healthy
          </span>
          {missingCount > 0 && (
            <span className="ml-1 text-amber-600">&nbsp;·&nbsp;{missingCount} registry gap{missingCount > 1 ? 's' : ''}</span>
          )}
        </p>
        <button
          type="button"
          onClick={() => setShowAddForm((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700"
        >
          {showAddForm ? <><ChevronUp className="h-3.5 w-3.5" /> Cancel</> : <><Plus className="h-3.5 w-3.5" /> Add Deployment</>}
        </button>
      </div>

      {/* Inline add form */}
      {showAddForm && (
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
          <h4 className="mb-3 text-sm font-semibold text-blue-900">
            Add a deployment — must be compatible with <strong>{mode}</strong> traffic
          </h4>

          <DeploymentSearchSelect
            search={memberSearchInput}
            onSearchChange={onMemberSearchChange}
            options={candidateDeployments}
            loading={loadingCandidates}
            selectedDeploymentId={memberForm.deployment_id}
            onSelect={(option) => onMemberFormChange({ ...memberForm, deployment_id: option.deployment_id })}
            searchPlaceholder="Search by deployment, model, or provider…"
            helperText={`Showing deployments compatible with "${mode}" traffic.`}
            emptyText={hasCandidateError ? 'Failed to load candidates. Enter ID manually below.' : 'No compatible deployments found.'}
          />

          {selectedCandidate && (
            <div className="mt-2 rounded-lg border border-blue-100 bg-white px-3 py-2.5 text-sm text-blue-900">
              <div className="font-medium">{selectedCandidate.model_name || selectedCandidate.deployment_id}</div>
              <div className="mt-0.5 text-xs text-blue-700">
                {selectedCandidate.deployment_id}
                {selectedCandidate.provider ? ` · ${selectedCandidate.provider}` : ''}
                {selectedCandidate.mode ? ` · ${selectedCandidate.mode}` : ''}
              </div>
            </div>
          )}

          {/* Advanced member options */}
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="mt-3 flex items-center gap-1 text-xs font-medium text-blue-700 hover:text-blue-900"
          >
            {showAdvanced ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            Advanced options
          </button>

          {showAdvanced && (
            <div className="mt-3 space-y-3">
              {manualMemberEntry && (
                <label className="block space-y-1">
                  <span className="text-xs font-medium text-gray-700">Manual Deployment ID</span>
                  <input
                    value={memberForm.deployment_id}
                    onChange={(e) => onMemberFormChange({ ...memberForm, deployment_id: e.target.value })}
                    placeholder="deployment_id"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </label>
              )}
              <button
                type="button"
                onClick={onToggleManualEntry}
                className="text-xs text-blue-600 hover:underline"
              >
                {manualMemberEntry ? 'Hide manual ID entry' : 'Enter deployment ID manually'}
              </button>
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="text-xs font-medium text-gray-700">Weight</span>
                  <input
                    value={memberForm.weight}
                    onChange={(e) => onMemberFormChange({ ...memberForm, weight: e.target.value })}
                    placeholder="e.g. 10"
                    type="number"
                    min="0"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <span className="block text-[11px] text-gray-500">For weighted traffic splits.</span>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-medium text-gray-700">Priority</span>
                  <input
                    value={memberForm.priority}
                    onChange={(e) => onMemberFormChange({ ...memberForm, priority: e.target.value })}
                    placeholder="e.g. 1"
                    type="number"
                    min="0"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <span className="block text-[11px] text-gray-500">For ordered fallback.</span>
                </label>
              </div>
              <label className="flex items-start gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={memberForm.enabled}
                  onChange={(e) => onMemberFormChange({ ...memberForm, enabled: e.target.checked })}
                  className="mt-0.5 rounded border-gray-300"
                />
                <span>
                  Eligible for routing
                  <span className="block text-xs text-gray-500">Uncheck to keep attached but temporarily exclude from selection.</span>
                </span>
              </label>
            </div>
          )}

          <div className="mt-4 flex gap-2">
            <button
              type="button"
              onClick={onAddMember}
              disabled={addingMember || !memberForm.deployment_id.trim()}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {addingMember ? 'Adding…' : 'Add'}
            </button>
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Member list */}
      {members.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-gray-200 px-6 py-10 text-center">
          <div className="text-sm font-medium text-gray-400">No deployments attached yet</div>
          <div className="mt-1 text-xs text-gray-400">Add at least one healthy deployment before enabling live traffic.</div>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-gray-200">
          {/* Header */}
          <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 border-b border-gray-100 bg-gray-50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
            <div>Deployment</div>
            <div className="w-28 text-center">Provider</div>
            {totalWeight > 0 && <div className="w-28 text-center">Weight</div>}
            <div className="w-20 text-center">Status</div>
            <div className="w-8" />
          </div>

          {/* Rows */}
          {members.map((m, i) => {
            const weightPct = totalWeight > 0 ? Math.round(((m.weight ?? 0) / totalWeight) * 100) : 0;
            return (
              <div
                key={m.deployment_id}
                className={`grid grid-cols-[1fr_auto_auto_auto_auto] items-center gap-4 px-4 py-3 transition hover:bg-gray-50 ${i < members.length - 1 ? 'border-b border-gray-100' : ''}`}
                onClick={() => navigate(modelDetailPath(m.deployment_id))}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && navigate(modelDetailPath(m.deployment_id))}
              >
                {/* Name + ID */}
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-gray-900">
                    {m.model_name || m.deployment_id}
                  </div>
                  <code className="font-mono text-[11px] text-gray-400">{m.deployment_id}</code>
                  {!m.enabled && (
                    <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400">paused</span>
                  )}
                </div>

                {/* Provider */}
                <div className="flex w-28 justify-center">
                  <ProviderBadge provider={m.provider} />
                </div>

                {/* Weight bar */}
                {totalWeight > 0 && (
                  <div className="flex w-28 flex-col items-center gap-1">
                    <div className="h-1.5 w-16 overflow-hidden rounded-full bg-gray-100">
                      <div className="h-full rounded-full bg-blue-400 transition-all" style={{ width: `${weightPct}%` }} />
                    </div>
                    <span className="text-[11px] font-semibold text-gray-500">
                      {m.weight ?? 0} ({weightPct}%)
                    </span>
                  </div>
                )}

                {/* Health */}
                <div className="flex w-20 justify-center">
                  {m.healthy === true ? (
                    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-600">
                      <CheckCircle2 className="h-3.5 w-3.5" /> Healthy
                    </span>
                  ) : m.healthy === false ? (
                    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-red-500">
                      <XCircle className="h-3.5 w-3.5" /> Down
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-500">
                      <ShieldAlert className="h-3.5 w-3.5" /> Missing
                    </span>
                  )}
                </div>

                {/* Remove */}
                <div className="flex w-8 justify-end" onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => onRequestRemoveMember(m.deployment_id)}
                    className="rounded-lg p-1 text-gray-300 hover:bg-red-50 hover:text-red-400"
                    aria-label={`Remove ${m.deployment_id}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
