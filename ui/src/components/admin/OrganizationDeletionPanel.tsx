import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, RotateCcw, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import ConfirmDialog from '../ConfirmDialog';
import { useToast } from '../ToastProvider';
import {
  organizationDeletion,
  type OrganizationDeletionJob,
  type OrganizationDeletionPlan,
} from '../../lib/organizationDeletion';


type Props = {
  organizationId: string;
  organizationName: string;
};

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'restored']);

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

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function recoveryLabel(value: string | null): string {
  if (!value) return 'Not scheduled';
  return new Date(value).toLocaleString();
}

function ImpactSummary({ plan }: { plan: OrganizationDeletionPlan }) {
  const counts = plan.counts;
  const removalRows = [
    ['Teams', counts.teams],
    ['API keys', counts.api_keys],
    ['Service accounts', counts.service_accounts],
    ['Memberships', counts.organization_memberships + counts.team_memberships],
    ['Pending invitations', counts.pending_invitations],
    ['Pending approvals', counts.pending_mcp_approvals],
    ['Policy and asset bindings', counts.scope_bindings],
    ['Owned MCP servers', counts.owned_mcp_servers],
    ['Owned prompt templates', counts.owned_prompt_templates],
    ['Owned route groups', counts.owned_route_groups],
    ['Prompt render logs', counts.prompt_render_logs],
    ['Conflicting sensitive records', counts.conflicting_sensitive_records],
    ['Unattributed sensitive records', counts.unattributed_sensitive_records],
    ['Batch records missing ownership', counts.unresolved_batch_ownership_records],
  ] as const;
  return (
    <div className="grid grid-cols-2 gap-2 rounded-lg border border-red-100 bg-red-50/60 p-3">
      {removalRows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between gap-2 text-xs">
          <span className="text-gray-600">{label}</span>
          <span className="font-semibold text-gray-900">{value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

function ProgressSummary({ job }: { job: OrganizationDeletionJob }) {
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

export default function OrganizationDeletionPanel({
  organizationId,
  organizationName,
}: Props) {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const operationController = useRef<AbortController | null>(null);
  const [plan, setPlan] = useState<OrganizationDeletionPlan | null>(null);
  const [job, setJob] = useState<OrganizationDeletionJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [confirmationName, setConfirmationName] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState('');

  const loadPlan = useCallback(async (signal: AbortSignal) => {
    const nextPlan = await organizationDeletion.plan(organizationId, signal);
    setPlan(nextPlan);
    if (!nextPlan.deletion_job_id) setJob(null);
    return nextPlan;
  }, [organizationId]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void loadPlan(controller.signal)
      .then(async (nextPlan) => {
        if (nextPlan.deletion_job_id) {
          setJob(await organizationDeletion.job(
            organizationId,
            nextPlan.deletion_job_id,
            controller.signal,
          ));
        }
      })
      .catch((nextError: unknown) => {
        if (!controller.signal.aborted) {
          setError(errorMessage(nextError, 'Unable to load deletion status.'));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [loadPlan, organizationId]);

  useEffect(() => {
    if (!job || TERMINAL_STATUSES.has(job.status)) return undefined;
    let timeoutId: number | undefined;
    const controller = new AbortController();
    const schedule = (delay: number) => {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      if (!document.hidden) timeoutId = window.setTimeout(() => void poll(), delay);
    };
    const poll = async () => {
      timeoutId = undefined;
      if (document.hidden) return;
      try {
        const nextJob = await organizationDeletion.job(
          organizationId,
          job.deletion_job_id,
          controller.signal,
        );
        setJob(nextJob);
        if (nextJob.status === 'completed') {
          pushToast({ tone: 'success', title: 'Organization deleted', message: `${organizationName} was permanently deleted.` });
          navigate('/organizations', { replace: true });
          return;
        }
        if (!TERMINAL_STATUSES.has(nextJob.status)) schedule(3000);
      } catch (nextError: unknown) {
        if (!controller.signal.aborted) {
          setError(errorMessage(nextError, 'Unable to refresh deletion status.'));
          schedule(5000);
        }
      }
    };
    const handleVisibilityChange = () => {
      if (document.hidden) {
        if (timeoutId !== undefined) window.clearTimeout(timeoutId);
        timeoutId = undefined;
      } else {
        void poll();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    schedule(3000);
    return () => {
      controller.abort();
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [job, navigate, organizationId, organizationName, pushToast]);

  useEffect(() => () => operationController.current?.abort(), []);

  const openDelete = () => {
    setConfirmationName('');
    setAcknowledged(false);
    setIdempotencyKey(crypto.randomUUID());
    setError(null);
    setDeleteOpen(true);
  };

  const requestDeletion = async () => {
    if (!plan) return;
    operationController.current?.abort();
    const controller = new AbortController();
    operationController.current = controller;
    setWorking(true);
    setError(null);
    try {
      const nextJob = await organizationDeletion.request(
        organizationId,
        {
          confirmation_name: confirmationName,
          plan_token: plan.plan_token,
          acknowledge_running_work_cancellation: acknowledged,
          options: {
            owned_mcp_servers: 'delete',
            owned_prompt_templates: 'delete',
            owned_route_groups: 'delete',
          },
        },
        idempotencyKey,
        controller.signal,
      );
      setJob(nextJob);
      setDeleteOpen(false);
      pushToast({ tone: 'info', title: 'Deletion scheduled', message: `Access is revoked now. Permanent deletion is scheduled after ${plan.recovery_window_hours} hours.` });
    } catch (nextError: unknown) {
      if (!controller.signal.aborted) setError(errorMessage(nextError, 'Unable to schedule deletion.'));
    } finally {
      if (!controller.signal.aborted) setWorking(false);
    }
  };

  const runJobAction = async (action: 'restore' | 'retry') => {
    if (!job) return;
    operationController.current?.abort();
    const controller = new AbortController();
    operationController.current = controller;
    setWorking(true);
    setError(null);
    try {
      const nextJob = action === 'restore'
        ? await organizationDeletion.restore(organizationId, job.deletion_job_id, controller.signal)
        : await organizationDeletion.retry(organizationId, job.deletion_job_id, controller.signal);
      setJob(nextJob);
      setRestoreOpen(false);
      if (action === 'restore') {
        await loadPlan(controller.signal);
        pushToast({ tone: 'success', title: 'Organization restored', message: 'Access is active again. Cancelled work is not restarted.' });
      } else {
        pushToast({ tone: 'info', title: 'Deletion retry scheduled', message: 'The worker will resume from the last safe phase.' });
      }
    } catch (nextError: unknown) {
      if (!controller.signal.aborted) setError(errorMessage(nextError, `Unable to ${action} deletion.`));
    } finally {
      if (!controller.signal.aborted) setWorking(false);
    }
  };

  if (loading) {
    return <div className="rounded-xl border border-gray-200 bg-white p-4 text-xs text-gray-500">Loading deletion controls…</div>;
  }

  return (
    <>
      <section className="rounded-xl border border-red-200 bg-white p-5" aria-labelledby="organization-danger-zone">
        <div className="flex items-start gap-3">
          <div className="rounded-lg bg-red-50 p-2 text-red-600"><Trash2 className="h-4 w-4" /></div>
          <div className="min-w-0 flex-1">
            <h3 id="organization-danger-zone" className="text-sm font-semibold text-gray-900">Danger zone</h3>
            <p className="mt-1 text-xs leading-relaxed text-gray-600">
              Deletion revokes access immediately, cancels pending work, and permanently removes tenant configuration after the recovery window.
            </p>
          </div>
        </div>

        {error && <p className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</p>}
        {plan && !plan.requests_enabled && (
          <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900">
            <p className="font-semibold">Organization deletion is not enabled yet.</p>
            <p className="mt-1">
              An operator must finish the lifecycle-aware fleet rollout before new deletion requests can be scheduled.
            </p>
          </div>
        )}
        {plan && plan.blocking_dependencies.length > 0 && (
          <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
            <p className="font-semibold">Deletion is blocked until dependencies are resolved.</p>
            <p className="mt-1">
              Transfer or unbind {plan.counts.external_mcp_dependencies.toLocaleString()} MCP reference(s) and {plan.counts.external_prompt_dependencies.toLocaleString()} prompt reference(s), then refresh this page.
            </p>
            {plan.counts.external_route_group_dependencies > 0 && (
              <p className="mt-1">
                Transfer or unbind {plan.counts.external_route_group_dependencies.toLocaleString()} route-group reference(s).
              </p>
            )}
            {plan.counts.conflicting_sensitive_records > 0 && (
              <p className="mt-1">
                Resolve {plan.counts.conflicting_sensitive_records.toLocaleString()} sensitive record(s) with contradictory organization claims.
              </p>
            )}
            {plan.counts.unattributed_sensitive_records > 0 && (
              <p className="mt-1">
                Classify {plan.counts.unattributed_sensitive_records.toLocaleString()} legacy sensitive record(s) without durable organization ownership.
              </p>
            )}
            {plan.counts.unresolved_batch_ownership_records > 0 && (
              <p className="mt-1">
                Normalize {plan.counts.unresolved_batch_ownership_records.toLocaleString()} legacy batch record(s) before deletion.
              </p>
            )}
          </div>
        )}
        {job && job.status !== 'restored' ? (
          <div className="mt-4 space-y-3">
            <ProgressSummary job={job} />
            <p className="text-[11px] text-gray-500">Permanent deletion no earlier than {recoveryLabel(job.not_before_at)}.</p>
            <div className="flex flex-wrap gap-2">
              {job.restore_allowed && (
                <button type="button" onClick={() => setRestoreOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">
                  <RotateCcw className="h-3.5 w-3.5" /> Restore
                </button>
              )}
              {job.status === 'failed' && (
                <button type="button" onClick={() => void runJobAction('retry')} disabled={working} className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-50">
                  Retry cleanup
                </button>
              )}
            </div>
          </div>
        ) : (
          <button type="button" onClick={openDelete} disabled={!plan?.can_request} className="mt-4 w-full rounded-lg border border-red-300 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
            Delete organization
          </button>
        )}
      </section>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete organization"
        description={`Type “${organizationName}” and acknowledge cancellation to schedule deletion.`}
        confirmLabel="Schedule deletion"
        destructive
        confirming={working}
        confirmDisabled={!plan?.can_request || confirmationName !== organizationName || !acknowledged}
        onConfirm={() => void requestDeletion()}
        onClose={() => setDeleteOpen(false)}
      >
        {plan && <ImpactSummary plan={plan} />}
        <div>
          <label htmlFor="organization-delete-confirmation" className="mb-1 block text-xs font-medium text-gray-700">Organization name</label>
          <input id="organization-delete-confirmation" value={confirmationName} onChange={(event) => setConfirmationName(event.target.value)} autoComplete="off" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-red-500 focus:outline-none focus:ring-2 focus:ring-red-200" />
        </div>
        <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} className="mt-0.5" />
          <span>I understand active batches will be cancelled and restored organizations do not restart cancelled work.</span>
        </label>
        {plan && (
          <p className="text-xs text-gray-500">
            Spend ({plan.counts.retained_spend_events.toLocaleString()}), audit ({plan.counts.retained_audit_events.toLocaleString()}), terminal batch ({plan.counts.retained_batch_jobs.toLocaleString()}), and batch file ({plan.counts.retained_batch_files.toLocaleString()}) records remain under their existing retention periods.
          </p>
        )}
      </ConfirmDialog>

      <ConfirmDialog
        open={restoreOpen}
        title="Restore organization"
        description="Restore access before irreversible cleanup begins? Cancelled work will remain cancelled."
        confirmLabel="Restore organization"
        confirming={working}
        onConfirm={() => void runJobAction('restore')}
        onClose={() => setRestoreOpen(false)}
      />
    </>
  );
}
