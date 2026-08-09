import {
  ApiError,
  type BatchJobDetail,
  type BatchWebhookDelivery,
  type BatchWebhookDeliveryList,
} from '../lib/api';

export type BatchDetailResource =
  | { kind: 'live'; job: BatchJobDetail }
  | { kind: 'archived'; delivery: BatchWebhookDeliveryList };

export interface BatchDetailResourceApi {
  get: (
    batchId: string,
    params: {
      items_limit: number;
      items_offset: number;
      after_line_number: number | null;
    },
  ) => Promise<BatchJobDetail>;
  webhookDeliveries: (batchId: string) => Promise<BatchWebhookDeliveryList>;
}

export interface MutationRefreshResult<T> {
  mutation: T;
  refreshError: unknown | null;
}

export async function runMutationAndRefresh<T>(
  mutate: () => Promise<T>,
  refresh: (response: T) => Promise<void>,
): Promise<MutationRefreshResult<T>> {
  const response = await mutate();
  try {
    await refresh(response);
    return { mutation: response, refreshError: null };
  } catch (refreshError: unknown) {
    return { mutation: response, refreshError };
  }
}

export function applyBatchDetailCancellation(
  resource: BatchDetailResource,
  cancellation: { batch_id: string; status: string },
): BatchDetailResource {
  if (resource.kind !== 'live' || resource.job.batch_id !== cancellation.batch_id) {
    return resource;
  }
  if (resource.job.capabilities.cancel === false) {
    return resource;
  }
  return {
    kind: 'live',
    job: {
      ...resource.job,
      capabilities: {
        ...resource.job.capabilities,
        cancel: false,
      },
    },
  };
}

export function canCancelBatchDetailJob(
  job: Pick<BatchJobDetail, 'status' | 'cancel_requested_at' | 'capabilities'>,
): boolean {
  return Boolean(job.capabilities.cancel)
    && !job.cancel_requested_at
    && ['validating', 'queued', 'in_progress', 'finalizing'].includes(job.status);
}

function replaceDelivery(
  deliveries: BatchWebhookDelivery[],
  replacement: BatchWebhookDelivery,
): BatchWebhookDelivery[] {
  let replaced = false;
  const next = deliveries.map((delivery) => {
    if (delivery.event_id !== replacement.event_id) return delivery;
    replaced = true;
    return replacement;
  });
  return replaced ? next : deliveries;
}

export function replaceBatchDetailWebhookDelivery(
  resource: BatchDetailResource,
  replacement: BatchWebhookDelivery,
): BatchDetailResource {
  if (resource.kind === 'archived') {
    const data = replaceDelivery(resource.delivery.data, replacement);
    return data === resource.delivery.data
      ? resource
      : { kind: 'archived', delivery: { ...resource.delivery, data } };
  }

  const deliveries = resource.job.webhook_deliveries || [];
  const webhookDeliveries = replaceDelivery(deliveries, replacement);
  return webhookDeliveries === deliveries
    ? resource
    : {
        kind: 'live',
        job: { ...resource.job, webhook_deliveries: webhookDeliveries },
      };
}

export function mergeBatchDetailWebhookDeliveries(
  resource: BatchDetailResource,
  delivery: BatchWebhookDeliveryList,
): BatchDetailResource {
  if (delivery.batch_id !== (
    resource.kind === 'live' ? resource.job.batch_id : resource.delivery.batch_id
  )) {
    return resource;
  }
  if (resource.kind === 'archived') {
    return { kind: 'archived', delivery };
  }
  return {
    kind: 'live',
    job: {
      ...resource.job,
      capabilities: {
        ...resource.job.capabilities,
        replay_webhook: delivery.capabilities.replay_webhook,
      },
      webhook_deliveries: delivery.data,
    },
  };
}

function webhookDeliverySnapshot(resource: BatchDetailResource): BatchWebhookDeliveryList {
  if (resource.kind === 'archived') return resource.delivery;
  return {
    batch_id: resource.job.batch_id,
    capabilities: resource.job.capabilities,
    data: resource.job.webhook_deliveries || [],
  };
}

export function reconcileBatchDetailReload(
  current: BatchDetailResource | null,
  loaded: BatchDetailResource,
  requestedWebhookGeneration: number,
  currentWebhookGeneration: number,
): BatchDetailResource {
  if (current === null || requestedWebhookGeneration === currentWebhookGeneration) {
    return loaded;
  }
  return mergeBatchDetailWebhookDeliveries(loaded, webhookDeliverySnapshot(current));
}

export function shouldPreserveBatchDetailResourceOnReloadError(
  current: BatchDetailResource | null,
  requestedWebhookGeneration: number,
  currentWebhookGeneration: number,
  preserveCurrentOnError = false,
): boolean {
  return current !== null && (
    preserveCurrentOnError
    || requestedWebhookGeneration !== currentWebhookGeneration
  );
}

export function isCurrentBatchDetailRoute(
  activeBatchId: string | undefined,
  activeRouteGeneration: number,
  requestBatchId: string,
  requestRouteGeneration: number,
): boolean {
  return activeBatchId === requestBatchId
    && activeRouteGeneration === requestRouteGeneration;
}

export async function loadBatchDetailResource(
  api: BatchDetailResourceApi,
  batchId: string,
  params: {
    items_limit: number;
    items_offset: number;
    after_line_number: number | null;
  },
): Promise<BatchDetailResource> {
  try {
    return { kind: 'live', job: await api.get(batchId, params) };
  } catch (error: unknown) {
    if (!(error instanceof ApiError) || error.status !== 404) {
      throw error;
    }
  }

  return {
    kind: 'archived',
    delivery: await api.webhookDeliveries(batchId),
  };
}
