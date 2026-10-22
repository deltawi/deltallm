// Model form state for parameter rules, header rules and the outbound proxy.
// The gateway applies these per deployment; see docs/admin-ui/models.md.

export type ParamAction = 'default' | 'override' | 'drop';
export type HeaderAction = 'forward' | 'set' | 'session';

export interface ParamRule {
  action: ParamAction;
  key: string;
  value: string;
}

export interface HeaderRule {
  action: HeaderAction;
  name: string;
  value: string;
}

export interface RequestShapingForm {
  params: ParamRule[];
  headers: HeaderRule[];
  outboundProxy: string;
}

export const EMPTY_REQUEST_SHAPING: RequestShapingForm = { params: [], headers: [], outboundProxy: '' };

export const OPENCODE_SESSION_HEADER = 'x-opencode-session';

// Mirrors the gateway's rules so the form can explain a rejection before saving.
const BLOCKED_HEADER_NAMES = new Set([
  'authorization', 'proxy-authorization', 'cookie', 'set-cookie', 'x-api-key', 'api-key', 'x-goog-api-key',
  'host', 'content-length', 'content-type', 'content-encoding', 'transfer-encoding', 'connection', 'keep-alive',
  'accept-encoding', 'expect', 'te', 'trailer', 'upgrade', 'forwarded', 'x-real-ip', 'via',
]);
const BLOCKED_HEADER_PREFIXES = ['proxy-', 'x-forwarded-', 'x-amz-', 'x-deltallm-'];
const BLOCKED_HEADER_WORDS = ['auth', 'token', 'secret', 'key', 'cookie', 'credential', 'password', 'signature'];
const PROTECTED_PARAMS = new Set([
  'model', 'messages', 'input', 'prompt', 'stream', 'stream_options', 'n',
  'max_tokens', 'max_completion_tokens', 'max_output_tokens',
]);
// Provider-native limit names inside objects, such as generationConfig.maxOutputTokens.
const PROTECTED_NESTED_KEYS = new Set([
  'max_tokens', 'max_output_tokens', 'max_completion_tokens', 'maxtokens', 'maxoutputtokens', 'candidatecount', 'stream',
]);

function protectedNestedKey(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = protectedNestedKey(item);
      if (found) return found;
    }
  } else if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      if (PROTECTED_NESTED_KEYS.has(key.toLowerCase())) return key;
      const found = protectedNestedKey(item);
      if (found) return found;
    }
  }
  return null;
}

export function headerNameBlocked(name: string): boolean {
  const lowered = name.trim().toLowerCase();
  return BLOCKED_HEADER_NAMES.has(lowered)
    || BLOCKED_HEADER_PREFIXES.some((prefix) => lowered.startsWith(prefix))
    || BLOCKED_HEADER_WORDS.some((word) => lowered.includes(word));
}

