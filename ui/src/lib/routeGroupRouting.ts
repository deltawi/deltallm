import { effectiveRouteGroupStrategy, orderedGuidedMemberIds, toGuidedPolicy } from './routeGroups';
import type { RouteGroupMemberDetail } from './api/routeGroups';
import { qualitySettingLabel, readQualitySetting, sameQualitySetting, type QualitySetting } from './routeGroupQuality';

/*
 * Simple routing maps three plain choices onto policy documents:
 *   even   -> simple-shuffle, every enabled group member (no member list)
 *   backup -> priority-based-routing, explicit member list, priority = position
 *   split  -> weighted, explicit member list, weight = percentage (0% is left out)
 * plus the optional "send low-quality models last" setting. Anything else (other
 * strategies, selector, context, timeouts, custom retries, per-member extras) is
 * only editable in advanced routing.
 */

export type SimpleChoice = 'even' | 'backup' | 'split';
export type SimpleRetry = 'on' | 'off' | 'default';

export interface SimpleRouting {
  choice: SimpleChoice;
  /** Backup order. Contains only the members that the policy routes to. */
  order: string[];
  /** Custom split percentage per member, as typed. */
  percents: Record<string, string>;
  retry: SimpleRetry;
  /** Models the live policy lists but the group has paused. A save keeps them, so they route as before once they are back. */
  paused?: PausedMember[];
  /** Null when the policy has no quality setting; a save then leaves it out. */
  quality?: QualitySetting | null;
}

export interface PausedMember {
  id: string;
  /** Place in the full backup order, paused models included. */
  index: number;
  /** Share in percent of the full split, or null when the policy gives none. */
  weight: number | null;
}

export interface SimpleRoutingRead {
  routing: SimpleRouting | null;
  /** Why the policy needs advanced routing. Empty when `routing` is set. */
  reasons: string[];
}

export type RoutingMember = Pick<
  RouteGroupMemberDetail,
  'deployment_id' | 'enabled' | 'weight' | 'priority' | 'model_name' | 'provider' | 'healthy'
>;

export type TextSegment = string | { strong: string };

export const SIMPLE_CHOICES: ReadonlyArray<{ id: SimpleChoice; label: string; hint: string }> = [
  { id: 'even', label: 'Spread evenly', hint: 'Equal share for each model.' },
  { id: 'backup', label: 'Primary & backup', hint: 'Falls back in order.' },
  { id: 'split', label: 'Custom split', hint: 'Set a percentage per model.' },
];

const CHOICE_STRATEGY: Record<SimpleChoice, string> = {
  even: 'simple-shuffle',
  backup: 'priority-based-routing',
  split: 'weighted',
};

export const SIMPLE_RETRY_CLASSES = ['timeout', 'rate_limit'] as const;
export const SIMPLE_RETRY_ATTEMPTS = 2;

const STRATEGY_LABELS: Record<string, string> = {
  'simple-shuffle': 'Spread evenly',
  'priority-based-routing': 'Primary & backup',
  weighted: 'Custom split',
  'least-busy': 'Least busy',
  'latency-based-routing': 'Lowest latency',
  'cost-based-routing': 'Lowest cost',
  'usage-based-routing': 'Usage based',
  'rate-limit-aware': 'Rate limit aware',
  'tag-based-routing': 'Tag based (legacy)',
};

const SIMPLE_POLICY_KEYS = new Set(['strategy', 'mode', 'members', 'retry', 'timeouts', 'context', 'selector', 'quality']);
const SIMPLE_MEMBER_KEYS = new Set(['deployment_id', 'enabled', 'weight', 'priority']);
const EXTRA_MEMBER_SETTINGS = 'It has per-model settings that only advanced routing shows.';

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

export function strategyLabel(strategy: string | null | undefined): string {
  if (!strategy) return STRATEGY_LABELS['simple-shuffle'];
  return STRATEGY_LABELS[strategy] ?? strategy;
}

export function routingLabel(
  policy: Record<string, unknown> | null,
  groupDefault: string | null | undefined,
  routableCount?: number,
): string {
  if (policy && isRecord(policy.selector)) return 'Model selector';
  if (routableCount === 1) return 'Single model';
  return strategyLabel(effectiveRouteGroupStrategy(policy, groupDefault ?? null));
}

