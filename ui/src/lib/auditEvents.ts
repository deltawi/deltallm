import type { AuditCategory, AuditEvent, AuditEventContext, AuditEventQuery } from './api/audit';
import type { UIAccess } from './authorization';
import { parseLogTime, utcTimestamp } from './requestLogs';
import { keyTail, parseUtcInput, REQUEST_WINDOWS, requestWindowSince, utcInputValue, type RequestWindowKey } from './usageFilters';

// ---------------------------------------------------------------------------
// Categories
// ---------------------------------------------------------------------------

export const AUDIT_CATEGORIES: ReadonlyArray<{ key: AuditCategory; label: string; color: string }> = [
  { key: 'admin', label: 'Admin changes', color: '#7c3aed' },
  { key: 'auth', label: 'Sign-ins & accounts', color: '#0e9aa7' },
  { key: 'api', label: 'API requests', color: '#2a78d6' },
  { key: 'mcp', label: 'MCP tools', color: '#d63384' },
  { key: 'spend', label: 'Spend reports read', color: '#eb6834' },
  { key: 'system', label: 'System & external auth', color: '#8a93a3' },
];

// Same prefixes as the server (src/audit/categories.py). Anything else is an API request.
const CATEGORY_PREFIXES: ReadonlyArray<[AuditCategory, readonly string[]]> = [
  ['admin', ['ADMIN_', 'MANAGED_ASSET_']],
  ['auth', ['AUTH_']],
  ['mcp', ['MCP_']],
  ['spend', ['GLOBAL_SPEND_', 'SPEND_']],
  ['system', ['SYSTEM_', 'EXTERNAL_AUTH_', 'EMAIL_DELIVERY_', 'BATCH_WEBHOOK_']],
];

export function actionCategory(action: string): AuditCategory {
  for (const [category, prefixes] of CATEGORY_PREFIXES) {
    if (prefixes.some((prefix) => action.startsWith(prefix))) return category;
  }
  return 'api';
}

export function categoryInfo(category: AuditCategory) {
  return AUDIT_CATEGORIES.find((item) => item.key === category) ?? AUDIT_CATEGORIES[2];
}

export function isAuditCategory(value: string | null): value is AuditCategory {
  return AUDIT_CATEGORIES.some((item) => item.key === value);
}

// ---------------------------------------------------------------------------
// Actions in plain words
// ---------------------------------------------------------------------------

