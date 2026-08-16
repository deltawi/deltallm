import { Play, ShieldCheck, ShieldX } from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';
import type {
  TierPolicySimulation,
  TierPolicySimulationPayload,
  TierSimulationBillingMode,
} from '../../lib/api';
import {
  describeRateLimit,
  errorMessage,
  formatSimulationPerRequestPrice,
  formatSimulationPrice,
  formatPricingValue,
  pricingEntries,
  summarizePricing,
  summarizeSimulation,
  tierSimulationFormToPayload,
  type TierSimulationFormValues,
} from '../../lib/tiers';

type SimulationForm = TierSimulationFormValues & {
  callable_key: string;
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
  billing_mode: 'chat',
  request_count: '1',
  prompt_tokens: '1000',
  completion_tokens: '500',
  audio_prompt_tokens: '0',
  audio_completion_tokens: '0',
  input_images: '0',
  output_images: '1',
  input_characters: '1000',
  output_characters: '0',
  input_audio_tokens: '0',
  output_audio_tokens: '0',
  duration_seconds: '60',
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
      const payload = tierSimulationFormToPayload(form, selectedCallable);
      await onRun(payload);
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
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-xs font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
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
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
            />
            <datalist id="tier-simulation-callables">
              {callableOptions.map((option) => <option key={option} value={option} />)}
            </datalist>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Pricing mode">
              <select
                value={form.mode}
                onChange={(event) => setForm({ ...form, mode: event.target.value })}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
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
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
              />
            </Field>
          </div>
          <Field label="Workload type">
            <select
              value={form.billing_mode}
              onChange={(event) => setForm({ ...form, billing_mode: event.target.value as TierSimulationBillingMode })}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
            >
              <option value="chat">Chat / completion</option>
              <option value="embedding">Embedding</option>
              <option value="rerank">Rerank</option>
              <option value="image_generation">Image generation</option>
              <option value="audio_speech">Audio speech</option>
              <option value="audio_transcription">Audio transcription</option>
            </select>
          </Field>
          {['chat', 'embedding', 'rerank'].includes(form.billing_mode) ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label={form.billing_mode === 'rerank' ? 'Input units / tokens' : 'Prompt tokens'}>
                <input
                  value={form.prompt_tokens}
                  onChange={(event) => setForm({ ...form, prompt_tokens: event.target.value })}
                  inputMode="numeric"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                />
              </Field>
              {form.billing_mode === 'chat' ? (
                <Field label="Output tokens">
                  <input
                    value={form.completion_tokens}
                    onChange={(event) => setForm({ ...form, completion_tokens: event.target.value })}
                    inputMode="numeric"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                  />
                </Field>
              ) : null}
            </div>
          ) : null}
          {form.billing_mode === 'image_generation' ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Input images">
                <input
                  value={form.input_images}
                  onChange={(event) => setForm({ ...form, input_images: event.target.value })}
                  inputMode="numeric"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                />
              </Field>
              <Field label="Generated images">
                <input
                  value={form.output_images}
                  onChange={(event) => setForm({ ...form, output_images: event.target.value })}
                  inputMode="numeric"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                />
              </Field>
            </div>
          ) : null}
          {form.billing_mode === 'audio_speech' ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Input characters">
                  <input
                    value={form.input_characters}
                    onChange={(event) => setForm({ ...form, input_characters: event.target.value })}
                    inputMode="numeric"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                  />
                </Field>
                <Field label="Output characters">
                  <input
                    value={form.output_characters}
                    onChange={(event) => setForm({ ...form, output_characters: event.target.value })}
                    inputMode="numeric"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                  />
                </Field>
              </div>
              <AudioUsageFields form={form} setForm={setForm} includeOutputAudioTokens />
            </>
          ) : null}
          {form.billing_mode === 'audio_transcription' ? (
            <AudioUsageFields form={form} setForm={setForm} />
          ) : null}
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
                  {simulation.decision.allowed
                    ? <ShieldCheck className="h-5 w-5 text-emerald-600" />
                    : <ShieldX className="h-5 w-5 text-red-600" />}
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{summarizeSimulation(simulation)}</p>
                    <p className="font-mono text-xs text-gray-400">
                      {simulation.decision.primary_limiting_scope || simulation.decision.reason}
                    </p>
                  </div>
                </div>
                <span className="rounded bg-white px-2 py-1 text-xs font-semibold text-gray-600">
                  {simulation.mode} · {formatBillingMode(simulation.request.billing_mode)} · {summarizeUsage(simulation.request.usage)}
                </span>
              </div>

              {simulation.pricing ? (
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase text-gray-400">Pricing</p>
                  <p className="mb-2 text-xs text-gray-600">{summarizePricing(simulation.pricing.pricing)}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {pricingEntries(simulation.pricing.pricing).map((entry) => (
                      <span key={entry.payloadField} className="rounded bg-white px-2 py-0.5 text-[11px] text-gray-600">
                        {entry.shortLabel}: {formatPricingValue(entry.value, entry.unit)}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              <div>
                <p className="mb-2 text-xs font-semibold uppercase text-gray-400">
                  Total customer price
                </p>
                <p className={`text-sm font-semibold ${simulation.calculated_price.status === 'available' ? 'text-gray-800' : 'text-amber-700'}`}>
                  {formatSimulationPrice(simulation.calculated_price)}
                </p>
                <p className="mt-1 text-xs text-gray-500">
                  {simulation.calculated_price.request_count}{' '}
                  {simulation.calculated_price.request_count === 1 ? 'request' : 'requests'} total
                  {formatSimulationPerRequestPrice(simulation.calculated_price)
                    ? ` · ${formatSimulationPerRequestPrice(simulation.calculated_price)} per request`
                    : ''}
                  . Based on configured routes; live health and routing selection are not evaluated.
                </p>
              </div>

              <div>
                <p className="mb-2 text-xs font-semibold uppercase text-gray-400">Static Limit Checks</p>
                <p className="mb-2 text-xs text-gray-500">
                  Assumes an empty rate-limit window. Live fair-share capacity and in-flight requests are not evaluated.
                </p>
                {simulation.static_limit_checks.length === 0 ? (
                  <p className="text-sm text-gray-400">No tier, capacity-pool, or organization hard-cap checks apply to this request shape.</p>
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

function AudioUsageFields({
  form,
  setForm,
  includeOutputAudioTokens = false,
}: {
  form: SimulationForm;
  setForm: (value: SimulationForm) => void;
  includeOutputAudioTokens?: boolean;
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <Field label="Text input tokens">
        <input
          value={form.audio_prompt_tokens}
          onChange={(event) => setForm({ ...form, audio_prompt_tokens: event.target.value })}
          inputMode="numeric"
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
        />
      </Field>
      <Field label="Text output tokens">
        <input
          value={form.audio_completion_tokens}
          onChange={(event) => setForm({ ...form, audio_completion_tokens: event.target.value })}
          inputMode="numeric"
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
        />
      </Field>
      <Field label="Input audio tokens">
        <input
          value={form.input_audio_tokens}
          onChange={(event) => setForm({ ...form, input_audio_tokens: event.target.value })}
          inputMode="numeric"
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
        />
      </Field>
      {includeOutputAudioTokens ? (
        <Field label="Output audio tokens">
          <input
            value={form.output_audio_tokens}
            onChange={(event) => setForm({ ...form, output_audio_tokens: event.target.value })}
            inputMode="numeric"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
          />
        </Field>
      ) : null}
      <Field label="Duration seconds">
        <input
          value={form.duration_seconds}
          onChange={(event) => setForm({ ...form, duration_seconds: event.target.value })}
          inputMode="decimal"
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
        />
      </Field>
    </div>
  );
}

function formatBillingMode(mode: TierSimulationBillingMode | null): string {
  if (!mode) return 'unknown workload';
  return mode.replaceAll('_', ' ');
}

function summarizeUsage(usage: Record<string, number>): string {
  const entries = Object.entries(usage).filter(([, value]) => Number(value) > 0);
  if (entries.length === 0) return 'zero usage';
  return entries
    .slice(0, 2)
    .map(([key, value]) => `${Number(value).toLocaleString()} ${key.replaceAll('_', ' ')}`)
    .join(' · ');
}
