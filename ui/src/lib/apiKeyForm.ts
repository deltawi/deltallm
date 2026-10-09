import type { SelfServicePolicy } from './api';
import { modelOutputPayload, type ModelOutputRow } from './modelOutputTpm';
import { parseOutputTpm } from './outputTpm';

export type KeyFormTab = 'details' | 'access' | 'limits';
export type KeyFormState = {
  key_name: string;
  team_id: string;
  owner_mode: 'self' | 'service_account';
  owner_service_account_id: string;
  max_budget: string;
  rpm_limit: string;
  tpm_limit: string;
  output_tpm_limit: string;
  model_output_tpm_limit: ModelOutputRow[];
  rph_limit: string;
  rpd_limit: string;
  tpd_limit: string;
  expires: string;
  asset_access_mode: 'inherit' | 'restrict';
  selected_callable_keys: string[];
  selected_access_group_keys: string[];
};
export type KeyMutationPayload = Record<string, number | string | Record<string, number> | null | undefined>;

export function emptyKeyForm(): KeyFormState {
  return {
    key_name: '', team_id: '', owner_mode: 'self', owner_service_account_id: '',
    max_budget: '', rpm_limit: '', tpm_limit: '', rph_limit: '', rpd_limit: '', tpd_limit: '',
    output_tpm_limit: '', model_output_tpm_limit: [],
    expires: '', asset_access_mode: 'inherit', selected_callable_keys: [], selected_access_group_keys: [],
  };
}

export function validateKeyForm(form: KeyFormState, selfService: boolean, policy: SelfServicePolicy | null, now = new Date()): { tab: KeyFormTab; message: string } | null {
  if (!form.key_name.trim()) return { tab: 'details', message: 'Enter a key name.' };
  if (!form.team_id) return { tab: 'details', message: 'Select a team.' };
  if (!selfService && form.owner_mode === 'service_account' && !form.owner_service_account_id) {
    return { tab: 'details', message: 'Select a service account or switch ownership to You.' };
  }
  if (form.expires && Number.isNaN(new Date(form.expires).getTime())) return { tab: 'limits', message: 'Enter a valid expiry date.' };
  try {
    parseOutputTpm(form.output_tpm_limit);
    modelOutputPayload(form.model_output_tpm_limit);
  } catch (error: unknown) {
    return { tab: 'limits', message: error instanceof Error ? error.message : 'Enter valid output token limits.' };
  }
  if (selfService && policy) {
    if (policy.self_service_require_expiry && !form.expires) return { tab: 'limits', message: 'This team requires an expiry date for self-service keys.' };
    if (policy.self_service_max_expiry_days != null && form.expires) {
      const maxDate = new Date(now);
      maxDate.setDate(maxDate.getDate() + policy.self_service_max_expiry_days);
      if (new Date(form.expires) > maxDate) return { tab: 'limits', message: `Expiry must be within ${policy.self_service_max_expiry_days} days from today.` };
    }
    if (policy.self_service_budget_ceiling != null && form.max_budget && Number(form.max_budget) > policy.self_service_budget_ceiling) {
      return { tab: 'limits', message: `Budget cannot exceed the team ceiling of $${policy.self_service_budget_ceiling}.` };
    }
  }
  return null;
}

export function keyMutationPayload(form: KeyFormState, context: { selfService: boolean; editing: boolean; accountId: string }): KeyMutationPayload {
  const payload: KeyMutationPayload = { key_name: form.key_name.trim(), team_id: form.team_id || undefined };
  payload.output_tpm_limit = parseOutputTpm(form.output_tpm_limit);
  payload.model_output_tpm_limit = modelOutputPayload(form.model_output_tpm_limit);
  for (const key of ['max_budget', 'rpm_limit', 'tpm_limit', 'rph_limit', 'rpd_limit', 'tpd_limit'] as const) {
    payload[key] = form[key] ? Number(form[key]) : context.editing ? null : undefined;
  }
  if (!context.selfService && !context.editing) {
    payload.owner_account_id = form.owner_mode === 'self' ? context.accountId || undefined : undefined;
    payload.owner_service_account_id = form.owner_mode === 'service_account' ? form.owner_service_account_id || undefined : undefined;
  }
  if (!context.editing && form.expires) payload.expires = new Date(form.expires).toISOString();
  if (context.editing && context.selfService) payload.expires = form.expires ? new Date(form.expires).toISOString() : null;
  return payload;
}