const ACTION_LABELS: Record<string, string> = {
  CHAT_COMPLETION_REQUEST: 'Chat completion',
  COMPLETION_REQUEST: 'Completion',
  RESPONSES_REQUEST: 'Responses request',
  EMBEDDING_REQUEST: 'Embedding',
  RERANK_REQUEST: 'Rerank',
  IMAGE_GENERATION_REQUEST: 'Image generation',
  AUDIO_SPEECH_REQUEST: 'Text to speech',
  AUDIO_TRANSCRIPTION_REQUEST: 'Transcription',
  BATCH_CREATE_REQUEST: 'Batch created',
  BATCH_CANCEL_REQUEST: 'Batch cancelled',
  BATCH_LIST_REQUEST: 'Batches listed',
  BATCH_READ_REQUEST: 'Batch read',
  FILE_CREATE_REQUEST: 'File uploaded',
  FILE_READ_REQUEST: 'File read',
  FILE_CONTENT_READ_REQUEST: 'File content read',
  PROMPT_RESOLUTION_REQUEST: 'Prompt resolved',
  MCP_TOOL_CALL: 'MCP tool call',
  MCP_OAUTH_CONNECT: 'MCP account connected',
  MCP_OAUTH_DISCONNECT: 'MCP account disconnected',
  MCP_TOOLS_LIST: 'MCP tools listed',
  BATCH_WEBHOOK_DELIVERED: 'Batch webhook delivered',
  BATCH_WEBHOOK_FAILED: 'Batch webhook failed',
  EMAIL_DELIVERY_RESULT: 'Email delivery result',
  AUTH_INTERNAL_LOGIN: 'Internal login',
  AUTH_INTERNAL_LOGOUT: 'Signed out',
  AUTH_MASTER_LOGIN: 'Master key login',
  AUTH_SSO_CALLBACK: 'SSO sign-in',
  AUTH_MFA_VERIFY: 'MFA code checked',
  AUTH_MFA_ENROLL_START: 'MFA setup started',
  AUTH_MFA_ENROLL_CONFIRM: 'MFA set up',
  AUTH_PASSWORD_CHANGE: 'Password changed',
  AUTH_PASSWORD_RESET_REQUEST: 'Password reset requested',
  AUTH_PASSWORD_RESET_COMPLETE: 'Password reset',
  AUTH_INVITATION_ACCEPT: 'Invitation accepted',
  AUTH_SELF_REGISTRATION_ACCEPTED: 'Self sign-up accepted',
  AUTH_SELF_REGISTRATION_BLOCKED: 'Self sign-up blocked',
  AUTH_SELF_REGISTRATION_PROVISIONING_FAILED: 'Self sign-up setup failed',
  ADMIN_RBAC_ORG_MEMBERSHIP_UPSERT: 'Organization member added or changed',
  ADMIN_RBAC_ORG_MEMBERSHIP_DELETE: 'Organization member removed',
  ADMIN_RBAC_TEAM_MEMBERSHIP_UPSERT: 'Team member added or changed',
  ADMIN_RBAC_TEAM_MEMBERSHIP_DELETE: 'Team member removed',
  ADMIN_RBAC_ACCOUNT_UPSERT: 'Account saved',
  ADMIN_RBAC_ACCOUNT_DELETE: 'Account deleted',
  ADMIN_KEY_SELF_CREATE: 'Own key created',
  ADMIN_KEY_SELF_REVOKE: 'Own key revoked',
  ADMIN_KEY_SELF_ROTATE: 'Own key rotated',
  ADMIN_KEY_AUTO_REVOKE_MEMBERSHIP_REMOVED: 'Key revoked after member removal',
  ADMIN_EMAIL_TEST: 'Test email sent',
  ADMIN_MCP_APPROVAL_DECIDE: 'MCP tool approval decided',
  ADMIN_MCP_SERVER_HEALTH_CHECK: 'MCP server health checked',
  ADMIN_MCP_SERVER_OAUTH_DISCOVERY: 'MCP server OAuth discovery run',
  ADMIN_ROUTE_SELECTOR_EVALUATE: 'Route selector tested',
  ADMIN_ROUTING_UPDATE: 'Routing updated',
  ADMIN_SETTINGS_UPDATE: 'Settings updated',
  ADMIN_GUARDRAILS_UPDATE: 'Guardrails updated',
  ADMIN_PROMPT_REGISTRY_UPDATE: 'Prompt updated',
  SYSTEM_BUDGET_NOTIFICATION_ENQUEUE: 'Budget alert queued',
  SYSTEM_KEY_NOTIFICATION_ENQUEUE: 'Key alert queued',
  GLOBAL_SPEND_READ: 'Spend read',
  GLOBAL_SPEND_REPORT_READ: 'Spend report read',
  GLOBAL_SPEND_KEYS_READ: 'Spend by key read',
  GLOBAL_SPEND_MODELS_READ: 'Spend by model read',
  GLOBAL_SPEND_TEAMS_READ: 'Spend by team read',
  GLOBAL_SPEND_END_USERS_READ: 'Spend by end user read',
  SPEND_LOGS_READ: 'Spend logs read',
  MANAGED_ASSET_ACCESS_UPDATE: 'Asset access updated',
};

const VERBS: Record<string, string> = {
  CREATE: 'created',
  UPDATE: 'updated',
  DELETE: 'deleted',
  REVOKE: 'revoked',
  UPSERT: 'saved',
  REGENERATE: 'regenerated',
  ROTATE: 'rotated',
  ADD: 'added',
  REMOVE: 'removed',
  CANCEL: 'cancelled',
  RESEND: 'resent',
  ACCEPT: 'accepted',
  BLOCK: 'blocked',
  RESET: 'reset',
  RESTORE: 'restored',
  RETRY: 'retried',
  EXPEDITE: 'expedited',
  REQUEST: 'requested',
  ACTIVATE: 'activated',
  ARCHIVE: 'archived',
  CLONE: 'cloned',
  ENQUEUE: 'queued',
  COMPLETE: 'completed',
  FAILED: 'failed',
  SUSPEND: 'suspended',
  RESUME: 'resumed',
  ATTACH: 'attached',
  DETACH: 'detached',
  GRANT: 'granted',
  LINK: 'linked',
  PROVISION: 'provisioned',
  REGISTER: 'registered',
  UPLOAD: 'uploaded',
  REPLAY: 'replayed',
  REFRESH: 'refreshed',
  BACKFILL: 'backfilled',
  RESOLVE: 'resolved',
  EXPIRE: 'expired',
  REQUEUE: 'requeued',
  ATTEMPT: 'attempt',
  DENIAL: 'denied',
  SUCCESS: 'succeeded',
  READ: 'read',
};