export function routableMemberIds(members: RoutingMember[]): string[] {
  return members.filter((member) => member.enabled).map((member) => member.deployment_id);
}

export function memberLabels(members: RoutingMember[]): Record<string, string> {
  const names = members.map((member) => member.model_name || member.deployment_id);
  return Object.fromEntries(members.map((member, index) => {
    const name = names[index];
    const duplicate = names.some((other, otherIndex) => otherIndex !== index && other === name);
    return [member.deployment_id, duplicate && name !== member.deployment_id ? `${name} (${member.deployment_id})` : name];
  }));
}

/** Integer percentages that add up to 100 (largest remainder), or all 0 when nothing has weight. */
export function percentagesFromWeights(weights: Record<string, number>): Record<string, number> {
  const ids = Object.keys(weights);
  const total = ids.reduce((sum, id) => sum + Math.max(0, weights[id]), 0);
  if (total <= 0) return Object.fromEntries(ids.map((id) => [id, 0]));
  const exact = ids.map((id) => (Math.max(0, weights[id]) / total) * 100);
  const result = exact.map(Math.floor);
  let remaining = 100 - result.reduce((sum, value) => sum + value, 0);
  const byRemainder = exact
    .map((value, index) => ({ index, remainder: value - Math.floor(value) }))
    .sort((a, b) => b.remainder - a.remainder || a.index - b.index);
  for (const { index } of byRemainder) {
    if (remaining <= 0) break;
    result[index] += 1;
    remaining -= 1;
  }
  return Object.fromEntries(ids.map((id, index) => [id, result[index]]));
}

export function splitEvenly(ids: string[]): Record<string, string> {
  const shares = percentagesFromWeights(Object.fromEntries(ids.map((id) => [id, 1])));
  return Object.fromEntries(ids.map((id) => [id, String(shares[id])]));
}

export function readSimpleRetry(value: unknown): SimpleRetry | null {
  if (value == null) return 'default';
  if (!isRecord(value)) return null;
  const keys = Object.keys(value);
  if (keys.length === 0) return 'default';
  if (keys.some((key) => key !== 'max_attempts' && key !== 'retryable_error_classes')) return null;
  if (value.max_attempts === 0) return 'off';
  const classes = Array.isArray(value.retryable_error_classes) ? value.retryable_error_classes : null;
  if (
    value.max_attempts === SIMPLE_RETRY_ATTEMPTS
    && classes
    && classes.length === SIMPLE_RETRY_CLASSES.length
    && SIMPLE_RETRY_CLASSES.every((item) => classes.includes(item))
  ) return 'on';
  return null;
}

function percentOf(routing: SimpleRouting, id: string): number {
  const value = Number((routing.percents[id] ?? '').trim() || '0');
  return Number.isFinite(value) ? value : 0;
}

function listedOrder(doc: Record<string, unknown>, id: string): number {
  const entries = Array.isArray(doc.members) ? doc.members : [];
  return entries.findIndex((entry) => isRecord(entry) && entry.deployment_id === id);
}

/** Puts paused models back at their place in a backup order. */
function withPausedBackups(order: string[], paused: PausedMember[]): string[] {
  const result = [...order];
  for (const item of [...paused].sort((a, b) => a.index - b.index)) result.splice(Math.min(item.index, result.length), 0, item.id);
  return result;
}

