import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildPolicyFromGuided,
  effectivePolicyMemberIds,
  LEGACY_TAG_ROUTING_STRATEGY,
  reconcileGuidedPolicyMembers,
  restoreDraftPolicyTombstones,
  ROUTE_GROUP_STRATEGY_OPTIONS,
  routeGroupStrategyOptions,
  toGuidedPolicy,
  validateGuidedPolicy,
  validatePolicyContextCompatibility,
  withGuidedPolicyStrategy,
  type PolicyMemberOption,
} from '../src/lib/routeGroups';

const MEMBERS: PolicyMemberOption[] = [
  { deployment_id: 'dep-a', enabled: true, weight: 2, priority: 0 },
  { deployment_id: 'dep-b', enabled: true, weight: 3, priority: 1 },
  { deployment_id: 'dep-off', enabled: false, weight: 4, priority: 2 },
];

test('deprecated tag routing is visible only for an existing legacy selection', () => {
  assert.equal(
    (ROUTE_GROUP_STRATEGY_OPTIONS as readonly string[]).includes(LEGACY_TAG_ROUTING_STRATEGY),
    false,
  );
  assert.equal(routeGroupStrategyOptions('weighted').includes(LEGACY_TAG_ROUTING_STRATEGY), false);
  assert.equal(routeGroupStrategyOptions(LEGACY_TAG_ROUTING_STRATEGY)[0], LEGACY_TAG_ROUTING_STRATEGY);
});

test('guided policy round-trips weights, fallback order, zero retries, and opaque fields', () => {
  const base = {
    mode: 'fallback',
    members: [
      { deployment_id: 'dep-b', weight: 7, priority: 0, server_hint: 'keep-me' },
      { deployment_id: 'dep-a', weight: 5, priority: 1 },
    ],
    retry: { max_attempts: 0, server_retry_hint: true },
    timeouts: { global_ms: 9000, provider_budget_ms: 8000 },
    context: {
      mode: 'smallest-sufficient',
      unknown_capacity: 'exclude',
      default_output_tokens: 2048,
      safety_margin_tokens: 512,
      server_capacity_source: 'catalog',
    },
    server_policy_hint: 'preserved',
  };

  const guided = toGuidedPolicy(base, MEMBERS);
  assert.equal(guided.strategy, 'priority-based-routing');
  assert.equal(guided.memberSelection, 'explicit');
  assert.deepEqual(guided.memberIds, ['dep-b', 'dep-a']);
  assert.deepEqual(guided.memberWeights, { 'dep-a': '5', 'dep-b': '7', 'dep-off': '' });
  assert.equal(guided.retryMaxAttempts, '0');
  assert.equal(guided.contextMode, 'smallest-sufficient');
  assert.equal(guided.contextUnknownCapacity, 'exclude');

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
  assert.deepEqual(rebuilt.context, base.context);
  assert.equal('mode' in rebuilt, false);
});

test('explicit empty membership stays empty and is rejected locally', () => {
  const guided = toGuidedPolicy({ mode: 'weighted', members: [] }, MEMBERS);
  assert.equal(guided.memberSelection, 'explicit');
  assert.deepEqual(guided.memberIds, []);

  const reconciled = reconcileGuidedPolicyMembers(guided, MEMBERS);
  assert.deepEqual(reconciled.memberIds, []);
  assert.equal(
    validateGuidedPolicy(reconciled, MEMBERS, 'chat'),
    'Select at least one enabled deployment.',
  );
  assert.deepEqual(buildPolicyFromGuided({}, reconciled).members, []);
});

test('inherited membership follows enabled group members without serializing a subset', () => {
  const guided = toGuidedPolicy({ strategy: 'least-busy' }, MEMBERS);
  assert.equal(guided.memberSelection, 'inherit');
  assert.deepEqual(guided.memberIds, ['dep-a', 'dep-b']);

  const changedMembers = [
    { ...MEMBERS[0], enabled: false },
    MEMBERS[1],
    { deployment_id: 'dep-c', enabled: true, weight: 1, priority: 2 },
  ];
  const reconciled = reconcileGuidedPolicyMembers(guided, changedMembers);
  assert.deepEqual(reconciled.memberIds, ['dep-b', 'dep-c']);
  assert.equal('members' in buildPolicyFromGuided({}, reconciled), false);
});

test('effective policy membership filters explicit, disabled, unknown, and duplicate members', () => {
  assert.deepEqual(effectivePolicyMemberIds({}, MEMBERS), ['dep-a', 'dep-b']);
  assert.deepEqual(effectivePolicyMemberIds({
    members: [
      { deployment_id: 'dep-b' },
      { deployment_id: 'dep-off' },
      { deployment_id: 'dep-a', enabled: false },
      { deployment_id: 'missing' },
      { deployment_id: 'dep-b' },
    ],
  }, MEMBERS), ['dep-b']);
});