const WORDS: Record<string, string> = {
  ORG: 'organization',
  MCP: 'MCP',
  UI: 'UI',
  SSO: 'SSO',
  MFA: 'MFA',
  API: 'API',
  TPM: 'TPM',
  RPM: 'RPM',
  KEY: 'key',
};

const STRIP_PREFIXES = ['ADMIN_', 'AUTH_', 'SYSTEM_', 'GLOBAL_'];

function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** An action code in plain words, such as "Model updated". */
export function actionLabel(action: string): string {
  if (ACTION_LABELS[action]) return ACTION_LABELS[action];
  let code = action;
  for (const prefix of STRIP_PREFIXES) {
    if (code.startsWith(prefix)) {
      code = code.slice(prefix.length);
      break;
    }
  }
  code = code.replace(/_REQUEST$/, '');
  const words = code.split('_').filter(Boolean).filter((word) => word !== 'RBAC');
  if (!words.length) return action;
  const last = words[words.length - 1];
  const verb = words.length > 1 ? VERBS[last] : undefined;
  const nouns = (verb ? words.slice(0, -1) : words).map((word) => WORDS[word] ?? word.toLowerCase());
  return capitalize([...nouns, ...(verb ? [verb] : [])].join(' '));
}

/** The verb of an action in the past tense ("revoked"), or null when the code has none. */
export function actionVerb(action: string): string | null {
  const last = action.replace(/_REQUEST$/, '').split('_').pop() ?? '';
  return VERBS[last] ?? null;
}

// ---------------------------------------------------------------------------
// Status, duration and time
// ---------------------------------------------------------------------------

export function isFailed(event: Pick<AuditEvent, 'status'>): boolean {
  return Boolean(event.status) && event.status !== 'success';
}

/** "OK", an HTTP code such as "504", or "Failed". */
export function statusLabel(event: Pick<AuditEvent, 'status' | 'error_code'>): string {
  if (!isFailed(event)) return 'OK';
  return event.error_code && /^\d{3}$/.test(event.error_code) ? event.error_code : 'Failed';
}

export const SLOW_MS = 10_000;

/** Exact UTC time: "10:21:44" today, "Oct 13 17:40:02" on other days. */
export function utcClock(value: string | null | undefined, now = new Date()): string {
  const date = parseLogTime(value);
  if (!date) return '—';
  const time = date.toISOString().slice(11, 19);
  if (date.toISOString().slice(0, 10) === now.toISOString().slice(0, 10)) return time;
  const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
  return `${day} ${time}`;
}

/** "Oct 14, 10:21:44 UTC" for the panel header. */
export function utcLong(value: string | null | undefined): string {
  const date = parseLogTime(value);
  if (!date) return '—';
  const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
  return `${day}, ${date.toISOString().slice(11, 19)} UTC`;
}

export { utcTimestamp };

export function tokensLabel(event: Pick<AuditEvent, 'input_tokens' | 'output_tokens'>): string {
  if (event.input_tokens == null && event.output_tokens == null) return '—';
  const compact = (value: number | null) => {
    const number = value ?? 0;
    return number >= 1000 ? `${(number / 1000).toFixed(number >= 10_000 ? 0 : 1)}K` : String(number);
  };
  return `${compact(event.input_tokens)} → ${compact(event.output_tokens)}`;
}

// ---------------------------------------------------------------------------
// Who and what
// ---------------------------------------------------------------------------

export interface ActorDisplay {
  name: string;
  /** What the actor is: "person", "API key …41ab", "master key". */
  kind: string;
  /** A named key's hash tail, shown in gray after its name like the Usage request list. */
  tail?: string;
  /** Whether the name is a real person or key (rather than "Master key" or "System"). */
  named: boolean;
  /** The filter a click on the name sets. */
  filter: ActorFilter | null;
}

export function eventKeyHash(event: Pick<AuditEvent, 'api_key' | 'actor_type' | 'actor_id'>): string | null {
  if (event.api_key) return event.api_key;
  if (event.actor_type === 'api_key' && event.actor_id) return event.actor_id;
  return null;
}

export function shortId(value: string | null | undefined, size = 8): string {
  if (!value) return '—';
  return value.length > size + 2 ? `${value.slice(0, size)}…` : value;
}

const ACTOR_TYPE_NAMES: Record<string, string> = {
  master_key: 'Master key',
  system: 'System',
  operator: 'Operator',
};

