import assert from 'node:assert/strict';
import test from 'node:test';
import {
  emptyCapacityPoolForm,
  emptyModelPolicyForm,
  capacityPoolFormToPayload,
  capacityPoolsToPayload,
  capacityPoolToPayload,
  isAssignableTierVersion,
  modelPolicyFormToPayload,
  modelPoliciesToPayload,
  modelPolicyToForm,
  modelPolicyToPayload,
  parseNonNegativeIntegerInput,
  parsePositiveIntegerInput,
  poolOptionsForCallable,
  summarizeSimulation,
  tierAssignmentRequiresActiveVersion,
} from '../src/lib/tiers';
import type { TierPolicySimulation } from '../src/lib/api';

test('modelPolicyFormToPayload normalizes optional limits and pricing', () => {
  const payload = modelPolicyFormToPayload({
    ...emptyModelPolicyForm(),
    callable_key: 'gpt-4o-mini',
    rpm_limit: '120',
    tpm_limit: '',
    input_cost_per_token: '0.01',
    output_cost_per_token: '0.02',
    cached_input_cost_per_token: '0.005',
    capacity_pool_key: 'shared-chat',
    priority: '3',
  });

  assert.equal(payload.callable_key, 'gpt-4o-mini');
  assert.equal(payload.rpm_limit, 120);
  assert.equal(payload.tpm_limit, null);
  assert.deepEqual(payload.pricing, {
    input_cost_per_token: 0.01,
    output_cost_per_token: 0.02,
    input_cost_per_token_cache_hit: 0.005,
  });
  assert.equal(payload.capacity_pool_key, 'shared-chat');
  assert.equal(payload.priority, 3);
});

