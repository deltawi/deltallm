import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Plus,
  Search,
  Server,
  ShieldAlert,
  Trash2,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
  const [showAddForm, setShowAddForm] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const healthyCount = members.filter((m) => m.healthy === true).length;
  const missingCount = members.filter((m) => m.healthy == null).length;
  const totalWeight = members.reduce((s, m) => s + (m.weight ?? 0), 0);
  const selectedCandidate = candidateDeployments.find((c) => c.deployment_id === memberForm.deployment_id);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          {members.length} {members.length === 1 ? 'deployment' : 'deployments'}&nbsp;·&nbsp;
          <span
            className={
              healthyCount === members.length && members.length > 0
                ? 'font-medium text-emerald-600'
                : 'text-gray-500'
            }
          >
            {healthyCount} healthy
          </span>
          {missingCount > 0 && (
            <span className="ml-1 text-amber-600">
              &nbsp;·&nbsp;{missingCount} registry gap{missingCount > 1 ? 's' : ''}
            </span>
          )}
        </p>
      </div>

      {showAddForm && (
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 shadow-sm">
          <h4 className="mb-3 text-sm font-semibold text-blue-900">
            Add a deployment — must be compatible with <strong>{mode}</strong> traffic
          </h4>

          {/* Search with icon */}
          <div className="relative mb-3">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 pointer-events-none" />
            <DeploymentSearchSelect
              search={memberSearchInput}
              onSearchChange={onMemberSearchChange}
              options={candidateDeployments}
              loading={loadingCandidates}
              selectedDeploymentId={memberForm.deployment_id}
              onSelect={(option) => onMemberFormChange({ ...memberForm, deployment_id: option.deployment_id })}
              searchPlaceholder="Search by deployment, model, or provider…"
              helperText={`Showing deployments compatible with "${mode}" traffic.`}
              emptyText={
                hasCandidateError
                  ? 'Failed to load candidates. Enter ID manually below.'
                  : 'No compatible deployments found.'
              }
              inputClassName="pl-9"
            />
          </div>

          {/* Selected candidate preview */}
          {selectedCandidate && (
            <div className="mb-3 rounded-lg border border-blue-100 bg-white px-4 py-3 shadow-sm flex items-center justify-between">
              <div>
                <div className="font-semibold text-slate-900">
                  {selectedCandidate.model_name || selectedCandidate.deployment_id}
                </div>
                <code className="mt-0.5 block font-mono text-xs text-slate-500">
                  {selectedCandidate.deployment_id}
                </code>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <ProviderBadge provider={selectedCandidate.provider} />
                {selectedCandidate.mode && (
                  <span className="rounded border border-slate-200 bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                    {selectedCandidate.mode}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Advanced options toggle */}
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="mb-1 flex items-center gap-1 text-xs font-medium text-blue-700 hover:text-blue-900 transition-colors"
          >
            {showAdvanced ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            Advanced options
          </button>

          {showAdvanced && (
            <div className="mt-3 space-y-3">
              <button
                type="button"
                onClick={onToggleManualEntry}
                className="text-xs text-blue-600 hover:underline"
              >
                {manualMemberEntry ? 'Hide manual ID entry' : 'Enter deployment ID manually'}
              </button>
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
                  <span className="block text-xs text-gray-500">
                    Uncheck to keep attached but temporarily exclude from selection.
                  </span>
                </span>
              </label>
            </div>
          )}

          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              onClick={onAddMember}
              disabled={addingMember || !memberForm.deployment_id.trim()}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {addingMember ? 'Adding…' : 'Add'}
            </button>
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-5 py-4">
          <h3 className="text-sm font-semibold text-gray-900">
            All Models {members.length > 0 && `(${members.length})`}
          </h3>
          <button
            type="button"
            onClick={() => setShowAddForm((v) => !v)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700"
          >
            {showAddForm ? (
              <><ChevronUp className="h-3.5 w-3.5" /> Cancel</>
            ) : (
              <><Plus className="h-3.5 w-3.5" /> Add Model</>
            )}
          </button>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100">
              <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Model</th>
              <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Provider</th>
              {totalWeight > 0 && (
                <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Weight</th>
              )}
              <th className="px-5 py-3 text-left text-xs font-semibold text-gray-500">Status</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody>
            {members.length === 0 ? (
              <tr>
                <td
                  colSpan={totalWeight > 0 ? 5 : 4}
                  className="px-5 py-12 text-center text-sm text-gray-400"
                >
                  No models yet.{' '}
                  <button
                    type="button"
                    onClick={() => setShowAddForm(true)}
                    className="text-blue-600 hover:underline"
                  >
                    Add the first one
                  </button>
                </td>
              </tr>
            ) : (
              members.map((m, index) => {
                const weightPct =
                  totalWeight > 0 ? Math.round(((m.weight ?? 0) / totalWeight) * 100) : 0;
                return (
                  <tr
                    key={m.deployment_id}
                    className={`group cursor-pointer hover:bg-blue-50/40 ${index < members.length - 1 ? 'border-b border-gray-100' : ''}`}
                    onClick={() => navigate(modelDetailPath(m.deployment_id))}
                  >
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-50">
                          <Server className="h-4 w-4 text-indigo-600" />
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-gray-900">
                            {m.model_name || m.deployment_id}
                          </p>
                          <code className="font-mono text-[10px] text-gray-400">{m.deployment_id}</code>
                          {!m.enabled && (
                            <span className="ml-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                              paused
                            </span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3.5">
                      <ProviderBadge provider={m.provider} />
                    </td>
                    {totalWeight > 0 && (
                      <td className="px-5 py-3.5">
                        <div className="flex flex-col gap-1">
                          <div className="h-1.5 w-full max-w-[120px] overflow-hidden rounded-full bg-gray-100">
                            <div
                              className="h-full rounded-full bg-blue-400"
                              style={{ width: `${weightPct}%` }}
                            />
                          </div>
                          <span className="text-xs text-gray-500">
                            {m.weight ?? 0} ({weightPct}%)
                          </span>
                        </div>
                      </td>
                    )}
                    <td className="px-5 py-3.5">
                      {m.healthy === true ? (
                        <span className="inline-flex items-center gap-1 text-sm font-medium text-emerald-600">
                          <CheckCircle2 className="h-4 w-4" /> Healthy
                        </span>
                      ) : m.healthy === false ? (
                        <span className="inline-flex items-center gap-1 text-sm font-medium text-red-600">
                          <XCircle className="h-4 w-4" /> Down
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-sm font-medium text-amber-500">
                          <ShieldAlert className="h-4 w-4" /> Missing
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3.5 text-right" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        onClick={() => onRequestRemoveMember(m.deployment_id)}
                        className="rounded-lg p-1.5 text-gray-300 opacity-0 transition-all hover:bg-red-50 hover:text-red-500 group-hover:opacity-100"
                        aria-label={`Remove ${m.deployment_id}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
