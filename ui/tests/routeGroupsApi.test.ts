import assert from 'node:assert/strict';
import test from 'node:test';

import { routeGroups } from '../src/lib/api';
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
      policy: { mode: 'fallback' },
      metadata: { tags: ['vip'] },
      outcomes: [{ deployment_id: 'dep-a', outcome: 'timeout' }],
    }, controller.signal);

    assert.equal(capturedPath, '/ui/api/route-groups/support%20%2F%20eu/policy/simulate');
    assert.equal(capturedInit?.method, 'POST');
    assert.equal(capturedInit?.signal, controller.signal);
    assert.deepEqual(JSON.parse(String(capturedInit?.body)), {
      iterations: 2,
      policy: { mode: 'fallback' },
      metadata: { tags: ['vip'] },
      outcomes: [{ deployment_id: 'dep-a', outcome: 'timeout' }],
    });
    assert.equal(result.served_deployments[0].deployment_id, 'dep-b');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
