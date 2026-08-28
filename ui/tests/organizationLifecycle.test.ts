import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement, useEffect } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';

import {
  OrganizationLifecycleBadge,
  OrganizationLifecycleNotice,
} from '../src/components/admin/OrganizationLifecycleStatus';
import {
  formatOrganizationDeletionDeadline,
  isOrganizationActive,
  normalizeOrganizationLifecycleState,
  organizationLifecycleTransitionForDeletionJob,
  organizationLifecyclePresentation,
} from '../src/lib/organizationLifecycle';
import {
  normalizeOrganizationRecord,
  organizationRecordsApi,
} from '../src/lib/api/organizations';
import type { OrganizationDeletionJob } from '../src/lib/organizationDeletion';
import {
  reconcileOrganizationLifecycleTransition,
  useOrganizationResource,
} from '../src/lib/useOrganizationResource';

test('organization lifecycle presentation distinguishes disabled states from active', () => {
  assert.equal(organizationLifecyclePresentation('active').label, 'Active');
  assert.equal(organizationLifecyclePresentation('deletion_pending').label, 'Deletion pending');
  assert.equal(organizationLifecyclePresentation('purging').label, 'Purging');
  assert.equal(organizationLifecyclePresentation('deletion_failed').label, 'Deletion failed');
  assert.equal(isOrganizationActive('active'), true);
  assert.equal(isOrganizationActive('deletion_pending'), false);
  assert.equal(isOrganizationActive('purging'), false);
  assert.equal(isOrganizationActive('deletion_failed'), false);
  assert.equal(isOrganizationActive('unavailable'), false);
});

test('organization lifecycle normalization fails closed for old and future API responses', () => {
  assert.equal(normalizeOrganizationLifecycleState(undefined), 'unavailable');
  assert.equal(normalizeOrganizationLifecycleState('future_state'), 'unavailable');
  assert.equal(organizationLifecyclePresentation('unavailable').label, 'Status unavailable');

  const organization = normalizeOrganizationRecord({
    organization_id: 'org-1',
    organization_name: 'Legacy API organization',
    capabilities: {
      view: true,
      edit: true,
      add_team: true,
      manage_members: true,
      manage_assets: true,
      manage_service_policy: true,
      view_usage: true,
    },
    service_policy: {},
  });

  assert.equal(organization.lifecycle_state, 'unavailable');
  assert.deepEqual(organization.capabilities, {
    view: true,
    edit: false,
    add_team: false,
    manage_members: false,
    manage_assets: false,
    manage_service_policy: false,
    view_usage: false,
  });
});

test('organization deletion deadline formatting rejects missing and invalid values', () => {
  assert.equal(formatOrganizationDeletionDeadline(null), null);
  assert.equal(formatOrganizationDeletionDeadline('not-a-date'), null);
  assert.ok(formatOrganizationDeletionDeadline('2026-09-03T09:16:35.000Z'));
});

test('organization lifecycle UI explains immediate disablement and recovery', () => {
  const badge = renderToStaticMarkup(OrganizationLifecycleBadge({ state: 'deletion_pending' }));
  const notice = renderToStaticMarkup(OrganizationLifecycleNotice({
    state: 'deletion_pending',
    deletionNotBeforeAt: '2026-09-03T09:16:35.000Z',
  }));

  assert.match(badge, /Deletion pending/);
  assert.match(notice, /access is disabled/);
  assert.match(notice, /administrative changes are disabled now/);
  assert.match(notice, /platform administrator can restore/);
});

test('organization lifecycle UI renders unavailable state without crashing', () => {
  const badge = renderToStaticMarkup(OrganizationLifecycleBadge({ state: 'unavailable' }));
  const notice = renderToStaticMarkup(OrganizationLifecycleNotice({ state: 'unavailable' }));

  assert.match(badge, /Status unavailable/);
  assert.match(notice, /could not be verified/);
  assert.match(notice, /Administrative changes are disabled/);
});

