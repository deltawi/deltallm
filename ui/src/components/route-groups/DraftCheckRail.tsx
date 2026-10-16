import { ArrowRight, Check } from 'lucide-react';
import Button from '../Button';
import type { RouteGroupMemberDetail } from '../../lib/api';
import { memberLabels, routableMemberIds } from '../../lib/routeGroupRouting';
import { shareColor, type DraftIssue } from '../../lib/routeGroupSummary';
import type { useRoutePolicySimulation } from '../../lib/useRoutePolicySimulation';
import { ShareBar } from './RouteGroupModelCell';

type Simulation = ReturnType<typeof useRoutePolicySimulation>;



const ISSUE_STYLE = {
  error: { mark: '!', className: 'bg-red-50 text-red-700' },
  warning: { mark: '?', className: 'bg-amber-50 text-amber-700' },
  ok: { mark: '', className: 'bg-emerald-50 text-emerald-700' },
};



interface DraftCheckRailProps {
  simulation: Simulation;
  onRun: () => void;
  issues: DraftIssue[];
  members: RouteGroupMemberDetail[];
  draftShares: Record<string, number> | null;
  liveShares: Record<string, number> | null;
  canSimulate: boolean;
  blocked: boolean;
  onOpenSimulate: () => void;
}

export default function DraftCheckRail({
  simulation, onRun, issues, members, draftShares, liveShares, canSimulate, blocked, onOpenSimulate,
}: DraftCheckRailProps) {
  const labels = memberLabels(members);
  const routable = routableMemberIds(members);
  const shown = routable.filter((id) => (draftShares?.[id] ?? 0) > 0 || (liveShares?.[id] ?? 0) > 0);
  const segments = (shares: Record<string, number> | null) => routable.map((id, index) => ({ id, label: labels[id], value: shares?.[id] ?? 0, color: shareColor(index) }));
  const result = simulation.stale ? null : simulation.data;

  return (
    <aside aria-label="Draft check" className="space-y-3">
      <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <header className="border-b border-gray-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-gray-900">Draft check</h3>
          <p className="mt-0.5 text-xs text-gray-500">Updates as you edit.</p>
        </header>
        <div className="space-y-3 px-4 py-3 text-xs">
          {draftShares && shown.length > 0 ? (
            <>
              <div className="grid grid-cols-[3rem_1fr] items-center gap-x-2 gap-y-1.5">
                <span className="text-gray-500">Live</span>{liveShares ? <ShareBar segments={segments(liveShares)} label="Live traffic" className="h-2" /> : <span className="text-gray-400">Decided per request</span>}
                <span className="font-semibold text-gray-900">Draft</span><ShareBar segments={segments(draftShares)} label="Draft traffic" className="h-2" />
              </div>
              <ul className="space-y-1">
                {shown.map((id) => (
                  <li key={id} className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-1.5"><span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-sm" style={{ background: shareColor(routable.indexOf(id)) }} /><span className="truncate">{labels[id]}</span></span>
                    <span className="tabular-nums text-gray-500">{liveShares ? `${liveShares[id] ?? 0}% → ` : ''}<strong className="text-gray-900">{draftShares[id] ?? 0}%</strong></span>
                  </li>
                ))}
              </ul>
            </>
          ) : <p className="text-gray-500">This strategy decides the share for each request, so there is no fixed split to show.</p>}
          <ul className="space-y-1.5 border-t border-gray-100 pt-3">
            {issues.map((issue, index) => (
              <li key={index} role={issue.level === 'error' ? 'alert' : undefined} className="flex gap-2">
                <span aria-hidden="true" className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${ISSUE_STYLE[issue.level].className}`}>
                  {issue.level === 'ok' ? <Check className="h-2.5 w-2.5" /> : ISSUE_STYLE[issue.level].mark}
                </span>
                <span className={issue.level === 'error' ? 'text-red-700' : 'text-gray-700'}>{issue.text}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <header className="border-b border-gray-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-gray-900">Quick dry run</h3>
          <p className="mt-0.5 text-xs text-gray-500">100 requests against current health. No provider calls.</p>
        </header>
        <div className="space-y-2 px-4 py-3 text-xs">
          {result ? (
            <>
              <p className="text-gray-700"><strong>{result.summary.served_requests}</strong> of {result.iterations} served · {result.summary.fallback_requests} fallbacks · {result.summary.failed_requests} failed</p>
              <ul className="space-y-0.5 text-gray-600">
                {result.selections.slice(0, 5).map((item) => <li key={item.deployment_id} className="flex justify-between gap-2"><span className="truncate">{labels[item.deployment_id] ?? item.deployment_id}</span><span className="tabular-nums">{item.count}</span></li>)}
              </ul>
            </>
          ) : <p className="text-gray-500">{simulation.stale ? 'The draft changed. Run it again.' : 'Run it to see where 100 requests would go with this draft.'}</p>}
          {simulation.error ? <p role="alert" className="text-red-700">{simulation.error instanceof Error && simulation.error.message ? simulation.error.message : 'The dry run failed. Try again.'}</p> : null}
          <div className="flex items-center gap-3 pt-1">
            <Button size="sm" variant="outline" disabled={!canSimulate || blocked} loading={simulation.loading}
              onClick={onRun}>Run dry run</Button>
            <button type="button" onClick={onOpenSimulate} className="inline-flex items-center gap-1 font-medium text-brand-primary-ink hover:underline">Full simulation<ArrowRight aria-hidden="true" className="h-3 w-3" /></button>
          </div>
          {!canSimulate ? <p className="text-gray-500">You do not have permission to run dry runs.</p> : blocked ? <p className="text-gray-500">Fix the draft first.</p> : null}
        </div>
      </section>
    </aside>
  );
}