export function actorDisplay(event: AuditEvent): ActorDisplay {
  const labels = event.labels;
  const type = event.actor_type ?? '';
  if (type === 'platform_account') {
    const name = labels?.actor || (event.actor_id ? `Account ${shortId(event.actor_id)}` : 'Unknown person');
    return {
      name,
      kind: 'person',
      named: true,
      filter: event.actor_id ? { value: event.actor_id, label: labels?.actor || name } : null,
    };
  }
  if (type === 'api_key') {
    const hash = eventKeyHash(event);
    const label = labels?.actor || labels?.key_name;
    const name = label || (hash ? `Key ${keyTail(hash)}` : 'API key');
    return {
      name,
      tail: label && hash ? keyTail(hash) : undefined,
      kind: hash ? `API key ${keyTail(hash)}` : 'API key',
      named: true,
      filter: hash ? { value: hash, label: name } : null,
    };
  }
  if (ACTOR_TYPE_NAMES[type]) {
    return {
      name: ACTOR_TYPE_NAMES[type],
      kind: type === 'system' && event.actor_id ? event.actor_id.replace(/_/g, ' ') : ACTOR_TYPE_NAMES[type].toLowerCase(),
      named: false,
      filter: { value: type, label: ACTOR_TYPE_NAMES[type], type: true },
    };
  }
  const name = event.actor_id || (type ? type.replace(/_/g, ' ') : 'Unknown');
  return { name, kind: type.replace(/_/g, ' ') || 'unknown', named: false, filter: event.actor_id ? { value: event.actor_id, label: name } : null };
}

const RESOURCE_TYPE_LABELS: Record<string, string> = {
  model_deployment: 'Model',
  model: 'Model',
  route_group: 'Route group',
  mcp_server: 'MCP server',
  mcp_tool: 'Tool',
  prompt_template: 'Prompt',
  api_key: 'API key',
  team: 'Team',
  organization: 'Organization',
  account: 'Account',
  batch: 'Batch',
  file: 'File',
  named_credential: 'Credential',
  tier: 'Tier',
  session: 'Session',
  guardrail: 'Guardrail',
  guardrails: 'Guardrails',
  settings: 'Settings',
  invitation: 'Invitation',
};

export function resourceTypeLabel(type: string | null | undefined): string {
  if (!type) return '';
  return RESOURCE_TYPE_LABELS[type] ?? capitalize(type.replace(/_/g, ' '));
}

export function resourceName(event: Pick<AuditEvent, 'resource_type' | 'resource_id' | 'labels'>): string | null {
  if (!event.resource_id) return null;
  if (event.labels?.resource) return event.labels.resource;
  if (event.resource_type === 'api_key') return `Key ${keyTail(event.resource_id)}`;
  return event.resource_id;
}

/** The UI page of a resource, when the viewer may open it. */
export function resourceLink(type: string | null | undefined, id: string | null | undefined, access: Partial<UIAccess>): string | null {
  if (!type || !id) return null;
  const segment = encodeURIComponent(id);
  switch (type) {
    case 'model_deployment':
      return access.models ? `/models/${segment}` : null;
    case 'route_group':
      return access.route_groups ? `/route-groups/${segment}` : null;
    case 'prompt_template':
      return access.prompts ? `/prompts/${segment}` : null;
    case 'mcp_server':
      return access.mcp_servers ? `/mcp-servers/${segment}` : null;
    case 'api_key':
      return access.keys ? '/keys' : null;
    case 'organization':
      return access.organizations ? `/organizations/${segment}` : null;
    case 'team':
      return access.teams ? `/teams/${segment}` : null;
    case 'batch':
      return access.batches ? `/batches/${segment}` : null;
    case 'tier':
      return access.tiers ? `/tiers/${segment}` : null;
    default:
      return null;
  }
}

const PLATFORM_ROLE_LABELS: Record<string, string> = {
  platform_admin: 'Platform admin',
  org_user: 'Organization user',
};

const ORG_ROLE_LABELS: Record<string, string> = {
  org_member: 'Org member',
  org_owner: 'Org owner',
  org_admin: 'Org admin',
  org_billing: 'Org billing',
  org_auditor: 'Org auditor',
};

/** The actor's role today: "Platform admin" or "Org member in Acme Corp". */
export function actorRole(context: AuditEventContext['actor'], organization: string | null): string | null {
  if (!context || context.state === 'missing') return null;
  if (context.platform_role === 'platform_admin') return PLATFORM_ROLE_LABELS.platform_admin;
  if (context.organization_role) {
    const role = ORG_ROLE_LABELS[context.organization_role] ?? context.organization_role;
    return organization ? `${role} in ${organization}` : role;
  }
  return context.platform_role ? PLATFORM_ROLE_LABELS[context.platform_role] ?? context.platform_role : null;
}

