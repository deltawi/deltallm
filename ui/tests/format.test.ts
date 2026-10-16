import assert from 'node:assert/strict';
import test from 'node:test';

import { fmtDuration, fmtSpendAxis, fmtSpendPrecise, fmtSpendValue } from '../src/lib/format';

test('spend value formatting never rounds real micro-spend down to zero', () => {
  assert.equal(fmtSpendValue(0), '$0');
  assert.equal(fmtSpendValue(12.5), '$12.50');
  assert.equal(fmtSpendValue(0.125), '$0.125');
  assert.equal(fmtSpendValue(0.00000242), '$0.00000242');
  assert.equal(fmtSpendValue(-0.00000242), '-$0.00000242');
  assert.equal(fmtSpendValue(0.0000000014), '$1.40e-9');
});

test('spend axis formatting preserves meaningful micro-spend values', () => {
  assert.equal(fmtSpendAxis(0), '$0');
  assert.equal(fmtSpendAxis(0.42), '$0.42');
  assert.equal(fmtSpendAxis(12.5), '$12.5');
  assert.equal(fmtSpendAxis(1_250), '$1.3K');
  assert.equal(fmtSpendAxis(0.000014), '$0.000014');
  assert.equal(fmtSpendAxis(0.000028), '$0.000028');
  assert.notEqual(fmtSpendAxis(0.000014), fmtSpendAxis(0.000028));
  assert.equal(fmtSpendAxis(0.00000014), '$1.4e-7');
  assert.equal(fmtSpendAxis(-0.000014), '-$0.000014');
  assert.equal(fmtSpendAxis(Number.NaN), '$0');
});

test('precise spend formatting retains tooltip detail', () => {
  assert.equal(fmtSpendPrecise(0), '$0.0000');
  assert.equal(fmtSpendPrecise(0.00005275), '$0.00005275');
  assert.equal(fmtSpendPrecise(12.5), '$12.50');
  assert.equal(fmtSpendPrecise(0.0000000014), '$1.40e-9');
});

test('durations read in ms under a second and in larger units above it', () => {
  assert.equal(fmtDuration(0), '0 ms');
  assert.equal(fmtDuration(41), '41 ms');
  assert.equal(fmtDuration(999.4), '999 ms');
  assert.equal(fmtDuration(999.6), '1.00 s');
  assert.equal(fmtDuration(1_234), '1.23 s');
  assert.equal(fmtDuration(9_999), '10.0 s');
  assert.equal(fmtDuration(30_000), '30.0 s');
  assert.equal(fmtDuration(59_960), '1m');
  assert.equal(fmtDuration(125_400), '2m 5s');
  assert.equal(fmtDuration(3_599_600), '1h');
  assert.equal(fmtDuration(5_400_000), '1h 30m');
  assert.equal(fmtDuration(null), '—');
  assert.equal(fmtDuration(undefined), '—');
  assert.equal(fmtDuration(Number.NaN), '—');
  assert.equal(fmtDuration(-1), '—');
});
