import type { BatchJobListItem } from '../../lib/api/batches';
import { batchProgress, endpointLabel, fmtAgo, jobStatus } from '../../lib/batchJobs';
import { TableSkeleton } from '../usage/ReportBits';
import { ProgressBar, StatusText } from './BatchBits';

interface BatchJobsMobileListProps {
  jobs: BatchJobListItem[];
  loading: boolean;
  now: number;
  onOpen: (batchId: string) => void;
}

/** Two-line rows for narrow screens. The filters and the pager are shared with the table. */
export default function BatchJobsMobileList({ jobs, loading, now, onOpen }: BatchJobsMobileListProps) {
  if (loading) return <TableSkeleton rows={6} columns={2} />;
  return (
    <ul aria-label="Batch jobs">
      {jobs.map((job) => {
        const status = jobStatus(job.status);
        const progress = batchProgress({ ...job, total: job.total_items });
        return (
          <li key={job.batch_id}>
            <button
              type="button"
              onClick={() => onOpen(job.batch_id)}
              className="grid w-full gap-1 border-b border-gray-100 px-2.5 py-2.5 text-left hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary"
            >
              <span className="flex min-w-0 items-center gap-2">
                <StatusText label={status.label} tone={status.tone} />
                <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-gray-900">{job.batch_id}</span>
                <span className="shrink-0 text-xs text-gray-500">{fmtAgo(job.created_at, now, true)}</span>
              </span>
              <span className="flex min-w-0 items-center gap-2 text-xs text-gray-500">
                <ProgressBar progress={progress} className="h-1.5 w-16" />
                <span className="shrink-0 tabular-nums">
                  {progress.percent}% of {progress.total.toLocaleString()}
                  {progress.failed > 0 && <span className="text-rose-700"> · {progress.failed.toLocaleString()} failed</span>}
                </span>
                <span className="min-w-0 flex-1 truncate text-right">
                  {endpointLabel(job.endpoint)} · {job.model}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
