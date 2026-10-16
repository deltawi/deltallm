import { Link } from 'react-router-dom';
import type { BatchJobListItem } from '../../lib/api/batches';
import {
  batchPace,
  batchProgress,
  canCancelJob,
  elapsedMs,
  endpointLabel,
  fmtAgo,
  fmtCost,
  fmtDateTime,
  isActiveJob,
  jobStatus,
} from '../../lib/batchJobs';
import { fmtDuration } from '../../lib/format';
import { TableSkeleton } from '../usage/ReportBits';
import { ActionMenu, EndpointTag, ProgressBar, StatusText } from './BatchBits';

const HEAD = 'whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500';
const CELL = 'whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] align-middle';
const MUTED = 'text-gray-400';
const LARGE = 'hidden lg:table-cell';
const WIDE = 'hidden 2xl:table-cell';

interface BatchJobsTableProps {
  jobs: BatchJobListItem[];
  loading: boolean;
  refreshing: boolean;
  now: number;
  onOpen: (batchId: string) => void;
  onCancel: (job: BatchJobListItem) => void;
}

export function ProgressText({ job }: { job: BatchJobListItem }) {
  const progress = batchProgress({ ...job, total: job.total_items });
  if (progress.total === 0) return <span className={MUTED}>No lines yet</span>;
  return (
    <span className="tabular-nums text-gray-600">
      {progress.percent}% of {progress.total.toLocaleString()}
      {progress.failed > 0 && <span className="text-rose-700"> · {progress.failed.toLocaleString()} failed</span>}
    </span>
  );
}

export function DurationText({ job, now }: { job: BatchJobListItem; now: number }) {
  const elapsed = elapsedMs(job.started_at, job.completed_at, now);
  if (elapsed === null) return <span className={MUTED}>—</span>;
  const pace = isActiveJob(job.status)
    ? batchPace(batchProgress({ ...job, total: job.total_items }), job.started_at, now)
    : null;
  return (
    <span className="tabular-nums text-gray-700">
      {fmtDuration(elapsed)}
      {pace && <span className="text-gray-500"> · {fmtDuration(pace.remainingMs)} left</span>}
    </span>
  );
}

export default function BatchJobsTable({ jobs, loading, refreshing, now, onOpen, onCancel }: BatchJobsTableProps) {
  if (loading) return <TableSkeleton rows={8} columns={7} />;
  return (
    <div className={`overflow-x-auto transition-opacity ${refreshing ? 'opacity-70' : ''}`} aria-busy={refreshing}>
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr>
            <th className={`${HEAD} w-[104px]`}>Status</th>
            <th className={HEAD}>Batch</th>
            <th className={HEAD}>Model</th>
            <th className={HEAD}>Progress</th>
            <th className={`${HEAD} text-right`}>Cost</th>
            <th className={`${HEAD} ${LARGE}`}>Team<span className="hidden 2xl:inline"> · API key</span></th>
            <th className={HEAD}>Created</th>
            <th className={`${HEAD} ${WIDE}`}>Duration</th>
            <th className={`${HEAD} w-10`}><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const status = jobStatus(job.status);
            const progress = batchProgress({ ...job, total: job.total_items });
            return (
              <tr
                key={job.batch_id}
                onClick={() => onOpen(job.batch_id)}
                className="group cursor-pointer hover:bg-gray-50"
              >
                <td className={CELL}><StatusText label={status.label} tone={status.tone} /></td>
                <td className={CELL}>
                  <span className="inline-flex items-center gap-1.5">
                    <Link
                      to={`/batches/${encodeURIComponent(job.batch_id)}`}
                      onClick={(event) => event.stopPropagation()}
                      className="font-mono text-[12px] text-gray-900 hover:text-brand-primary-ink hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                    >
                      {job.batch_id}
                    </Link>
                                        <EndpointTag label={endpointLabel(job.endpoint)} />
                  </span>
                </td>
                <td className={`${CELL} max-w-[220px] truncate text-gray-900`} title={job.model}>{job.model || <span className={MUTED}>—</span>}</td>
                <td className={CELL}>
                  <span className="inline-flex items-center gap-2.5">
                    <ProgressBar progress={progress} />
                    <ProgressText job={job} />
                  </span>
                </td>
                <td className={`${CELL} text-right tabular-nums text-gray-900`}>
                  {job.total_cost > 0 ? fmtCost(job.total_cost) : <span className={MUTED}>—</span>}
                </td>
                <td className={`${CELL} ${LARGE} max-w-[260px] truncate text-gray-700`}>
                  {job.team_alias || job.created_by_team_id || <span className={MUTED}>No team</span>}
                  {job.created_by_api_key && <span className="hidden font-mono text-[11.5px] text-gray-500 2xl:inline"> · {job.created_by_api_key}</span>}
                </td>
                <td className={`${CELL} text-gray-500`} title={fmtDateTime(job.created_at)}>{fmtAgo(job.created_at, now, true)}</td>
                <td className={`${CELL} ${WIDE}`}><DurationText job={job} now={now} /></td>
                <td className={`${CELL} text-right`}>
                  <ActionMenu
                    ghost
                    label={`Actions for ${job.batch_id}`}
                    items={[
                      { label: 'Open batch', onSelect: () => onOpen(job.batch_id) },
                      { label: 'Copy batch ID', onSelect: () => void navigator.clipboard?.writeText(job.batch_id).catch(() => undefined) },
                      ...(canCancelJob(job) ? [{ label: 'Cancel batch', danger: true, onSelect: () => onCancel(job) }] : []),
                    ]}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
