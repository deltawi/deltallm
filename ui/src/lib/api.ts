export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

let masterKey: string | null = null;

export function setMasterKey(value: string | null) {
  masterKey = value;
}

function buildHeaders(init?: HeadersInit): HeadersInit {
  const headers = new Headers(init);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (masterKey) headers.set('X-Master-Key', masterKey);
  return headers;
}

async function parseErrorDetail(res: Response): Promise<unknown> {
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    try {
      return await res.json();
    } catch {
      return undefined;
    }
  }
  try {
    return await res.text();
  } catch {
    return undefined;
  }
}

function errorMessage(status: number, detail: unknown): string {
  if (detail && typeof detail === 'object' && 'detail' in (detail as any)) {
    const d = (detail as any).detail;
    if (typeof d === 'string' && d.trim()) return d;
  }
  if (typeof detail === 'string' && detail.trim()) return detail;
  return `Request failed (${status})`;
}

async function apiFetch<T>(path: string, opts?: RequestInit & { json?: unknown }): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',
    ...opts,
    headers: buildHeaders(opts?.headers),
    body: opts && 'json' in opts ? JSON.stringify((opts as any).json ?? null) : opts?.body,
  });

  if (!res.ok) {
    const detail = await parseErrorDetail(res);
    throw new ApiError(errorMessage(res.status, detail), res.status, detail);
  }

  if (res.status === 204) return undefined as T;

  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) return (await res.text()) as unknown as T;
  return (await res.json()) as T;
}

