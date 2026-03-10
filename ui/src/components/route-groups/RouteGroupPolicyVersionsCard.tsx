import { RotateCcw } from 'lucide-react';
import Card from '../Card';
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
    <Card title="History and Rollback">
      <div className="space-y-3">
        <p className="text-xs text-slate-500">Use rollback only when you need to restore a previously published policy. This is a maintenance view, not part of the first-time setup path.</p>
        {hasError && (
          <div className="text-sm text-red-700 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
            Failed to load policy history.
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={selectedRollbackVersion ?? ''}
            onChange={(event) => onRollbackVersionChange(event.target.value ? Number(event.target.value) : null)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Select version to rollback</option>
            {canRollbackVersions.map((policy) => (
              <option key={policy.route_policy_id} value={policy.version}>
                v{policy.version} ({policy.status})
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={onRollback}
            disabled={!selectedRollbackVersion || isPolicyBusy}
            className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-50 flex items-center gap-2"
          >
            <RotateCcw className="w-4 h-4" />
            {policyAction === 'rollback' ? 'Rolling back...' : 'Rollback'}
          </button>
        </div>
        {loading && <div className="text-sm text-gray-500">Loading policy versions...</div>}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs text-gray-500">
                <th className="text-left py-2">Version</th>
                <th className="text-left py-2">Status</th>
                <th className="text-left py-2">Published By</th>
                <th className="text-left py-2">Published At</th>
              </tr>
            </thead>
            <tbody>
              {policies.map((policy) => (
                <tr key={policy.route_policy_id} className="border-b border-gray-50">
                  <td className="py-2 font-medium">v{policy.version}</td>
                  <td className="py-2">
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        policy.status === 'published'
                          ? 'bg-green-100 text-green-700'
                          : policy.status === 'draft'
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-100 text-gray-700'
                      }`}
                    >
                      {policy.status}
                    </span>
                  </td>
                  <td className="py-2 text-gray-600">{policy.published_by || '—'}</td>
                  <td className="py-2 text-gray-600">
                    {policy.published_at ? new Date(policy.published_at).toLocaleString() : '—'}
                  </td>
                </tr>
              ))}
              {policies.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-6 text-center text-gray-400">
                    No policy versions yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
