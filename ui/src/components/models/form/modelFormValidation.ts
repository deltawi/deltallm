import type { ProviderPreset } from '../../../lib/api';
import { providerDisplayName } from '../../../lib/providers';
import {
  MODE_OPTIONS,
  validateContextCapacityFields,
  type ContextCapacityField,
  type ModelFormValues,
} from '../../modelFormShared';
import { validateRequestShaping, type RequestShapingForm } from '../requestShaping';
import { firstInvalidPriceField } from '../pricingUnits';

export type RequiredField =
  | 'model_name'
  | 'api_model_id'
  | 'api_model_slug'
  | 'api_namespace'
  | 'provider'
  | 'model'
  | 'api_base'
  | 'named_credential_id'
  | ContextCapacityField;

/** The optional settings row that holds an invalid field. It opens so the user sees the error. */
export type ErrorSection = 'limits' | 'batch' | 'pricing' | 'shaping' | null;

export interface ModelFormValidationContext {
  namespaceRequired: boolean;
  lockApiModelId: boolean;
  allowInlineCredentials: boolean;
  preset?: ProviderPreset;
}

export type ModelFormValidation =
  | { ok: true; values: ModelFormValues }
  | { ok: false; message: string; fieldErrors: Partial<Record<RequiredField, string>>; section: ErrorSection };

const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
const NAMESPACE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;

export function validatePositiveInteger(value: string, label: string, minimum = 1): string | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum) {
    return `${label} must be a whole number greater than or equal to ${minimum}.`;
  }
  return null;
}

export function validateCapacityFraction(value: string): string | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0 || parsed > 1) {
    return 'Capacity fraction must be greater than 0 and less than or equal to 1.';
  }
  return null;
}

function modeLabel(mode: string): string {
  return MODE_OPTIONS.find((option) => option.value === mode)?.label || mode;
}

function fail(message: string, section: ErrorSection = null, fieldErrors: Partial<Record<RequiredField, string>> = {}): ModelFormValidation {
  return { ok: false, message, fieldErrors, section };
}

function batchError(form: ModelFormValues): string | null {
  const mode = form.mode;
  if (mode === 'embedding' && form.upstream_max_batch_inputs.trim()) {
    const error = validatePositiveInteger(form.upstream_max_batch_inputs, 'Batch worker upstream max inputs');
    if (error) return `${error} Leave it empty to keep batch-worker micro-batching off.`;
  }
  if (mode === 'chat') {
    const disabled = form.chat_batching_mode === 'disabled';
    const microbatch = form.chat_batching_mode === 'sync_microbatch';
    const error = [
      disabled ? null : validatePositiveInteger(form.chat_batching_max_in_flight, 'Max in-flight'),
      microbatch ? validatePositiveInteger(form.chat_batching_upstream_max_batch_size, 'Upstream max size') : null,
      microbatch ? validatePositiveInteger(form.chat_batching_max_total_input_tokens, 'Max total input tokens') : null,
    ].find(Boolean);
    if (error) return error;
    if (microbatch) {
      const size = Number(form.chat_batching_upstream_max_batch_size);
      if (!Number.isInteger(size) || size < 2) return 'Sync microbatch needs an upstream max size of 2 or more.';
    }
  }
  if (mode === 'chat' || mode === 'embedding') {
    return [
      validatePositiveInteger(form.batch_capacity_max_in_flight, 'Scheduler max in-flight'),
      validatePositiveInteger(form.batch_capacity_max_claim_work_units, 'Scheduler max claim work units'),
      validateCapacityFraction(form.batch_capacity_capacity_fraction),
    ].find(Boolean) || null;
  }
  return null;
}

/**
 * Checks the form before save and returns the trimmed values to send.
 * The checks run in the order the user sees the sections.
 */
export function validateModelForm(
  form: ModelFormValues,
  shaping: RequestShapingForm,
  context: ModelFormValidationContext,
): ModelFormValidation {
  const fieldErrors: Partial<Record<RequiredField, string>> = {};
  const modelName = form.model_name.trim();
  const apiNamespace = form.api_namespace.trim().toLowerCase();
  const apiModelSlug = form.api_model_slug.trim().toLowerCase();
  const apiModelId = context.namespaceRequired ? `${apiNamespace}/${apiModelSlug}` : form.api_model_id.trim();
  const provider = form.provider.trim();
  const model = form.model.trim();
  const apiBase = form.api_base.trim();
  const namedCredentialId = form.named_credential_id.trim();
  const useNamedCredential = !context.allowInlineCredentials || form.credential_source === 'named';

  if (!modelName) fieldErrors.model_name = 'Enter a name.';
  if (context.namespaceRequired) {
    if (apiNamespace.length < 3 || apiNamespace.length > 32 || !NAMESPACE.test(apiNamespace) || apiNamespace.includes('--')) {
      fieldErrors.api_namespace = 'Use 3-32 lowercase letters, numbers, or hyphens.';
    }
    if (!SLUG.test(apiModelSlug) || apiModelSlug.includes('--')) {
      fieldErrors.api_model_slug = 'Use 1-64 lowercase letters, numbers, or hyphens.';
    }
  } else if (context.lockApiModelId ? !apiModelId : (!SLUG.test(apiModelId) || apiModelId.includes('--'))) {
    fieldErrors.api_model_id = 'Use 1-64 lowercase letters, numbers, or hyphens.';
  }
  if (!provider) fieldErrors.provider = 'Select a provider.';
  if (!model) fieldErrors.model = 'Enter the provider model.';
  if (useNamedCredential && !form.keep_existing_named_credential && !namedCredentialId) {
    fieldErrors.named_credential_id = 'Select a saved credential.';
  }
  if (!useNamedCredential && !apiBase) fieldErrors.api_base = 'Enter the API base URL.';
  if (Object.keys(fieldErrors).length > 0) {
    return fail('Fill in the required fields that show an error.', null, fieldErrors);
  }

  if (context.preset && !context.preset.supported_modes.includes(form.mode)) {
    const supported = context.preset.supported_modes.map(modeLabel).join(', ');
    return fail(
      `${providerDisplayName(context.preset.provider)} does not support ${modeLabel(form.mode)}. Supported types: ${supported || 'none'}.`,
      null,
      { provider: 'This provider does not support the model type.' },
    );
  }

  const invalidPrice = firstInvalidPriceField(form);
  if (invalidPrice) return fail('Enter prices as numbers, for example 2.50.', 'pricing');

  const capacityErrors = validateContextCapacityFields(form);
  if (Object.keys(capacityErrors).length > 0) {
    return fail('Fix the context and output limits that show an error.', 'limits', capacityErrors);
  }

  const batch = batchError(form);
  if (batch) return fail(batch, 'batch');

  const shapingError = validateRequestShaping(shaping, { chat: form.mode === 'chat' });
  if (shapingError) return fail(shapingError, 'shaping');

  return {
    ok: true,
    values: {
      ...form,
      model_name: modelName,
      api_model_id: apiModelId,
      api_model_slug: apiModelSlug,
      api_namespace: apiNamespace,
      provider,
      model,
      api_base: apiBase,
      named_credential_id: namedCredentialId,
    },
  };
}
