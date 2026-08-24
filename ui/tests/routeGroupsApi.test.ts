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
