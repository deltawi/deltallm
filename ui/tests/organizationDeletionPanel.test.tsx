import assert from 'node:assert/strict';
import test from 'node:test';
import { act, type ChangeEvent } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { JSDOM } from 'jsdom';

import OrganizationDeletionPanel from '../src/components/admin/OrganizationDeletionPanel';
import { ToastProvider } from '../src/components/ToastProvider';
import type {
  OrganizationDeletionCounts,
  OrganizationDeletionJob,
  OrganizationDeletionPlan,
} from '../src/lib/organizationDeletion';

type PendingResponse = {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
};

const EMPTY_COUNTS: OrganizationDeletionCounts = {
  teams: 0,
  api_keys: 0,
  service_accounts: 0,
  organization_memberships: 0,
  team_memberships: 0,
  pending_invitations: 0,
  pending_mcp_approvals: 0,
  scope_bindings: 0,
  owned_mcp_servers: 0,
  owned_prompt_templates: 0,
  owned_route_groups: 0,
  external_mcp_dependencies: 0,
  external_prompt_dependencies: 0,
  external_route_group_dependencies: 0,
  prompt_render_logs: 0,
  ambiguous_sensitive_records: 0,
  conflicting_sensitive_records: 0,
  unattributed_sensitive_records: 0,
  active_batches: 0,
  staged_batch_sessions: 0,
  unresolved_batch_ownership_records: 0,
  retained_spend_events: 0,
  retained_audit_events: 0,
  retained_batch_jobs: 0,
  retained_batch_files: 0,
};

const PLAN: OrganizationDeletionPlan = {
  organization_id: 'org-1',
  organization_name: 'Example Org',
  lifecycle_state: 'deletion_pending',
  lifecycle_version: 1,
  deletion_job_id: 'delete-1',
  deletion_requested_at: '2026-09-06T09:00:00Z',
  deletion_not_before_at: '2026-09-13T09:00:00Z',
  counts: EMPTY_COUNTS,
  automatic_cleanup: [],
  retained_history: [],
  cancellation_effects: [],
  blocking_dependencies: [],
  recovery_window_hours: 168,
  lifecycle_protocol_version: 2,
  requests_enabled: true,
  can_request: false,
  plan_token: 'a'.repeat(64),
};

function job(overrides: Partial<OrganizationDeletionJob> = {}): OrganizationDeletionJob {
  return {
    deletion_job_id: 'delete-1',
    organization_id: 'org-1',
    status: 'waiting',
    phase: 'wait_for_batches',
    progress: { active_batches: 0, recovery_window_elapsed: false },
    not_before_at: '2026-09-13T09:00:00Z',
    attempt_count: 0,
    max_attempts: 20,
    last_error_code: null,
    last_error_detail: null,
    created_at: '2026-09-06T09:00:00Z',
    updated_at: '2026-09-06T09:00:00Z',
    completed_at: null,
    restored_at: null,
    expedited_at: null,
    recovery_window_waived: false,
    restore_allowed: true,
    ...overrides,
  };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function deferredResponse(): PendingResponse {
  let resolvePromise: (response: Response) => void = () => undefined;
  const promise = new Promise<Response>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

function installDom() {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: 'https://admin.example.test/organizations/org-1',
  });
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    navigator: globalThis.navigator,
    HTMLElement: globalThis.HTMLElement,
    IS_REACT_ACT_ENVIRONMENT: (globalThis as typeof globalThis & {
      IS_REACT_ACT_ENVIRONMENT?: boolean;
    }).IS_REACT_ACT_ENVIRONMENT,
  };
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    navigator: { configurable: true, value: dom.window.navigator },
    HTMLElement: { configurable: true, value: dom.window.HTMLElement },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });
  Object.defineProperty(dom.window.document, 'hidden', {
    configurable: true,
    value: false,
  });
  return () => {
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previous.window },
      document: { configurable: true, value: previous.document },
      navigator: { configurable: true, value: previous.navigator },
      HTMLElement: { configurable: true, value: previous.HTMLElement },
      IS_REACT_ACT_ENVIRONMENT: {
        configurable: true,
        value: previous.IS_REACT_ACT_ENVIRONMENT,
      },
    });
  };
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function findButton(label: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button')).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  assert.ok(button, `Expected button: ${label}`);
  return button;
}

function invokeReactInputChange(input: HTMLInputElement) {
  const property = Object.keys(input).find((key) => key.startsWith('__reactProps$'));
  assert.ok(property, 'Expected React input props');
  const props = (input as unknown as Record<
    string,
    { onChange?: (event: ChangeEvent<HTMLInputElement>) => void }
  >)[property];
  assert.ok(props.onChange, 'Expected React onChange handler');
  props.onChange({ target: input } as ChangeEvent<HTMLInputElement>);
}

async function fillExpediteDialog() {
  await act(async () => {
    findButton('Delete permanently now').click();
    await flushPromises();
  });
  const input = document.querySelector<HTMLInputElement>('#organization-expedite-confirmation');
  const checkbox = document.querySelector<HTMLInputElement>(
    '#organization-expedite-confirmation ~ * input[type="checkbox"]',
  ) || document.querySelector<HTMLInputElement>('[role="dialog"] input[type="checkbox"]');
  assert.ok(input);
  assert.ok(checkbox);
  await act(async () => {
    input.value = 'Example Org';
    invokeReactInputChange(input);
    checkbox.checked = true;
    invokeReactInputChange(checkbox);
    await flushPromises();
  });
  assert.equal(findButton('Waive recovery window').disabled, false);
}

