import { Play, ShieldCheck, ShieldX } from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';
import type { TierPolicySimulation, TierPolicySimulationPayload } from '../../lib/api';
import {
  describeRateLimit,
  errorMessage,
  parseNonNegativeIntegerInput,
  parsePositiveIntegerInput,
  summarizeSimulation,
} from '../../lib/tiers';

type SimulationForm = {
  callable_key: string;
  mode: string;
  request_count: string;
  prompt_tokens: string;
  completion_tokens: string;
};

type TierSimulationPanelProps = {
  callableOptions?: string[];
  simulation: TierPolicySimulation | null;
  loading: boolean;
  error: string | null;
  onRun: (payload: TierPolicySimulationPayload) => Promise<void>;
};

const INITIAL_FORM: SimulationForm = {
  callable_key: '',
  mode: 'sync',
  request_count: '1',
  prompt_tokens: '1000',
  completion_tokens: '500',
};

export default function TierSimulationPanel({
  callableOptions = [],
  simulation,
  loading,
  error,
  onRun,
}: TierSimulationPanelProps) {
  const [form, setForm] = useState<SimulationForm>(INITIAL_FORM);
  const [localError, setLocalError] = useState<string | null>(null);
  const selectedCallable = form.callable_key || callableOptions[0] || '';

  const handleRun = async () => {
    try {
      setLocalError(null);
      await onRun({
        callable_key: selectedCallable,
        mode: form.mode,
        request_count: parsePositiveIntegerInput(form.request_count, 'Requests'),
        prompt_tokens: parseNonNegativeIntegerInput(form.prompt_tokens, 'Prompt tokens'),
        completion_tokens: parseNonNegativeIntegerInput(form.completion_tokens, 'Output tokens'),
      });
    } catch (err: unknown) {
      setLocalError(errorMessage(err, 'Simulation failed.'));
    }
  };

  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Policy Simulation</h3>
          <p className="mt-0.5 text-xs text-gray-500">Static preview of access, pricing, and limit scopes for one request shape.</p>
        </div>
        <button
          type="button"
          onClick={handleRun}
          disabled={loading || !selectedCallable}
          className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {loading ? 'Running...' : 'Run'}
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="space-y-3">
          <Field label="Model or callable key">
            <input
              list="tier-simulation-callables"
              value={selectedCallable}
              onChange={(event) => setForm({ ...form, callable_key: event.target.value })}
              placeholder="gpt-4o-mini"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <datalist id="tier-simulation-callables">
              {callableOptions.map((option) => <option key={option} value={option} />)}
            </datalist>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Mode">
              <select
                value={form.mode}
                onChange={(event) => setForm({ ...form, mode: event.target.value })}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="sync">Sync</option>
                <option value="batch">Batch</option>
              </select>
            </Field>
            <Field label="Requests">
              <input
                value={form.request_count}
                onChange={(event) => setForm({ ...form, request_count: event.target.value })}
                inputMode="numeric"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Prompt tokens">
              <input
                value={form.prompt_tokens}
                onChange={(event) => setForm({ ...form, prompt_tokens: event.target.value })}
                inputMode="numeric"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </Field>
            <Field label="Output tokens">
              <input
                value={form.completion_tokens}
                onChange={(event) => setForm({ ...form, completion_tokens: event.target.value })}
                inputMode="numeric"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </Field>
          </div>
          {localError || error ? (
            <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{localError || error}</div>
          ) : null}
        </div>

        <div className="min-w-0 rounded-xl border border-gray-100 bg-gray-50 p-4">
          {!simulation ? (
            <div className="flex h-full min-h-44 items-center justify-center text-sm text-gray-400">
              Run a simulation to inspect the effective decision.
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  {simulation.access.allowed
                    ? <ShieldCheck className="h-5 w-5 text-emerald-600" />
                    : <ShieldX className="h-5 w-5 text-red-600" />}
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{summarizeSimulation(simulation)}</p>
                    <p className="font-mono text-xs text-gray-400">{simulation.access.reason}</p>
                  </div>
                </div>
                <span className="rounded bg-white px-2 py-1 text-xs font-semibold text-gray-600">
                  {simulation.mode} · {simulation.request.aggregate_tokens.toLocaleString()} total tokens
                </span>
              </div>

              {simulation.pricing ? (
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase text-gray-400">Pricing</p>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(simulation.pricing.pricing).map(([key, value]) => (
                      <span key={key} className="rounded bg-white px-2 py-0.5 text-[11px] text-gray-600">
                        {key}: {value}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              <div>
                <p className="mb-2 text-xs font-semibold uppercase text-gray-400">Static Limit Checks</p>
                {simulation.static_limit_checks.length === 0 ? (
                  <p className="text-sm text-gray-400">No tier limit checks for this request shape.</p>
                ) : (
                  <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    {simulation.static_limit_checks.map((check) => (
                      <div
                        key={`${check.scope}:${check.entity_id}:${check.mode}`}
                        className={`rounded-lg border px-3 py-2 ${
                          check.would_exceed_limit
                            ? 'border-amber-200 bg-amber-50'
                            : 'border-gray-100 bg-white'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-mono text-xs font-semibold text-gray-700">{check.scope}</p>
                          <span className="text-xs text-gray-500">{describeRateLimit(check)}</span>
                        </div>
                        <p className="mt-1 text-xs text-gray-500">
                          Amount {check.amount.toLocaleString()} · remaining {check.remaining_after_amount.toLocaleString()}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-gray-500">{label}</span>
      {children}
    </label>
  );
}
