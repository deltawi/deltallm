import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildPolicyFromGuided,
  reconcileGuidedPolicyMembers,
  toGuidedPolicy,
  validateGuidedPolicy,
  withGuidedPolicyMode,
  withGuidedPolicyStrategy,
  type PolicyMemberOption,
} from '../src/lib/routeGroups';

const MEMBERS: PolicyMemberOption[] = [
  { deployment_id: 'dep-a', enabled: true, weight: 2, priority: 0 },
  { deployment_id: 'dep-b', enabled: true, weight: 3, priority: 1 },
  { deployment_id: 'dep-off', enabled: false, weight: 4, priority: 2 },
];

test('guided policy round-trips weights, fallback order, zero retries, and opaque fields', () => {
  const base = {
    mode: 'fallback',
    members: [
      { deployment_id: 'dep-b', weight: 7, priority: 0, server_hint: 'keep-me' },
      { deployment_id: 'dep-a', weight: 5, priority: 1 },
    ],
    retry: { max_attempts: 0, server_retry_hint: true },
    timeouts: { global_ms: 9000, provider_budget_ms: 8000 },
    server_policy_hint: 'preserved',
  };

  const guided = toGuidedPolicy(base, MEMBERS);
  assert.equal(guided.mode, 'fallback');
  assert.equal(guided.strategy, 'priority-based-routing');
  assert.equal(guided.memberSelection, 'explicit');
  assert.deepEqual(guided.memberIds, ['dep-b', 'dep-a']);
  assert.deepEqual(guided.memberWeights, { 'dep-a': '5', 'dep-b': '7', 'dep-off': '' });
  assert.equal(guided.retryMaxAttempts, '0');

  const rebuilt = buildPolicyFromGuided(base, {
    ...guided,
    memberWeights: { ...guided.memberWeights, 'dep-b': '11' },
  });
  assert.deepEqual(rebuilt.members, [
    {
      deployment_id: 'dep-b',
      enabled: true,
      weight: 11,
      priority: 0,
      server_hint: 'keep-me',
    },
    { deployment_id: 'dep-a', enabled: true, weight: 5, priority: 1 },
  ]);
  assert.deepEqual(rebuilt.retry, { max_attempts: 0, server_retry_hint: true });
  assert.deepEqual(rebuilt.timeouts, { global_ms: 9000, provider_budget_ms: 8000 });
  assert.equal(rebuilt.server_policy_hint, 'preserved');
});

test('explicit empty membership stays empty and is rejected locally', () => {
  const guided = toGuidedPolicy({ mode: 'weighted', members: [] }, MEMBERS);
  assert.equal(guided.memberSelection, 'explicit');
  assert.deepEqual(guided.memberIds, []);

  const reconciled = reconcileGuidedPolicyMembers(guided, MEMBERS);
  assert.deepEqual(reconciled.memberIds, []);
  assert.equal(
    validateGuidedPolicy(reconciled, MEMBERS),
    'Select at least one enabled deployment.',
  );
  assert.deepEqual(buildPolicyFromGuided({}, reconciled).members, []);
});

test('inherited membership follows enabled group members without serializing a subset', () => {
  const guided = toGuidedPolicy({ strategy: 'least-busy' }, MEMBERS);
  assert.equal(guided.memberSelection, 'inherit');
  assert.deepEqual(guided.memberIds, ['dep-a', 'dep-b']);
  assert.equal(guided.mode, null);

  const changedMembers = [
    { ...MEMBERS[0], enabled: false },
    MEMBERS[1],
    { deployment_id: 'dep-c', enabled: true, weight: 1, priority: 2 },
  ];
  const reconciled = reconcileGuidedPolicyMembers(guided, changedMembers);
  assert.deepEqual(reconciled.memberIds, ['dep-b', 'dep-c']);
  assert.equal('members' in buildPolicyFromGuided({}, reconciled), false);
});

test('guided mode and concrete strategy remain synchronized', () => {
  const guided = toGuidedPolicy({}, MEMBERS);
  const fallback = withGuidedPolicyMode(guided, 'fallback');
  assert.equal(fallback.strategy, 'priority-based-routing');
  assert.equal(fallback.mode, 'fallback');

  const weighted = withGuidedPolicyStrategy(fallback, 'weighted');
  assert.equal(weighted.mode, 'weighted');
  const leastBusy = withGuidedPolicyStrategy(weighted, 'least-busy');
  assert.equal(leastBusy.mode, null);

  const conflicting = toGuidedPolicy({
    mode: 'weighted',
    strategy: 'priority-based-routing',
  }, MEMBERS);
  assert.equal(conflicting.mode, 'fallback');
});

test('guided validation matches backend integer and retry-class constraints', () => {
  const guided = {
    ...toGuidedPolicy({ members: [{ deployment_id: 'dep-a' }] }, MEMBERS),
    memberWeights: { 'dep-a': '0' },
  };
  assert.match(validateGuidedPolicy(guided, MEMBERS) || '', /greater than or equal to 1/);
  assert.match(
    validateGuidedPolicy(
      { ...guided, memberWeights: { 'dep-a': '1' }, retryableErrors: '5xx' },
      MEMBERS,
    ) || '',
    /Unsupported retryable error class/,
  );
});

test('guided timeout normalizes seconds to its single millisecond control', () => {
  const guided = toGuidedPolicy({
    timeouts: { global_seconds: 1.5, server_timeout_hint: true },
  }, MEMBERS);
  assert.equal(guided.timeoutMs, '1500');
  assert.deepEqual(buildPolicyFromGuided({
    timeouts: { global_seconds: 1.5, server_timeout_hint: true },
  }, guided).timeouts, {
    global_ms: 1500,
    server_timeout_hint: true,
  });
});