/** Reads a published policy as one of the three simple choices, or says why it cannot. */
export function readSimpleRouting(
  policy: Record<string, unknown> | null,
  groupDefault: string | null | undefined,
  members: RoutingMember[],
): SimpleRoutingRead {
  const doc = policy ?? {};
  const reasons: string[] = [];
  const add = (reason: string) => { if (!reasons.includes(reason)) reasons.push(reason); };
  const routable = routableMemberIds(members);
  const memberById = new Map(members.map((member) => [member.deployment_id, member]));

  if (Object.keys(doc).some((key) => !SIMPLE_POLICY_KEYS.has(key))) add('It has settings that only the JSON view shows.');
  if (doc.selector != null) add('It uses a model selector.');
  if (doc.context != null) add('It uses context-aware routing.');
  if (isRecord(doc.timeouts) && Object.keys(doc.timeouts).length > 0) add('It sets a request timeout.');
  const retry = readSimpleRetry(doc.retry);
  if (retry === null) add('It uses custom retry settings.');
  const quality = readQualitySetting(doc.quality);
  if (quality === undefined) add('It has quality settings that only the JSON view shows.');
  const strategy = effectiveRouteGroupStrategy(policy, groupDefault ?? null) ?? 'simple-shuffle';
  const choice = (Object.keys(CHOICE_STRATEGY) as SimpleChoice[]).find((key) => CHOICE_STRATEGY[key] === strategy);
  if (!choice) add(`It uses the ${strategyLabel(strategy)} strategy.`);

  let listed: string[] | null = null;
  const pausedIds: string[] = [];
  const weights = new Map<string, number | null>();
  const priorities = new Map<string, number | null>();
  if (Array.isArray(doc.members)) {
    listed = [];
    for (const entry of doc.members) {
      if (!isRecord(entry) || typeof entry.deployment_id !== 'string') { add(EXTRA_MEMBER_SETTINGS); continue; }
      if (Object.keys(entry).some((key) => !SIMPLE_MEMBER_KEYS.has(key)) || entry.enabled === false) {
        add(EXTRA_MEMBER_SETTINGS);
        continue;
      }
      const id = entry.deployment_id;
      if (listed.includes(id) || pausedIds.includes(id)) continue;
      const paused = memberById.get(id)?.enabled === false;
      if (!paused && !routable.includes(id)) continue;
      (paused ? pausedIds : listed).push(id);
      weights.set(id, typeof entry.weight === 'number' ? entry.weight : null);
      priorities.set(id, typeof entry.priority === 'number' ? entry.priority : null);
    }
  }
  if (reasons.length > 0 || !choice || retry === null || quality === undefined) return { routing: null, reasons };

  const base: SimpleRouting = { choice, order: [], percents: {}, retry, quality };
  if (choice === 'even') {
    if (listed && routable.some((id) => !listed?.includes(id))) {
      return { routing: null, reasons: ['It uses only some of the group’s models.'] };
    }
    return { routing: { ...base, order: [...routable], percents: splitEvenly(routable) }, reasons: [] };
  }
  if (choice === 'backup') {
    // Paused models keep their place in the list (in policy order when ranks are missing).
    const ids = listed ? [...listed, ...pausedIds].sort((a, b) => listedOrder(doc, a) - listedOrder(doc, b)) : [...routable];
    const rank = (id: string) => (listed ? priorities.get(id) : memberById.get(id)?.priority) ?? null;
    const full = ids.every((id) => rank(id) != null)
      ? [...ids].sort((a, b) => (rank(a) ?? 0) - (rank(b) ?? 0))
      : ids;
    const order = full.filter((id) => !pausedIds.includes(id));
    const paused = full.flatMap((id, index) => (pausedIds.includes(id) ? [{ id, index, weight: null }] : []));
    return { routing: { ...base, order, percents: splitEvenly(order), paused }, reasons: [] };
  }
  const included = listed ?? routable;
  const weightOf = (id: string) => weights.get(id) ?? memberById.get(id)?.weight ?? 1;
  const shares = percentagesFromWeights(Object.fromEntries(included.map((id) => [id, weightOf(id)])));
  const percents = Object.fromEntries(routable.map((id) => [id, String(shares[id] ?? 0)]));
  // A paused model keeps its share of the whole split, so it gets about the same traffic when it is back.
  const fullShares = percentagesFromWeights(Object.fromEntries([...included, ...pausedIds].map((id) => [id, weightOf(id)])));
  const paused = pausedIds.map((id) => ({ id, index: listedOrder(doc, id), weight: Math.max(1, fullShares[id] ?? 0) }));
  return { routing: { ...base, order: [...included], percents, paused }, reasons: [] };
}

/** Gives members that joined after the split was saved a 0% row, so they show without changing traffic. */
export function withSplitMembers(routing: SimpleRouting, members: RoutingMember[]): SimpleRouting {
  if (routing.choice !== 'split') return routing;
  const missing = routableMemberIds(members).filter((id) => !(id in routing.percents));
  if (missing.length === 0) return routing;
  return { ...routing, percents: { ...routing.percents, ...Object.fromEntries(missing.map((id) => [id, '0'])) } };
}

