import assert from 'node:assert/strict';
import test from 'node:test';

import { routeGroups, type RoutePolicy } from '../src/lib/api';
import { routeGroupMutationOutcome } from '../src/lib/routeGroups';

test('route-group policy responses preserve semantics and post-commit warnings', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    group_key: 'support',
    policy: {
      route_policy_id: 'policy-1',
      route_group_id: 'group-1',
      version: 3,
      semantics_version: 2,
      status: 'published',
      policy_json: { strategy: 'weighted' },
      published_at: null,
      published_by: 'admin_api',
    },
    warnings: ['Runtime refresh is pending'],
  }), { headers: { 'content-type': 'application/json' } })) as typeof fetch;

  try {
    const result = await routeGroups.publishPolicy('support', { strategy: 'weighted' });

    assert.equal(result.policy.semantics_version, 2);
    assert.deepEqual(result.warnings, ['Runtime refresh is pending']);
    assert.deepEqual(
      routeGroupMutationOutcome('Published policy version 3.', result.warnings),
      {
        tone: 'info',
        message: 'Published policy version 3. Runtime warning: Runtime refresh is pending',
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('route-group policy history keeps future semantics opaque on reads', async () => {
  const historicalPolicy: RoutePolicy = {
    route_policy_id: 'policy-future',
    route_group_id: 'group-1',
    version: 9,
    semantics_version: 99,
    status: 'archived',
    policy_json: {
      selector: {
        kind: 'future-selector',
        server_owned_revision: 7,
      },
    },
    published_at: null,
    published_by: null,
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    group_key: 'support',
    policies: [historicalPolicy],
  }), { headers: { 'content-type': 'application/json' } })) as typeof fetch;

  try {
    const result = await routeGroups.listPolicies('support');

    assert.deepEqual(result.policies[0].policy_json, historicalPolicy.policy_json);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('route-group policy validation preserves typed selector and context fields', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response(JSON.stringify({
    group_key: 'support',
    valid: true,
    policy: {
      strategy: 'least-busy',
      context: {
        mode: 'smallest-sufficient',
        unknown_capacity: 'exclude',
        default_output_tokens: 2048,
        safety_margin_tokens: 512,
      },
      selector: {
        kind: 'llm-tier',
        classifier_deployment_id: 'dep-mini',
        timeout_ms: 750,
        max_input_chars: 8000,
        default_lane: 'quality',
        lanes: [
          { id: 'economy', rank: 0, description: 'Routine work' },
          { id: 'quality', rank: 1, description: 'Complex work' },
        ],
      },
    },
    warnings: [],
  }), { headers: { 'content-type': 'application/json' } })) as typeof fetch;

  try {
    const result = await routeGroups.validatePolicy('support', {
      context: { mode: 'smallest-sufficient' },
      selector: {
        kind: 'llm-tier',
        classifier_deployment_id: 'dep-mini',
        lanes: [
          { id: 'economy', rank: 0, description: 'Routine work' },
          { id: 'quality', rank: 1, description: 'Complex work' },
        ],
      },
    });

    assert.equal(result.valid, true);
    assert.equal(result.policy.context?.default_output_tokens, 2048);
    assert.equal(result.policy.selector?.default_lane, 'quality');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('route-group reads pass AbortSignal to the shared transport', async () => {
  const originalFetch = globalThis.fetch;
  let capturedSignal: AbortSignal | null | undefined;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    capturedSignal = init?.signal;
    return new Response(JSON.stringify({
      data: [],
      pagination: { total: 0, limit: 20, offset: 0, has_more: false },
    }), { headers: { 'content-type': 'application/json' } });
  }) as typeof fetch;

  try {
    const controller = new AbortController();
    await routeGroups.list({ limit: 20, offset: 0 }, controller.signal);
    assert.equal(capturedSignal, controller.signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('route-group policy mutations pass AbortSignal to the shared transport', async () => {
  const originalFetch = globalThis.fetch;
  let capturedSignal: AbortSignal | null | undefined;
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    capturedSignal = init?.signal;
    return new Response(JSON.stringify({ warnings: [] }), {
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof fetch;

  try {
    const controller = new AbortController();
    await routeGroups.publishPolicy('support', {}, controller.signal);
    assert.equal(capturedSignal, controller.signal);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('route-group policy simulation sends the typed scenario and AbortSignal', async () => {
  const originalFetch = globalThis.fetch;
  let capturedPath = '';
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    capturedPath = String(input);
    capturedInit = init;
    return new Response(JSON.stringify({
      group_key: 'support / eu',
      iterations: 2,
      basis: 'live_state_dry_run',
      warnings: [],
      prompt: null,
      effective_metadata: { tags: ['vip'] },
      summary: {
        selected_requests: 2,
        no_selection_requests: 0,
        served_requests: 2,
        failed_requests: 0,
        fallback_requests: 2,
        timed_out_requests: 0,
        total_attempts: 4,
      },
      reason_counts: { priority: 2 },
      selections: [{ deployment_id: 'dep-a', count: 2, ratio: 1 }],
      served_deployments: [{ deployment_id: 'dep-b', count: 2, ratio: 1 }],
      terminal_outcomes: { success: 2 },
      sample_decision: null,
      sample_attempts: [],
    }), { headers: { 'content-type': 'application/json' } });
  }) as typeof fetch;

  try {
    const controller = new AbortController();
    const result = await routeGroups.simulatePolicy('support / eu', {
      iterations: 2,
      input_tokens: 9_000,
      requested_output_tokens: 1_000,
      policy: { mode: 'fallback' },
      metadata: { tags: ['vip'] },
      outcomes: [{ deployment_id: 'dep-a', outcome: 'timeout' }],
    }, controller.signal);

    assert.equal(capturedPath, '/ui/api/route-groups/support%20%2F%20eu/policy/simulate');
    assert.equal(capturedInit?.method, 'POST');
    assert.equal(capturedInit?.signal, controller.signal);
    assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
      iterations: 2,
      input_tokens: 9000,
      requested_output_tokens: 1000,
      policy: { mode: 'fallback' },
      metadata: { tags: ['vip'] },
      outcomes: [{ deployment_id: 'dep-a', outcome: 'timeout' }],
    });
    assert.equal(result.served_deployments[0].deployment_id, 'dep-b');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
