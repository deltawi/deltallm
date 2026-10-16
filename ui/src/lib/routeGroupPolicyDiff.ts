import { effectiveRouteGroupStrategy } from './routeGroups';
import { strategyLabel } from './routeGroupRouting';

export interface PolicyChange {
  label: string;
  before: string;
  after: string;
}

export interface DiffLine {
  kind: 'same' | 'added' | 'removed';
  text: string;
}

type Policy = Record<string, unknown>;

const KNOWN_KEYS = new Set(['strategy', 'mode', 'members', 'timeouts', 'retry', 'context', 'selector']);
export const RETRY_CLASS_LABELS: Record<string, string> = {
  timeout: 'Timeout',
  rate_limit: 'Rate limit',
  context_window_exceeded: 'Context too long',
  content_policy_violation: 'Content policy',
  generic: 'Other provider errors',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (!isRecord(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
}

export function stableJson(value: unknown): string {
  return JSON.stringify(stable(value), null, 2) ?? 'null';
}

interface MemberSetting { weight: number | null; priority: number | null; lane: string | null; enabled: boolean }

function memberSettings(policy: Policy | null): Map<string, MemberSetting> | null {
  if (!policy || !Array.isArray(policy.members)) return null;
  const result = new Map<string, MemberSetting>();
  for (const entry of policy.members) {
    if (!isRecord(entry) || typeof entry.deployment_id !== 'string') continue;
    result.set(entry.deployment_id, {
      weight: typeof entry.weight === 'number' ? entry.weight : null,
      priority: typeof entry.priority === 'number' ? entry.priority : null,
      lane: typeof entry.lane === 'string' ? entry.lane : null,
      enabled: entry.enabled !== false,
    });
  }
  return result;
}

function memberOrder(settings: Map<string, MemberSetting>): string[] {
  const ids = [...settings.keys()].filter((id) => settings.get(id)?.enabled);
  if (ids.every((id) => settings.get(id)?.priority != null)) {
    ids.sort((a, b) => (settings.get(a)?.priority ?? 0) - (settings.get(b)?.priority ?? 0));
  }
  return ids;
}

function timeoutMs(policy: Policy | null): number | null {
  const timeouts = policy?.timeouts;
  if (!isRecord(timeouts)) return null;
  if (typeof timeouts.global_ms === 'number') return timeouts.global_ms;
  if (typeof timeouts.global_seconds === 'number') return Math.round(timeouts.global_seconds * 1000);
  return null;
}

export function formatSeconds(ms: number | null): string {
  if (ms == null) return 'Not set';
  const seconds = ms / 1000;
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} s`;
}

function retryAttempts(policy: Policy | null): string {
  const retry = policy?.retry;
  const value = isRecord(retry) ? retry.max_attempts : undefined;
  if (typeof value !== 'number') return 'Gateway default';
  if (value === 0) return 'No retries';
  return value === 1 ? '1 retry' : `${value} retries`;
}

function retryClasses(policy: Policy | null): string {
  const retry = policy?.retry;
  const classes = isRecord(retry) && Array.isArray(retry.retryable_error_classes) ? retry.retryable_error_classes : null;
  if (!classes || classes.length === 0) return 'Gateway default';
  return classes.map((item) => RETRY_CLASS_LABELS[String(item)] ?? String(item)).join(', ');
}

function contextSummary(policy: Policy | null): string {
  const context = policy?.context;
  if (!isRecord(context)) return 'Off';
  return context.mode === 'smallest-sufficient' ? 'On, smallest model that fits' : 'On, models that fit';
}

function selectorSummary(policy: Policy | null, names: Record<string, string>): string {
  const selector = policy?.selector;
  if (!isRecord(selector)) return 'Off';
  const classifier = typeof selector.classifier_deployment_id === 'string' ? selector.classifier_deployment_id : '';
  return classifier ? `On, ${names[classifier] ?? classifier} decides` : 'On';
}

/** Human lines that say what changes from `before` to `after`. */
export function policyChanges(
  before: Policy | null,
  after: Policy,
  names: Record<string, string> = {},
  groupDefault: string | null = null,
): PolicyChange[] {
  const name = (id: string) => names[id] ?? id;
  const changes: PolicyChange[] = [];
  const push = (label: string, from: string, to: string) => { if (from !== to) changes.push({ label, before: from, after: to }); };

  const fromStrategy = effectiveRouteGroupStrategy(before, groupDefault);
  const toStrategy = effectiveRouteGroupStrategy(after, groupDefault);
  push('How to pick', strategyLabel(fromStrategy), strategyLabel(toStrategy));

  const fromMembers = memberSettings(before);
  const toMembers = memberSettings(after);
  if (!fromMembers && toMembers) {
    push('Models', 'All group models', memberOrder(toMembers).map(name).join(', ') || 'None');
  } else if (fromMembers && !toMembers) {
    push('Models', memberOrder(fromMembers).map(name).join(', ') || 'None', 'All group models');
  } else if (fromMembers && toMembers) {
    const ids = [...new Set([...fromMembers.keys(), ...toMembers.keys()])];
    for (const id of ids) {
      const from = fromMembers.get(id);
      const to = toMembers.get(id);
      const used = (setting?: MemberSetting) => (setting?.enabled ? 'Used' : 'Not used');
      if (used(from) !== used(to)) { push(name(id), used(from), used(to)); continue; }
      if (!from || !to) continue;
      if (from.weight !== to.weight) push(name(id), `weight ${from.weight ?? 'default'}`, `weight ${to.weight ?? 'default'}`);
      if (from.lane !== to.lane) push(name(id), `lane ${from.lane ?? 'none'}`, `lane ${to.lane ?? 'none'}`);
    }
    if (toStrategy === 'priority-based-routing') {
      push('Order', memberOrder(fromMembers).map(name).join(' → '), memberOrder(toMembers).map(name).join(' → '));
    }
  }

  push('Request timeout', formatSeconds(timeoutMs(before)), formatSeconds(timeoutMs(after)));
  push('Retries', retryAttempts(before), retryAttempts(after));
  push('Retry on', retryClasses(before), retryClasses(after));
  const context = [contextSummary(before), contextSummary(after)];
  if (context[0] === context[1] && stableJson(before?.context ?? null) !== stableJson(after.context ?? null)) {
    push('Context-aware routing', context[0], `${context[1]} (settings changed)`);
  } else push('Context-aware routing', context[0], context[1]);
  const selector = [selectorSummary(before, names), selectorSummary(after, names)];
  if (selector[0] === selector[1] && stableJson(before?.selector ?? null) !== stableJson(after.selector ?? null)) {
    push('Model selector', selector[0], `${selector[1]} (settings changed)`);
  } else push('Model selector', selector[0], selector[1]);

  const rest = (policy: Policy | null) => Object.fromEntries(Object.entries(policy ?? {}).filter(([key]) => !KNOWN_KEYS.has(key)));
  if (stableJson(rest(before)) !== stableJson(rest(after))) push('Other settings', 'Before', 'Changed (see JSON)');
  return changes;
}

/** Who published a version. The email is visible only to platform admins and to the publisher. */
export function publisherLabel(policy: { published_by: string | null; published_by_email?: string | null; status?: string }): string {
  if (policy.published_by_email) return policy.published_by_email;
  if (policy.published_by === 'admin_api') return 'the master key';
  if (policy.published_by) return 'another user';
  return policy.status === 'draft' ? 'not published' : 'unknown';
}

/** One line for the history table. */
export function policyChangeSummary(
  before: Policy | null,
  after: Policy,
  names: Record<string, string> = {},
  groupDefault: string | null = null,
): string {
  if (!before) {
    const strategy = strategyLabel(effectiveRouteGroupStrategy(after, groupDefault));
    return `First version: ${strategy}`;
  }
  const changes = policyChanges(before, after, names, groupDefault);
  if (changes.length === 0) return 'Same settings as the version before';
  const lines = changes.slice(0, 2).map((change) => `${change.label} ${change.before} → ${change.after}`);
  const more = changes.length - lines.length;
  return `${lines.join('; ')}${more > 0 ? `; ${more} more` : ''}`;
}

/** Line diff of two JSON documents (longest common subsequence). */
export function jsonLineDiff(before: unknown, after: unknown): DiffLine[] {
  const a = stableJson(before).split('\n');
  const b = stableJson(after).split('\n');
  const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { lines.push({ kind: 'same', text: a[i] }); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) { lines.push({ kind: 'removed', text: a[i] }); i += 1; }
    else { lines.push({ kind: 'added', text: b[j] }); j += 1; }
  }
  while (i < a.length) { lines.push({ kind: 'removed', text: a[i] }); i += 1; }
  while (j < b.length) { lines.push({ kind: 'added', text: b[j] }); j += 1; }
  return lines;
}
