import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiError,
  type BatchJobDetail,
  type BatchWebhookDelivery,
  type BatchWebhookDeliveryList,
} from '../src/lib/api';
import {
  applyBatchDetailCancellation,
  canCancelBatchDetailJob,
  isCurrentBatchDetailRoute,
  loadBatchDetailResource,
  mergeBatchDetailWebhookDeliveries,
  reconcileBatchDetailReload,
  replaceBatchDetailWebhookDelivery,
  runMutationAndRefresh,
  shouldPreserveBatchDetailResourceOnReloadError,
  type BatchDetailResourceApi,
} from '../src/pages/batchDetailResource';

const params = {
  items_limit: 50,
  items_offset: 0,
  after_line_number: null,
};

const liveJob = { batch_id: 'batch-1' } as BatchJobDetail;
const archivedDelivery = {
  batch_id: 'batch-1',
  capabilities: { view: true, cancel: false, replay_webhook: true },
  data: [],
} satisfies BatchWebhookDeliveryList;

const failedDelivery = {
  event_id: 'event-1',
  event_type: 'batch.completed',
  status: 'failed',
  attempt_count: 8,
  max_attempts: 8,
  created_at: '2026-08-06T00:00:00Z',
  updated_at: '2026-08-06T00:01:00Z',
} satisfies BatchWebhookDelivery;

const queuedDelivery = {
  ...failedDelivery,
  status: 'queued',
  attempt_count: 0,
  updated_at: '2026-08-06T00:02:00Z',
} satisfies BatchWebhookDelivery;

test('returns the live batch without querying retained deliveries', async () => {
  let deliveryCalls = 0;
  const api: BatchDetailResourceApi = {
    get: async () => liveJob,
    webhookDeliveries: async () => {
      deliveryCalls += 1;
      return archivedDelivery;
    },
  };

  const result = await loadBatchDetailResource(api, 'batch-1', params);

  assert.deepEqual(result, { kind: 'live', job: liveJob });
  assert.equal(deliveryCalls, 0);
});

test('falls back to retained deliveries only when batch metadata is missing', async () => {
  const api: BatchDetailResourceApi = {
    get: async () => {
      throw new ApiError('not found', 404);
    },
    webhookDeliveries: async () => archivedDelivery,
  };

  const result = await loadBatchDetailResource(api, 'batch-1', params);

  assert.deepEqual(result, { kind: 'archived', delivery: archivedDelivery });
});

for (const status of [403, 500]) {
  test(`does not hide a ${status} response behind the archive endpoint`, async () => {
    let deliveryCalls = 0;
    const error = new ApiError('request failed', status);
    const api: BatchDetailResourceApi = {
      get: async () => {
        throw error;
      },
      webhookDeliveries: async () => {
        deliveryCalls += 1;
        return archivedDelivery;
      },
    };

    await assert.rejects(
      loadBatchDetailResource(api, 'batch-1', params),
      (received) => received === error,
    );
    assert.equal(deliveryCalls, 0);
  });
}

test('preserves not found when neither live nor retained state exists', async () => {
  const archivedError = new ApiError('retained delivery not found', 404);
  const api: BatchDetailResourceApi = {
    get: async () => {
      throw new ApiError('batch not found', 404);
    },
    webhookDeliveries: async () => {
      throw archivedError;
    },
  };

  await assert.rejects(
    loadBatchDetailResource(api, 'batch-1', params),
    (received) => received === archivedError,
  );
});

test('replaces a replayed delivery in live batch state', () => {
  const resource = {
    kind: 'live' as const,
    job: { ...liveJob, webhook_deliveries: [failedDelivery] },
  };

  const result = replaceBatchDetailWebhookDelivery(resource, queuedDelivery);

  assert.equal(result.kind, 'live');
  assert.deepEqual(result.kind === 'live' ? result.job.webhook_deliveries : null, [queuedDelivery]);
});

test('replaces a replayed delivery in archived state', () => {
  const resource = {
    kind: 'archived' as const,
    delivery: { ...archivedDelivery, data: [failedDelivery] },
  };

  const result = replaceBatchDetailWebhookDelivery(resource, queuedDelivery);

  assert.equal(result.kind, 'archived');
  assert.deepEqual(result.kind === 'archived' ? result.delivery.data : null, [queuedDelivery]);
});