async function mountPanel({
  canManageLifecycle = true,
  canExpedite = true,
}: {
  canManageLifecycle?: boolean;
  canExpedite?: boolean;
} = {}): Promise<{ root: Root; restoreDom: () => void }> {
  const restoreDom = installDom();
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  await act(async () => {
    root.render(
      <MemoryRouter>
        <ToastProvider>
          <OrganizationDeletionPanel
            organizationId="org-1"
            organizationName="Example Org"
            canManageLifecycle={canManageLifecycle}
            canExpedite={canExpedite}
          />
        </ToastProvider>
      </MemoryRouter>,
    );
    await flushPromises();
  });
  return { root, restoreDom };
}

test('scoped organization administrators can expedite without gaining restore or request', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const path = String(input);
    if (path.endsWith('/deletion-plan')) return jsonResponse(PLAN);
    return jsonResponse(job());
  };
  const mounted = await mountPanel({ canManageLifecycle: false, canExpedite: true });

  try {
    const buttonLabels = Array.from(document.querySelectorAll('button')).map(
      (button) => button.textContent?.trim(),
    );
    assert.ok(buttonLabels.includes('Delete permanently now'));
    assert.equal(buttonLabels.includes('Restore'), false);
    assert.equal(buttonLabels.includes('Delete organization'), false);
  } finally {
    await act(async () => mounted.root.unmount());
    globalThis.fetch = originalFetch;
    mounted.restoreDom();
  }
});

test('successful expedite is not overwritten by a stale pre-mutation poll', async () => {
  const originalFetch = globalThis.fetch;
  const stalePoll = deferredResponse();
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  let jobReads = 0;
  let expediteKey: string | null = null;
  const restoreDom = installDom();
  const originalSetTimeout = window.setTimeout;
  const originalClearTimeout = window.clearTimeout;
  window.setTimeout = ((callback: TimerHandler, delay?: number) => {
    scheduled.push({ callback: callback as () => void, delay: delay ?? 0 });
    return scheduled.length;
  }) as typeof window.setTimeout;
  window.clearTimeout = (() => undefined) as typeof window.clearTimeout;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    if (path.endsWith('/deletion-plan')) return jsonResponse(PLAN);
    if (init?.method === 'POST' && path.endsWith('/expedite')) {
      expediteKey = new Headers(init.headers).get('Idempotency-Key');
      return jsonResponse({
        ...job({
          not_before_at: '2026-09-06T10:00:00Z',
          expedited_at: '2026-09-06T10:00:00Z',
          recovery_window_waived: true,
          restore_allowed: false,
          progress: { active_batches: 0, recovery_window_elapsed: true },
        }),
        idempotency_resolution: 'applied',
      }, 202);
    }
    jobReads += 1;
    return jobReads === 1 ? jsonResponse(job()) : stalePoll.promise;
  };
  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);

  try {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <ToastProvider>
            <OrganizationDeletionPanel
              organizationId="org-1"
              organizationName="Example Org"
              canManageLifecycle
              canExpedite
            />
          </ToastProvider>
        </MemoryRouter>,
      );
      await flushPromises();
    });
    const pollTimer = scheduled.find((entry) => entry.delay === 3000);
    assert.ok(pollTimer);
    await act(async () => {
      pollTimer.callback();
      await flushPromises();
    });
    assert.equal(jobReads, 2);

    await fillExpediteDialog();
    await act(async () => {
      findButton('Waive recovery window').click();
      await flushPromises();
    });
    assert.ok(expediteKey);
    assert.match(document.body.textContent || '', /Recovery window waived at/);

    await act(async () => {
      stalePoll.resolve(jsonResponse(job()));
      await flushPromises();
    });
    assert.match(document.body.textContent || '', /Recovery window waived at/);
    assert.doesNotMatch(document.body.textContent || '', /Permanent deletion no earlier than/);
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    window.setTimeout = originalSetTimeout;
    window.clearTimeout = originalClearTimeout;
    restoreDom();
  }
});

test('expedite keeps one idempotency key across an uncertain retry', async () => {
  const originalFetch = globalThis.fetch;
  const keys: Array<string | null> = [];
  let jobReads = 0;
  let expediteAttempts = 0;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    if (path.endsWith('/deletion-plan')) return jsonResponse(PLAN);
    if (init?.method === 'POST' && path.endsWith('/expedite')) {
      keys.push(new Headers(init.headers).get('Idempotency-Key'));
      expediteAttempts += 1;
      if (expediteAttempts === 1) throw new TypeError('connection closed after send');
      return jsonResponse({
        ...job({
          expedited_at: '2026-09-06T10:00:00Z',
          recovery_window_waived: true,
          restore_allowed: false,
        }),
        idempotency_resolution: 'replayed',
      }, 202);
    }
    jobReads += 1;
    return jsonResponse(job());
  };
  const mounted = await mountPanel();

  try {
    assert.equal(jobReads, 1);
    await fillExpediteDialog();
    await act(async () => {
      findButton('Waive recovery window').click();
      await flushPromises();
    });
    assert.match(document.body.textContent || '', /connection closed after send/);

    await act(async () => {
      findButton('Waive recovery window').click();
      await flushPromises();
    });
    assert.equal(keys.length, 2);
    assert.ok(keys[0]);
    assert.equal(keys[1], keys[0]);
    assert.match(document.body.textContent || '', /earlier request was confirmed/);
  } finally {
    await act(async () => mounted.root.unmount());
    globalThis.fetch = originalFetch;
    mounted.restoreDom();
  }
});
