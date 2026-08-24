export class ApiError extends Error {
  status: number;
  detail?: unknown;
  retryAfterSeconds?: number;

  constructor(message: string, status: number, detail?: unknown, retryAfterSeconds?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export type ApiFetchOptions = RequestInit & { json?: unknown };

export interface StructuredApiErrorDetail {
  code?: string;
  message?: string;
  [key: string]: unknown;
}

export function structuredApiErrorDetail(error: unknown): StructuredApiErrorDetail | null {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') {
    return null;
  }
  const wrapper = error.detail as { detail?: unknown };
  const detail = wrapper.detail;
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) return null;
  return detail as StructuredApiErrorDetail;
}

function buildHeaders(init?: HeadersInit, body?: BodyInit | null): HeadersInit {
  const headers = new Headers(init);
  if (!(body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

async function parseErrorDetail(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try {
      return await response.json();
    } catch {
      return undefined;
    }
  }
  try {
    return await response.text();
  } catch {
    return undefined;
  }
}

function objectMessage(value: unknown): string | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const record = value as Record<string, unknown>;
  if (typeof record.message === 'string' && record.message.trim()) return record.message;
  if (typeof record.detail === 'string' && record.detail.trim()) return record.detail;
  return objectMessage(record.detail);
}

function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  return objectMessage(detail) || `Request failed (${status})`;
}

export async function apiFetch<T>(path: string, options?: ApiFetchOptions): Promise<T> {
  const hasJson = options !== undefined && Object.prototype.hasOwnProperty.call(options, 'json');
  const body = hasJson ? JSON.stringify(options?.json ?? null) : options?.body;
  const response = await fetch(path, {
    credentials: 'include',
    ...options,
    headers: buildHeaders(options?.headers, body),
    body,
  });

  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    const rawRetryAfter = response.headers.get('retry-after');
    const retryAfterSeconds = rawRetryAfter && /^\d+$/.test(rawRetryAfter)
      ? Number.parseInt(rawRetryAfter, 10)
      : undefined;
    throw new ApiError(errorMessage(response.status, detail), response.status, detail, retryAfterSeconds);
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    return (await response.text()) as unknown as T;
  }
  return (await response.json()) as T;
}