export interface Pagination {
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface Paginated<T> {
  data: T[];
  pagination: Pagination;
}

function withQuery(path: string, params?: Record<string, unknown>): string {
  if (!params) return path;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    const s = String(v);
    if (!s.trim()) continue;
    qs.set(k, s);
  }
  const suffix = qs.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export const health = {
  check: () => apiFetch<any>('/health'),
};

export const spend = {
  summary: (start_date?: string, end_date?: string) => {
    const qs = new URLSearchParams();
    if (start_date) qs.set('start_date', start_date);
    if (end_date) qs.set('end_date', end_date);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return apiFetch<any>(`/ui/api/spend/summary${suffix}`);
  },
  report: (group_by: 'model' | 'provider' | 'day' | 'user' | 'team', start_date?: string, end_date?: string) => {
    const qs = new URLSearchParams({ group_by });
    if (start_date) qs.set('start_date', start_date);
    if (end_date) qs.set('end_date', end_date);
    return apiFetch<any>(`/ui/api/spend/report?${qs.toString()}`);
  },
  logs: (params?: Record<string, string>) => {
    const qs = new URLSearchParams(params || {});
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return apiFetch<any>(`/ui/api/logs${suffix}`);
  },
};

export const models = {
  list: (params?: { search?: string; provider?: string; mode?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/models', params as any)),
  get: (deploymentId: string) => apiFetch<any>(`/ui/api/models/${encodeURIComponent(deploymentId)}`),
  create: (payload: any) => apiFetch<any>('/ui/api/models', { method: 'POST', json: payload }),
  update: (deploymentId: string, payload: any) =>
    apiFetch<any>(`/ui/api/models/${encodeURIComponent(deploymentId)}`, { method: 'PUT', json: payload }),
  delete: (deploymentId: string) => apiFetch<any>(`/ui/api/models/${encodeURIComponent(deploymentId)}`, { method: 'DELETE' }),
};

export const settings = {
  get: () => apiFetch<any>('/ui/api/settings'),
  update: (payload: any) => apiFetch<any>('/ui/api/settings', { method: 'PUT', json: payload }),
};

export const organizations = {
  list: (params?: { search?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/organizations', params as any)),
  get: (orgId: string) => apiFetch<any>(`/ui/api/organizations/${encodeURIComponent(orgId)}`),
  create: (payload: any) => apiFetch<any>('/ui/api/organizations', { method: 'POST', json: payload }),
  update: (orgId: string, payload: any) =>
    apiFetch<any>(`/ui/api/organizations/${encodeURIComponent(orgId)}`, { method: 'PUT', json: payload }),
  members: (orgId: string) => apiFetch<any[]>(`/ui/api/organizations/${encodeURIComponent(orgId)}/members`),
  teams: (orgId: string) => apiFetch<any[]>(`/ui/api/organizations/${encodeURIComponent(orgId)}/teams`),
};

export const teams = {
  list: (params?: { search?: string; organization_id?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/teams', params as any)),
  get: (teamId: string) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}`),
  create: (payload: any) => apiFetch<any>('/ui/api/teams', { method: 'POST', json: payload }),
  update: (teamId: string, payload: any) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}`, { method: 'PUT', json: payload }),
  delete: (teamId: string) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}`, { method: 'DELETE' }),
  members: (teamId: string) => apiFetch<any[]>(`/ui/api/teams/${encodeURIComponent(teamId)}/members`),
  addMember: (teamId: string, payload: any) => apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}/members`, { method: 'POST', json: payload }),
  removeMember: (teamId: string, userId: string) =>
    apiFetch<any>(`/ui/api/teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
};

export const users = {
  list: (params?: { search?: string; team_id?: string; user_role?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/users', params as any)),
  create: (payload: any) => apiFetch<any>('/ui/api/users', { method: 'POST', json: payload }),
  update: (userId: string, payload: any) => apiFetch<any>(`/ui/api/users/${encodeURIComponent(userId)}`, { method: 'PUT', json: payload }),
  block: (userId: string, blocked: boolean) =>
    apiFetch<any>(`/ui/api/users/${encodeURIComponent(userId)}/block`, { method: 'POST', json: { blocked } }),
};

export const keys = {
  list: (params?: { search?: string; team_id?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/keys', params as any)),
  create: (payload: any) => apiFetch<any>('/ui/api/keys', { method: 'POST', json: payload }),
  update: (tokenHash: string, payload: any) =>
    apiFetch<any>(`/ui/api/keys/${encodeURIComponent(tokenHash)}`, { method: 'PUT', json: payload }),
  regenerate: (tokenHash: string) => apiFetch<any>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/regenerate`, { method: 'POST' }),
  revoke: (tokenHash: string) => apiFetch<any>(`/ui/api/keys/${encodeURIComponent(tokenHash)}/revoke`, { method: 'POST' }),
  delete: (tokenHash: string) => apiFetch<any>(`/ui/api/keys/${encodeURIComponent(tokenHash)}`, { method: 'DELETE' }),
};

export const batches = {
  list: (params?: { search?: string; status?: string; limit?: number; offset?: number }) =>
    apiFetch<Paginated<any>>(withQuery('/ui/api/batches', params as any)),
  summary: () => apiFetch<any>('/ui/api/batches/summary'),
  get: (batchId: string, params?: { items_limit?: number; items_offset?: number }) =>
    apiFetch<any>(withQuery(`/ui/api/batches/${encodeURIComponent(batchId)}`, params as any)),
  cancel: (batchId: string) => apiFetch<any>(`/ui/api/batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' }),
};

export const guardrails = {
  list: async () => {
    const res = await apiFetch<{ guardrails: any[] }>('/ui/api/guardrails');
    return res.guardrails || [];
  },
  update: async (payload: any) => {
    const res = await apiFetch<{ guardrails: any[] }>('/ui/api/guardrails', { method: 'PUT', json: payload });
    return res.guardrails || [];
  },
  getScoped: (scope: 'organization' | 'team' | 'key', entityId: string) =>
    apiFetch<any>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`),
  updateScoped: (scope: 'organization' | 'team' | 'key', entityId: string, payload: any) =>
    apiFetch<any>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`, { method: 'PUT', json: payload }),
  deleteScoped: (scope: 'organization' | 'team' | 'key', entityId: string) =>
    apiFetch<any>(`/ui/api/guardrails/scope/${encodeURIComponent(scope)}/${encodeURIComponent(entityId)}`, { method: 'DELETE' }),
};

export interface RBACAccount {
  account_id: string;
  email: string;
  role: string;
  is_active: boolean;
  force_password_change?: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  updated_at?: string;
}

export interface OrgMembership {
  membership_id: string;
  account_id: string;
  organization_id: string;
  role: string;
  created_at?: string;
  updated_at?: string;
}

export interface TeamMembership {
  membership_id: string;
  account_id: string;
  team_id: string;
  role: string;
  created_at?: string;
  updated_at?: string;
}

export const rbac = {
  accounts: {
    list: () => apiFetch<RBACAccount[]>('/ui/api/rbac/accounts'),
    upsert: (payload: any) => apiFetch<any>('/ui/api/rbac/accounts', { method: 'POST', json: payload }),
  },
  orgMemberships: {
    list: () => apiFetch<OrgMembership[]>('/ui/api/rbac/organization-memberships'),
    upsert: (payload: any) => apiFetch<any>('/ui/api/rbac/organization-memberships', { method: 'POST', json: payload }),
  },
  teamMemberships: {
    list: () => apiFetch<TeamMembership[]>('/ui/api/rbac/team-memberships'),
    upsert: (payload: any) => apiFetch<any>('/ui/api/rbac/team-memberships', { method: 'POST', json: payload }),
  },
};

export const auth = {
  me: () => apiFetch<any>('/auth/me', { headers: new Headers({ 'Content-Type': 'application/json' }) }),
  internalLogin: (payload: { email: string; password: string; mfa_code?: string }) =>
    apiFetch<any>('/auth/internal/login', { method: 'POST', json: payload }),
  internalLogout: () => apiFetch<any>('/auth/internal/logout', { method: 'POST' }),
  changePassword: (current_password: string | null, new_password: string) =>
    apiFetch<any>('/auth/internal/change-password', { method: 'POST', json: { current_password, new_password } }),
  ssoConfig: () => apiFetch<{ sso_enabled: boolean; provider?: string }>('/auth/sso-config'),
  ssoLogin: (state: string) => apiFetch<{ authorize_url: string }>(`/auth/login?state=${encodeURIComponent(state)}`),
  mfaEnrollStart: () => apiFetch<{ secret: string; otpauth_url: string }>('/auth/mfa/enroll/start', { method: 'POST' }),
  mfaEnrollConfirm: (code: string) => apiFetch<{ mfa_enabled: boolean }>('/auth/mfa/enroll/confirm', { method: 'POST', json: { code } }),
};