export type NowTone = 'ok' | 'bad' | 'warn' | 'muted';

/** The current state of a key or resource as a short pill. */
export function stateDisplay(
  resourceType: string | null | undefined,
  state: { state?: string; progress?: number | null } | null | undefined,
): { label: string; tone: NowTone } | null {
  if (!state?.state) return null;
  const value = state.state;
  if (value === 'missing') return { label: resourceType === 'api_key' ? 'Revoked or deleted' : 'No longer exists', tone: 'muted' };
  if (value === 'active') return { label: 'Active', tone: 'ok' };
  if (value === 'expired') return { label: 'Expired', tone: 'bad' };
  if (value === 'inactive') return { label: 'Deactivated', tone: 'bad' };
  if (value === 'blocked') return { label: 'Blocked', tone: 'bad' };
  if (resourceType === 'batch') {
    const progress = typeof state.progress === 'number' ? ` at ${state.progress}%` : '';
    if (value === 'completed') return { label: 'Completed', tone: 'ok' };
    if (value === 'failed') return { label: `Failed${progress}`, tone: 'bad' };
    if (value === 'cancelled' || value === 'expired') return { label: `${capitalize(value)}${progress}`, tone: 'warn' };
    return { label: `${capitalize(value.replace(/_/g, ' '))}${progress}`, tone: 'ok' };
  }
  return { label: capitalize(value.replace(/_/g, ' ')), tone: value.includes('delet') ? 'muted' : 'ok' };
}

// ---------------------------------------------------------------------------
// Client name from the user agent
// ---------------------------------------------------------------------------

function shortVersion(version: string | undefined): string {
  if (!version) return '';
  return version.split('.').slice(0, 2).join('.');
}

const SDK_PATTERNS: ReadonlyArray<[RegExp, string]> = [
  [/^OpenAI\/Python\s+([\d.]+)/i, 'openai-python'],
  [/^OpenAI\/(?:JS|Node)\s+([\d.]+)/i, 'openai-node'],
  [/^Anthropic\/Python\s+([\d.]+)/i, 'anthropic-python'],
  [/^Anthropic\/(?:JS|Node)\s+([\d.]+)/i, 'anthropic-node'],
  [/^curl\/([\d.]+)/i, 'curl'],
  [/^python-requests\/([\d.]+)/i, 'python-requests'],
  [/^python-httpx\/([\d.]+)/i, 'httpx'],
  [/^aiohttp\/([\d.]+)/i, 'aiohttp'],
  [/^axios\/([\d.]+)/i, 'axios'],
  [/^node-fetch\/?([\d.]*)/i, 'node-fetch'],
  [/^undici\/?([\d.]*)/i, 'undici'],
  [/^Go-http-client\/([\d.]+)/i, 'Go HTTP client'],
  [/^okhttp\/([\d.]+)/i, 'okhttp'],
  [/^PostmanRuntime\/([\d.]+)/i, 'Postman'],
  [/^insomnia\/([\d.]+)/i, 'Insomnia'],
];

/** A short client name from a user agent: "Chrome on macOS", "openai-python 1.51", "curl 8.5". */
export function clientName(userAgent: string | null | undefined): string | null {
  const agent = userAgent?.trim();
  if (!agent) return null;
  for (const [pattern, name] of SDK_PATTERNS) {
    const match = agent.match(pattern);
    if (match) return `${name} ${shortVersion(match[1])}`.trim();
  }
  if (/Mozilla\//.test(agent)) {
    const browser = /Edg\//.test(agent)
      ? 'Edge'
      : /OPR\//.test(agent)
        ? 'Opera'
        : /Firefox\//.test(agent)
          ? 'Firefox'
          : /Chrome\//.test(agent)
            ? 'Chrome'
            : /Safari\//.test(agent)
              ? 'Safari'
              : 'Browser';
    const os = /iPhone|iPad/.test(agent)
      ? 'iOS'
      : /Android/.test(agent)
        ? 'Android'
        : /Mac OS X|Macintosh/.test(agent)
          ? 'macOS'
          : /Windows/.test(agent)
            ? 'Windows'
            : /Linux/.test(agent)
              ? 'Linux'
              : null;
    return os ? `${browser} on ${os}` : browser;
  }
  const generic = agent.match(/^([A-Za-z][\w.-]*)\/([\d.]+)/);
  if (generic) return `${generic[1]} ${shortVersion(generic[2])}`;
  return agent.length > 40 ? `${agent.slice(0, 40)}…` : agent;
}

