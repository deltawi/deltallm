import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { OrganizationMembershipSummary } from '../src/components/admin/OrganizationMembershipSummary';
import { normalizeOrganizationPage } from '../src/lib/api/organizations';
import {
  formatOrganizationCount,
  normalizeOrganizationCount,
} from '../src/lib/organizationCounts';

function organizationPage(memberCount: unknown, teamCount: unknown) {
  return {
    data: [
      {
        organization_id: 'org-1',
        lifecycle_state: 'active',
        service_policy: {},
        capabilities: {},
        member_count: memberCount,
        team_count: teamCount,
      },
    ],
    pagination: { total: 1, limit: 20, offset: 0, has_more: false },
  };
}

test('organization list normalization preserves authoritative counts', () => {
  const populated = normalizeOrganizationPage(organizationPage(3, 2)).data[0];
  assert.equal(populated.member_count, 3);
  assert.equal(populated.team_count, 2);

  const empty = normalizeOrganizationPage(organizationPage(0, 0)).data[0];
  assert.equal(empty.member_count, 0);
  assert.equal(empty.team_count, 0);
});

test('organization counts fail closed to unavailable instead of zero', () => {
  for (const invalid of [undefined, null, -1, 1.5, '2', Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(normalizeOrganizationCount(invalid), null);
  }
  assert.equal(formatOrganizationCount(null), '—');

  const unavailable = normalizeOrganizationPage(organizationPage(undefined, -1)).data[0];
  assert.equal(unavailable.member_count, null);
  assert.equal(unavailable.team_count, null);
});

test('organization membership summary renders counts and unavailable state explicitly', () => {
  const populated = renderToStaticMarkup(
    createElement(OrganizationMembershipSummary, { memberCount: 3, teamCount: 1 }),
  );
  assert.match(populated, /aria-label="3 members"/);
  assert.match(populated, /aria-label="1 team"/);

  const empty = renderToStaticMarkup(
    createElement(OrganizationMembershipSummary, { memberCount: 0, teamCount: 0 }),
  );
  assert.match(empty, /aria-label="0 members"/);
  assert.match(empty, /aria-label="0 teams"/);

  const unavailable = renderToStaticMarkup(
    createElement(OrganizationMembershipSummary, { memberCount: undefined, teamCount: null }),
  );
  assert.match(unavailable, /aria-label="Member count unavailable"/);
  assert.match(unavailable, /aria-label="Team count unavailable"/);
  assert.match(unavailable, /—/);
});