test('organization lifecycle transition disables mutations before refresh and restores fail closed', () => {
  const active = normalizeOrganizationRecord({
    organization_id: 'org-1',
    lifecycle_state: 'active',
    service_policy: {},
    capabilities: {
      view: true,
      edit: true,
      add_team: true,
      manage_members: true,
      manage_assets: true,
      manage_service_policy: true,
      view_usage: true,
    },
  });
  const pending = reconcileOrganizationLifecycleTransition(active, {
    lifecycleState: 'deletion_pending',
    deletionNotBeforeAt: '2026-09-03T09:16:35.000Z',
  });
  assert.equal(pending.lifecycle_state, 'deletion_pending');
  assert.equal(pending.capabilities.edit, false);
  assert.equal(pending.capabilities.add_team, false);

  const restored = reconcileOrganizationLifecycleTransition(pending, {
    lifecycleState: 'active',
  });
  assert.equal(restored.lifecycle_state, 'active');
  assert.equal(restored.deletion_not_before_at, null);
  assert.equal(restored.capabilities.edit, false);
  assert.equal(restored.capabilities.add_team, false);
});

test('organization refresh is awaitable and preserves last-good data on failure', async () => {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>');
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  const previousActEnvironment = (
    globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
  ).IS_REACT_ACT_ENVIRONMENT;
  Object.defineProperties(globalThis, {
    window: { configurable: true, value: dom.window },
    document: { configurable: true, value: dom.window.document },
    IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: true },
  });

  const originalGet = organizationRecordsApi.get;
  const pending: Array<{
    signal?: AbortSignal;
    resolve: (value: ReturnType<typeof normalizeOrganizationRecord>) => void;
    reject: (reason: Error) => void;
  }> = [];
  organizationRecordsApi.get = (_organizationId, signal) => new Promise((resolve, reject) => {
    pending.push({ signal, resolve, reject });
  });

  let resource: ReturnType<typeof useOrganizationResource> | null = null;
  function Probe() {
    const current = useOrganizationResource('org-1');
    useEffect(() => {
      resource = current;
    }, [current]);
    return createElement('div', null, current.data?.lifecycle_state ?? 'loading');
  }

  const rootNode = document.getElementById('root');
  assert.ok(rootNode);
  const root = createRoot(rootNode);
  const active = normalizeOrganizationRecord({
    organization_id: 'org-1',
    lifecycle_state: 'active',
    capabilities: { view: true, edit: true },
    service_policy: {},
  });

  try {
    await act(async () => root.render(createElement(Probe)));
    assert.equal(pending.length, 1);
    await act(async () => {
      pending[0].resolve(active);
      await Promise.resolve();
    });
    assert.equal(resource?.data?.lifecycle_state, 'active');

    const refreshError = new Error('refresh unavailable');
    let refreshPromise: Promise<ReturnType<typeof normalizeOrganizationRecord>> | undefined;
    await act(async () => {
      refreshPromise = resource?.refresh();
      await Promise.resolve();
    });
    assert.equal(pending.length, 2);
    await act(async () => {
      pending[1].reject(refreshError);
      await assert.rejects(refreshPromise, /refresh unavailable/);
    });
    assert.equal(resource?.data, active);
    assert.equal(resource?.refreshError, refreshError);
    assert.equal(resource?.refreshing, false);

    await act(async () => root.unmount());
    assert.equal(pending[1].signal?.aborted, true);
  } finally {
    organizationRecordsApi.get = originalGet;
    dom.window.close();
    Object.defineProperties(globalThis, {
      window: { configurable: true, value: previousWindow },
      document: { configurable: true, value: previousDocument },
      IS_REACT_ACT_ENVIRONMENT: { configurable: true, value: previousActEnvironment },
    });
  }
});

test('deletion job progress maps to the parent lifecycle state', () => {
  const job = (overrides: Partial<OrganizationDeletionJob>): OrganizationDeletionJob => ({
    deletion_job_id: 'job-1',
    organization_id: 'org-1',
    status: 'waiting',
    phase: 'wait_for_batches',
    progress: {},
    not_before_at: '2026-09-03T09:16:35.000Z',
    attempt_count: 0,
    max_attempts: 20,
    last_error_code: null,
    last_error_detail: null,
    created_at: null,
    updated_at: null,
    completed_at: null,
    restored_at: null,
    restore_allowed: true,
    ...overrides,
  });

  assert.equal(organizationLifecycleTransitionForDeletionJob(job({})).lifecycleState, 'deletion_pending');
  assert.equal(organizationLifecycleTransitionForDeletionJob(job({ phase: 'remove_scoped_access' })).lifecycleState, 'purging');
  assert.equal(organizationLifecycleTransitionForDeletionJob(job({ status: 'failed' })).lifecycleState, 'deletion_failed');
  assert.equal(organizationLifecycleTransitionForDeletionJob(job({ status: 'restored' })).lifecycleState, 'active');
});