// ---------------------------------------------------------------------------
// One-line summary
// ---------------------------------------------------------------------------

/** A sentence as parts, so names render bold without building HTML. */
export type SummaryPart = string | { bold: string };

function actorPhrase(event: AuditEvent, actor: ActorDisplay): SummaryPart[] {
  if (event.actor_type === 'api_key') return ['API key ', { bold: actor.name }];
  if (event.actor_type === 'master_key') return ['The ', { bold: 'master key' }];
  if (event.actor_type === 'system') return ['The gateway'];
  return [{ bold: actor.name }];
}

/** "Route group" → "route group", but "API key" and "MCP server" keep their capitals. */
function lowerFirst(text: string): string {
  return text.length > 1 && text[1] === text[1].toLowerCase() ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

export function eventSummary(event: AuditEvent): SummaryPart[] {
  const actor = actorDisplay(event);
  const who = actorPhrase(event, actor);
  const category = actionCategory(event.action);
  const failed = isFailed(event);
  const type = lowerFirst(resourceTypeLabel(event.resource_type));
  const name = resourceName(event);
  const thing: SummaryPart[] = name ? [`${type ? `${type} ` : ''}`, { bold: name }] : type ? [type] : [];
  const label = actionLabel(event.action);
  const reason = event.error_code || event.error_type;
  const failure: SummaryPart[] = failed ? [` and it failed${reason ? ` (${reason})` : ''}.`] : ['.'];
  const metadata = event.metadata ?? {};

  if (category === 'auth') {
    const signIn = /LOGIN|SSO_CALLBACK/.test(event.action);
    if (signIn) {
      return failed
        ? [...who, ' failed to sign in', ...(event.ip ? [' from ', { bold: event.ip }] : []), '.']
        : [...who, ' signed in', ...(event.ip ? [' from ', { bold: event.ip }] : []), '.'];
    }
    return [...who, `: ${label.toLowerCase()}`, ...(failed ? [' (failed).'] : ['.'])];
  }
  if (category === 'mcp') {
    const server = typeof metadata.server_key === 'string' ? metadata.server_key : null;
    if (event.action === 'MCP_TOOL_CALL' && name) {
      return [...who, ' called tool ', { bold: name }, ...(server ? [' on MCP server ', { bold: server }] : []), ...failure];
    }
    return [...who, ` ${label.toLowerCase()}`, ...(server ? [' on MCP server ', { bold: server }] : []), ...failure];
  }
  if (category === 'admin') {
    const changed = Array.isArray(metadata.changed_fields) ? metadata.changed_fields.length : 0;
    const verb = actionVerb(event.action);
    const organization = event.labels?.organization;
    const where: SummaryPart[] = organization && event.resource_type !== 'organization' ? [' in ', { bold: organization }] : [];
    if (verb === 'updated' && changed > 0 && thing.length) {
      return [...who, ' changed ', { bold: plural(changed, 'setting') }, ' on ', ...thing, ...where, ...(failed ? [' but it failed.'] : ['.'])];
    }
    if (verb && thing.length) {
      return failed
        ? [...who, ` tried to ${verbBase(event.action)} `, ...thing, ...where, ' and it failed.']
        : [...who, ` ${verb} `, ...thing, ...where, '.'];
    }
    return [...who, `: ${label.toLowerCase()}`, ...(thing.length ? [' on ', ...thing] : []), ...(failed ? [' (failed).'] : ['.'])];
  }
  if (category === 'system') {
    return ['The gateway recorded ', { bold: label.toLowerCase() }, ...(thing.length ? [' for ', ...thing] : []), ...(failed ? [' (failed).'] : ['.'])];
  }
  if (category === 'spend') {
    return [...who, ` read ${label.replace(/ read$/, '').toLowerCase()}`, ...failure];
  }
  // API requests.
  if (event.resource_type === 'model' || event.resource_type === 'model_deployment' || event.resource_type === 'route_group') {
    return [...who, ' called ', { bold: name ?? 'a model' }, ...failure];
  }
  const verb = actionVerb(event.action);
  if (verb && thing.length) {
    return failed
      ? [...who, ` tried to ${verbBase(event.action)} `, ...thing, ' and it failed', ...(reason ? [` (${reason})`] : []), '.']
      : [...who, ` ${verb} `, ...thing, '.'];
  }
  return [...who, `: ${label.toLowerCase()}`, ...(thing.length ? [' on ', ...thing] : []), ...failure];
}

const BASE_VERBS: Record<string, string> = {
  CREATE: 'create',
  CANCEL: 'cancel',
  READ: 'read',
  UPDATE: 'update',
  DELETE: 'delete',
  REVOKE: 'revoke',
  UPSERT: 'save',
  REGENERATE: 'regenerate',
  ROTATE: 'rotate',
  ADD: 'add',
  REMOVE: 'remove',
};

function verbBase(action: string): string {
  const last = action.split('_').pop() ?? '';
  return BASE_VERBS[last] ?? 'change';
}

/** A short error title for the panel. */
export function errorTitle(event: Pick<AuditEvent, 'action' | 'error_code' | 'error_type'>): string {
  const code = Number(event.error_code);
  if (code === 429) return 'Rate limited';
  if (code === 408 || code === 504) return 'Timeout';
  if (code === 401 || code === 403) return 'Not authorized';
  if (code === 404) return 'Not found';
  if (code >= 500) return 'Provider error';
  if (actionCategory(event.action) === 'auth' && /LOGIN|SSO/.test(event.action)) return 'Sign-in failed';
  return 'Failed';
}

export function errorDetail(event: Pick<AuditEvent, 'error_code' | 'error_type' | 'metadata'>): string | null {
  const error = event.metadata?.error;
  if (typeof error === 'string' && error.trim()) return error;
  if (error && typeof error === 'object') {
    const message = (error as Record<string, unknown>).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  const parts = [event.error_type, event.error_code ? `error code ${event.error_code}` : null].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}

export function signInMethod(action: string): string | null {
  if (action === 'AUTH_INTERNAL_LOGIN') return 'Email and password';
  if (action === 'AUTH_MASTER_LOGIN') return 'Master key';
  if (action === 'AUTH_SSO_CALLBACK') return 'Single sign-on';
  return null;
}

export function changedFields(event: Pick<AuditEvent, 'metadata'>): string[] {
  const value = event.metadata?.changed_fields;
  return Array.isArray(value) ? value.map(String) : [];
}

// ---------------------------------------------------------------------------
// Filters in the URL
// ---------------------------------------------------------------------------

export interface ActorFilter {
  value: string;
  label: string;
  /** Set when the value is an actor type ("master_key", "system") rather than an ID. */
  type?: boolean;
}

export interface AuditFilters {
  category?: AuditCategory;
  status?: 'success' | 'error';
  action?: string;
  actor?: ActorFilter;
  organization?: { value: string; label: string };
  resource?: { type: string; id?: string; label?: string };
  ip?: string;
  requestId?: string;
  search?: string;
}

export type AuditWindow = RequestWindowKey | 'custom';

export function parseAuditFilters(params: URLSearchParams): AuditFilters {
  const filters: AuditFilters = {};
  const category = params.get('category');
  if (isAuditCategory(category)) filters.category = category;
  const status = params.get('status');
  if (status === 'success' || status === 'error') filters.status = status;
  const action = params.get('action')?.trim();
  if (action) filters.action = action;
  const actor = params.get('actor')?.trim();
  const actorType = params.get('actor_type')?.trim();
  if (actor) filters.actor = { value: actor, label: params.get('actor_label') || actor };
  else if (actorType) filters.actor = { value: actorType, label: ACTOR_TYPE_NAMES[actorType] ?? actorType, type: true };
  const organization = params.get('org')?.trim();
  if (organization) filters.organization = { value: organization, label: params.get('org_label') || organization };
  const resourceType = params.get('resource_type')?.trim();
  if (resourceType) {
    filters.resource = {
      type: resourceType,
      id: params.get('resource_id')?.trim() || undefined,
      label: params.get('resource_label') || undefined,
    };
  }
  const ip = params.get('ip')?.trim();
  if (ip) filters.ip = ip;
  const requestId = params.get('request_id')?.trim();
  if (requestId) filters.requestId = requestId;
  const search = params.get('q')?.trim();
  if (search) filters.search = search;
  return filters;
}

const FILTER_PARAMS = [
  'category', 'status', 'action', 'actor', 'actor_label', 'actor_type', 'org', 'org_label',
  'resource_type', 'resource_id', 'resource_label', 'ip', 'request_id', 'q',
];

export function writeAuditFilters(current: URLSearchParams, filters: AuditFilters): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const key of FILTER_PARAMS) next.delete(key);
  next.delete('event');
  if (filters.category) next.set('category', filters.category);
  if (filters.status) next.set('status', filters.status);
  if (filters.action) next.set('action', filters.action);
  if (filters.actor?.type) next.set('actor_type', filters.actor.value);
  else if (filters.actor) {
    next.set('actor', filters.actor.value);
    if (filters.actor.label !== filters.actor.value) next.set('actor_label', filters.actor.label);
  }
  if (filters.organization) {
    next.set('org', filters.organization.value);
    if (filters.organization.label !== filters.organization.value) next.set('org_label', filters.organization.label);
  }
  if (filters.resource) {
    next.set('resource_type', filters.resource.type);
    if (filters.resource.id) next.set('resource_id', filters.resource.id);
    if (filters.resource.label && filters.resource.label !== filters.resource.id) next.set('resource_label', filters.resource.label);
  }
  if (filters.ip) next.set('ip', filters.ip);
  if (filters.requestId) next.set('request_id', filters.requestId);
  if (filters.search) next.set('q', filters.search);
  return next;
}

