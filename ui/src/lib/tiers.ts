import type {
  Tier,
  TierCapacityPool,
  TierCapacityPoolPayload,
  TierModelPolicy,
  TierModelPolicyPayload,
  TierPolicySimulation,
  TierRateLimitDescriptor,
  TierVersion,
} from './api';

export type TierFormValues = {
  tier_key: string;
  name: string;
  description: string;
  enabled: boolean;
};

export type TierModelPolicyForm = {
  callable_key: string;
  enabled: boolean;
  access_mode: string;
  rpm_limit: string;
  tpm_limit: string;
  rph_limit: string;
  rpd_limit: string;
  tpd_limit: string;
  max_parallel_requests: string;
  batch_rpm_limit: string;
  batch_tpm_limit: string;
  input_cost_per_token: string;
  output_cost_per_token: string;
  cached_input_cost_per_token: string;
  batch_input_cost_per_token: string;
  batch_output_cost_per_token: string;
  capacity_pool_key: string;
  priority: string;
};

export type TierCapacityPoolForm = {
  pool_key: string;
  callable_key: string;
  rpm_capacity: string;
  tpm_capacity: string;
  max_parallel_requests: string;
  strategy: string;
  saturation_threshold: string;
  burst_multiplier: string;
};

export type TierCapacityPoolOption = Pick<TierCapacityPool, 'pool_key' | 'callable_key'>;

const PRICING_FIELDS = [
  ['input_cost_per_token', 'input_cost_per_token', 'Input price'],
  ['output_cost_per_token', 'output_cost_per_token', 'Output price'],
  ['cached_input_cost_per_token', 'input_cost_per_token_cache_hit', 'Cached input price'],
  ['batch_input_cost_per_token', 'batch_input_cost_per_token', 'Batch input price'],
  ['batch_output_cost_per_token', 'batch_output_cost_per_token', 'Batch output price'],
] as const;

const EDITABLE_PRICING_PAYLOAD_FIELDS = new Set<string>(
  PRICING_FIELDS.map(([, payloadField]) => payloadField),
);

const DECIMAL_PATTERN = /^(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

export function tierToForm(tier?: Tier | null): TierFormValues {
  return {
    tier_key: tier?.tier_key || '',
    name: tier?.name || '',
    description: tier?.description || '',
    enabled: tier?.enabled ?? true,
  };
}

export function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  if (error && typeof error === 'object' && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message.trim()) return message;
  }
  return fallback;
}

export function formatLimit(value?: number | null): string {
  return value == null ? 'Unlimited' : Number(value).toLocaleString();
}