/** Adds a model to the end of the backup order. */
export function addBackup(routing: SimpleRouting, id: string): SimpleRouting {
  return routing.order.includes(id) ? routing : { ...routing, order: [...routing.order, id] };
}

/** Takes a model out of the backup order. The primary model cannot be the only one removed. */
export function removeBackup(routing: SimpleRouting, id: string): SimpleRouting {
  if (routing.order.length <= 1 || !routing.order.includes(id)) return routing;
  return { ...routing, order: routing.order.filter((item) => item !== id) };
}

/** Moves the editor to another choice and fills sensible values for it. */
export function chooseSimpleRouting(routing: SimpleRouting, choice: SimpleChoice, members: RoutingMember[]): SimpleRouting {
  const routable = routableMemberIds(members);
  if (choice === routing.choice) return routing;
  if (choice === 'backup') {
    const order = routing.order.filter((id) => routable.includes(id));
    return { ...routing, choice, order: [...order, ...routable.filter((id) => !order.includes(id))] };
  }
  if (choice === 'split') {
    const hasSplit = routable.some((id) => percentOf(routing, id) > 0)
      && routable.reduce((sum, id) => sum + percentOf(routing, id), 0) === 100;
    return { ...routing, choice, percents: hasSplit ? routing.percents : splitEvenly(routable) };
  }
  return { ...routing, choice };
}

export function moveBackup(routing: SimpleRouting, id: string, delta: -1 | 1): SimpleRouting {
  const order = [...routing.order];
  const index = order.indexOf(id);
  const next = index + delta;
  if (index < 0 || next < 0 || next >= order.length) return routing;
  [order[index], order[next]] = [order[next], order[index]];
  return { ...routing, order };
}

export function validateSimpleRouting(routing: SimpleRouting, members: RoutingMember[]): string | null {
  const routable = routableMemberIds(members);
  if (routable.length === 0) return 'Add a model to this group first.';
  if (routing.choice === 'backup' && routing.order.length === 0) return 'Choose a primary model.';
  if (routing.choice === 'split') {
    for (const id of routable) {
      const text = (routing.percents[id] ?? '').trim();
      if (text && (!/^\d+$/.test(text) || Number(text) > 100)) return 'Each share must be a whole number from 0 to 100.';
    }
    const total = routable.reduce((sum, id) => sum + percentOf(routing, id), 0);
    if (total !== 100) return `Shares must add up to 100%. They add up to ${total}%.`;
  }
  return null;
}

export function splitTotal(routing: SimpleRouting, members: RoutingMember[]): number {
  return routableMemberIds(members).reduce((sum, id) => sum + percentOf(routing, id), 0);
}

/** The policy document for a simple choice. Call only after validateSimpleRouting passes. */
export function buildSimpleRoutingPolicy(routing: SimpleRouting, members: RoutingMember[]): Record<string, unknown> {
  const routable = routableMemberIds(members);
  const policy: Record<string, unknown> = { strategy: CHOICE_STRATEGY[routing.choice] };
  const paused = (routing.paused ?? []).filter((item) => !routable.includes(item.id) && members.some((member) => member.deployment_id === item.id));
  if (routing.choice === 'backup') {
    policy.members = withPausedBackups(routing.order.filter((id) => routable.includes(id)), paused)
      .map((id, priority) => ({ deployment_id: id, enabled: true, priority }));
  } else if (routing.choice === 'split') {
    policy.members = [
      ...routable
        .filter((id) => percentOf(routing, id) > 0)
        .map((id) => ({ deployment_id: id, enabled: true, weight: percentOf(routing, id) })),
      ...paused.flatMap((item) => (item.weight ? [{ deployment_id: item.id, enabled: true, weight: item.weight }] : [])),
    ];
  }
  if (routing.retry === 'on') {
    policy.retry = { max_attempts: SIMPLE_RETRY_ATTEMPTS, retryable_error_classes: [...SIMPLE_RETRY_CLASSES] };
  } else if (routing.retry === 'off') {
    policy.retry = { max_attempts: 0 };
  }
  if (routing.quality) policy.quality = { ...routing.quality };
  return policy;
}

