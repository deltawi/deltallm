import { useState } from 'react';
import Button from '../Button';
import { selectorReports, type RoutingCostCursor, type RoutingCostRequest } from '../../lib/api/selectorReports';
import { useExplicitReport } from '../../lib/useExplicitReport';
import RoutingCostSummary from './RoutingCostSummary';

type CostWindow = Omit<RoutingCostRequest, 'before_created_at' | 'before_operation_id'>;

async function loadCostPage(input: { window: CostWindow; cursor?: RoutingCostCursor }, signal: AbortSignal) {
  const page = await selectorReports.costs({
    ...input.window, before_created_at: input.cursor?.created_at, before_operation_id: input.cursor?.operation_id,
  }, signal);
  // Accept the page and its exact pagination window together, never on an attempted load.
  return { page, window: input.window };
}

export default function SelectorCostPanel({ scope, modelGroup, allowed }: {
  scope: string; modelGroup: string; allowed: boolean;
}) {
  const [days, setDays] = useState('1');
  const report = useExplicitReport(scope, JSON.stringify([modelGroup, days]), loadCostPage);
  const page = report.data?.page;
  const loadLatest = () => {
    if (!allowed || report.loading) return;
    const end = new Date();
    void report.run({ window: {
      model_group: modelGroup, limit: 100,
      start: new Date(end.getTime() - Number(days) * 86400000).toISOString(), end: end.toISOString(),
    } });
  };
  const loadOlder = () => {
    if (!allowed || report.loading || report.stale || !report.data?.page.next_cursor) return;
    void report.run({ window: report.data.window, cursor: report.data.page.next_cursor });
  };
  return <details className="rounded-xl border border-slate-200 bg-white p-4">
    <summary className="cursor-pointer text-sm font-semibold">Selector costs (optional)</summary>
    <div className="mt-3 space-y-3">
      <p className="text-sm text-slate-600">Read recorded selector and answer costs for this billed model group within your authorized spend scope. Reports never call providers.</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1"><span className="block text-xs font-medium">Window</span><select className="rounded-lg border border-slate-200 p-2 text-sm" value={days} onChange={(event) => setDays(event.target.value)}>
          <option value="1">Last 24 hours</option><option value="7">Last 7 days</option><option value="31">Last 31 days</option>
        </select></label>
        <Button size="sm" variant="secondary" disabled={!allowed || report.loading} onClick={loadLatest}>Load latest costs</Button>
      </div>
      {!allowed && <p role="alert">Spend reporting is not available for your account.</p>}
      {report.loading && <p role="status">Loading costs…</p>}
      {!!report.error && <p role="alert" className="text-sm text-red-700">{report.error instanceof Error ? report.error.message : 'Cost report unavailable. Retry explicitly.'}</p>}
      {report.stale && <p role="status" className="text-amber-800">Window changed; load the latest costs before relying on this report.</p>}
      {page && <div className="space-y-3">
        <p className="text-xs text-slate-500">Page of up to 100 operations · generated {new Date(page.generated_at).toLocaleString()} · not a full-period total</p>
        <RoutingCostSummary summary={page.summary} />
        {page.has_more && <Button size="sm" variant="secondary" disabled={!allowed || report.loading || report.stale}
          onClick={loadOlder}>Older operations</Button>}
      </div>}
    </div>
  </details>;
}
