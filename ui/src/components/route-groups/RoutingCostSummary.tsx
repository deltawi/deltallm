import type { RoutingCostSummary as CostSummary } from '../../lib/api/selectorReports';

function amount(value: string, coverage: number): string {
  return coverage ? `$${value.replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1')}` : 'Unavailable';
}

export default function RoutingCostSummary({ summary }: { summary: CostSummary }) {
  if (summary.operation_count === 0) return <p className="text-sm text-slate-500">No selector operations in this page/window.</p>;
  return <div className="space-y-3 text-sm text-slate-700">
    <p>{summary.operation_count} operations · {summary.complete_cost_count} with complete costs · {summary.pending_reconciliation_count} pending reconciliation</p>
    <dl className="grid gap-3 md:grid-cols-2">
      {([
        ['Selector provider cost', summary.selector_provider_cost_exact, summary.selector_provider_cost_count],
        ['Selector customer charge', summary.selector_customer_charge_exact, summary.selector_customer_charge_count],
        ['Answer provider cost', summary.answer_provider_cost_exact, summary.answer_provider_cost_count],
        ['Answer customer charge', summary.answer_customer_charge_exact, summary.answer_customer_charge_count],
      ] as const).map(([label, value, coverage]) => <div key={label}>
        <dt className="text-xs text-slate-500">{label}</dt>
        <dd className="break-all font-medium">{amount(value, coverage)} <span className="text-xs font-normal">({coverage}/{summary.operation_count} known)</span></dd>
      </div>)}
      <div><dt className="text-xs text-slate-500">Net savings (counterfactual estimate)</dt>
        <dd className="break-all font-medium">{summary.net_savings_exact === null ? 'Unavailable — baseline/penalty evidence missing' : amount(summary.net_savings_exact, summary.savings_covered_count)}</dd>
        <dd className="text-xs">{summary.savings_covered_count}/{summary.operation_count} operations covered</dd>
      </div>
    </dl>
    {summary.partial && <p className="text-amber-800">Partial report: amounts are known subtotals, not complete period totals. Missing costs or savings are not zero.</p>}
    {summary.answer_distribution.length > 0 && <div>
      <p className="font-medium">Answer-model distribution (recorded events)</p>
      <ul className="list-inside list-disc">{summary.answer_distribution.map(([model, count]) => <li key={model} className="break-all">{model}: {count}</li>)}</ul>
    </div>}
  </div>;
}
