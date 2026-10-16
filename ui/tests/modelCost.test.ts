import assert from 'node:assert/strict';
import test from 'node:test';

import { fmtUnitPrice, summarizeModelCost } from '../src/lib/modelCost';

test('unit prices use two decimals from $1 and three significant digits below', () => {
  assert.equal(fmtUnitPrice(0), '$0');
  assert.equal(fmtUnitPrice(2.5), '$2.50');
  assert.equal(fmtUnitPrice(1234.5), '$1,234.50');
  assert.equal(fmtUnitPrice(0.6), '$0.60');
  assert.equal(fmtUnitPrice(0.02), '$0.02');
  assert.equal(fmtUnitPrice(0.006), '$0.006');
  assert.equal(fmtUnitPrice(0.0018333), '$0.00183');
  assert.equal(fmtUnitPrice(0.00000002), '<$0.0001');
});

test('chat models show input and output per 1M tokens with cached input on the unit line', () => {
  const summary = summarizeModelCost('chat', {
    input_cost_per_token: 2.5e-6,
    output_cost_per_token: 1e-5,
    input_cost_per_token_cache_hit: 1.25e-6,
    batch_price_multiplier: 0.5,
  });
  assert.equal(summary.kind, 'priced');
  if (summary.kind !== 'priced') return;
  assert.equal(summary.primary, '$2.50 / $10.00');
  assert.equal(summary.unit, 'per 1M tokens · cached $1.25');
  assert.deepEqual(summary.rates, [
    { label: 'Input', value: '$2.50 / 1M tokens' },
    { label: 'Cached input', value: '$1.25 / 1M tokens' },
    { label: 'Output', value: '$10.00 / 1M tokens' },
    { label: 'Batch', value: '50% of standard' },
  ]);
});

test('other model types lead with the price in their own unit', () => {
  const cases: Array<[string, Record<string, unknown>, string, string]> = [
    ['embedding', { input_cost_per_token: 2e-8 }, '$0.02', 'per 1M tokens'],
    ['rerank', { input_cost_per_token: 5e-8 }, '$0.05', 'per 1M tokens'],
    ['image_generation', { input_cost_per_image: 0.04 }, '$0.04', 'per image'],
    ['audio_speech', { input_cost_per_character: 2.2e-5, input_cost_per_second: 0.001 }, '$22.00', 'per 1M characters'],
    ['audio_speech', { input_cost_per_second: 0.001 }, '$0.06', 'per minute of audio'],
    ['audio_transcription', { input_cost_per_second: 0.0001 }, '$0.006', 'per minute of audio'],
    ['realtime', { input_cost_per_token: '0.000004', output_cost_per_token: '0.000016', input_cost_per_audio_token: '0.000032', output_cost_per_audio_token: '0.000064' },
      '$4.00 / $16.00', 'per 1M text tokens · audio $32.00 / $64.00'],
  ];
  for (const [mode, info, primary, unit] of cases) {
    const summary = summarizeModelCost(mode, info);
    assert.equal(summary.kind, 'priced', mode);
    if (summary.kind !== 'priced') continue;
    assert.equal(summary.primary, primary, mode);
    assert.equal(summary.unit, unit, mode);
  }
});

test('models without a standard price are not set, and explicit zero is free', () => {
  assert.deepEqual(summarizeModelCost('chat', {}), { kind: 'unset', rates: [] });
  assert.deepEqual(summarizeModelCost('chat', null), { kind: 'unset', rates: [] });
  assert.deepEqual(summarizeModelCost('chat', { input_cost_per_token: 'n/a', output_cost_per_token: -1 }), { kind: 'unset', rates: [] });
  const cachedOnly = summarizeModelCost('chat', { input_cost_per_token_cache_hit: 1e-7 });
  assert.equal(cachedOnly.kind, 'unset');
  assert.deepEqual(cachedOnly.rates, [{ label: 'Cached input', value: '$0.10 / 1M tokens' }]);
  const free = summarizeModelCost('chat', { input_cost_per_token: 0, output_cost_per_token: 0 });
  assert.equal(free.kind === 'priced' && free.primary, 'Free');
});
