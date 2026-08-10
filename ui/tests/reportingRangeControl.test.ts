import assert from 'node:assert/strict';
import test from 'node:test';

import { compactReportingRangeSelection } from '../src/lib/reportingRange';

test('compact range control keeps the applied custom value separate from its edit action', () => {
  assert.equal(compactReportingRangeSelection('30d', false), '30d');
  assert.equal(compactReportingRangeSelection('custom', false), 'custom-current');
  assert.equal(compactReportingRangeSelection('custom', true), 'custom-action');
});
