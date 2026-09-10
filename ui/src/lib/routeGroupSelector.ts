import type { PolicyGuidedValues } from './routeGroups';
import type { RoutePolicySelectorLane, SelectorDeploymentOption } from './api/routeGroups';

export interface GuidedSelectorLane {
  id: string;
  rank: string;
  description: string;
}

export interface GuidedSelector {
  // Disabled leaves an omitted selector untouched; removed is an explicit tombstone.
  state: 'disabled' | 'removed' | 'enabled' | 'unsupported';
  classifier: string;
  lanes: GuidedSelectorLane[];
  assignments: Record<string, string>;
  defaultLane: string;
  timeoutMs: string;
  maxInputChars: string;
}

export function selectorDefaults(): GuidedSelector {
  return {
    state: 'disabled', classifier: '', defaultLane: 'quality',
    timeoutMs: '750', maxInputChars: '8000', assignments: {},
    lanes: [
      { id: 'economy', rank: '0', description: 'Routine extraction, formatting, classification and simple questions' },
      { id: 'quality', rank: '1', description: 'Complex reasoning, ambiguity, advanced tools and difficult requests' },
    ],
  };
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function selectorLane(value: unknown): value is RoutePolicySelectorLane {
  return record(value) && typeof value.id === 'string'
    && typeof value.description === 'string' && typeof value.rank === 'number';
}

export function readGuidedSelector(policy: Record<string, unknown>): GuidedSelector {
  const defaults = selectorDefaults();
  const raw = policy.selector;
  if (raw === null) return { ...defaults, state: 'removed' };
  if (raw == null) return defaults;
  if (!record(raw) || raw.kind !== 'llm-tier' || !Array.isArray(raw.lanes)
    || !raw.lanes.every(selectorLane)) {
    return { ...defaults, state: 'unsupported' };
  }
  const lanes = raw.lanes.map((lane) => ({
    id: String(lane.id), rank: String(lane.rank), description: String(lane.description),
  }));
  const highest = [...lanes].sort((a, b) => Number(b.rank) - Number(a.rank))[0];
  return {
    state: 'enabled', lanes,
    classifier: typeof raw.classifier_deployment_id === 'string' ? raw.classifier_deployment_id : '',
    timeoutMs: String(raw.timeout_ms ?? 750), maxInputChars: String(raw.max_input_chars ?? 8000),
    defaultLane: typeof raw.default_lane === 'string' ? raw.default_lane : highest?.id ?? '',
    assignments: Object.fromEntries((Array.isArray(policy.members) ? policy.members : [])
      .filter((entry): entry is Record<string, unknown> => record(entry)
        && typeof entry.deployment_id === 'string' && typeof entry.lane === 'string')
      .map((entry) => [String(entry.deployment_id), String(entry.lane)])),
  };
}

export function selectorPublishConfirmation(
  policy: Record<string, unknown>, activePolicy: Record<string, unknown> | null,
): { title: string; description: string } | null {
  // This describes write intent only; the server owns policy merging and validation.
  const intent = !Object.prototype.hasOwnProperty.call(policy, 'selector')
    ? 'preserve' : policy.selector === null ? 'remove' : 'replace';
  if (intent === 'preserve') {
    if (activePolicy?.selector == null) return null;
    return {
      title: 'Publish model routing?',
      description: 'Publishing leaves the selector unchanged. Classification continues for uncached requests, and customers continue to pay the added provider cost. Evaluation is optional.',
    };
  }
  if (intent === 'remove') {
    if (activePolicy?.selector == null) return null;
    return {
      title: 'Disable the model selector?',
      description: 'Publishing removes the selector from this group. New requests will use the routing strategy without classification.',
    };
  }
  const selector = readGuidedSelector(policy);
  return {
    title: 'Publish model routing?',
    description: selector.state === 'enabled'
      ? `Publishing makes this policy active immediately. Uncached request text will be sent to ${selector.classifier || 'the configured selector'}, and customers pay the added provider cost. Evaluation is optional; review any results you chose to run.`
      : 'This selector configuration cannot be interpreted by the guided editor. The server will validate it before publication. An active selector may process request text and add customer charges. Evaluation is optional.',
  };
}

export function chooseSelector(guided: PolicyGuidedValues, classifier: string): PolicyGuidedValues {
  const selector = guided.selector;
  if (!classifier) return { ...guided, selector: { ...selector, state: 'removed' } };
  return {
    ...guided, memberSelection: 'explicit',
    // Publication requires known capacity. Do not weaken an existing context policy.
    contextMode: guided.contextMode === 'disabled' ? 'eligible-only' : guided.contextMode,
    contextUnknownCapacity: 'exclude',
    selector: {
      ...selector, state: 'enabled', classifier,
      assignments: selector.assignments,
    },
  };
}

export function eligibleClassifiers(deployments: readonly SelectorDeploymentOption[]): SelectorDeploymentOption[] {
  return deployments.filter((deployment) => deployment.mode === 'chat');
}

function integerInRange(value: string, min: number, max: number): boolean {
  return /^\d+$/.test(value) && Number.isSafeInteger(Number(value))
    && Number(value) >= min && Number(value) <= max;
}

export function validateGuidedSelector(
  selector: GuidedSelector, ids: string[], workload: string,
): string | null {
  if (selector.state === 'disabled' || selector.state === 'removed') return null;
  if (selector.state === 'unsupported') return 'This selector cannot be edited here. Use Raw JSON to preserve its configuration.';
  if (workload !== 'chat') return 'Model selectors are available only for chat groups.';
  if (!selector.classifier.trim()) return 'Choose a chat deployment as the selector.';
  if (selector.lanes.length < 2 || selector.lanes.length > 8) return 'Configure between 2 and 8 lanes.';
  const laneIds = selector.lanes.map((lane) => lane.id.trim());
  if (laneIds.some((id) => !/^[a-z][a-z0-9_-]{0,31}$/.test(id))) return 'Lane IDs must start with a lowercase letter and use up to 32 lowercase letters, digits, hyphens or underscores.';
  if (new Set(laneIds).size !== laneIds.length) return 'Lane IDs must be unique.';
  if (selector.lanes.some((lane) => !integerInRange(lane.rank, 0, 7))
    || selector.lanes.map((lane) => Number(lane.rank)).sort((a, b) => a - b).some((rank, index) => rank !== index)) {
    return 'Lane ranks must be unique and consecutive, starting at 0 (economy first).';
  }
  if (selector.lanes.some((lane) => !lane.description.trim() || lane.description.trim().length > 512)) return 'Each lane needs a description of 1–512 characters.';
  if (!laneIds.includes(selector.defaultLane)) return 'Choose a configured safe-default lane.';
  if (!integerInRange(selector.timeoutMs, 100, 5000)) return 'Selector timeout must be 100–5000 ms.';
  if (!integerInRange(selector.maxInputChars, 256, 32768)) return 'Selector input limit must be 256–32768 characters.';
  if (ids.some((id) => !laneIds.includes(selector.assignments[id]))) return 'Assign every selected deployment to a configured lane.';
  if (laneIds.some((lane) => !ids.some((id) => selector.assignments[id] === lane))) return 'Each lane needs at least one selected deployment.';
  return null;
}

export function applyGuidedSelector(
  policy: Record<string, unknown>, base: Record<string, unknown>, selector: GuidedSelector,
): Record<string, unknown> {
  if (selector.state === 'unsupported') return policy;
  const next = { ...policy };
  if (selector.state === 'disabled' || selector.state === 'removed') {
    const remove = selector.state === 'removed' || 'selector' in base;
    if (remove) next.selector = null;
    if (remove && Array.isArray(next.members)) next.members = next.members.map((member: unknown) => {
      if (!record(member)) return member;
      const clean = { ...member };
      delete clean.lane;
      return clean;
    });
    return next;
  }
  next.selector = {
    ...(record(base.selector) ? base.selector : {}),
    kind: 'llm-tier', classifier_deployment_id: selector.classifier,
    timeout_ms: Number(selector.timeoutMs), max_input_chars: Number(selector.maxInputChars),
    default_lane: selector.defaultLane,
    lanes: selector.lanes.map((lane) => {
      const previous = record(base.selector) && Array.isArray(base.selector.lanes)
        ? base.selector.lanes.find((item: unknown) => record(item) && item.id === lane.id.trim())
        : null;
      return { ...(record(previous) ? previous : {}), id: lane.id.trim(), rank: Number(lane.rank), description: lane.description.trim() };
    }),
  };
  if (Array.isArray(next.members)) next.members = next.members.map((member: unknown) => record(member)
    ? { ...member, lane: selector.assignments[String(member.deployment_id)] ?? '' } : member);
  return next;
}
