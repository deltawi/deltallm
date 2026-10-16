import type { ReactNode } from 'react';
import { RefreshCw } from 'lucide-react';
import type {
  BatchCapabilities,
  BatchErrorGroup,
  BatchJobCosts,
  BatchJobDetail,
  BatchWebhookDelivery,
} from '../../lib/api/batches';
import {
  batchPace,
  fmtCost,
  fmtDateTime,
  isActiveJob,
  webhookStatus,
  type BatchProgress,
} from '../../lib/batchJobs';
import { fmtDuration } from '../../lib/format';
import { Skeleton } from '../usage/ReportBits';
import { CopyButton, ProgressBar, ProgressLegend, StatusText } from './BatchBits';

export function RailCard({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-auto flex-col rounded-xl border border-gray-200 bg-white px-4 py-3.5">
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Row({ label, children, title }: { label: ReactNode; children: ReactNode; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-[12.5px]">
      <span className="min-w-0 truncate text-gray-500">{label}</span>
      <span className="shrink-0 text-right tabular-nums text-gray-900" title={title}>{children}</span>
    </div>
  );
}

export function ProgressCard({
  job,
  progress,
  costs,
  costsError,
  onRetryCosts,
  now,
}: {
  job: BatchJobDetail;
  progress: BatchProgress;
  costs: BatchJobCosts | null;
  costsError: string | null;
  onRetryCosts: () => void;
  now: number;
}) {
  const pace = isActiveJob(job.status) ? batchPace(progress, job.started_at, now) : null;
  const money = (value: number | undefined) => (costs ? fmtCost(value) : costsError ? '—' : <Skeleton className="h-3.5 w-12" />);
  return (
    <RailCard title="Progress">
      <p className="text-[22px] font-semibold tabular-nums text-gray-900">
        {progress.percent}%
        <span className="ml-1.5 text-[13px] font-normal text-gray-500">
          of {progress.total.toLocaleString()} lines
          {pace && ` · about ${fmtDuration(pace.remainingMs)} left`}
        </span>
      </p>
      <ProgressBar progress={progress} className="my-2.5 h-2 w-full" />
      <ProgressLegend progress={progress} />
      <div className="mt-3 border-t border-gray-100 pt-2">
        <Row label="Billed">{money(costs?.total_billed_cost)}</Row>
        <Row label="Provider cost">{money(costs?.total_provider_cost)}</Row>
        {pace && <Row label="Pace">{Math.round(pace.perMinute).toLocaleString()} lines a minute</Row>}
        {costsError && (
          <button type="button" onClick={onRetryCosts} className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-rose-700 hover:text-rose-900">
            <RefreshCw className="h-3 w-3" aria-hidden="true" /> Cost could not be loaded. Retry
          </button>
        )}
      </div>
    </RailCard>
  );
}

function errorTitle(group: BatchErrorGroup): string {
  return [group.retry_category, group.code].filter(Boolean).join(' · ');
}

export function FailuresCard({
  failed,
  groups,
  loading,
  error,
  onRetry,
  onShowFailed,
}: {
  failed: number;
  groups: BatchErrorGroup[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onShowFailed: () => void;
}) {
  return (
    <RailCard
      title="Failures"
      action={failed > 0 ? (
        <button type="button" onClick={onShowFailed} className="text-xs font-medium text-brand-primary-ink hover:underline">
          Show failed lines
        </button>
      ) : undefined}
    >
      {failed === 0 ? (
        <p className="text-[12.5px] text-gray-500">No failed lines.</p>
      ) : loading ? (
        <div className="grid gap-2" role="status" aria-label="Loading">
          <Skeleton className="h-3.5 w-3/4" />
          <Skeleton className="h-3.5 w-1/2" />
        </div>
      ) : error ? (
        <button type="button" onClick={onRetry} className="inline-flex items-center gap-1 text-xs font-medium text-rose-700 hover:text-rose-900">
          <RefreshCw className="h-3 w-3" aria-hidden="true" /> Failures could not be loaded. Retry
        </button>
      ) : (
        <div className="grid">
          {(groups ?? []).map((group) => (
            <Row key={`${group.message}|${group.retry_category}|${group.code}`} label={<span title={errorTitle(group) || undefined}>{group.message}</span>}>
              <span className="text-rose-700">{group.count.toLocaleString()}</span>
            </Row>
          ))}
          {groups !== null && groups.length === 0 && <p className="text-[12.5px] text-gray-500">The failed lines have no error details.</p>}
        </div>
      )}
    </RailCard>
  );
}

export function TimelineCard({ job, progress, now }: { job: BatchJobDetail; progress: BatchProgress; now: number }) {
  const pace = isActiveJob(job.status) ? batchPace(progress, job.started_at, now) : null;
  return (
    <RailCard title="Timeline">
      <Row label="Created">{fmtDateTime(job.created_at)}</Row>
      <Row label="Started">{job.started_at ? fmtDateTime(job.started_at) : 'Not started'}</Row>
      {job.cancel_requested_at && <Row label="Cancel requested">{fmtDateTime(job.cancel_requested_at)}</Row>}
      {job.completed_at ? (
        <Row label="Finished">{fmtDateTime(job.completed_at)}</Row>
      ) : pace ? (
        <Row label="Finishes" title="An estimate from the speed so far">about {fmtDateTime(new Date(now + pace.remainingMs).toISOString())}</Row>
      ) : null}
      {job.started_at && <Row label="Run time">{fmtDuration(Math.max(0, (job.completed_at ? new Date(job.completed_at).getTime() : now) - new Date(job.started_at).getTime()))}</Row>}
      {job.expires_at && <Row label="Expires">{fmtDateTime(job.expires_at)}</Row>}
    </RailCard>
  );
}

export function WebhooksCard({
  deliveries,
  capabilities,
  replayingEventId,
  notice,
  onReplay,
}: {
  deliveries: BatchWebhookDelivery[];
  capabilities: BatchCapabilities;
  replayingEventId: string | null;
  notice: string | null;
  onReplay: (delivery: BatchWebhookDelivery) => void;
}) {
  return (
    <RailCard title="Webhooks">
      {notice && <p className="mb-2 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">{notice}</p>}
      {deliveries.map((delivery) => {
        const status = webhookStatus(delivery.status);
        const canReplay = capabilities.replay_webhook && delivery.status === 'failed';
        return (
          <div key={delivery.event_id} className="border-b border-gray-100 py-1.5 last:border-b-0">
            <div className="flex items-center justify-between gap-3 text-[12.5px]">
              <span className="min-w-0 truncate font-mono text-[12px] text-gray-700" title={delivery.event_id}>{delivery.event_type}</span>
              <span className="flex shrink-0 items-center gap-2">
                <StatusText label={status.label} tone={status.tone} />
                {canReplay && (
                  <button
                    type="button"
                    onClick={() => onReplay(delivery)}
                    disabled={replayingEventId !== null}
                    className="text-xs font-medium text-brand-primary-ink hover:underline disabled:opacity-50"
                  >
                    {replayingEventId === delivery.event_id ? 'Sending' : 'Replay'}
                  </button>
                )}
              </span>
            </div>
            <p className="mt-0.5 text-[11.5px] text-gray-500">
              Attempt {delivery.attempt_count} of {delivery.max_attempts}
              {delivery.last_status_class && ` · HTTP ${delivery.last_status_class}`}
              {' · '}{fmtDateTime(delivery.updated_at)}
            </p>
            {delivery.last_error && <p className="mt-0.5 break-words text-[11.5px] text-rose-700">{delivery.last_error}</p>}
          </div>
        );
      })}
      <p className="mt-auto pt-2 text-[11px] text-gray-400">The URL, headers, payload and signing secret are not shown.</p>
    </RailCard>
  );
}

export function FilesCard({ job }: { job: BatchJobDetail }) {
  const files: Array<[string, string | null | undefined]> = [
    ['Input', job.input_file_id],
    ['Output', job.output_file_id],
    ['Errors', job.error_file_id],
  ];
  return (
    <RailCard title="Files">
      {files.map(([label, id]) => (
        <Row key={label} label={label}>
          {id ? (
            <span className="inline-flex items-center gap-1">
              <span className="max-w-[170px] truncate font-mono text-[11.5px] text-gray-700" title={id}>{id}</span>
              <CopyButton value={id} label={`Copy ${label.toLowerCase()} file ID`} />
            </span>
          ) : (
            <span className="text-gray-400">{label === 'Input' ? '—' : 'Not yet'}</span>
          )}
        </Row>
      ))}
    </RailCard>
  );
}
