import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM } from 'jsdom';

import RouteGroupQualityCell from '../src/components/route-groups/RouteGroupQualityCell';
import type { RouteGroupMemberQuality } from '../src/lib/api';
import { policyChanges } from '../src/lib/routeGroupPolicyDiff';
import { formatFirstToken, qualityScore, qualityTone, readQualitySetting } from '../src/lib/routeGroupQuality';
import { buildSimpleRoutingPolicy, readSimpleRouting } from '../src/lib/routeGroupRouting';
import { buildPolicyFromGuided, toGuidedPolicy } from '../src/lib/routeGroups';

const MEMBERS = [
  { deployment_id: 'dep-a', enabled: true, weight: null, priority: null, model_name: 'gpt-4o', provider: 'openai', healthy: true },
  { deployment_id: 'dep-b', enabled: true, weight: null, priority: null, model_name: 'glm-4.6', provider: 'zai', healthy: true },
];

const QUALITY: RouteGroupMemberQuality = {
  deployment_id: 'dep-b', calls: 63, failed_calls: 12, provider_errors: 2, empty_answers: 1, bad_tool_calls: 9, bad_json: 0,
  failure_rate: 12 / 63, avg_first_token_ms: 640, output_tokens_per_second: 48, sent_last_reason: 'failed_calls',
};

test('reads the quality block and rejects shapes the form cannot show', () => {
  assert.equal(readQualitySetting(undefined), null);
  assert.deepEqual(readQualitySetting({ deprioritize: true }), { deprioritize: true, sensitivity: 'balanced' });
  assert.equal(readQualitySetting({ deprioritize: true, sensitivity: 'loose' }), undefined);
  assert.equal(readQualitySetting({ deprioritize: true, threshold: 0.3 }), undefined);
});

test('simple routing keeps the quality block when it reads and builds a policy', () => {
  const live = { strategy: 'weighted', quality: { deprioritize: true, sensitivity: 'relaxed' } };
  const { routing } = readSimpleRouting(live, null, MEMBERS);
  assert.ok(routing);
  assert.deepEqual(routing.quality, { deprioritize: true, sensitivity: 'relaxed' });
  assert.deepEqual(buildSimpleRoutingPolicy(routing, MEMBERS).quality, { deprioritize: true, sensitivity: 'relaxed' });
  assert.equal('quality' in buildSimpleRoutingPolicy({ ...routing, quality: null }, MEMBERS), false);
});

test('advanced routing reads, changes and keeps the quality block', () => {
  const live = { strategy: 'weighted', quality: { deprioritize: true, sensitivity: 'strict' } };
  const guided = toGuidedPolicy(live, []);
  assert.deepEqual(guided.quality, { deprioritize: true, sensitivity: 'strict' });
  const off = buildPolicyFromGuided(live, { ...guided, quality: { deprioritize: false, sensitivity: 'strict' } });
  assert.deepEqual(off.quality, { deprioritize: false, sensitivity: 'strict' });

  const odd = { strategy: 'weighted', quality: { deprioritize: true, max_failure_rate: 0.3 } };
  const unreadable = toGuidedPolicy(odd, []);
  assert.equal(unreadable.quality, undefined);
  assert.deepEqual(buildPolicyFromGuided(odd, unreadable).quality, odd.quality);
});

test('policy history names a quality change', () => {
  const changes = policyChanges({ strategy: 'weighted' }, { strategy: 'weighted', quality: { deprioritize: true, sensitivity: 'strict' } });
  assert.deepEqual(changes, [{ label: 'Send low-quality models last', before: 'Off', after: 'Strict' }]);
});

test('scores and formats quality numbers', () => {
  const score = qualityScore(QUALITY)!;
  assert.equal(score.toFixed(1), '81.0');
  assert.equal(qualityTone(score), 'warn');
  assert.equal(qualityTone(99.6), 'good');
  assert.equal(qualityTone(60), 'bad');
  assert.equal(formatFirstToken(1250), '1.3 s');
  assert.equal(qualityScore({ ...QUALITY, calls: 0, failure_rate: null }), null);
});

test('quality cell shows the score, the calls and the details on focus', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost' });
  const previous = Object.getOwnPropertyDescriptors(globalThis);
  const values = { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true };
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value });
  const root = createRoot(document.getElementById('root')!);
  try {
    await act(async () => root.render(<RouteGroupQualityCell quality={QUALITY} modelLabel="glm-4.6" windowLabel="last 15 min" />));
    const cell = document.querySelector('button')!;
    assert.match(cell.textContent ?? '', /81\.0%63 callsSent last/);
    await act(async () => cell.focus());
    const tooltip = document.querySelector('[role="tooltip"]');
    assert.ok(tooltip);
    assert.match(tooltip.textContent ?? '', /glm-4\.6 · last 15 min/);
    assert.match(tooltip.textContent ?? '', /Bad tool calls9/);
    assert.match(tooltip.textContent ?? '', /First token \(avg\)640 ms/);

    await act(async () => root.render(<RouteGroupQualityCell quality={undefined} modelLabel="gpt-4o" windowLabel="last 15 min" />));
    assert.match(document.body.textContent ?? '', /No calls yet/);
  } finally {
    await act(async () => root.unmount());
    dom.window.close();
    for (const key of Object.keys(values)) {
      if (previous[key]) Object.defineProperty(globalThis, key, previous[key]);
      else Reflect.deleteProperty(globalThis, key);
    }
  }
});
