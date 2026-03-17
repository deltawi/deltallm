import { RotateCcw } from 'lucide-react';
import type { RoutePolicy } from '../../lib/api';
import type { PolicyAction } from '../../lib/routeGroups';

interface RouteGroupPolicyVersionsCardProps {
  policies: RoutePolicy[];
  canRollbackVersions: RoutePolicy[];
  selectedRollbackVersion: number | null;
  loading: boolean;
  hasError: boolean;
  isPolicyBusy: boolean;
  policyAction: PolicyAction;
  onRollbackVersionChange: (next: number | null) => void;
  onRollback: () => void;
}

const STATUS_CHIP: Record<string, string> = {
  published: 'bg-emerald-100 text-emerald-700',
  draft:     'bg-blue-100 text-blue-700',
  archived:  'bg-gray-100 text-gray-500',
};

export default function RouteGroupPolicyVersionsCard({
  policies,
  canRollbackVersions,
  selectedRollbackVersion,
  loading,
  hasError,
  isPolicyBusy,
  policyAction,
  onRollbackVersionChange,
  onRollback,
}: RouteGroupPolicyVersionsCardProps) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Policy History</h3>
          <p className="mt-0.5 text-xs text-gray-500">Rollback restores a previous policy as the new published version.</p>
        </div>
        {canRollbackVersions.length > 0 && (
          <div className="flex shrink-0 items-center gap-2">
            <select
              value={selectedRollbackVersion ?? ''}
              onChange={(e) => onRollbackVersionChange(e.target.value ? Number(e.target.value) : null)}
              className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Select version to restore</option>
              {canRollbackVersions.map((p) => (
                <option key={p.route_policy_id} value={p.version}>
                  v{p.version} ({p.status})
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onRollback}
              disabled={!selectedRollbackVersion || isPolicyBusy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {policyAction === 'rollback' ? 'Restoring…' : 'Rollback'}
            </button>
          </div>
        )}
      </div>

      {hasError && (
        <div className="mb-3 rounded-xl border border-red-100 bg-red-50 px-3 py-2.5 text-sm text-red-700">
          Failed to load policy history.
        </div>
      )}

      {loading && (
        <div className="text-sm text-gray-400">Loading versions…</div>
      )}

      {!loading && policies.length === 0 && !hasError && (
        <div className="rounded-2xl border border-dashed border-gray-200 py-6 text-center text-sm text-gray-400">
          No policy versions yet. Publish a policy override to start tracking history.
        </div>
      )}

      {policies.length > 0 && (
        <div className="space-y-2">
          {policies.map((p) => (
            <div
              key={p.route_policy_id}
              className="flex items-center justify-between rounded-xl border border-gray-100 px-4 py-2.5"
            >
              <div className="flex items-center gap-3">
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_CHIP[p.status] || STATUS_CHIP.archived}`}>
                  {p.status}
                </span>
                <span className="text-sm font-medium text-gray-700">Version {p.version}</span>
                {p.published_by && (
                  <span className="hidden text-xs text-gray-400 sm:inline">by {p.published_by}</span>
                )}
                {p.published_at && (
                  <span className="hidden text-xs text-gray-400 md:inline">
                    {new Date(p.published_at).toLocaleDateString()}
                  </span>
                )}
              </div>
              {p.status !== 'published' && (
                <button
                  type="button"
                  onClick={() => {
                    onRollbackVersionChange(p.version);
                    onRollback();
                  }}
                  disabled={isPolicyBusy}
                  className="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-500 hover:bg-gray-50 disabled:opacity-50"
                >
                  Roll back
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
