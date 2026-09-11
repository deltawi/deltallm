import { useState } from 'react';
import Button from '../Button';
import { selectorReports } from '../../lib/api/selectorReports';
import { evaluationRequest } from '../../lib/selectorEvaluation';
import { useExplicitReport } from '../../lib/useExplicitReport';
import RoutingCostSummary from './RoutingCostSummary';

const percent = (value: number | null) => value === null ? 'Unavailable' : `${(value * 100).toFixed(1)}%`;

export default function SelectorEvaluationPanel({ policy, scope, allowed }: {
  policy: Record<string, unknown> | null; scope: string; allowed: boolean;
}) {
  const [samples, setSamples] = useState('');
  const [inputError, setInputError] = useState<string | null>(null);
  const report = useExplicitReport(scope, JSON.stringify([policy, samples]), selectorReports.evaluate);
  const run = () => {
    if (!policy) { setInputError('Fix the policy validation errors first.'); return; }
    try {
      const payload = evaluationRequest(policy, samples);
      setInputError(null);
      void report.run(payload);
    } catch (error: unknown) {
      setInputError(error instanceof Error ? error.message : 'Invalid samples.');
    }
  };
  return <details className="rounded-xl border border-slate-200 bg-white p-4">
    <summary className="cursor-pointer text-sm font-semibold">Evaluate selector (optional)</summary>
    <div className="mt-3 space-y-4">
      <p className="text-sm text-slate-600">Analyze labeled test results. This replays supplied selector outputs; it does not call models, incur charges, or change routing. Publishing does not require an evaluation.</p>
      <label className="block space-y-1"><span className="text-sm font-medium">Test samples (JSON array, up to 100 / 256 KiB)</span>
        <textarea className="w-full rounded-lg border border-slate-200 p-3 font-mono text-xs" rows={6} value={samples} maxLength={262144}
          placeholder={'[{"expected_lane":"economy","output":"{\\"lane\\":\\"economy\\"}","latency_ms":120}]'}
          onChange={(event) => { setSamples(event.target.value); setInputError(null); }} />
      </label>
      <p className="text-xs text-slate-500">Optional evidence: answer_quality_score (0–1), latency_ms and exact decimal cost strings. Lane agreement alone does not prove answer quality.</p>
      {!allowed && <p role="alert">You do not have permission to evaluate selectors.</p>}
      <Button size="sm" onClick={run} disabled={!allowed || !policy || report.loading || !samples.trim()} loading={report.loading}>Analyze results</Button>
      {report.loading && <p role="status">Analyzing supplied results…</p>}
      {!!(inputError || report.error) && <p role="alert" className="text-sm text-red-700">{inputError || (report.error instanceof Error ? report.error.message : 'Evaluation unavailable. Retry explicitly.')}</p>}
      {report.stale && <p role="status" className="text-amber-800">Results are stale: the policy or samples changed. Analyze again before relying on them.</p>}
      {report.data && <div className="space-y-4">
        <p className="text-sm">{report.data.correct_count}/{report.data.sample_count} expected lanes matched · {percent(report.data.default_rate)} safe defaults</p>
        <div className="overflow-x-auto"><table className="w-full text-left text-xs">
          <caption className="text-left font-medium">Lane distribution and quality confusion</caption>
          <thead><tr>{['Lane', 'Expected', 'Selected', 'Precision', 'Recall'].map((label) => <th key={label} scope="col" className="p-2">{label}</th>)}</tr></thead>
          <tbody>{report.data.lanes.map((lane) => <tr key={lane.lane}>
            <th scope="row" className="p-2">{lane.lane}</th><td>{lane.expected}</td><td>{lane.selected}</td><td>{percent(lane.precision)}</td><td>{percent(lane.recall)}</td>
          </tr>)}</tbody>
        </table></div>
        <ul className="list-inside list-disc text-xs">{report.data.confusion.map((row) => <li key={`${row.expected_lane}/${row.selected_lane}`}>Expected {row.expected_lane} → selected {row.selected_lane}: {row.count}</li>)}</ul>
        <p className="text-xs">Supplied latency p50 / p95: {report.data.latency_p50_ms ?? 'Unavailable'} / {report.data.latency_p95_ms ?? 'Unavailable'} ms ({report.data.latency_sample_count} samples).</p>
        <p className="text-xs">Supplied answer-quality mean: {percent(report.data.answer_quality_mean)} ({report.data.answer_quality_sample_count} samples).</p>
        <p className="text-xs">Failures/default causes: {Object.entries(report.data.failure_counts).map(([cause, count]) => `${cause}: ${count}`).join(', ') || 'None in supplied results'}.</p>
        <RoutingCostSummary summary={report.data.costs} />
      </div>}
    </div>
  </details>;
}