// Values keep their JSON type: numbers, booleans, null, objects and arrays.
export function parseParamValue(raw: string): unknown {
  const value = raw.trim();
  if (value === '') return '';
  if (/^[[{"]/.test(value) || value === 'true' || value === 'false' || value === 'null') {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  if (!Number.isNaN(Number(value))) return Number(value);
  return value;
}

export function formatParamValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function recordOrEmpty(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function shapingFromModel(
  deltallmParams: Record<string, unknown>,
  modelInfo: Record<string, unknown>,
): RequestShapingForm {
  const params: ParamRule[] = [
    ...Object.entries(recordOrEmpty(modelInfo.default_params))
      .map(([key, value]) => ({ action: 'default' as const, key, value: formatParamValue(value) })),
    ...Object.entries(recordOrEmpty(deltallmParams.override_params))
      .map(([key, value]) => ({ action: 'override' as const, key, value: formatParamValue(value) })),
    ...stringList(deltallmParams.drop_params).map((key) => ({ action: 'drop' as const, key, value: '' })),
  ];
  const session = typeof deltallmParams.session_header === 'string' ? deltallmParams.session_header : '';
  const headers: HeaderRule[] = [
    ...(session ? [{ action: 'session' as const, name: session, value: '' }] : []),
    ...stringList(deltallmParams.forward_headers).map((name) => ({ action: 'forward' as const, name, value: '' })),
    ...Object.entries(recordOrEmpty(deltallmParams.extra_headers))
      .map(([name, value]) => ({ action: 'set' as const, name, value: String(value) })),
  ];
  const outboundProxy = typeof deltallmParams.outbound_proxy === 'string' ? deltallmParams.outbound_proxy : '';
  return { params, headers, outboundProxy };
}

export function validateRequestShaping(shaping: RequestShapingForm, { chat }: { chat: boolean }): string | null {
  const seenParams = new Map<string, ParamAction>();
  for (const rule of shaping.params) {
    const key = rule.key.trim();
    if (!key) continue;
    if (rule.action !== 'default') {
      if (!chat) return 'Override and Drop apply to chat models only.';
      const [top, ...nested] = key.split('.');
      const nestedKey = rule.action === 'override'
        ? protectedNestedKey(parseParamValue(rule.value))
        : nested.find((segment) => PROTECTED_NESTED_KEYS.has(segment.toLowerCase())) ?? null;
      if (PROTECTED_PARAMS.has(top) || nestedKey) {
        return `"${nestedKey ?? key}" controls admission and billing, so it cannot be overridden or dropped.`;
      }
      if (rule.action === 'override' && key.includes('.')) {
        return `Override "${key}" with an object value on its top-level name instead.`;
      }
    }
    const previous = seenParams.get(key);
    if (previous && previous !== rule.action && previous !== 'default' && rule.action !== 'default') {
      return `"${key}" cannot be both overridden and dropped.`;
    }
    if (previous === rule.action) return `"${key}" is listed twice.`;
    seenParams.set(key, rule.action);
  }
  const seenHeaders = new Set<string>();
  let sessions = 0;
  for (const rule of shaping.headers) {
    const name = rule.name.trim();
    if (!name) continue;
    if (!/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(name)) return `"${name}" is not a valid header name.`;
    if (headerNameBlocked(name)) {
      return `"${name}" cannot be sent: auth, credential and transport headers are never forwarded or set.`;
    }
    if (seenHeaders.has(name.toLowerCase())) return `Header "${name}" is listed twice.`;
    seenHeaders.add(name.toLowerCase());
    if (rule.action === 'session') sessions += 1;
    if (rule.action === 'set' && /[\r\n]/.test(rule.value)) return `Header "${name}" cannot contain line breaks.`;
  }
  if (sessions > 1) return 'Only one header can be the session ID.';
  return null;
}

// Writes every shaping field, with null for an empty one so saving clears it.
export function applyRequestShaping(
  shaping: RequestShapingForm,
  deltallmParams: Record<string, unknown>,
  modelInfo: Record<string, unknown>,
  { chat }: { chat: boolean },
): void {
  const defaults: Record<string, unknown> = {};
  const overrides: Record<string, unknown> = {};
  const drops: string[] = [];
  for (const rule of shaping.params) {
    const key = rule.key.trim();
    if (!key) continue;
    if (rule.action === 'default') defaults[key] = parseParamValue(rule.value);
    else if (chat && rule.action === 'override') overrides[key] = parseParamValue(rule.value);
    else if (chat && rule.action === 'drop') drops.push(key);
  }
  modelInfo.default_params = defaults;

  const forward: string[] = [];
  const extra: Record<string, string> = {};
  let session: string | null = null;
  if (chat) {
    for (const rule of shaping.headers) {
      const name = rule.name.trim();
      if (!name) continue;
      if (rule.action === 'forward') forward.push(name);
      else if (rule.action === 'set') extra[name] = rule.value;
      else session = name;
    }
  }
  deltallmParams.override_params = Object.keys(overrides).length > 0 ? overrides : null;
  deltallmParams.drop_params = drops.length > 0 ? drops : null;
  deltallmParams.forward_headers = forward.length > 0 ? forward : null;
  deltallmParams.extra_headers = Object.keys(extra).length > 0 ? extra : null;
  deltallmParams.session_header = session;
  deltallmParams.outbound_proxy = shaping.outboundProxy.trim() || null;
}

export function activeParamCount(shaping: RequestShapingForm): number {
  return shaping.params.filter((rule) => rule.key.trim()).length;
}

export function activeNetworkCount(shaping: RequestShapingForm, { chat }: { chat: boolean }): number {
  const headers = chat ? shaping.headers.filter((rule) => rule.name.trim()).length : 0;
  return headers + (shaping.outboundProxy ? 1 : 0);
}
