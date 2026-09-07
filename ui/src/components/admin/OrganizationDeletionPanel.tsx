import { useCallback, useEffect, useRef, useState } from 'react';
import { RotateCcw, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { useToast } from '../ToastProvider';
import {
  OrganizationDeletionExpediteDialog,
  OrganizationDeletionRequestDialog,
  OrganizationDeletionRestoreDialog,
} from './OrganizationDeletionDialogs';
import OrganizationDeletionProgress from './OrganizationDeletionProgress';
import {
  organizationDeletion,
  type OrganizationDeletionJob,
  type OrganizationDeletionPlan,
} from '../../lib/organizationDeletion';
import {
  organizationLifecycleTransitionForDeletionJob,
  type OrganizationLifecycleTransition,
} from '../../lib/organizationLifecycle';

type Props = {
  organizationId: string;
  organizationName: string;
  canManageLifecycle: boolean;
  canExpedite: boolean;
  onLifecycleChange?: (transition: OrganizationLifecycleTransition) => Promise<void>;
};

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'restored']);

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function recoveryLabel(value: string | null): string {
  if (!value) return 'Not scheduled';
  return new Date(value).toLocaleString();
}

export default function OrganizationDeletionPanel({
  organizationId,
  organizationName,
  canManageLifecycle,
  canExpedite,
  onLifecycleChange,
}: Props) {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const operationController = useRef<AbortController | null>(null);
  const pollController = useRef<AbortController | null>(null);
  const stateGeneration = useRef(0);
  const expediteAttempt = useRef<{ deletionJobId: string; idempotencyKey: string } | null>(null);
  const [plan, setPlan] = useState<OrganizationDeletionPlan | null>(null);
  const [job, setJob] = useState<OrganizationDeletionJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [expediteOpen, setExpediteOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [confirmationName, setConfirmationName] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [expediteConfirmationName, setExpediteConfirmationName] = useState('');
  const [expediteAcknowledged, setExpediteAcknowledged] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState('');

  const reconcileLifecycle = useCallback(async (nextJob: OrganizationDeletionJob) => {
    if (!onLifecycleChange) return;
    try {
      await onLifecycleChange(organizationLifecycleTransitionForDeletionJob(nextJob));
    } catch {
      // The lifecycle transition remains successful; the parent renders refresh recovery.
    }
  }, [onLifecycleChange]);

  const loadPlan = useCallback(
    (signal: AbortSignal) => organizationDeletion.plan(organizationId, signal),
    [organizationId],
  );

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++stateGeneration.current;
    setLoading(true);
    setError(null);
    void loadPlan(controller.signal)
      .then(async (nextPlan) => {
        if (controller.signal.aborted || generation !== stateGeneration.current) return;
        setPlan(nextPlan);
        if (nextPlan.deletion_job_id) {
          const nextJob = await organizationDeletion.job(
            organizationId,
            nextPlan.deletion_job_id,
            controller.signal,
          );
          if (controller.signal.aborted || generation !== stateGeneration.current) return;
          setJob(nextJob);
        } else setJob(null);
      })
      .catch((nextError: unknown) => {
        if (!controller.signal.aborted && generation === stateGeneration.current) {
          setError(errorMessage(nextError, 'Unable to load deletion status.'));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted && generation === stateGeneration.current) setLoading(false);
      });
    return () => controller.abort();
  }, [loadPlan, organizationId]);

  useEffect(() => {
    if (!job || TERMINAL_STATUSES.has(job.status) || working) return undefined;
    let timeoutId: number | undefined;
    const controller = new AbortController();
    pollController.current = controller;
    const generation = stateGeneration.current;
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
        if (controller.signal.aborted || generation !== stateGeneration.current) return;
        setJob(nextJob);
        if (nextJob.expedited_at) expediteAttempt.current = null;
        if (nextJob.phase !== job.phase || nextJob.status !== job.status) {
          await reconcileLifecycle(nextJob);
          if (controller.signal.aborted || generation !== stateGeneration.current) return;
        }
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
      if (pollController.current === controller) pollController.current = null;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [job, navigate, organizationId, organizationName, pushToast, reconcileLifecycle, working]);

  useEffect(() => () => {
    stateGeneration.current += 1;
    pollController.current?.abort();
    operationController.current?.abort();
  }, []);

  useEffect(() => {
    if (!job) {
      expediteAttempt.current = null;
      return;
    }
    if (
      expediteAttempt.current?.deletionJobId !== job.deletion_job_id
      || job.expedited_at
    ) {
      expediteAttempt.current = null;
    }
  }, [job]);

  const beginOperation = () => {
    operationController.current?.abort();
    pollController.current?.abort();
    const controller = new AbortController();
    operationController.current = controller;
    const generation = ++stateGeneration.current;
    setWorking(true);
    setError(null);
    return { controller, generation };
  };

  const operationIsCurrent = (controller: AbortController, generation: number) => (
    !controller.signal.aborted && generation === stateGeneration.current
  );

  const openDelete = () => {
    setConfirmationName('');
    setAcknowledged(false);
    setIdempotencyKey(crypto.randomUUID());
    setError(null);
    setDeleteOpen(true);
  };

  const requestDeletion = async () => {
    if (!plan) return;
    const { controller, generation } = beginOperation();
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
      if (!operationIsCurrent(controller, generation)) return;
      setJob(nextJob);
      setDeleteOpen(false);
      pushToast({ tone: 'info', title: 'Deletion scheduled', message: `Access is revoked now. Permanent deletion is scheduled after ${plan.recovery_window_hours} hours.` });
      void reconcileLifecycle(nextJob);
    } catch (nextError: unknown) {
      if (operationIsCurrent(controller, generation)) {
        setError(errorMessage(nextError, 'Unable to schedule deletion.'));
      }
    } finally {
      if (operationIsCurrent(controller, generation)) setWorking(false);
    }
  };

  const openExpedite = () => {
    setExpediteConfirmationName('');
    setExpediteAcknowledged(false);
    setError(null);
    setExpediteOpen(true);
  };

  const expediteDeletion = async () => {
    if (!job) return;
    const targetJobId = job.deletion_job_id;
    if (expediteAttempt.current?.deletionJobId !== targetJobId) {
      expediteAttempt.current = {
        deletionJobId: targetJobId,
        idempotencyKey: crypto.randomUUID(),
      };
    }
    const idempotencyKey = expediteAttempt.current.idempotencyKey;
    const { controller, generation } = beginOperation();
    try {
      const result = await organizationDeletion.expedite(
        organizationId,
        targetJobId,
        {
          confirmation_name: expediteConfirmationName,
          acknowledge_immediate_irreversible_deletion: expediteAcknowledged,
        },
        idempotencyKey,
        controller.signal,
      );
      if (!operationIsCurrent(controller, generation)) return;
      setJob(result);
      expediteAttempt.current = null;
      setExpediteOpen(false);
      pushToast({
        tone: 'info',
        title: 'Recovery window waived',
        message: result.idempotency_resolution === 'replayed'
          ? 'The earlier request was confirmed. Permanent cleanup will begin after active batches stop.'
          : 'Permanent cleanup will begin after active batches stop.',
      });
    } catch (nextError: unknown) {
      if (operationIsCurrent(controller, generation)) {
        setError(errorMessage(nextError, 'Unable to waive the recovery window.'));
      }
    } finally {
      if (operationIsCurrent(controller, generation)) setWorking(false);
    }
  };

  const runJobAction = async (action: 'restore' | 'retry') => {
    if (!job) return;
    const { controller, generation } = beginOperation();
    try {
      const nextJob = action === 'restore'
        ? await organizationDeletion.restore(organizationId, job.deletion_job_id, controller.signal)
        : await organizationDeletion.retry(organizationId, job.deletion_job_id, controller.signal);
      if (!operationIsCurrent(controller, generation)) return;
      setJob(nextJob);
      setRestoreOpen(false);
      void reconcileLifecycle(nextJob);
      if (action === 'restore') {
        const nextPlan = await loadPlan(controller.signal);
        if (!operationIsCurrent(controller, generation)) return;
        setPlan(nextPlan);
        if (!nextPlan.deletion_job_id) setJob(null);
        pushToast({ tone: 'success', title: 'Organization restored', message: 'Access is active again. Cancelled work is not restarted.' });
      } else {
        pushToast({ tone: 'info', title: 'Deletion retry scheduled', message: 'The worker will resume from the last safe phase.' });
      }
    } catch (nextError: unknown) {
      if (operationIsCurrent(controller, generation)) {
        setError(errorMessage(nextError, `Unable to ${action} deletion.`));
      }
    } finally {
      if (operationIsCurrent(controller, generation)) setWorking(false);
    }
  };

  if (loading) {
    return <div className="rounded-xl border border-gray-200 bg-white p-4 text-xs text-gray-500">Loading deletion controls…</div>;
  }

  if (!canManageLifecycle && (!job || job.status === 'restored')) return null;

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
            <OrganizationDeletionProgress job={job} />
            <p className="text-[11px] text-gray-500">
              {job.recovery_window_waived
                ? `Recovery window waived${job.expedited_at ? ` at ${recoveryLabel(job.expedited_at)}` : ''}. Permanent cleanup begins after active batches stop.`
                : `Permanent deletion no earlier than ${recoveryLabel(job.not_before_at)}.`}
            </p>
            {canExpedite && job.status === 'failed' && job.restore_allowed && (
              <p className="text-[11px] text-amber-700">
                Retry cleanup before waiving the remaining recovery window.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {canManageLifecycle && job.restore_allowed && (
                <button type="button" onClick={() => setRestoreOpen(true)} disabled={working} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">
                  <RotateCcw className="h-3.5 w-3.5" /> Restore
                </button>
              )}
              {canExpedite && job.restore_allowed && job.status !== 'failed' && (
                <button type="button" onClick={openExpedite} disabled={working} className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50">
                  Delete permanently now
                </button>
              )}
              {canExpedite && job.status === 'failed' && (
                <button type="button" onClick={() => void runJobAction('retry')} disabled={working} className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-50">
                  Retry cleanup
                </button>
              )}
            </div>
          </div>
        ) : (
          canManageLifecycle && (
            <button type="button" onClick={openDelete} disabled={!plan?.can_request} className="mt-4 w-full rounded-lg border border-red-300 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
              Delete organization
            </button>
          )
        )}
      </section>

      <OrganizationDeletionRequestDialog
        open={deleteOpen}
        organizationName={organizationName}
        plan={plan}
        confirmationName={confirmationName}
        acknowledged={acknowledged}
        working={working}
        onConfirmationNameChange={setConfirmationName}
        onAcknowledgedChange={setAcknowledged}
        onConfirm={() => void requestDeletion()}
        onClose={() => setDeleteOpen(false)}
      />

      <OrganizationDeletionExpediteDialog
        open={expediteOpen}
        organizationName={organizationName}
        confirmationName={expediteConfirmationName}
        acknowledged={expediteAcknowledged}
        working={working}
        onConfirmationNameChange={setExpediteConfirmationName}
        onAcknowledgedChange={setExpediteAcknowledged}
        onConfirm={() => void expediteDeletion()}
        onClose={() => setExpediteOpen(false)}
      />

      <OrganizationDeletionRestoreDialog
        open={restoreOpen}
        working={working}
        onConfirm={() => void runJobAction('restore')}
        onClose={() => setRestoreOpen(false)}
      />
    </>
  );
}
