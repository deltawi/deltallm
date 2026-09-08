import { AlertTriangle, Loader2 } from 'lucide-react';

import type { OrganizationDeletionJob } from '../../lib/organizationDeletion';

const PHASE_LABELS: Record<string, string> = {
  cancel_pending: 'Cancelling pending invitations and approvals',
  cancel_batches: 'Requesting batch cancellation',
  wait_for_batches: 'Recovery window and batch shutdown',
  resolve_owned_assets: 'Removing organization-owned assets',
  purge_sensitive_history: 'Removing sensitive prompt and approval history',
  remove_scoped_access: 'Removing organization-owned access policies',
  revoke_credentials: 'Revoking API keys and service accounts',
  remove_tenant_state: 'Removing teams, memberships, and policies',
  finalize: 'Finalizing permanent deletion',
  completed: 'Deletion complete',
  restored: 'Organization restored',
};

export default function OrganizationDeletionProgress({ job }: { job: OrganizationDeletionJob }) {
  const numericProgress = Object.entries(job.progress).filter(
    (entry): entry is [string, number] => typeof entry[1] === 'number',
  );
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
      <div className="flex items-start gap-2">
        {job.status === 'processing' ? (
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-amber-600" />
        ) : (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
        )}
        <div className="min-w-0">
          <p className="text-xs font-semibold text-amber-900">
            {PHASE_LABELS[job.phase] || job.phase}
          </p>
          <p className="mt-1 text-[11px] text-amber-800">
            Status: {job.status} · attempt {job.attempt_count} of {job.max_attempts}
          </p>
          {numericProgress.length > 0 && (
            <p className="mt-1 text-[11px] text-amber-700">
              {numericProgress.map(([key, value]) => `${key.replaceAll('_', ' ')}: ${value}`).join(' · ')}
            </p>
          )}
          {job.last_error_detail && (
            <p className="mt-1 text-[11px] text-red-700">{job.last_error_detail}</p>
          )}
        </div>
      </div>
    </div>
  );
}
