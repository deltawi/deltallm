import type { RoutingCostSummary } from '../../src/lib/api/selectorReports';
import { buildPolicyFromGuided, toGuidedPolicy } from '../../src/lib/routeGroups';
import { chooseSelector } from '../../src/lib/routeGroupSelector';

export const selectorPolicy = () => buildPolicyFromGuided({}, chooseSelector(toGuidedPolicy({}, [
  { deployment_id: 'mini', enabled: true, mode: 'chat', weight: null, priority: null },
  { deployment_id: 'large', enabled: true, mode: 'chat', weight: null, priority: null },
]), 'mini'));

export const emptyCosts: RoutingCostSummary = {
  savings_kind: 'counterfactual_estimate', operation_count: 0, complete_cost_count: 0,
  pending_reconciliation_count: 0, savings_covered_count: 0, selector_provider_cost_count: 0,
  selector_customer_charge_count: 0, answer_provider_cost_count: 0, answer_customer_charge_count: 0,
  selector_provider_cost_exact: '0.000000000000000000', selector_customer_charge_exact: '0.000000000000000000',
  answer_provider_cost_exact: '0.000000000000000000', answer_customer_charge_exact: '0.000000000000000000',
  net_savings_exact: null, partial: false, answer_distribution: [],
};
