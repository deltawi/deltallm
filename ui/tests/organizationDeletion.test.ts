import assert from 'node:assert/strict';
import test from 'node:test';

import { ApiError } from '../src/lib/apiClient';
import { organizationDeletion } from '../src/lib/organizationDeletion';


test('organization deletion request is encoded and idempotent', async () => {
  const controller = new AbortController();
  let capturedPath = '';
  let capturedInit: RequestInit | undefined;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (path, init) => {
    capturedPath = String(path);
    capturedInit = init;
    return new Response(JSON.stringify({ deletion_job_id: 'delete-1' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    await organizationDeletion.request(
      'org/one',
      {
        confirmation_name: 'Example',
        plan_token: 'a'.repeat(64),
        acknowledge_running_work_cancellation: true,
        options: {
          owned_mcp_servers: 'delete',
          owned_prompt_templates: 'delete',
          owned_route_groups: 'delete',
        },
      },
      'request-1',
      controller.signal,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(capturedPath, '/ui/api/organizations/org%2Fone/deletion-requests');
  assert.equal(capturedInit?.method, 'POST');
  assert.equal(capturedInit?.signal, controller.signal);
  assert.equal(new Headers(capturedInit?.headers).get('Idempotency-Key'), 'request-1');
  assert.equal(new Headers(capturedInit?.headers).get('Content-Type'), 'application/json');
  assert.equal(
    JSON.parse(String(capturedInit?.body)).acknowledge_running_work_cancellation,
    true,
  );
});


test('structured deletion errors expose the server message', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({
      detail: {
        code: 'organization_deletion_plan_stale',
        message: 'Refresh the deletion preview',
      },
    }),
    { status: 409, headers: { 'Content-Type': 'application/json' } },
  );

  try {
    await assert.rejects(
      organizationDeletion.plan('org-1', new AbortController().signal),
      (error: unknown) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 409);
        assert.equal(error.message, 'Refresh the deletion preview');
        return true;
      },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