/** The policy a new group publishes so that it routes well with no setup. */
export function defaultSimplePolicy(): Record<string, unknown> {
  return buildSimpleRoutingPolicy({ choice: 'even', order: [], percents: {}, retry: 'on' }, []);
}

function joinNames(names: string[]): TextSegment[] {
  const segments: TextSegment[] = [];
  names.forEach((name, index) => {
    if (index > 0) segments.push(index === names.length - 1 ? ', then ' : ', ');
    segments.push({ strong: name });
  });
  return segments;
}

/** One plain sentence (with bold names) that says what the routing does. */
export function describeSimpleRouting(routing: SimpleRouting, members: RoutingMember[]): TextSegment[] {
  const labels = memberLabels(members);
  const routable = routableMemberIds(members);
  if (routable.length === 0) return ['This group has no models yet.'];
  if (routable.length === 1) return ['Every request goes to ', { strong: labels[routable[0]] }, '.'];
  const retryTail: Record<SimpleRetry, string> = {
    on: ' If a call times out or is rate limited, it is retried on another model.',
    off: ' Failed calls are not retried.',
    default: ' Retries follow the gateway default.',
  };
  if (routing.choice === 'even') {
    const share = Math.round(100 / routable.length);
    return [
      'Each request goes to one of your ', { strong: `${routable.length} models` },
      ', picked at random, so each gets about ', { strong: `${share}%` }, ' of traffic.', retryTail[routing.retry],
    ];
  }
  if (routing.choice === 'backup') {
    const [primary, ...backups] = routing.order.map((id) => labels[id] ?? id);
    if (!primary) return ['Choose a primary model.'];
    if (backups.length === 0) return ['Every request goes to ', { strong: primary }, '.', retryTail[routing.retry]];
    return routing.retry === 'on'
      ? ['Every request goes to ', { strong: primary }, '. If it fails, it moves to ', ...joinNames(backups), '. Timeouts and rate limits count as failures.']
      : ['Every request goes to ', { strong: primary }, '. While it is unhealthy, requests move to ', ...joinNames(backups), '.', retryTail[routing.retry]];
  }
  const parts = routable.filter((id) => percentOf(routing, id) > 0);
  const segments: TextSegment[] = ['Traffic is shared by your percentages: '];
  parts.forEach((id, index) => {
    if (index > 0) segments.push(', ');
    segments.push(`${labels[id]} `, { strong: `${percentOf(routing, id)}%` });
  });
  segments.push('.', retryTail[routing.retry]);
  return segments;
}

const CHOICE_LABEL: Record<SimpleChoice, string> = { even: 'Spread evenly', backup: 'Primary & backup', split: 'Custom split' };
const RETRY_LABEL: Record<SimpleRetry, string> = { on: 'Retries on', off: 'Retries off', default: 'Retries: gateway default' };

/** Short lines that say what Save will change. */
export function simpleRoutingChanges(
  baseline: SimpleRouting,
  current: SimpleRouting,
  members: RoutingMember[],
): string[] {
  const labels = memberLabels(members);
  const name = (id: string) => labels[id] ?? id;
  const routable = routableMemberIds(members);
  const changes: string[] = [];
  if (baseline.choice !== current.choice) {
    changes.push(`${CHOICE_LABEL[baseline.choice]} → ${CHOICE_LABEL[current.choice]}`);
    if (current.choice === 'backup' && current.order[0]) changes.push(`Primary: ${name(current.order[0])}`);
    if (current.choice === 'split') {
      changes.push(routable.filter((id) => percentOf(current, id) > 0).map((id) => `${name(id)} ${percentOf(current, id)}%`).join(', '));
    }
  } else if (current.choice === 'backup') {
    const added = current.order.filter((id) => !baseline.order.includes(id));
    added.forEach((id) => changes.push(`Add ${name(id)} as backup ${current.order.indexOf(id)}`));
    baseline.order.filter((id) => !current.order.includes(id)).forEach((id) => changes.push(`Stop using ${name(id)}`));
    const kept = current.order.filter((id) => baseline.order.includes(id));
    const before = baseline.order.filter((id) => current.order.includes(id));
    if (kept.join('\n') !== before.join('\n')) {
      changes.push(kept[0] !== before[0] ? `Primary: ${name(kept[0])}` : `Backup order: ${kept.slice(1).map(name).join(', ')}`);
    }
  } else if (current.choice === 'split') {
    for (const id of routable) {
      const before = percentOf(baseline, id);
      const after = percentOf(current, id);
      if (before !== after) changes.push(`${name(id)} ${before}% → ${after}%`);
    }
  }
  if (baseline.retry !== current.retry) changes.push(RETRY_LABEL[current.retry]);
  if (!sameQualitySetting(baseline.quality, current.quality)) {
    changes.push(`Send low-quality models last: ${qualitySettingLabel(current.quality)}`);
  }
  return changes;
}

