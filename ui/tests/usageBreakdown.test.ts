import assert from 'node:assert/strict';
import test from 'node:test';

import type { SpendCapabilities, SpendGroupRow } from '../src/lib/api';
import {
  lastUsagePageOffset,
  relativeUsageBarWidth,
  resolveUsageDimension,
  resolveUsageView,
  supportsCursorSpendLogs,
  usageDimensionLabel,
  usageGroupIdentity,
  usageGroupLabel,
  usageGroupSecondaryLabel,
  usageMetricLabel,
  usageMetricValue,
  usageModelLabel,
  verifiedReportingResponse,
} from '../src/lib/usageBreakdown';

const row: SpendGroupRow = {
  group_key: 'team-1',
  is_unassigned: false,
  display_name: 'Core team',
  total_spend: 12.5,
  total_tokens: 1500,
  prompt_tokens: 1000,
  completion_tokens: 500,
  request_count: 20,
};

test('usage breakdown labels managed owners without hiding their stable id', () => {
  assert.equal(usageGroupLabel('team', row), 'Core team');
  assert.equal(usageGroupSecondaryLabel(row), 'team-1');
  assert.equal(usageDimensionLabel('organization', true), 'Organizations');
});

test('usage breakdown always resolves to an allowed dimension', () => {
  assert.equal(resolveUsageDimension('organization', ['team', 'user']), 'team');
  assert.equal(resolveUsageDimension('user', ['team', 'user']), 'user');
  assert.equal(resolveUsageDimension('organization', []), 'organization');
});

test('usage view compatibility falls back safely for legacy reporting APIs', () => {
  assert.equal(resolveUsageView(undefined, 'self'), 'platform');
  assert.equal(supportsCursorSpendLogs(undefined), false);
  assert.equal(supportsCursorSpendLogs(1), false);
  assert.equal(supportsCursorSpendLogs(2), true);
  assert.equal(supportsCursorSpendLogs(3), true);

  const capabilities: SpendCapabilities = {
    visibility_level: 'team',
    active_view: 'team',
    default_view: 'team',
    available_views: ['team', 'self'],
    self_scoped: false,
    allowed_dimensions: ['team', 'user'],
    request_logs: false,
    user_identity_labels: false,
  };
  assert.equal(resolveUsageView(capabilities, 'self'), 'self');
  assert.equal(resolveUsageView(capabilities, 'organization'), 'team');
});

test('self usage capabilities expose only safe ownership partitions', () => {
  const capabilities: SpendCapabilities = {
    visibility_level: 'self',
    active_view: 'self',
    default_view: 'self',
    available_views: ['self'],
    self_scoped: true,
    allowed_dimensions: ['organization', 'team'],
    request_logs: false,
    user_identity_labels: false,
  };

  assert.deepEqual(capabilities.allowed_dimensions, ['organization', 'team']);
  assert.equal(capabilities.request_logs, false);
  assert.equal(capabilities.allowed_dimensions.includes('user'), false);
});

test('usage pagination clamps stale pages to the final valid offset', () => {
  assert.equal(lastUsagePageOffset(0, 12), 0);
  assert.equal(lastUsagePageOffset(12, 12), 0);
  assert.equal(lastUsagePageOffset(13, 12), 12);
  assert.equal(lastUsagePageOffset(25, 12), 24);
});

test('usage breakdown names unattributed data explicitly', () => {
  const unassigned = { ...row, group_key: null, is_unassigned: true, display_name: null };
  const literalSentinel = {
    ...row,
    group_key: '__unassigned__',
    is_unassigned: false,
    display_name: null,
  };
  assert.equal(usageGroupLabel('user', unassigned), 'Unassigned user');
  assert.equal(usageGroupSecondaryLabel(unassigned), null);
  assert.equal(usageModelLabel(unassigned), 'Unspecified model');
  assert.equal(usageGroupLabel('user', literalSentinel), '__unassigned__');
  assert.equal(usageGroupIdentity(unassigned), 'unassigned');
  assert.equal(usageGroupIdentity(literalSentinel), 'assigned:__unassigned__');
});

test('usage breakdown uses one measure consistently for values and labels', () => {
  assert.equal(usageMetricValue(row, 'spend'), 12.5);
  assert.equal(usageMetricValue(row, 'tokens'), 1500);
  assert.equal(usageMetricLabel('spend'), 'USD');
  assert.equal(usageMetricLabel('tokens'), 'Tokens');
});

test('usage bars remain truthful for zero and tiny positive values', () => {
  assert.equal(relativeUsageBarWidth(0, 100, 3), 0);
  assert.equal(relativeUsageBarWidth(-1, 100, 3), 0);
  assert.equal(relativeUsageBarWidth(Number.NaN, 100, 3), 0);
  assert.equal(relativeUsageBarWidth(0.1, 100, 3), 3);
  assert.equal(relativeUsageBarWidth(50, 100, 3), 50);
  assert.equal(relativeUsageBarWidth(200, 100, 3), 100);
});

test('reporting responses must confirm the requested v2 visibility view', () => {
  const response = {
    reporting_context: { api_version: 2, active_view: 'self' },
    total_spend: 1,
  };

  assert.equal(verifiedReportingResponse(response, 'self'), response);
  assert.throws(
    () => verifiedReportingResponse({ total_spend: 1 }, 'self'),
    /updated reporting service/,
  );
  assert.throws(
    () => verifiedReportingResponse(response, 'team'),
    /updated reporting service/,
  );
  assert.throws(
    () => verifiedReportingResponse({ ...response, reporting_context: { api_version: 1, active_view: 'self' } }, 'self'),
    /updated reporting service/,
  );
});
