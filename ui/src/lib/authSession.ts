import type { SessionInfo } from './authTypes';
import { uiMount } from './uiMount';

export type SessionFailureKind = 'anonymous' | 'retryable' | 'fatal';

export type SessionFailure =
  | { kind: 'anonymous'; message: string; retryAfterSeconds?: number }
  | { kind: 'retryable'; message: string; retryAfterSeconds?: number }
  | { kind: 'fatal'; message: string; retryAfterSeconds?: number };

type ErrorWithStatus = Error & {
  status?: unknown;
  retryAfterSeconds?: unknown;
};

export function isValidSessionPayload(value: unknown): value is SessionInfo {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.authenticated !== 'boolean') return false;
  if (!candidate.authenticated) return true;
  if (candidate.auth_mode !== 'session' && candidate.auth_mode !== 'master_key') return false;
  if (!uiMount().external_console && candidate.session_source !== 'external_customer') return true;
  if (candidate.auth_mode !== 'session' || candidate.session_source !== 'external_customer') return false;
  if (typeof candidate.account_id !== 'string' || !candidate.account_id) return false;
  if (typeof candidate.expires_at !== 'string' || !Number.isFinite(Date.parse(candidate.expires_at))) return false;
  const workspace = candidate.workspace;
  if (!workspace || typeof workspace !== 'object') return false;
  const fields = workspace as Record<string, unknown>;
  return ['integration_id', 'binding_id', 'organization_id', 'team_id', 'inference_user_id']
    .every((key) => typeof fields[key] === 'string' && !!fields[key]);
}

export function classifySessionCheckError(error: unknown): SessionFailure {
  const candidate = error instanceof Error ? error as ErrorWithStatus : null;
  const status = typeof candidate?.status === 'number' ? candidate.status : null;
  const retryAfterSeconds = typeof candidate?.retryAfterSeconds === 'number'
    ? candidate.retryAfterSeconds
    : undefined;

  if (status === 401 || status === 403) {
    return { kind: 'anonymous', message: 'Your session is no longer valid.' };
  }
  if (status === 408 || status === 425 || status === 429 || (status !== null && status >= 500)) {
    const retryMessage = status === 429 && retryAfterSeconds
      ? `Session verification is temporarily rate limited. Try again in ${retryAfterSeconds} seconds.`
      : 'We could not verify your session. Check your connection and try again.';
    return { kind: 'retryable', message: retryMessage, retryAfterSeconds };
  }
  if (status !== null) {
    return {
      kind: 'fatal',
      message: 'The authentication service returned an unexpected response. Reload the app or contact an administrator.',
    };
  }
  if (error instanceof TypeError || (error instanceof Error && error.name === 'AbortError')) {
    return {
      kind: 'retryable',
      message: 'We could not verify your session. Check your connection and try again.',
    };
  }
  return {
    kind: 'fatal',
    message: 'The application could not complete session verification. Reload the app or contact an administrator.',
  };
}
