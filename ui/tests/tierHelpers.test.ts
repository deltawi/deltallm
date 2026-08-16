import assert from 'node:assert/strict';
import test from 'node:test';
import {
  emptyCapacityPoolForm,
  emptyModelPolicyForm,
  formatSimulationPerRequestPrice,
  formatSimulationPrice,
  capacityPoolFormWithStrategy,
  capacityPoolFormToPayload,
  isAssignableTierVersion,
  modelPolicyFormToPayload,
  modelPolicyToForm,
  parseNonNegativeIntegerInput,
  parsePositiveIntegerInput,
  pickEditableVersion,
  pricingEntries,
  pricingProfileForModelMode,
  poolOptionsForCallable,
  summarizePricing,
  summarizeSimulation,
  tierConfigurationBadges,
  tierConfigurationEmptyLabel,
  tierPackageSummary,
  tierSimulationFormToPayload,
  tierAssignmentRequiresActiveVersion,
} from '../src/lib/tiers';
import {
  clampTierPaginationOffset,
  visibleTierPaginationPages,
} from '../src/lib/tierPagination';
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

test('tier catalog configuration badges follow the Live and Draft truth table', () => {
  const base = {
    tier_id: 'tier-1',
    tier_key: 'pro',
    name: 'Pro',
    enabled: true,
    version_count: 0,
    assignment_count: 0,
  };
  const live = {
    tier_version_id: 'version-live',
    version_number: 2,
    configuration_revision: 0,
    model_policy_count: 3,
    capacity_pool_count: 1,
    created_by_kind: 'account',
  };
  const draft = {
    ...live,
    tier_version_id: 'version-draft',
    version_number: 3,
    model_policy_count: 4,
  };

  assert.deepEqual(tierConfigurationBadges({ ...base, active_version: live }), [
    { kind: 'active', label: 'Live v2' },
  ]);
  assert.deepEqual(tierConfigurationBadges({ ...base, version_count: 1, latest_draft_version: draft }), [
    { kind: 'draft', label: 'Draft v3' },
  ]);
  assert.deepEqual(tierConfigurationBadges({ ...base, active_version: live, latest_draft_version: draft }), [
    { kind: 'active', label: 'Live v2' },
    { kind: 'draft', label: 'Draft v3' },
  ]);
  assert.equal(tierConfigurationEmptyLabel(base), 'No configuration');
  assert.equal(tierConfigurationEmptyLabel({ ...base, version_count: 2 }), 'No live version');
  assert.deepEqual(tierPackageSummary({ ...base, active_version: live, latest_draft_version: draft }), {
    modelPolicyCount: 4,
    capacityPoolCount: 1,
    versionNumber: 3,
    versionStatus: 'draft',
  });
});

test('tier pagination exposes compact numbered pages', () => {
  assert.deepEqual(visibleTierPaginationPages(1, 4), [1, 2, 3, 4]);
  assert.deepEqual(visibleTierPaginationPages(5, 12), [1, null, 4, 5, 6, null, 12]);
  assert.equal(clampTierPaginationOffset(51, 25, 75), 50);
  assert.equal(clampTierPaginationOffset(25, 25, 25), 0);
  assert.equal(clampTierPaginationOffset(0, 25, 50), 0);
});

test('tier workspace never silently chooses among multiple drafts', () => {
  const live = {
    tier_version_id: 'live',
    tier_id: 'tier-1',
    version_number: 1,
    status: 'active',
    configuration_revision: 0,
    created_by_kind: 'unknown',
    model_policy_count: 0,
    capacity_pool_count: 0,
    assignment_count: 0,
  };
  const draftTwo = { ...live, tier_version_id: 'draft-2', version_number: 2, status: 'draft' };
  const draftThree = { ...live, tier_version_id: 'draft-3', version_number: 3, status: 'draft' };

  assert.equal(pickEditableVersion([live, draftTwo])?.tier_version_id, 'draft-2');
  assert.equal(pickEditableVersion([live, draftThree, draftTwo])?.tier_version_id, 'live');
  assert.equal(pickEditableVersion([draftThree, draftTwo]), null);
});

test('modelPolicyFormToPayload supports full token pricing fields', () => {
  const payload = modelPolicyFormToPayload({
    ...emptyModelPolicyForm('token'),
    callable_key: 'gpt-4o-mini',
    input_cost_per_token: '0.01',
    output_cost_per_token: '0.02',
    cached_input_cost_per_token: '0.005',
    cached_output_cost_per_token: '0.006',
    batch_input_cost_per_token: '0.004',
    batch_output_cost_per_token: '0.008',
    batch_price_multiplier: '0.5',
    cost_per_request: '0.001',
  });

  assert.deepEqual(payload.pricing, {
    input_cost_per_token: 0.01,
    output_cost_per_token: 0.02,
    input_cost_per_token_cache_hit: 0.005,
    output_cost_per_token_cache_hit: 0.006,
    batch_input_cost_per_token: 0.004,
    batch_output_cost_per_token: 0.008,
    batch_price_multiplier: 0.5,
    cost_per_request: 0.001,
  });
});

