import { apiFetch, withQuery } from './transport';
import type { RoutePolicySelector } from './routeGroups';

export interface RoutingCostSummary {
  savings_kind: 'counterfactual_estimate';
  operation_count: number;
  complete_cost_count: number;
  pending_reconciliation_count: number;
  savings_covered_count: number;
  selector_provider_cost_count: number;
  selector_customer_charge_count: number;
  answer_provider_cost_count: number;
  answer_customer_charge_count: number;
  selector_provider_cost_exact: string;
  selector_customer_charge_exact: string;
  answer_provider_cost_exact: string;
  answer_customer_charge_exact: string;
  net_savings_exact: string | null;
  partial: boolean;
  answer_distribution: Array<[string, number]>;
}

export interface RoutingCostCursor {
  created_at: string;
  operation_id: string;
}

export interface RoutingCostPage {
  generated_at: string;
  summary: RoutingCostSummary;
  has_more: boolean;
  next_cursor: RoutingCostCursor | null;
}

export interface RoutingCostRequest {
  start: string;
  end: string;
  limit?: number;
  model_group?: string;
  before_created_at?: string;
  before_operation_id?: string;
  view?: 'organization' | 'team' | 'self';
}

export type EvaluationCostField = 'selector_provider_cost' | 'selector_customer_charge'
  | 'answer_provider_cost' | 'answer_customer_charge' | 'baseline_answer_provider_cost' | 'measurable_penalty';

export interface SelectorEvaluationSample {
  expected_lane: string;
  prompt?: string | null;
  output?: string | null;
  failure?: 'selector_timeout' | 'provider_error' | 'capacity_denied' | null;
  latency_ms?: number | null;
  answer_quality_score?: number | null;
  costs?: Partial<Record<EvaluationCostField, string | null>>;
}

export interface SelectorEvaluationRequest {
  selector: RoutePolicySelector;
  samples: SelectorEvaluationSample[];
}

export interface SelectorEvaluationReport {
  basis: 'supplied_fixture_replay';
  selector_fingerprint: string;
  sample_count: number;
  correct_count: number;
  default_count: number;
  default_rate: number;
  failure_counts: Record<string, number>;
  lanes: Array<{ lane: string; expected: number; selected: number; correct: number; precision: number | null; recall: number | null }>;
  confusion: Array<{ expected_lane: string; selected_lane: string; count: number }>;
  latency_sample_count: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  answer_quality_sample_count: number;
  answer_quality_mean: number | null;
  costs: RoutingCostSummary;
  warnings: string[];
}

export const selectorReports = {
  evaluate: (payload: SelectorEvaluationRequest, signal?: AbortSignal) =>
    apiFetch<SelectorEvaluationReport>('/ui/api/route-policy-evaluations', { method: 'POST', json: payload, signal }),
  costs: (query: RoutingCostRequest, signal?: AbortSignal) =>
    apiFetch<RoutingCostPage>(withQuery('/ui/api/spend/routing-costs', query), { signal }),
};