test('legacy policy mode is read as a canonical strategy and omitted on write', () => {
  const guided = toGuidedPolicy({}, MEMBERS);
  const fallback = toGuidedPolicy({ mode: 'fallback' }, MEMBERS);
  assert.equal(fallback.strategy, 'priority-based-routing');
  assert.equal('mode' in buildPolicyFromGuided({ mode: 'fallback' }, fallback), false);

  const weighted = withGuidedPolicyStrategy(guided, 'weighted');
  assert.equal(weighted.strategy, 'weighted');
  const leastBusy = withGuidedPolicyStrategy(weighted, 'least-busy');
  assert.equal(leastBusy.strategy, 'least-busy');

  const conflicting = toGuidedPolicy({
    mode: 'weighted',
    strategy: 'priority-based-routing',
  }, MEMBERS);
  assert.equal(conflicting.strategy, 'priority-based-routing');
});

test('guided validation matches backend integer and retry-class constraints', () => {
  const guided = {
    ...toGuidedPolicy({ members: [{ deployment_id: 'dep-a' }] }, MEMBERS),
    memberWeights: { 'dep-a': '0' },
  };
  assert.match(validateGuidedPolicy(guided, MEMBERS, 'chat') || '', /greater than or equal to 1/);
  assert.match(
    validateGuidedPolicy(
      { ...guided, memberWeights: { 'dep-a': '1' }, retryableErrors: '5xx' },
      MEMBERS,
      'chat',
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

test('guided context routing is opt-in and validates non-negative token controls', () => {
  const disabled = toGuidedPolicy({}, MEMBERS);
  assert.equal(disabled.contextMode, 'disabled');
  assert.equal('context' in buildPolicyFromGuided({}, disabled), false);

  const enabled = {
    ...disabled,
    contextMode: 'eligible-only' as const,
    contextDefaultOutputTokens: '1024',
    contextSafetyMarginTokens: '256',
  };
  assert.deepEqual(buildPolicyFromGuided({}, enabled).context, {
    mode: 'eligible-only',
    unknown_capacity: 'allow',
    default_output_tokens: 1024,
    safety_margin_tokens: 256,
  });
  assert.match(
    validateGuidedPolicy(
      { ...enabled, contextSafetyMarginTokens: '-1' },
      MEMBERS,
      'chat',
    ) || '',
    /safety margin/,
  );
  assert.match(validateGuidedPolicy(enabled, MEMBERS, 'rerank') || '', /current mode is rerank/);
  assert.match(
    validatePolicyContextCompatibility({ context: {} }, 'image_generation') || '',
    /current mode is image_generation/,
  );
  assert.equal(validatePolicyContextCompatibility({ context: {} }, 'embedding'), null);
  assert.equal(validatePolicyContextCompatibility({ context: null }, 'rerank'), null);
});

test('guided context disable emits an explicit tombstone without retaining opaque fields', () => {
  const base = {
    strategy: 'weighted',
    context: {
      mode: 'smallest-sufficient',
      server_capacity_source: 'catalog',
    },
  };
  const guided = { ...toGuidedPolicy(base, MEMBERS), contextMode: 'disabled' as const };

  assert.deepEqual(buildPolicyFromGuided(base, guided), {
    strategy: 'weighted',
    context: null,
  });
});

test('saved draft restores context deletion tombstone before direct publication', () => {
  const published = {
    strategy: 'weighted',
    context: {
      mode: 'eligible-only',
      unknown_capacity: 'exclude',
    },
  };
  const storedDraft = { strategy: 'least-busy', server_policy_hint: 'keep-me' };

  const editableDraft = restoreDraftPolicyTombstones(storedDraft, published);
  const guided = toGuidedPolicy(editableDraft, MEMBERS);
  const publishDocument = buildPolicyFromGuided(editableDraft, guided);

  assert.deepEqual(editableDraft, {
    strategy: 'least-busy',
    server_policy_hint: 'keep-me',
    context: null,
  });
  assert.equal(guided.contextMode, 'disabled');
  assert.equal(publishDocument.context, null);
  assert.equal(publishDocument.server_policy_hint, 'keep-me');
});

test('draft without context stays omission when the published policy has no context', () => {
  const storedDraft = { strategy: 'weighted' };

  assert.deepEqual(
    restoreDraftPolicyTombstones(storedDraft, { strategy: 'least-busy' }),
    storedDraft,
  );
  assert.deepEqual(restoreDraftPolicyTombstones(storedDraft, null), storedDraft);
});
