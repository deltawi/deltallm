import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_KEY_LIST_SORT,
  EMPTY_KEY_LIST_FILTERS,
  activeKeyFilterCount,
  isKeySortKey,
  keyListQuery,
} from '../src/lib/apiKeyListFilters';
import { withQuery } from '../src/lib/api/transport';

test('empty key filters send only the default sort', () => {
  assert.equal(activeKeyFilterCount(EMPTY_KEY_LIST_FILTERS), 0);
  assert.equal(
    withQuery('/ui/api/keys', keyListQuery(EMPTY_KEY_LIST_FILTERS, DEFAULT_KEY_LIST_SORT)),
    '/ui/api/keys?sort_by=created_at&sort_direction=desc',
  );
});

test('key filters map to the list API parameters', () => {
  const filters = {
    team_id: 'team-a',
    organization_id: 'org-a',
    owner: 'service_account' as const,
    status: 'expiring' as const,
    last_used: 'inactive_30d' as const,
    key_type: 'coding_agent' as const,
  };
  assert.equal(activeKeyFilterCount(filters), 6);
  assert.deepEqual(keyListQuery(filters, { key: 'budget_used', direction: 'desc' }), {
    team_id: 'team-a',
    organization_id: 'org-a',
    owner: 'service_account',
    status: 'expiring',
    last_used: 'inactive_30d',
    coding_agent: true,
    sort_by: 'budget_used',
    sort_direction: 'desc',
  });
  assert.equal(keyListQuery({ ...EMPTY_KEY_LIST_FILTERS, key_type: 'standard' }, DEFAULT_KEY_LIST_SORT).coding_agent, false);
});

test('only list sort keys are accepted from table headers', () => {
  assert.equal(isKeySortKey('last_used'), true);
  assert.equal(isKeySortKey('rpm_limit'), false);
});