export type TrafficShare =
  | { kind: 'percent'; value: number }
  | { kind: 'rank'; rank: number }
  | { kind: 'off'; reason: 'paused' | 'unhealthy' | 'unused' }
  | { kind: 'varies' };

/** Expected traffic per member right now, for the read-only Models table. */
export function memberTrafficShares(
  policy: Record<string, unknown> | null,
  groupDefault: string | null | undefined,
  members: RoutingMember[],
): Record<string, TrafficShare> {
  const shares: Record<string, TrafficShare> = {};
  const { routing } = readSimpleRouting(policy, groupDefault, members);
  const listed = Array.isArray(policy?.members)
    ? new Set((policy?.members as unknown[]).flatMap((entry) => (
      isRecord(entry) && typeof entry.deployment_id === 'string' && entry.enabled !== false ? [entry.deployment_id] : []
    )))
    : null;
  const routable = routableMemberIds(members);
  const healthy = (id: string) => members.find((member) => member.deployment_id === id)?.healthy !== false;
  for (const member of members) {
    if (!member.enabled) shares[member.deployment_id] = { kind: 'off', reason: 'paused' };
    else if (listed && !listed.has(member.deployment_id)) shares[member.deployment_id] = { kind: 'off', reason: 'unused' };
    else if (!routing) shares[member.deployment_id] = { kind: 'varies' };
  }
  if (!routing) return shares;
  if (routing.choice === 'backup') {
    for (const id of routable) shares[id] = { kind: 'off', reason: 'unused' };
    routing.order.forEach((id, rank) => { shares[id] = { kind: 'rank', rank }; });
    return shares;
  }
  const weights = Object.fromEntries(routable
    .filter((id) => shares[id] === undefined)
    .map((id) => [id, routing.choice === 'even' ? 1 : percentOf(routing, id)]));
  const live = Object.fromEntries(Object.entries(weights).filter(([id, weight]) => weight > 0 && healthy(id)));
  const percents = percentagesFromWeights(live);
  for (const id of Object.keys(weights)) {
    if (weights[id] <= 0) shares[id] = { kind: 'off', reason: 'unused' };
    else if (!healthy(id)) shares[id] = { kind: 'off', reason: 'unhealthy' };
    else shares[id] = { kind: 'percent', value: percents[id] ?? 0 };
  }
  return shares;
}

/**
 * Planned traffic share (0-100) per deployment for any policy, or null when the
 * strategy or a model selector decides per request. Unset weights count as 1,
 * like the router default.
 */
export function plannedTrafficShares(
  policy: Record<string, unknown> | null,
  groupDefault: string | null | undefined,
  members: RoutingMember[],
): Record<string, number> | null {
  const doc = policy ?? {};
  if (isRecord(doc.selector)) return null;
  const strategy = effectiveRouteGroupStrategy(policy, groupDefault ?? null) ?? 'simple-shuffle';
  const guided = toGuidedPolicy(doc, members);
  const ids = orderedGuidedMemberIds({ ...guided, strategy }, members);
  if (strategy === 'priority-based-routing') return Object.fromEntries(ids.map((id, index) => [id, index === 0 ? 100 : 0]));
  if (strategy === 'simple-shuffle') return percentagesFromWeights(Object.fromEntries(ids.map((id) => [id, 1])));
  if (strategy !== 'weighted') return null;
  const weight = (id: string) => {
    const typed = Number(guided.memberWeights[id]);
    if (Number.isInteger(typed) && typed >= 1) return typed;
    return members.find((member) => member.deployment_id === id)?.weight ?? 1;
  };
  return percentagesFromWeights(Object.fromEntries(ids.map((id) => [id, weight(id)])));
}
