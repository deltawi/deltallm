import { mountedPath, uiMount } from '../uiMount';
import {
  isBackgroundRequest,
  isSessionNeutralPath,
  markSessionActivity,
  notifySessionRejected,
} from '../sessionActivity';

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

export interface StructuredApiErrorDetail {
  code?: string;
  message?: string;
  [key: string]: unknown;
}

export function structuredApiErrorDetail(error: unknown): StructuredApiErrorDetail | null {
  if (!(error instanceof ApiError) || !error.detail || typeof error.detail !== 'object') {
    return null;
  }
  const wrapper = error.detail as { detail?: unknown; error?: unknown };
  const detail = wrapper.detail ?? wrapper.error;
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

function errorMessage(status: number, detail: unknown): string {
  if (detail && typeof detail === 'object') {
    const wrapper = detail as { detail?: unknown; error?: unknown };
    const nested = wrapper.detail ?? wrapper.error;
    if (typeof nested === 'string' && nested.trim()) return nested;
    if (nested && typeof nested === 'object' && 'message' in nested) {
      const message = (nested as { message?: unknown }).message;
      if (typeof message === 'string' && message.trim()) return message;
    }
  }
  if (typeof detail === 'string' && detail.trim()) return detail;
  return `Request failed (${status})`;
}

async function send(path: string, opts?: RequestInit & { json?: unknown }): Promise<Response> {
  const body = opts && 'json' in opts ? JSON.stringify(opts.json ?? null) : opts?.body;
  const headers = buildHeaders(opts?.headers, body);
  if (uiMount().external_console) {
    const supplied = new Headers(headers);
    if (supplied.has('Authorization') || supplied.has('X-Master-Key')) {
      throw new Error('Browser credentials are not accepted by the Console proxy');
    }
  }
  const response = await fetch(mountedPath(path), {
    credentials: 'include',
    ...opts,
    headers,
    body,
  });

  if (response.status === 401) {
    if (!isSessionNeutralPath(path)) notifySessionRejected();
  } else if (response.status < 500 && !isBackgroundRequest(new Headers(headers))) {
    // A 5xx (such as the session store being unavailable) may not have renewed anything.
    markSessionActivity();
  }

  if (!response.ok) {
    const detail = await parseErrorDetail(response);
    const rawRetryAfter = response.headers.get('retry-after');
    const retryAfterSeconds = rawRetryAfter && /^\d+$/.test(rawRetryAfter)
      ? Number.parseInt(rawRetryAfter, 10)
      : undefined;
    throw new ApiError(
      errorMessage(response.status, detail),
      response.status,
      detail,
      retryAfterSeconds,
    );
  }
  return response;
}

export async function apiFetch<T>(
  path: string,
  opts?: RequestInit & { json?: unknown },
): Promise<T> {
  const response = await send(path, opts);
  if (response.status === 204) return undefined as T;

  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) return (await response.text()) as T;
  return (await response.json()) as T;
}

export interface DownloadedFile {
  blob: Blob;
  /** From ``Content-Disposition``, or ``fallbackName`` when the server sends none. */
  filename: string;
  headers: Headers;
}

/** A file download with the same auth, session and error handling as ``apiFetch``. */
export async function apiDownload(path: string, fallbackName: string, opts?: RequestInit): Promise<DownloadedFile> {
  const response = await send(path, opts);
  const disposition = response.headers.get('content-disposition') || '';
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: await response.blob(), filename: match?.[1] || fallbackName, headers: response.headers };
}

export function withQuery<Query extends object>(path: string, params?: Query): string {
  if (!params) return path;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    const serialized = String(value);
    if (!serialized.trim()) continue;
    query.set(key, serialized);
  }
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}
