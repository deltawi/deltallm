export const ROUTE_GROUP_MODE_OPTIONS = ['chat', 'embedding', 'image_generation', 'audio_speech', 'audio_transcription', 'rerank'] as const;

export const ROUTE_GROUP_STRATEGY_OPTIONS = [
  'simple-shuffle',
  'least-busy',
  'latency-based-routing',
  'cost-based-routing',
  'usage-based-routing',
  'tag-based-routing',
  'priority-based-routing',
  'weighted',
  'rate-limit-aware',
] as const;

export type PolicyEditorMode = 'guided' | 'json';
export type PolicyAction = 'validate' | 'save-draft' | 'publish-json' | 'publish-draft' | 'rollback' | null;

export interface PolicyGuidedValues {
  strategy: string;
  mode: 'fallback' | 'weighted' | 'conditional' | 'adaptive';
  memberIds: string[];
  timeoutMs: string;
  retryMaxAttempts: string;
  retryableErrors: string;
}

export const GUIDED_POLICY_DEFAULTS: PolicyGuidedValues = {
  strategy: 'weighted',
  mode: 'weighted',
  memberIds: [],
  timeoutMs: '',
  retryMaxAttempts: '',
  retryableErrors: '',
};

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function toPositiveIntegerString(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '';
  return String(Math.trunc(value));
}

function parseInteger(value: string): number | null {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return parsed;
}

function memberReferenceFromEntry(entry: unknown): string | null {
  if (typeof entry === 'string' && entry.trim()) {
    return entry.trim();
  }
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
    if (typeof candidate === 'string' && candidate.trim()) {
      return candidate.trim();
    }
  }
  return null;
}

export function parsePolicyTextLoose(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw);
    return isObjectRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function toGuidedPolicy(policy: Record<string, unknown>, memberIds: string[]): PolicyGuidedValues {
  const strategy = typeof policy.strategy === 'string' ? policy.strategy : GUIDED_POLICY_DEFAULTS.strategy;
  const mode = policy.mode;
  const guidedMode: PolicyGuidedValues['mode'] =
    mode === 'fallback' || mode === 'weighted' || mode === 'conditional' || mode === 'adaptive' ? mode : GUIDED_POLICY_DEFAULTS.mode;

  const timeoutBlock = isObjectRecord(policy.timeouts) ? policy.timeouts : {};
  const retryBlock = isObjectRecord(policy.retry) ? policy.retry : {};
  const memberEntries = Array.isArray(policy.members) ? policy.members : [];
  const extractedMembers = memberEntries
    .map(memberReferenceFromEntry)
    .filter((value): value is string => !!value);
  const selectedMembers = extractedMembers.length > 0 ? extractedMembers : memberIds;

  const retryable = Array.isArray(retryBlock.retryable_error_classes)
    ? retryBlock.retryable_error_classes.filter((value): value is string => typeof value === 'string')
    : [];

  return {
    strategy,
    mode: guidedMode,
    memberIds: selectedMembers,
    timeoutMs: toPositiveIntegerString(timeoutBlock.global_ms),
    retryMaxAttempts: toPositiveIntegerString(retryBlock.max_attempts),
    retryableErrors: retryable.join(','),
  };
}

export function buildPolicyFromGuided(basePolicy: Record<string, unknown>, guided: PolicyGuidedValues): Record<string, unknown> {
  const policy: Record<string, unknown> = { ...basePolicy };
  policy.strategy = guided.strategy;
  policy.mode = guided.mode;
  if (guided.memberIds.length > 0) {
    policy.members = guided.memberIds.map((memberId) => ({ deployment_id: memberId }));
  }

  const timeoutValue = parseInteger(guided.timeoutMs);
  const currentTimeouts = isObjectRecord(policy.timeouts) ? { ...policy.timeouts } : {};
  if (timeoutValue) {
    currentTimeouts.global_ms = timeoutValue;
  } else {
    delete currentTimeouts.global_ms;
  }
  if (Object.keys(currentTimeouts).length > 0) {
    policy.timeouts = currentTimeouts;
  } else {
    delete policy.timeouts;
  }

  const retryValue = parseInteger(guided.retryMaxAttempts);
  const retryClasses = guided.retryableErrors
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  const currentRetry = isObjectRecord(policy.retry) ? { ...policy.retry } : {};
  if (retryValue) {
    currentRetry.max_attempts = retryValue;
  } else {
    delete currentRetry.max_attempts;
  }
  if (retryClasses.length > 0) {
    currentRetry.retryable_error_classes = retryClasses;
  } else {
    delete currentRetry.retryable_error_classes;
  }
  if (Object.keys(currentRetry).length > 0) {
    policy.retry = currentRetry;
  } else {
    delete policy.retry;
  }

  return policy;
}