test('preserves resource identity when the replayed event is not present', () => {
  const resource = {
    kind: 'archived' as const,
    delivery: { ...archivedDelivery, data: [failedDelivery] },
  };

  const result = replaceBatchDetailWebhookDelivery(resource, {
    ...queuedDelivery,
    event_id: 'event-other',
  });

  assert.equal(result, resource);
});

test('merges refreshed live deliveries without replacing the current item page', () => {
  const items = {
    data: [{ item_id: 'item-page-2' }],
    pagination: { limit: 50, offset: 50, has_more: true },
  } as BatchJobDetail['items'];
  const resource = {
    kind: 'live' as const,
    job: {
      ...liveJob,
      capabilities: { view: true, cancel: true, replay_webhook: true },
      items,
      webhook_deliveries: [failedDelivery],
    },
  };
  const refreshed = {
    ...archivedDelivery,
    capabilities: { view: true, cancel: false, replay_webhook: false },
    data: [queuedDelivery],
  };

  const result = mergeBatchDetailWebhookDeliveries(resource, refreshed);

  assert.equal(result.kind, 'live');
  assert.equal(result.kind === 'live' ? result.job.items : null, items);
  assert.deepEqual(
    result.kind === 'live' ? result.job.webhook_deliveries : null,
    [queuedDelivery],
  );
  assert.equal(
    result.kind === 'live' ? result.job.capabilities.cancel : null,
    true,
  );
  assert.equal(
    result.kind === 'live' ? result.job.capabilities.replay_webhook : null,
    false,
  );
});

test('replaces archived delivery state with the webhook-only refresh', () => {
  const resource = {
    kind: 'archived' as const,
    delivery: { ...archivedDelivery, data: [failedDelivery] },
  };
  const refreshed = { ...archivedDelivery, data: [queuedDelivery] };

  const result = mergeBatchDetailWebhookDeliveries(resource, refreshed);

  assert.deepEqual(result, { kind: 'archived', delivery: refreshed });
});

test('preserves newer replay state while accepting fields from a stale full reload', () => {
  const current = {
    kind: 'live' as const,
    job: {
      ...liveJob,
      items: {
        data: [{ item_id: 'old-page' }],
        pagination: { limit: 50, offset: 0, has_more: true },
      } as BatchJobDetail['items'],
      capabilities: { view: true, cancel: true, replay_webhook: false },
      webhook_deliveries: [queuedDelivery],
    },
  };
  const loaded = {
    kind: 'live' as const,
    job: {
      ...liveJob,
      items: {
        data: [{ item_id: 'new-page' }],
        pagination: { limit: 50, offset: 50, has_more: false },
      } as BatchJobDetail['items'],
      capabilities: { view: true, cancel: false, replay_webhook: true },
      webhook_deliveries: [failedDelivery],
    },
  };

  const result = reconcileBatchDetailReload(current, loaded, 1, 2);

  assert.equal(result.kind, 'live');
  assert.deepEqual(result.kind === 'live' ? result.job.items : null, loaded.job.items);
  assert.deepEqual(
    result.kind === 'live' ? result.job.webhook_deliveries : null,
    [queuedDelivery],
  );
  assert.equal(
    result.kind === 'live' ? result.job.capabilities.replay_webhook : null,
    false,
  );
  assert.equal(result.kind === 'live' ? result.job.capabilities.cancel : null, false);
});

test('accepts a full reload when no newer webhook mutation exists', () => {
  const current = {
    kind: 'archived' as const,
    delivery: { ...archivedDelivery, data: [queuedDelivery] },
  };
  const loaded = {
    kind: 'archived' as const,
    delivery: { ...archivedDelivery, data: [failedDelivery] },
  };

  assert.equal(reconcileBatchDetailReload(current, loaded, 4, 4), loaded);
});

test('preserves an existing resource when a reload error is stale', () => {
  const current = {
    kind: 'archived' as const,
    delivery: { ...archivedDelivery, data: [queuedDelivery] },
  };

  assert.equal(
    shouldPreserveBatchDetailResourceOnReloadError(current, 4, 5),
    true,
  );
});

