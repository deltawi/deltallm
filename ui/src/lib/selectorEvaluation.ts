import type { EvaluationCostField, SelectorEvaluationRequest, SelectorEvaluationSample } from './api/selectorReports';
import { readGuidedSelector } from './routeGroupSelector';

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

const COST_FIELDS: EvaluationCostField[] = ['selector_provider_cost', 'selector_customer_charge',
  'answer_provider_cost', 'answer_customer_charge', 'baseline_answer_provider_cost', 'measurable_penalty'];

function sampleFromJson(value: unknown): SelectorEvaluationSample {
  if (!record(value) || typeof value.expected_lane !== 'string') throw new Error('Each sample needs an expected_lane.');
  const allowed = ['expected_lane', 'prompt', 'output', 'failure', 'latency_ms', 'answer_quality_score', 'costs'];
  if (Object.keys(value).some((key) => !allowed.includes(key))) throw new Error('Unsupported sample field.');
  const sample: SelectorEvaluationSample = { expected_lane: value.expected_lane };
  for (const key of ['output', 'prompt'] as const) {
    if (value[key] != null && typeof value[key] !== 'string') throw new Error('Prompt and output must be text.');
    if (typeof value[key] === 'string') sample[key] = value[key];
  }
  if (value.failure != null) {
    if (value.failure !== 'selector_timeout' && value.failure !== 'provider_error' && value.failure !== 'capacity_denied') throw new Error('Unsupported failure cause.');
    sample.failure = value.failure;
  }
  if ((sample.output == null) === (sample.failure == null)) throw new Error('Each sample needs exactly one output or failure.');
  for (const key of ['latency_ms', 'answer_quality_score'] as const) {
    if (value[key] != null) {
      if (typeof value[key] !== 'number' || !Number.isFinite(value[key]) || value[key] < 0) throw new Error('Scores and timings must be non-negative finite numbers.');
      sample[key] = value[key];
    }
  }
  if (value.costs != null) {
    if (!record(value.costs)) throw new Error('Costs must be an object of exact decimal strings.');
    const costs: SelectorEvaluationSample['costs'] = {};
    for (const [key, amount] of Object.entries(value.costs)) {
      if (!COST_FIELDS.includes(key as EvaluationCostField) || (amount !== null
        && (typeof amount !== 'string' || !/^\d+(\.\d{1,18})?$/.test(amount)))) {
        throw new Error('Costs must use supported fields and non-negative exact decimal strings, or null for unknown.');
      }
      costs[key as EvaluationCostField] = amount;
    }
    sample.costs = costs;
  }
  return sample;
}

export function evaluationRequest(policy: Record<string, unknown>, raw: string): SelectorEvaluationRequest {
  if (new TextEncoder().encode(raw).byteLength > 262144) throw new Error('Samples exceed 256 KiB.');
  let values: unknown;
  try { values = JSON.parse(raw); } catch { throw new Error('Enter a JSON array of labeled samples.'); }
  if (!Array.isArray(values) || values.length < 1 || values.length > 100) throw new Error('Use between 1 and 100 samples.');
  const selector = readGuidedSelector(policy);
  if (selector.state !== 'enabled') throw new Error('Configure a supported selector first.');
  const request: SelectorEvaluationRequest = {
    selector: {
      kind: 'llm-tier', classifier_deployment_id: selector.classifier,
      default_lane: selector.defaultLane, timeout_ms: Number(selector.timeoutMs),
      max_input_chars: Number(selector.maxInputChars),
      lanes: selector.lanes.map((lane) => ({ ...lane, rank: Number(lane.rank) })),
    },
    samples: values.map(sampleFromJson),
  };
  if (new TextEncoder().encode(JSON.stringify(request)).byteLength > 262144) throw new Error('Evaluation exceeds 256 KiB.');
  return request;
}