export function hasAuditFilters(filters: AuditFilters): boolean {
  return Object.values(filters).some((value) => value !== undefined);
}

export function parseAuditWindow(value: string | null): AuditWindow {
  if (value === 'custom') return 'custom';
  return REQUEST_WINDOWS.some((window) => window.key === value) ? (value as RequestWindowKey) : '24h';
}

export { parseUtcInput, utcInputValue };

/** The server query for the filters and time window. A custom window without a start falls back to 24 hours. */
export function auditQuery(
  filters: AuditFilters,
  window: AuditWindow,
  custom: { from?: string | null; to?: string | null },
  now = new Date(),
): AuditEventQuery {
  const query: AuditEventQuery = {};
  if (window === 'custom') {
    query.since = parseUtcInput(custom.from) ?? requestWindowSince('24h', now);
    const until = parseUtcInput(custom.to);
    if (until) query.until = until;
  } else {
    query.since = requestWindowSince(window, now);
  }
  if (filters.category) query.category = filters.category;
  if (filters.status) query.status = filters.status;
  if (filters.action) query.action = filters.action;
  if (filters.actor?.type) query.actor_type = filters.actor.value;
  else if (filters.actor) query.actor = filters.actor.value;
  if (filters.organization) query.organization_id = filters.organization.value;
  if (filters.resource) {
    query.resource_type = filters.resource.type;
    if (filters.resource.id) query.resource_id = filters.resource.id;
  }
  if (filters.ip) query.ip = filters.ip;
  if (filters.requestId) query.request_id = filters.requestId;
  if (filters.search) query.search = filters.search;
  return query;
}

