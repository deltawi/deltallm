export const ROUTE_GROUP_MODE_OPTIONS = ['chat', 'embedding', 'image_generation', 'audio_speech', 'audio_transcription', 'rerank'] as const;

export const ROUTE_GROUP_STRATEGY_OPTIONS = [
  'simple-shuffle',
  'least-busy',
  'latency-based-routing',
  'cost-based-routing',
  'usage-based-routing',
  'priority-based-routing',
  'weighted',
  'rate-limit-aware',
] as const;

export const LEGACY_TAG_ROUTING_STRATEGY = 'tag-based-routing';

export function routeGroupStrategyOptions(currentStrategy: string): string[] {
  const options = [...ROUTE_GROUP_STRATEGY_OPTIONS];
  return currentStrategy === LEGACY_TAG_ROUTING_STRATEGY
    ? [LEGACY_TAG_ROUTING_STRATEGY, ...options]
    : options;
}

export const RETRYABLE_ERROR_OPTIONS = [
  'timeout',
  'rate_limit',
  'context_window_exceeded',
  'content_policy_violation',
  'generic',
] as const;

export type PolicyEditorMode = 'guided' | 'json';
export type PolicyAction = 'validate' | 'save-draft' | 'publish-json' | 'publish-draft' | 'rollback' | null;
export type GuidedMemberSelection = 'inherit' | 'explicit';

export interface PolicyMemberOption {
  deployment_id: string;
  enabled: boolean;
  weight: number | null;
  priority: number | null;
}

export interface PolicyGuidedValues {
  strategy: string;
  memberSelection: GuidedMemberSelection;
  memberIds: string[];
  memberWeights: Record<string, string>;
  timeoutMs: string;
  retryMaxAttempts: string;
  retryableErrors: string;
}

export const GUIDED_POLICY_DEFAULTS: PolicyGuidedValues = {
  strategy: 'weighted',
  memberSelection: 'inherit',
  memberIds: [],
  memberWeights: {},
  timeoutMs: '',
  retryMaxAttempts: '',
  retryableErrors: '',
};

export { mutationOutcome as routeGroupMutationOutcome } from './mutationOutcome';

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function toIntegerString(value: unknown, minimum: number): string {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < minimum) return '';
  return String(value);
}

function parseIntegerString(value: string, minimum: number): number | null {
  const normalized = value.trim();
  if (!/^\d+$/.test(normalized)) return null;
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed) || parsed < minimum) return null;
  return parsed;
}

function memberReferenceFromEntry(entry: unknown): string | null {
  if (typeof entry === 'string' && entry.trim()) return entry.trim();
  if (!isObjectRecord(entry)) return null;
  const candidates = [
    entry.member,
    entry.member_id,
    entry.memberId,
    entry.deployment_id,
    entry.deploymentId,
    entry.id,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
  }
  return null;
}

function memberWeight(entry: unknown): string {
  if (!isObjectRecord(entry)) return '';
  return toIntegerString(entry.weight, 1);
}

function timeoutMilliseconds(timeoutBlock: Record<string, unknown>): string {
  const milliseconds = toIntegerString(timeoutBlock.global_ms, 1);
  if (milliseconds) return milliseconds;
  if (
    typeof timeoutBlock.global_seconds !== 'number'
    || !Number.isFinite(timeoutBlock.global_seconds)
    || timeoutBlock.global_seconds <= 0
  ) return '';
  return String(Math.max(1, Math.round(timeoutBlock.global_seconds * 1000)));
}

export function parsePolicyTextLoose(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw);
    return isObjectRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function toGuidedPolicy(
  policy: Record<string, unknown>,
  memberOptions: PolicyMemberOption[],
): PolicyGuidedValues {
  const rawMode = policy.mode;
  const strategy = typeof policy.strategy === 'string'
    ? policy.strategy
    : rawMode === 'fallback'
      ? 'priority-based-routing'
      : GUIDED_POLICY_DEFAULTS.strategy;
  const timeoutBlock = isObjectRecord(policy.timeouts) ? policy.timeouts : {};
  const retryBlock = isObjectRecord(policy.retry) ? policy.retry : {};
  const hasExplicitMembers = Array.isArray(policy.members);
  const memberEntries = hasExplicitMembers ? policy.members as unknown[] : [];
  const entriesById = new Map<string, unknown>();
  for (const entry of memberEntries) {
    const deploymentId = memberReferenceFromEntry(entry);
    if (deploymentId) entriesById.set(deploymentId, entry);
  }
  const enabledOptionIds = memberOptions
    .filter((member) => member.enabled)
    .map((member) => member.deployment_id);
  const selectedMembers = hasExplicitMembers
    ? memberEntries.flatMap((entry) => {
        const deploymentId = memberReferenceFromEntry(entry);
        if (!deploymentId || (isObjectRecord(entry) && entry.enabled === false)) return [];
        return [deploymentId];
      })
    : enabledOptionIds;
  const memberWeights = Object.fromEntries(memberOptions.map((member) => {
    const policyWeight = memberWeight(entriesById.get(member.deployment_id));
    return [member.deployment_id, policyWeight];
  }));
  const retryable = Array.isArray(retryBlock.retryable_error_classes)
    ? retryBlock.retryable_error_classes.filter((value): value is string => typeof value === 'string')
    : [];

  return {
    strategy,
    memberSelection: hasExplicitMembers ? 'explicit' : 'inherit',
    memberIds: selectedMembers,
    memberWeights,
    timeoutMs: timeoutMilliseconds(timeoutBlock),
    retryMaxAttempts: toIntegerString(retryBlock.max_attempts, 0),
    retryableErrors: retryable.join(','),
  };
}