test('modelPolicyFormToPayload supports image and audio pricing fields', () => {
  const imagePayload = modelPolicyFormToPayload({
    ...emptyModelPolicyForm('image_generation'),
    callable_key: 'image-model',
    input_cost_per_image: '0.25',
    cost_per_request: '0.75',
  });
  const audioPayload = modelPolicyFormToPayload({
    ...emptyModelPolicyForm('audio_speech'),
    callable_key: 'tts-model',
    input_cost_per_character: '0.002',
    output_cost_per_second: '0.03',
    input_cost_per_audio_token: '0.04',
    output_cost_per_audio_token: '0.05',
  });

  assert.deepEqual(imagePayload.pricing, {
    input_cost_per_image: 0.25,
    cost_per_request: 0.75,
  });
  assert.deepEqual(audioPayload.pricing, {
    input_cost_per_character: 0.002,
    output_cost_per_second: 0.03,
    input_cost_per_audio_token: 0.04,
    output_cost_per_audio_token: 0.05,
  });
});

test('model policy form payload preserves existing metadata and pricing', () => {
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

  assert.deepEqual(fromForm.metadata, { source: 'api' });
  assert.deepEqual(fromForm.pricing, { input_cost_per_token: 0.01 });
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

test('modelPolicyToForm infers non-token pricing profiles from existing pricing', () => {
  assert.equal(modelPolicyToForm({
    callable_key: 'image-model',
    enabled: true,
    access_mode: 'allow',
    pricing: { input_cost_per_image: 0.25 },
    priority: 0,
  }).pricing_profile, 'image_generation');

  assert.equal(modelPolicyToForm({
    callable_key: 'tts-model',
    enabled: true,
    access_mode: 'allow',
    pricing: { input_cost_per_character: 0.001 },
    priority: 0,
  }).pricing_profile, 'audio_speech');

  assert.equal(pricingProfileForModelMode('audio_transcription'), 'audio_transcription');
  assert.equal(pricingProfileForModelMode('embedding'), 'token');
});

test('pricing summaries use human labels and billing units', () => {
  const pricing = {
    input_cost_per_image: 0.25,
    cost_per_request: 0.75,
  };
  const entries = pricingEntries(pricing);

  assert.deepEqual(entries.map((entry) => entry.shortLabel), ['Input image', 'Request']);
  assert.equal(
    summarizePricing(pricing, 'image_generation'),
    'Input image 0.25 /image · Request 0.75 /request',
  );
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

test('capacity pool form payload preserves existing metadata', () => {
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

  assert.deepEqual(fromForm.metadata, { source: 'api' });
});

test('capacity pool strategy helper fills only relevant safe defaults', () => {
  const hardCap = emptyCapacityPoolForm();
  const weightedFair = capacityPoolFormWithStrategy(hardCap, 'weighted_fair');
  assert.equal(weightedFair.saturation_threshold, '0.85');
  assert.equal(weightedFair.burst_multiplier, '');

  const reservedBurst = capacityPoolFormWithStrategy(weightedFair, 'reserved_burst');
  assert.equal(reservedBurst.saturation_threshold, '0.85');
  assert.equal(reservedBurst.burst_multiplier, '1.2');

  const switchedBack = capacityPoolFormWithStrategy(reservedBurst, 'hard_cap');
  assert.equal(switchedBack.saturation_threshold, '');
  assert.equal(switchedBack.burst_multiplier, '');
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

test('summarizeSimulation reports the overall static decision', () => {
  const summary = summarizeSimulation({
    access: {
      allowed: true,
      reason: 'tier_policy_allowed',
      explicit_policy: true,
      tier_keys: ['growth'],
    },
    decision: {
      allowed: false,
      reason: 'static_limit_exceeded',
      primary_limiting_scope: 'tier_org_model_tpm',
      limiting_scopes: ['tier_org_model_tpm'],
      basis: 'empty_window_static',
      live_capacity_evaluated: false,
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

  assert.equal(summary, 'Denied: static_limit_exceeded');
});

test('formatSimulationPrice distinguishes exact, partial, and unavailable quotes', () => {
  const base = {
    currency: 'USD',
    billing_mode: 'chat',
    usage_snapshot: { prompt_tokens: 100 },
    configured_candidate_count: 2,
    priced_candidate_count: 2,
    unpriced_candidate_count: 0,
    unevaluated_candidate_count: 0,
    unpriced_reasons: [],
    pricing_sources: ['tier'],
    basis: 'configured_routes',
    request_count: 2,
    amount_scope: 'aggregate',
    per_request_amount: 0.625,
    per_request_minimum_amount: 0.625,
    per_request_maximum_amount: 0.625,
  } as const;

  assert.equal(formatSimulationPrice({
    ...base,
    status: 'available',
    reason: null,
    kind: 'exact',
    amount: 1.25,
    minimum_amount: 1.25,
    maximum_amount: 1.25,
  }), 'USD 1.250000');
  assert.equal(formatSimulationPrice({
    ...base,
    status: 'partial',
    reason: 'some_routes_unpriced',
    kind: 'exact',
    amount: null,
    minimum_amount: 1,
    maximum_amount: 1,
    per_request_amount: null,
    per_request_minimum_amount: 0.5,
    per_request_maximum_amount: 0.5,
    priced_candidate_count: 1,
    unpriced_candidate_count: 1,
  }), 'Partial route quote: USD 1.000000 (1 unpriced)');
  assert.equal(formatSimulationPrice({
    ...base,
    status: 'unavailable',
    reason: 'no_configured_routes',
    kind: null,
    amount: null,
    minimum_amount: null,
    maximum_amount: null,
    per_request_amount: null,
    per_request_minimum_amount: null,
    per_request_maximum_amount: null,
    configured_candidate_count: 0,
    priced_candidate_count: 0,
  }), 'Unavailable: No configured routes');
});

test('formatSimulationPrice preserves small positive prices and narrow ranges', () => {
  const base = {
    currency: 'USD',
    billing_mode: 'chat',
    usage_snapshot: { prompt_tokens: 1 },
    configured_candidate_count: 1,
    priced_candidate_count: 1,
    unpriced_candidate_count: 0,
    unevaluated_candidate_count: 0,
    unpriced_reasons: [],
    pricing_sources: ['default'],
    basis: 'configured_routes',
    status: 'available',
    reason: null,
    request_count: 1,
    amount_scope: 'aggregate',
    per_request_amount: 0.00000015,
    per_request_minimum_amount: 0.00000015,
    per_request_maximum_amount: 0.00000015,
  } as const;

  assert.equal(formatSimulationPrice({
    ...base,
    kind: 'exact',
    amount: 0.00000015,
    minimum_amount: 0.00000015,
    maximum_amount: 0.00000015,
  }), 'USD 0.00000015');
  assert.equal(formatSimulationPrice({
    ...base,
    configured_candidate_count: 2,
    priced_candidate_count: 2,
    kind: 'range',
    amount: null,
    minimum_amount: 0.00000015,
    maximum_amount: 0.0000006,
    per_request_amount: null,
    per_request_minimum_amount: 0.00000015,
    per_request_maximum_amount: 0.0000006,
  }), 'USD 0.00000015–0.0000006');
  assert.equal(formatSimulationPrice({
    ...base,
    kind: 'exact',
    amount: 0.000000000001,
    minimum_amount: 0.000000000001,
    maximum_amount: 0.000000000001,
  }), 'USD 1e-12');
});

test('formatSimulationPerRequestPrice labels exact and range quotes', () => {
  const base = {
    status: 'available',
    reason: null,
    currency: 'USD',
    billing_mode: 'chat',
    usage_snapshot: { prompt_tokens: 100 },
    configured_candidate_count: 2,
    priced_candidate_count: 2,
    unpriced_candidate_count: 0,
    unevaluated_candidate_count: 0,
    unpriced_reasons: [],
    pricing_sources: ['tier'],
    basis: 'configured_routes',
    request_count: 4,
    amount_scope: 'aggregate',
  } as const;

  assert.equal(formatSimulationPerRequestPrice({
    ...base,
    kind: 'exact',
    amount: 5,
    minimum_amount: 5,
    maximum_amount: 5,
    per_request_amount: 1.25,
    per_request_minimum_amount: 1.25,
    per_request_maximum_amount: 1.25,
  }), 'USD 1.250000');
  assert.equal(formatSimulationPerRequestPrice({
    ...base,
    kind: 'range',
    amount: null,
    minimum_amount: 4,
    maximum_amount: 6,
    per_request_amount: null,
    per_request_minimum_amount: 1,
    per_request_maximum_amount: 1.5,
  }), 'USD 1.000000–1.500000');
});

test('tierSimulationFormToPayload sends audio text tokens without chat defaults', () => {
  const base = {
    mode: 'sync',
    billing_mode: 'audio_speech' as const,
    request_count: '2',
    prompt_tokens: '1000',
    completion_tokens: '500',
    audio_prompt_tokens: '12',
    audio_completion_tokens: '8',
    input_images: '0',
    output_images: '1',
    input_characters: '100',
    output_characters: '0',
    input_audio_tokens: '3',
    output_audio_tokens: '4',
    duration_seconds: '2.5',
  };

  assert.deepEqual(tierSimulationFormToPayload(base, 'speech-model'), {
    callable_key: 'speech-model',
    mode: 'sync',
    billing_mode: 'audio_speech',
    request_count: 2,
    prompt_tokens: 12,
    completion_tokens: 8,
    input_characters: 100,
    output_characters: 0,
    input_audio_tokens: 3,
    output_audio_tokens: 4,
    duration_seconds: 2.5,
  });

  assert.deepEqual(tierSimulationFormToPayload({
    ...base,
    billing_mode: 'audio_transcription',
  }, 'transcription-model'), {
    callable_key: 'transcription-model',
    mode: 'sync',
    billing_mode: 'audio_transcription',
    request_count: 2,
    prompt_tokens: 12,
    completion_tokens: 8,
    input_audio_tokens: 3,
    duration_seconds: 2.5,
  });
});
