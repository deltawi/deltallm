import { AlertTriangle, FlaskConical, RefreshCw, ShieldAlert } from 'lucide-react';
import { useMemo, useState } from 'react';
import type {
  RouteGroupMemberDetail,
  RoutePolicySimulationOutcome,
  RoutePolicySimulationSelection,
} from '../../lib/api';
import { useRoutePolicySimulation } from '../../lib/useRoutePolicySimulation';

interface RouteGroupPolicySimulationPanelProps {
  groupKey: string;
  policy: Record<string, unknown> | null;
  policyError: string | null;
  members: RouteGroupMemberDetail[];
  canSimulate: boolean;
  promptRef?: Record<string, unknown> | null;
}

const OUTCOME_OPTIONS: Array<{ value: RoutePolicySimulationOutcome; label: string }> = [
  { value: 'success', label: 'Success' },
  { value: 'timeout', label: 'Timeout' },
  { value: 'rate_limit', label: 'Rate limit' },
  { value: 'unavailable', label: 'Unavailable' },
];

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : 'Policy simulation failed. Try again.';
}

function percentage(value: number): string {
  return `${(value * 100).toFixed(value > 0 && value < 0.01 ? 2 : 1)}%`;
}

function SelectionList({
  title,
  items,
  emptyLabel,
}: {
  title: string;
  items: RoutePolicySimulationSelection[];
  emptyLabel: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">{emptyLabel}</p>
      ) : (
        <div className="mt-3 space-y-3">
          {items.map((item) => (
            <div key={item.deployment_id}>
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate font-mono text-slate-700">{item.deployment_id}</span>
                <span className="shrink-0 font-medium text-slate-600">
                  {item.count} · {percentage(item.ratio)}
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full bg-brand-primary"
                  style={{ width: `${Math.min(100, item.ratio * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function RouteGroupPolicySimulationPanel({
  groupKey,
  policy,
  policyError,
  members,
  canSimulate,
  promptRef = null,
}: RouteGroupPolicySimulationPanelProps) {
  const [iterations, setIterations] = useState('100');
  const [tags, setTags] = useState('');
  const [outcomes, setOutcomes] = useState<Record<string, RoutePolicySimulationOutcome>>({});
  const [inputError, setInputError] = useState<string | null>(null);
  const enabledMembers = useMemo(() => members.filter((member) => member.enabled), [members]);
  const scenarioOutcomes = enabledMembers.map((member) => ({
    deployment_id: member.deployment_id,
    outcome: outcomes[member.deployment_id] || 'success' as RoutePolicySimulationOutcome,
  }));
  const fingerprint = JSON.stringify({
    groupKey,
    policy,
    promptRef,
    iterations,
    tags,
    outcomes: scenarioOutcomes,
  });
  const simulation = useRoutePolicySimulation(groupKey, fingerprint);

  const handleRun = () => {
    if (!policy) {
      setInputError(policyError || 'Enter a valid policy before running a simulation.');
      return;
    }
    if (!/^\d+$/.test(iterations)) {
      setInputError('Iterations must be an integer from 1 to 5000.');
      return;
    }
    const parsedIterations = Number(iterations);
    if (!Number.isSafeInteger(parsedIterations) || parsedIterations < 1 || parsedIterations > 5000) {
      setInputError('Iterations must be an integer from 1 to 5000.');
      return;
    }
    const normalizedTags = tags.split(',').map((tag) => tag.trim()).filter(Boolean);
    setInputError(null);
    void simulation.run({
      iterations: parsedIterations,
      policy,
      prompt_ref: promptRef,
      metadata: normalizedTags.length > 0 ? { tags: normalizedTags } : {},
      outcomes: scenarioOutcomes.filter((item) => item.outcome !== 'success'),
    });
  };

  if (!canSimulate) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="font-semibold">Simulation permission required</p>
            <p className="mt-1 text-amber-800">
              Your current session cannot access route-group policy simulation.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const result = simulation.data;
  const visibleError = inputError
    || (!policy ? policyError || 'Enter a valid policy before running a simulation.' : null)
    || (simulation.error ? errorMessage(simulation.error) : null);

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-blue-100 bg-blue-50/60 p-4">
        <div className="flex items-start gap-3">
          <FlaskConical className="mt-0.5 h-5 w-5 shrink-0 text-brand-primary-ink" />
          <div>
            <p className="text-sm font-semibold text-slate-900">Dry-run scenario</p>
            <p className="mt-1 text-xs leading-5 text-slate-600">
              Uses one snapshot of current health, cooldown, capacity, usage, and latency. It calls no provider and does not mutate live routing state.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <label className="space-y-1.5">
          <span className="text-xs font-medium text-slate-700">Requests to simulate</span>
          <input
            type="number"
            min="1"
            max="5000"
            step="1"
            value={iterations}
            onChange={(event) => setIterations(event.target.value)}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
          />
        </label>
        <label className="space-y-1.5">
          <span className="text-xs font-medium text-slate-700">Request tags</span>
          <input
            value={tags}
            onChange={(event) => setTags(event.target.value)}
            placeholder="vip, production"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
          />
        </label>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium text-slate-700">Assumed provider outcomes</p>
        {enabledMembers.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">
            There are no enabled deployments to simulate.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {enabledMembers.map((member) => (
              <label
                key={member.deployment_id}
                className="flex min-w-0 items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2"
              >
                <span className="truncate font-mono text-xs text-slate-700">
                  {member.deployment_id}
                </span>
                <select
                  value={outcomes[member.deployment_id] || 'success'}
                  onChange={(event) => setOutcomes((current) => ({
                    ...current,
                    [member.deployment_id]: event.target.value as RoutePolicySimulationOutcome,
                  }))}
                  className="shrink-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700"
                >
                  {OUTCOME_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={handleRun}
          disabled={simulation.loading || !policy || enabledMembers.length === 0}
          className="inline-flex items-center gap-2 rounded-lg bg-brand-primary px-4 py-2 text-sm font-semibold text-brand-on-primary shadow-sm hover:bg-brand-primary-hover disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${simulation.loading ? 'animate-spin' : ''}`} />
          {simulation.loading ? 'Simulating…' : result ? 'Run again' : 'Run simulation'}
        </button>
        {result && (
          <span className="text-xs text-slate-500">
            {result.iterations} requests · live-state dry run
          </span>
        )}
      </div>

      {visibleError && (
        <div role="alert" className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{visibleError}</span>
        </div>
      )}

      {simulation.stale && result && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          These results are stale because the policy or scenario changed. Run the simulation again before relying on them.
        </div>
      )}

      {!result && !simulation.loading && !visibleError && (
        <div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center">
          <FlaskConical className="mx-auto h-6 w-6 text-slate-300" />
          <p className="mt-2 text-sm font-medium text-slate-600">No simulation results yet</p>
          <p className="mt-1 text-xs text-slate-500">
            Choose any failure assumptions above, then run the current policy.
          </p>
        </div>
      )}

      {result && (
        <div className={`space-y-4 ${simulation.loading ? 'opacity-60' : ''}`} aria-busy={simulation.loading}>
          {result.warnings.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              {result.warnings.join(' ')}
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            {[
              ['Selected', result.summary.selected_requests],
              ['Served', result.summary.served_requests],
              ['Fallbacks', result.summary.fallback_requests],
              ['Failed', result.summary.failed_requests],
              ['Attempts', result.summary.total_attempts],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</p>
                <p className="mt-1 text-xl font-semibold text-slate-900">{value}</p>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <SelectionList title="Initial selection" items={result.selections} emptyLabel="No deployment was eligible." />
            <SelectionList title="Served by" items={result.served_deployments} emptyLabel="No request reached a successful deployment." />
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Terminal outcomes</h4>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.keys(result.terminal_outcomes).length === 0 ? (
                  <span className="text-sm text-slate-500">No terminal outcomes.</span>
                ) : Object.entries(result.terminal_outcomes).map(([outcome, count]) => (
                  <span key={outcome} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700">
                    {outcome}: {count}
                  </span>
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Eligibility decisions</h4>
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.keys(result.reason_counts).length === 0 ? (
                  <span className="text-sm text-slate-500">No route decision was available.</span>
                ) : Object.entries(result.reason_counts).map(([reason, count]) => (
                  <span key={reason} className="rounded-full bg-blue-50 px-2.5 py-1 text-xs text-blue-700">
                    {reason}: {count}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {result.sample_attempts.length > 0 && (
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Sample attempt trace</h4>
              <div className="mt-3 space-y-2 md:hidden">
                {result.sample_attempts.map((attempt, index) => (
                  <div key={`${attempt.iteration}-${attempt.attempt}-${index}`} className="rounded-lg bg-slate-50 p-3 text-xs">
                    <div className="flex justify-between gap-3">
                      <span className="font-mono text-slate-700">{attempt.deployment_id}</span>
                      <span className="font-medium text-slate-600">{attempt.outcome}</span>
                    </div>
                    <p className="mt-1 text-slate-500">
                      Request {attempt.iteration}, attempt {attempt.attempt} · {attempt.transition}
                    </p>
                  </div>
                ))}
              </div>
              <div className="mt-3 hidden overflow-x-auto md:block">
                <table className="min-w-full text-left text-xs">
                  <thead className="text-slate-400">
                    <tr>
                      <th className="pb-2 pr-4 font-medium">Request</th>
                      <th className="pb-2 pr-4 font-medium">Attempt</th>
                      <th className="pb-2 pr-4 font-medium">Deployment</th>
                      <th className="pb-2 pr-4 font-medium">Transition</th>
                      <th className="pb-2 font-medium">Outcome</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 text-slate-700">
                    {result.sample_attempts.map((attempt, index) => (
                      <tr key={`${attempt.iteration}-${attempt.attempt}-${index}`}>
                        <td className="py-2 pr-4">{attempt.iteration}</td>
                        <td className="py-2 pr-4">{attempt.attempt}</td>
                        <td className="py-2 pr-4 font-mono">{attempt.deployment_id}</td>
                        <td className="py-2 pr-4">{attempt.transition}</td>
                        <td className="py-2">{attempt.outcome}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