export function reconcileGuidedPolicyMembers(
  guided: PolicyGuidedValues,
  memberOptions: PolicyMemberOption[],
): PolicyGuidedValues {
  const optionsById = new Map(memberOptions.map((member) => [member.deployment_id, member]));
  const memberIds = guided.memberSelection === 'inherit'
    ? memberOptions.filter((member) => member.enabled).map((member) => member.deployment_id)
    : guided.memberIds.filter((deploymentId) => optionsById.get(deploymentId)?.enabled);
  const memberWeights = Object.fromEntries(memberOptions.map((member) => [
    member.deployment_id,
    guided.memberWeights[member.deployment_id] || '',
  ]));
  if (
    memberIds.length === guided.memberIds.length
    && memberIds.every((deploymentId, index) => deploymentId === guided.memberIds[index])
    && Object.keys(memberWeights).length === Object.keys(guided.memberWeights).length
    && Object.keys(memberWeights).every(
      (deploymentId) => memberWeights[deploymentId] === guided.memberWeights[deploymentId],
    )
  ) {
    return guided;
  }
  return { ...guided, memberIds, memberWeights };
}

export function withGuidedPolicyStrategy(
  guided: PolicyGuidedValues,
  strategy: string,
): PolicyGuidedValues {
  return { ...guided, strategy };
}

export function validateGuidedPolicy(
  guided: PolicyGuidedValues,
  memberOptions: PolicyMemberOption[],
): string | null {
  const enabledIds = new Set(
    memberOptions.filter((member) => member.enabled).map((member) => member.deployment_id),
  );
  if (guided.memberSelection === 'explicit') {
    if (guided.memberIds.length === 0) return 'Select at least one enabled deployment.';
    const unavailable = guided.memberIds.find((deploymentId) => !enabledIds.has(deploymentId));
    if (unavailable) return `Deployment ${unavailable} is disabled or no longer available.`;
  }
  for (const deploymentId of guided.memberIds) {
    const weight = guided.memberWeights[deploymentId]?.trim();
    if (weight && parseIntegerString(weight, 1) === null) {
      return `Weight for ${deploymentId} must be an integer greater than or equal to 1.`;
    }
  }
  if (guided.timeoutMs.trim() && parseIntegerString(guided.timeoutMs, 1) === null) {
    return 'Global timeout must be an integer greater than or equal to 1 ms.';
  }
  if (
    guided.retryMaxAttempts.trim()
    && parseIntegerString(guided.retryMaxAttempts, 0) === null
  ) {
    return 'Retry max attempts must be a non-negative integer.';
  }
  const retryClasses = guided.retryableErrors
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  const invalidRetryClass = retryClasses.find(
    (item) => !(RETRYABLE_ERROR_OPTIONS as readonly string[]).includes(item),
  );
  if (invalidRetryClass) return `Unsupported retryable error class: ${invalidRetryClass}.`;
  return null;
}

export function buildPolicyFromGuided(
  basePolicy: Record<string, unknown>,
  guided: PolicyGuidedValues,
): Record<string, unknown> {
  const policy: Record<string, unknown> = { ...basePolicy, strategy: guided.strategy };
  delete policy.mode;

  if (guided.memberSelection === 'explicit') {
    const baseMembers = Array.isArray(basePolicy.members) ? basePolicy.members : [];
    const baseById = new Map<string, Record<string, unknown>>();
    for (const entry of baseMembers) {
      const deploymentId = memberReferenceFromEntry(entry);
      if (deploymentId && isObjectRecord(entry)) baseById.set(deploymentId, entry);
    }
    policy.members = guided.memberIds.map((deploymentId, index) => {
      const current = baseById.get(deploymentId) || {};
      const member = Object.fromEntries(
        Object.entries(current).filter(
          ([key]) => !['deployment_id', 'enabled', 'weight', 'priority'].includes(key),
        ),
      );
      member.deployment_id = deploymentId;
      member.enabled = true;
      const weight = parseIntegerString(guided.memberWeights[deploymentId] || '', 1);
      if (weight !== null) member.weight = weight;
      if (guided.strategy === 'priority-based-routing') member.priority = index;
      return member;
    });
  } else {
    delete policy.members;
  }

  const timeoutValue = parseIntegerString(guided.timeoutMs, 1);
  const currentTimeouts = isObjectRecord(policy.timeouts) ? { ...policy.timeouts } : {};
  delete currentTimeouts.global_seconds;
  if (timeoutValue !== null) currentTimeouts.global_ms = timeoutValue;
  else delete currentTimeouts.global_ms;
  if (Object.keys(currentTimeouts).length > 0) policy.timeouts = currentTimeouts;
  else delete policy.timeouts;

  const retryValue = parseIntegerString(guided.retryMaxAttempts, 0);
  const retryClasses = guided.retryableErrors
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  const currentRetry = isObjectRecord(policy.retry) ? { ...policy.retry } : {};
  if (retryValue !== null) currentRetry.max_attempts = retryValue;
  else delete currentRetry.max_attempts;
  if (retryClasses.length > 0) currentRetry.retryable_error_classes = retryClasses;
  else delete currentRetry.retryable_error_classes;
  if (Object.keys(currentRetry).length > 0) policy.retry = currentRetry;
  else delete policy.retry;

  return policy;
}