test('model policy payload helpers preserve metadata and strip response-only fields', () => {
  const existing = {
    tier_model_policy_id: 'policy-1',
    tier_version_id: 'version-1',
    callable_key: 'gpt-4o-mini',
    enabled: true,
    access_mode: 'allow',
    rpm_limit: 120,
    tpm_limit: null,
    pricing: { input_cost_per_token: 0.01 },
    capacity_pool_key: 'shared-chat',
    priority: 3,
    metadata: { source: 'api' },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
  const fromForm = modelPolicyFormToPayload({
    ...modelPolicyToForm(existing),
    rpm_limit: '240',
  }, existing);
  const sanitized = modelPolicyToPayload(existing);

  assert.deepEqual(fromForm.metadata, { source: 'api' });
  assert.deepEqual(fromForm.pricing, { input_cost_per_token: 0.01 });
  assert.deepEqual(sanitized.metadata, { source: 'api' });
  assert.equal('tier_model_policy_id' in sanitized, false);
  assert.equal('created_at' in sanitized, false);
  assert.deepEqual(modelPoliciesToPayload([existing])[0], sanitized);
});

test('modelPolicyFormToPayload preserves hidden pricing keys while editing visible fields', () => {
  const existing = {
    callable_key: 'gpt-4o-mini',
    enabled: true,
    access_mode: 'allow',
    rpm_limit: 120,
    tpm_limit: null,
    pricing: {
      input_cost_per_token: 0.01,
      output_cost_per_token: 0.02,
      output_cost_per_token_cache_hit: 0.015,
      batch_price_multiplier: 0.5,
      input_cost_per_image: 0.04,
      cost_per_request: 0.001,
    },
    capacity_pool_key: 'shared-chat',
    priority: 3,
    metadata: { source: 'api' },
  };

  const payload = modelPolicyFormToPayload({
    ...modelPolicyToForm(existing),
    input_cost_per_token: '',
    output_cost_per_token: '0.025',
  }, existing);

  assert.deepEqual(payload.pricing, {
    output_cost_per_token: 0.025,
    output_cost_per_token_cache_hit: 0.015,
    batch_price_multiplier: 0.5,
    input_cost_per_image: 0.04,
    cost_per_request: 0.001,
  });
});

test('modelPolicyFormToPayload preserves all pricing during bulk-style limit edits', () => {
  const existing = {
    callable_key: 'gpt-4o-mini',
    enabled: true,
    access_mode: 'allow',
    rpm_limit: 120,
    tpm_limit: null,
    pricing: {
      input_cost_per_token: 0.01,
      output_cost_per_token: 0.02,
      output_cost_per_token_cache_hit: 0.015,
      batch_price_multiplier: 0.5,
      input_cost_per_audio_token: 0.0001,
    },
    capacity_pool_key: 'shared-chat',
    priority: 3,
    metadata: { source: 'api' },
  };

  const payload = modelPolicyFormToPayload({
    ...modelPolicyToForm(existing),
    rpm_limit: '240',
  }, existing);

  assert.equal(payload.rpm_limit, 240);
  assert.deepEqual(payload.pricing, existing.pricing);
});

test('modelPolicyFormToPayload rejects partial or non-positive numeric limits', () => {
  assert.throws(
    () => modelPolicyFormToPayload({
      ...emptyModelPolicyForm(),
      callable_key: 'gpt-4o-mini',
      rpm_limit: '120abc',
    }),
    /RPM must be a positive integer/,
  );
  assert.throws(
    () => modelPolicyFormToPayload({
      ...emptyModelPolicyForm(),
      callable_key: 'gpt-4o-mini',
      tpm_limit: '0',
    }),
    /TPM must be a positive integer/,
  );
});

test('capacityPoolFormToPayload normalizes optional shared capacity fields', () => {
  const payload = capacityPoolFormToPayload({
    ...emptyCapacityPoolForm(),
    pool_key: 'shared-chat',
    callable_key: 'gpt-4o-mini',
    rpm_capacity: '1000',
    saturation_threshold: '0.9',
  });

  assert.equal(payload.pool_key, 'shared-chat');
  assert.equal(payload.callable_key, 'gpt-4o-mini');
  assert.equal(payload.rpm_capacity, 1000);
  assert.equal(payload.tpm_capacity, null);
  assert.equal(payload.saturation_threshold, 0.9);
});

test('capacity pool payload helpers preserve metadata and strip response-only fields', () => {
  const existing = {
    tier_capacity_pool_id: 'pool-1',
    tier_version_id: 'version-1',
    pool_key: 'shared-chat',
    callable_key: 'gpt-4o-mini',
    rpm_capacity: 1000,
    tpm_capacity: null,
    max_parallel_requests: null,
    strategy: 'weighted_fair',
    saturation_threshold: 0.9,
    burst_multiplier: null,
    metadata: { source: 'api' },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
  const fromForm = capacityPoolFormToPayload({
    ...emptyCapacityPoolForm(),
    pool_key: 'shared-chat',
    callable_key: 'gpt-4o-mini',
    rpm_capacity: '1200',
  }, existing);
  const sanitized = capacityPoolToPayload(existing);

  assert.deepEqual(fromForm.metadata, { source: 'api' });
  assert.deepEqual(sanitized.metadata, { source: 'api' });
  assert.equal('tier_capacity_pool_id' in sanitized, false);
  assert.equal('created_at' in sanitized, false);
  assert.deepEqual(capacityPoolsToPayload([existing])[0], sanitized);
});

test('poolOptionsForCallable filters and deduplicates by callable key', () => {
  const pools = [
    { pool_key: 'shared-chat', callable_key: 'gpt-4o-mini' },
    { pool_key: 'shared-chat', callable_key: 'gpt-4o-mini' },
    { pool_key: 'shared-chat', callable_key: 'gpt-4o' },
    { pool_key: 'burst', callable_key: 'gpt-4o-mini' },
  ];

  assert.deepEqual(poolOptionsForCallable(pools, 'gpt-4o-mini'), [
    { pool_key: 'burst', callable_key: 'gpt-4o-mini' },
    { pool_key: 'shared-chat', callable_key: 'gpt-4o-mini' },
  ]);
  assert.deepEqual(poolOptionsForCallable(pools, ''), []);
  assert.deepEqual(poolOptionsForCallable(pools).map((pool) => `${pool.pool_key}:${pool.callable_key}`), [
    'burst:gpt-4o-mini',
    'shared-chat:gpt-4o',
    'shared-chat:gpt-4o-mini',
  ]);
});

test('capacityPoolFormToPayload validates ratio and burst numeric bounds', () => {
  assert.throws(
    () => capacityPoolFormToPayload({
      ...emptyCapacityPoolForm(),
      pool_key: 'shared-chat',
      callable_key: 'gpt-4o-mini',
      saturation_threshold: '1.2',
    }),
    /Saturation threshold must be greater than 0 and less than or equal to 1/,
  );
  assert.throws(
    () => capacityPoolFormToPayload({
      ...emptyCapacityPoolForm(),
      pool_key: 'shared-chat',
      callable_key: 'gpt-4o-mini',
      burst_multiplier: '0.5',
    }),
    /Burst multiplier must be greater than or equal to 1/,
  );
});

test('integer input helpers reject partial and unsafe values', () => {
  assert.equal(parsePositiveIntegerInput('42', 'Requests'), 42);
  assert.equal(parseNonNegativeIntegerInput('0', 'Prompt tokens'), 0);
  assert.throws(
    () => parsePositiveIntegerInput('42abc', 'Requests'),
    /Requests must be a positive integer/,
  );
  assert.throws(
    () => parseNonNegativeIntegerInput('-1', 'Prompt tokens'),
    /Prompt tokens must be a non-negative integer/,
  );
});

test('tier assignment helpers require active pinned versions for current enabled windows', () => {
  const now = new Date('2026-06-22T12:00:00Z');

  assert.equal(tierAssignmentRequiresActiveVersion({ enabled: true, ends_at: null }, now), true);
  assert.equal(tierAssignmentRequiresActiveVersion({ enabled: true, ends_at: '2026-06-22T13:00:00Z' }, now), true);
  assert.equal(tierAssignmentRequiresActiveVersion({ enabled: true, ends_at: '2026-06-22T11:59:59Z' }, now), false);
  assert.equal(tierAssignmentRequiresActiveVersion({ enabled: false, ends_at: null }, now), false);
  assert.equal(tierAssignmentRequiresActiveVersion({ enabled: true, ends_at: 'not-a-date' }, now), true);

  assert.equal(isAssignableTierVersion({ status: 'active' }, true), true);
  assert.equal(isAssignableTierVersion({ status: 'draft' }, true), false);
  assert.equal(isAssignableTierVersion({ status: 'archived' }, false), true);
});

test('summarizeSimulation highlights static limit overflow', () => {
  const summary = summarizeSimulation({
    access: {
      allowed: true,
      reason: 'tier_policy_allowed',
      explicit_policy: true,
      tier_keys: ['growth'],
    },
    static_limit_checks: [
      {
        scope: 'tier_org_model_tpm',
        entity_id: 'org-1:gpt-4o-mini',
        limit: 500,
        amount_kind: 'tokens',
        window_seconds: 60,
        mode: 'sync',
        amount: 800,
        would_exceed_limit: true,
        remaining_after_amount: -300,
      },
    ],
  } as TierPolicySimulation);

  assert.equal(summary, 'Allowed by tier, but exceeds tier_org_model_tpm');
});