export function formatDateTime(value?: string | null): string {
  if (!value) return 'Not set';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not set';
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function versionLabel(version?: TierVersion | null): string {
  if (!version) return 'No version';
  return `v${version.version_number} ${version.status}`;
}

export function tierAssignmentRequiresActiveVersion(
  assignment: { enabled: boolean; ends_at?: string | null },
  now: Date = new Date(),
): boolean {
  if (!assignment.enabled) return false;
  const normalizedEndsAt = (assignment.ends_at || '').trim();
  if (!normalizedEndsAt) return true;
  const endsAt = new Date(normalizedEndsAt);
  if (Number.isNaN(endsAt.getTime())) return true;
  return endsAt.getTime() > now.getTime();
}

export function isAssignableTierVersion(
  version: Pick<TierVersion, 'status'>,
  requireActiveVersion: boolean,
): boolean {
  return !requireActiveVersion || String(version.status || '').trim().toLowerCase() === 'active';
}

export function pickEditableVersion(versions: TierVersion[]): TierVersion | null {
  return versions.find((version) => version.status === 'draft')
    || versions.find((version) => version.status === 'active')
    || versions[0]
    || null;
}

export function emptyModelPolicyForm(): TierModelPolicyForm {
  return {
    callable_key: '',
    enabled: true,
    access_mode: 'allow',
    rpm_limit: '',
    tpm_limit: '',
    rph_limit: '',
    rpd_limit: '',
    tpd_limit: '',
    max_parallel_requests: '',
    batch_rpm_limit: '',
    batch_tpm_limit: '',
    input_cost_per_token: '',
    output_cost_per_token: '',
    cached_input_cost_per_token: '',
    batch_input_cost_per_token: '',
    batch_output_cost_per_token: '',
    capacity_pool_key: '',
    priority: '0',
  };
}

export function modelPolicyToForm(policy?: TierModelPolicy | null): TierModelPolicyForm {
  const pricing = policy?.pricing || {};
  return {
    callable_key: policy?.callable_key || '',
    enabled: policy?.enabled ?? true,
    access_mode: policy?.access_mode || 'allow',
    rpm_limit: numberToInput(policy?.rpm_limit),
    tpm_limit: numberToInput(policy?.tpm_limit),
    rph_limit: numberToInput(policy?.rph_limit),
    rpd_limit: numberToInput(policy?.rpd_limit),
    tpd_limit: numberToInput(policy?.tpd_limit),
    max_parallel_requests: numberToInput(policy?.max_parallel_requests),
    batch_rpm_limit: numberToInput(policy?.batch_rpm_limit),
    batch_tpm_limit: numberToInput(policy?.batch_tpm_limit),
    input_cost_per_token: numberToInput(pricing.input_cost_per_token),
    output_cost_per_token: numberToInput(pricing.output_cost_per_token),
    cached_input_cost_per_token: numberToInput(pricing.input_cost_per_token_cache_hit),
    batch_input_cost_per_token: numberToInput(pricing.batch_input_cost_per_token),
    batch_output_cost_per_token: numberToInput(pricing.batch_output_cost_per_token),
    capacity_pool_key: policy?.capacity_pool_key || '',
    priority: numberToInput(policy?.priority ?? 0) || '0',
  };
}

export function modelPolicyFormToPayload(
  form: TierModelPolicyForm,
  existing?: Pick<TierModelPolicy, 'metadata' | 'pricing'> | null,
): TierModelPolicyPayload {
  return withOptionalMetadata({
    callable_key: form.callable_key.trim(),
    enabled: form.enabled,
    access_mode: form.access_mode,
    rpm_limit: parseOptionalPositiveInt(form.rpm_limit, 'RPM'),
    tpm_limit: parseOptionalPositiveInt(form.tpm_limit, 'TPM'),
    rph_limit: parseOptionalPositiveInt(form.rph_limit, 'RPH'),
    rpd_limit: parseOptionalPositiveInt(form.rpd_limit, 'RPD'),
    tpd_limit: parseOptionalPositiveInt(form.tpd_limit, 'TPD'),
    max_parallel_requests: parseOptionalPositiveInt(form.max_parallel_requests, 'Parallel'),
    batch_rpm_limit: parseOptionalPositiveInt(form.batch_rpm_limit, 'Batch RPM'),
    batch_tpm_limit: parseOptionalPositiveInt(form.batch_tpm_limit, 'Batch TPM'),
    pricing: pricingFormToPayload(form, existing?.pricing),
    capacity_pool_key: form.capacity_pool_key.trim() || null,
    priority: parseRequiredInteger(form.priority, 'Priority'),
  }, existing);
}

export function modelPolicyToPayload(policy: TierModelPolicy): TierModelPolicyPayload {
  return withOptionalMetadata({
    callable_key: policy.callable_key,
    enabled: policy.enabled,
    access_mode: policy.access_mode,
    rpm_limit: policy.rpm_limit ?? null,
    tpm_limit: policy.tpm_limit ?? null,
    rph_limit: policy.rph_limit ?? null,
    rpd_limit: policy.rpd_limit ?? null,
    tpd_limit: policy.tpd_limit ?? null,
    max_parallel_requests: policy.max_parallel_requests ?? null,
    batch_rpm_limit: policy.batch_rpm_limit ?? null,
    batch_tpm_limit: policy.batch_tpm_limit ?? null,
    pricing: policy.pricing ?? null,
    capacity_pool_key: policy.capacity_pool_key ?? null,
    priority: policy.priority,
  }, policy);
}

export function modelPoliciesToPayload(policies: TierModelPolicy[]): TierModelPolicyPayload[] {
  return policies.map((policy) => modelPolicyToPayload(policy));
}

export function validateModelPolicyForm(form: TierModelPolicyForm): string | null {
  try {
    modelPolicyFormToPayload(form);
    return null;
  } catch (err: unknown) {
    return errorMessage(err, 'Invalid model policy.');
  }
}

export function emptyCapacityPoolForm(): TierCapacityPoolForm {
  return {
    pool_key: '',
    callable_key: '',
    rpm_capacity: '',
    tpm_capacity: '',
    max_parallel_requests: '',
    strategy: 'hard_cap',
    saturation_threshold: '',
    burst_multiplier: '',
  };
}

export function capacityPoolToForm(pool?: TierCapacityPool | null): TierCapacityPoolForm {
  return {
    pool_key: pool?.pool_key || '',
    callable_key: pool?.callable_key || '',
    rpm_capacity: numberToInput(pool?.rpm_capacity),
    tpm_capacity: numberToInput(pool?.tpm_capacity),
    max_parallel_requests: numberToInput(pool?.max_parallel_requests),
    strategy: pool?.strategy || 'hard_cap',
    saturation_threshold: numberToInput(pool?.saturation_threshold),
    burst_multiplier: numberToInput(pool?.burst_multiplier),
  };
}

export function capacityPoolFormToPayload(
  form: TierCapacityPoolForm,
  existing?: Pick<TierCapacityPool, 'metadata'> | null,
): TierCapacityPoolPayload {
  return withOptionalMetadata({
    pool_key: form.pool_key.trim(),
    callable_key: form.callable_key.trim(),
    rpm_capacity: parseOptionalPositiveInt(form.rpm_capacity, 'RPM capacity'),
    tpm_capacity: parseOptionalPositiveInt(form.tpm_capacity, 'TPM capacity'),
    max_parallel_requests: parseOptionalPositiveInt(form.max_parallel_requests, 'Parallel'),
    strategy: form.strategy,
    saturation_threshold: parseOptionalRatio(form.saturation_threshold, 'Saturation threshold'),
    burst_multiplier: parseOptionalNumberAtLeast(form.burst_multiplier, 'Burst multiplier', 1),
  }, existing);
}

export function capacityPoolToPayload(pool: TierCapacityPool): TierCapacityPoolPayload {
  return withOptionalMetadata({
    pool_key: pool.pool_key,
    callable_key: pool.callable_key,
    rpm_capacity: pool.rpm_capacity ?? null,
    tpm_capacity: pool.tpm_capacity ?? null,
    max_parallel_requests: pool.max_parallel_requests ?? null,
    strategy: pool.strategy,
    saturation_threshold: pool.saturation_threshold ?? null,
    burst_multiplier: pool.burst_multiplier ?? null,
  }, pool);
}

export function capacityPoolsToPayload(pools: TierCapacityPool[]): TierCapacityPoolPayload[] {
  return pools.map((pool) => capacityPoolToPayload(pool));
}

export function poolOptionsForCallable(
  pools: TierCapacityPoolOption[],
  callableKey?: string,
): TierCapacityPoolOption[] {
  const normalizedCallableKey = callableKey?.trim();
  if (callableKey !== undefined && !normalizedCallableKey) {
    return [];
  }

  const seen = new Set<string>();
  return pools
    .filter((pool) => {
      const poolKey = pool.pool_key.trim();
      const poolCallableKey = pool.callable_key.trim();
      if (!poolKey || !poolCallableKey) return false;
      if (normalizedCallableKey && poolCallableKey !== normalizedCallableKey) return false;
      const key = `${poolKey}:${poolCallableKey}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((left, right) => (
      left.pool_key.localeCompare(right.pool_key)
      || left.callable_key.localeCompare(right.callable_key)
    ));
}

export function parsePositiveIntegerInput(value: string, label: string): number {
  const normalized = value.trim();
  if (!/^\d+$/.test(normalized)) {
    throw new Error(`${label} must be a positive integer.`);
  }
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error(`${label} must be a positive integer.`);
  }
  return parsed;
}

export function parseNonNegativeIntegerInput(value: string, label: string): number {
  const normalized = value.trim();
  if (!/^\d+$/.test(normalized)) {
    throw new Error(`${label} must be a non-negative integer.`);
  }
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${label} must be a non-negative integer.`);
  }
  return parsed;
}

export function validateCapacityPoolForm(form: TierCapacityPoolForm): string | null {
  try {
    capacityPoolFormToPayload(form);
    return null;
  } catch (err: unknown) {
    return errorMessage(err, 'Invalid capacity pool.');
  }
}

export function describeRateLimit(descriptor: TierRateLimitDescriptor): string {
  const unit = descriptor.amount_kind === 'tokens' ? 'tokens' : 'requests';
  const windowLabel = descriptor.window_seconds === 60
    ? 'min'
    : descriptor.window_seconds === 3600
      ? 'hour'
      : descriptor.window_seconds === 86400
        ? 'day'
        : `${descriptor.window_seconds}s`;
  return `${Number(descriptor.limit).toLocaleString()} ${unit}/${windowLabel}`;
}

export function summarizeSimulation(simulation: TierPolicySimulation | null): string {
  if (!simulation) return 'No simulation run';
  if (!simulation.access.allowed) return `Denied: ${simulation.access.reason}`;
  const exceeded = simulation.static_limit_checks.filter((item) => item.would_exceed_limit);
  if (exceeded.length > 0) {
    return `Allowed by tier, but exceeds ${exceeded.map((item) => item.scope).join(', ')}`;
  }
  return 'Allowed by effective tier policy';
}

function withOptionalMetadata<T extends object>(
  payload: T,
  existing?: { metadata?: Record<string, unknown> | null } | null,
): T & { metadata?: Record<string, unknown> | null } {
  if (!existing || !Object.prototype.hasOwnProperty.call(existing, 'metadata')) {
    return payload;
  }
  return {
    ...payload,
    metadata: existing.metadata ?? null,
  };
}

function pricingFormToPayload(
  form: TierModelPolicyForm,
  existingPricing?: Record<string, number> | null,
): Record<string, number> | null {
  const pricing: Record<string, number> = {};
  for (const [key, value] of Object.entries(existingPricing || {})) {
    if (!EDITABLE_PRICING_PAYLOAD_FIELDS.has(key) && Number.isFinite(value)) {
      pricing[key] = value;
    }
  }

  for (const [formField, payloadField, label] of PRICING_FIELDS) {
    if (!form[formField].trim()) {
      delete pricing[payloadField];
      continue;
    }
    const value = parseOptionalNonNegativeNumber(form[formField], label);
    if (value != null) {
      pricing[payloadField] = value;
    }
  }

  return Object.keys(pricing).length ? pricing : null;
}

function numberToInput(value?: number | null): string {
  return value == null ? '' : String(value);
}

function parseOptionalPositiveInt(value: string, label: string): number | null {
  const normalized = value.trim();
  if (!normalized) return null;
  return parsePositiveIntegerInput(normalized, label);
}

function parseRequiredInteger(value: string, label: string): number {
  const normalized = value.trim();
  if (!normalized) return 0;
  if (!/^-?\d+$/.test(normalized)) {
    throw new Error(`${label} must be an integer.`);
  }
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${label} must be an integer.`);
  }
  return parsed;
}

function parseOptionalNonNegativeNumber(value: string, label: string): number | null {
  const parsed = parseOptionalDecimal(value, label, 'a non-negative number');
  if (parsed == null) return null;
  if (parsed < 0) {
    throw new Error(`${label} must be a non-negative number.`);
  }
  return parsed;
}

function parseOptionalRatio(value: string, label: string): number | null {
  const parsed = parseOptionalDecimal(value, label, 'greater than 0 and less than or equal to 1');
  if (parsed == null) return null;
  if (parsed <= 0 || parsed > 1) {
    throw new Error(`${label} must be greater than 0 and less than or equal to 1.`);
  }
  return parsed;
}

function parseOptionalNumberAtLeast(value: string, label: string, minimum: number): number | null {
  const parsed = parseOptionalDecimal(value, label, `greater than or equal to ${minimum}`);
  if (parsed == null) return null;
  if (parsed < minimum) {
    throw new Error(`${label} must be greater than or equal to ${minimum}.`);
  }
  return parsed;
}

function parseOptionalDecimal(value: string, label: string, requirement: string): number | null {
  const normalized = value.trim();
  if (!normalized) return null;
  if (!DECIMAL_PATTERN.test(normalized)) {
    throw new Error(`${label} must be ${requirement}.`);
  }
  const parsed = Number(normalized);
  if (!Number.isFinite(parsed)) {
    throw new Error(`${label} must be ${requirement}.`);
  }
  return parsed;
}