export function windowLabel(window: AuditWindow): string {
  if (window === 'custom') return 'custom range';
  const hours = REQUEST_WINDOWS.find((item) => item.key === window)?.hours ?? 24;
  return hours < 48 ? `last ${hours} h` : `last ${hours / 24} days`;
}

// ---------------------------------------------------------------------------
// Optional columns, saved in this browser
// ---------------------------------------------------------------------------

export type AuditColumn = 'team' | 'ip' | 'tokens' | 'request_id' | 'client';

export const OPTIONAL_COLUMNS: ReadonlyArray<{ key: AuditColumn; label: string }> = [
  { key: 'team', label: 'Team' },
  { key: 'ip', label: 'IP address' },
  { key: 'tokens', label: 'Tokens' },
  { key: 'request_id', label: 'Request ID' },
  { key: 'client', label: 'Client' },
];

const COLUMNS_STORAGE_KEY = 'deltallm.audit.columns';

export function readAuditColumns(storage?: Pick<Storage, 'getItem'>): AuditColumn[] {
  try {
    const parsed: unknown = JSON.parse((storage ?? globalThis.localStorage)?.getItem(COLUMNS_STORAGE_KEY) ?? '[]');
    if (!Array.isArray(parsed)) return [];
    return OPTIONAL_COLUMNS.map((column) => column.key).filter((key) => parsed.includes(key));
  } catch {
    return [];
  }
}

export function saveAuditColumns(columns: readonly AuditColumn[], storage?: Pick<Storage, 'setItem'>): void {
  try {
    (storage ?? globalThis.localStorage)?.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(columns));
  } catch {
    // Private windows and blocked storage: the choice lasts for this visit only.
  }
}

const UTC_STORAGE_KEY = 'deltallm.audit.utc';

export function readAuditUtc(storage?: Pick<Storage, 'getItem'>): boolean {
  try {
    return (storage ?? globalThis.localStorage)?.getItem(UTC_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

export function saveAuditUtc(value: boolean, storage?: Pick<Storage, 'setItem'>): void {
  try {
    (storage ?? globalThis.localStorage)?.setItem(UTC_STORAGE_KEY, value ? '1' : '0');
  } catch {
    // Private windows and blocked storage: the choice lasts for this visit only.
  }
}
