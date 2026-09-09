import assert from 'node:assert/strict';
import test from 'node:test';
import { selectorReports } from '../src/lib/api/selectorReports';
import { evaluationRequest } from '../src/lib/selectorEvaluation';
import { emptyCosts, selectorPolicy } from './fixtures/selectorReports';

test('evaluation adapter keeps explicit null, exact costs and does not infer absent evidence', () => {
  const request = evaluationRequest(selectorPolicy(), JSON.stringify([{
    expected_lane: 'economy', output: '{"lane":"economy"}',
    costs: { selector_provider_cost: '0.000000000000000001', answer_provider_cost: null },
  }]));
  assert.equal(request.selector.classifier_deployment_id, 'mini');
  assert.deepEqual(request.samples[0].costs, { selector_provider_cost: '0.000000000000000001', answer_provider_cost: null });
  assert.equal('answer_quality_score' in request.samples[0], false);
});

test('evaluation adapter bounds samples and rejects floating money and hidden fields', () => {
  for (const samples of [
    [], Array(101).fill({ expected_lane: 'economy', output: '{}' }),
    [{ expected_lane: 'economy', output: '{}', costs: { selector_provider_cost: .1 } }],
    [{ expected_lane: 'economy', output: '{}', api_key: 'never-forward' }],
    [{ expected_lane: 'economy', output: '{}', failure: 'provider_error' }],
  ]) assert.throws(() => evaluationRequest(selectorPolicy(), JSON.stringify(samples)));
  assert.throws(() => evaluationRequest(selectorPolicy(), 'x'.repeat(262145)), /256 KiB/);
});

test('reporting transport uses explicit endpoints, cursor, exact values and AbortSignal', async () => {
  const previous = globalThis.fetch;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const controller = new AbortController();
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify({ summary: { ...emptyCosts, selector_provider_cost_exact: '0.000000000000000001' } }), { headers: { 'content-type': 'application/json' } });
  };
  try {
    const result = await selectorReports.costs({ start: '2026-09-01T00:00:00Z', end: '2026-09-02T00:00:00Z', model_group: 'team/model', before_operation_id: 'cursor' }, controller.signal);
    assert.equal(result.summary.selector_provider_cost_exact, '0.000000000000000001');
    assert.match(calls[0].url, /spend\/routing-costs/);
    assert.match(calls[0].url, /model_group=team%2Fmodel/);
    assert.match(calls[0].url, /before_operation_id=cursor/);
    assert.equal(calls[0].init?.signal, controller.signal);
    await selectorReports.evaluate(evaluationRequest(selectorPolicy(), '[{"expected_lane":"quality","failure":"selector_timeout"}]'), controller.signal);
    assert.equal(calls[1].url, '/ui/api/route-policy-evaluations');
    assert.equal(calls[1].init?.method, 'POST');
    assert.equal(calls[1].init?.signal, controller.signal);
  } finally { globalThis.fetch = previous; }
});

test('reporting transport preserves permission failure, never empty data', async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Outside your spend scope' }), { status: 403, headers: { 'content-type': 'application/json' } });
  try {
    await assert.rejects(selectorReports.costs({ start: 'a', end: 'b' }), /Outside your spend scope/);
  } finally { globalThis.fetch = previous; }
});