test('does not preserve a resource for a current-generation reload error', () => {
  const current = {
    kind: 'archived' as const,
    delivery: { ...archivedDelivery, data: [queuedDelivery] },
  };

  assert.equal(
    shouldPreserveBatchDetailResourceOnReloadError(current, 5, 5),
    false,
  );
});

test('explicitly preserves an existing resource after a mutation refresh error', () => {
  const current = {
    kind: 'live' as const,
    job: liveJob,
  };

  assert.equal(
    shouldPreserveBatchDetailResourceOnReloadError(current, 5, 5, true),
    true,
  );
});

test('does not classify an initial load error as preservable', () => {
  assert.equal(
    shouldPreserveBatchDetailResourceOnReloadError(null, 4, 5, true),
    false,
  );
});

test('rejects a stale replay result after an A-to-B-to-A route transition', () => {
  assert.equal(isCurrentBatchDetailRoute('batch-1', 3, 'batch-1', 1), false);
  assert.equal(isCurrentBatchDetailRoute('batch-1', 3, 'batch-1', 3), true);
});

test('does not refresh when a mutation fails', async () => {
  const mutationError = new Error('mutation failed');
  let refreshCalls = 0;

  await assert.rejects(
    runMutationAndRefresh(
      async () => { throw mutationError; },
      async () => { refreshCalls += 1; },
    ),
    (received) => received === mutationError,
  );
  assert.equal(refreshCalls, 0);
});

test('preserves a successful mutation when refresh fails', async () => {
  const refreshError = new Error('refresh failed');
  const replay = { batch_id: 'batch-1', replayed: true, delivery: queuedDelivery };

  const result = await runMutationAndRefresh(
    async () => replay,
    async () => { throw refreshError; },
  );

  assert.equal(result.mutation, replay);
  assert.equal(result.refreshError, refreshError);
});

test('applies an accepted cancellation without losing current batch details', () => {
  const items = {
    data: [{ item_id: 'item-1' }],
    pagination: { limit: 50, offset: 0, has_more: false },
  } as BatchJobDetail['items'];
  const resource = {
    kind: 'live' as const,
    job: {
      ...liveJob,
      status: 'completed',
      capabilities: { view: true, cancel: true, replay_webhook: true },
      webhook_deliveries: [failedDelivery],
      items,
    },
  };

  const result = applyBatchDetailCancellation(resource, {
    batch_id: 'batch-1',
    status: 'in_progress',
  });

  assert.equal(result.kind, 'live');
  assert.equal(result.kind === 'live' ? result.job.status : null, 'completed');
  assert.equal(result.kind === 'live' ? result.job.capabilities.cancel : null, false);
  assert.equal(result.kind === 'live' ? result.job.items : null, items);
  assert.deepEqual(
    result.kind === 'live' ? result.job.webhook_deliveries : null,
    [failedDelivery],
  );
});

test('does not apply cancellation state to a different or archived batch', () => {
  const liveResource = {
    kind: 'live' as const,
    job: {
      ...liveJob,
      status: 'queued',
      capabilities: { view: true, cancel: true },
    },
  };
  const archivedResource = {
    kind: 'archived' as const,
    delivery: archivedDelivery,
  };
  const cancellation = { batch_id: 'batch-other', status: 'queued' };

  assert.equal(applyBatchDetailCancellation(liveResource, cancellation), liveResource);
  assert.equal(
    applyBatchDetailCancellation(archivedResource, {
      batch_id: 'batch-1',
      status: 'queued',
    }),
    archivedResource,
  );
});

test('allows cancellation only before a request is accepted on an active batch', () => {
  const cancellableJob = {
    status: 'in_progress',
    cancel_requested_at: null,
    capabilities: { view: true, cancel: true },
  };

  assert.equal(canCancelBatchDetailJob(cancellableJob), true);
  assert.equal(canCancelBatchDetailJob({
    ...cancellableJob,
    cancel_requested_at: '2026-08-09T00:00:00Z',
  }), false);
  assert.equal(canCancelBatchDetailJob({
    ...cancellableJob,
    status: 'completed',
  }), false);
});
