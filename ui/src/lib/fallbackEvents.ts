export interface FallbackEvent {
  timestamp: number;
  model_group: string;
  from_deployment: string | null;
  to_deployment: string | null;
  error_classification: string;
  success: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

export function parseFallbackEvents(payload: unknown): FallbackEvent[] {
  if (!isRecord(payload) || !Array.isArray(payload.events)) return [];

  return payload.events.flatMap((event) => {
    if (
      !isRecord(event)
      || typeof event.timestamp !== 'number'
      || typeof event.model_group !== 'string'
      || (event.from_deployment !== null && typeof event.from_deployment !== 'string')
      || (event.to_deployment !== null && typeof event.to_deployment !== 'string')
      || typeof event.error_classification !== 'string'
      || typeof event.success !== 'boolean'
    ) return [];

    return [{
      timestamp: event.timestamp,
      model_group: event.model_group,
      from_deployment: event.from_deployment,
      to_deployment: event.to_deployment,
      error_classification: event.error_classification,
      success: event.success,
    }];
  });
}
