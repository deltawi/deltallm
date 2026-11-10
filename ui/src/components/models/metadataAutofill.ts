import type { ProviderModelOption } from '../../lib/api';
import type { ModelFormValues } from '../modelFormShared';

export type MetadataAutofillField =
  | 'input_cost_per_token'
  | 'output_cost_per_token'
  | 'input_cost_per_token_cache_hit'
  | 'output_cost_per_token_cache_hit'
  | 'input_cost_per_token_cache_write'
  | 'input_cost_per_token_cache_write_1h'
  | 'max_context_window'
  | 'max_input_tokens'
  | 'max_output_tokens'
  | 'output_vector_size'
  | 'input_cost_per_image'
  | 'input_cost_per_character'
  | 'output_cost_per_character'
  | 'input_cost_per_second'
  | 'output_cost_per_second'
  | 'input_cost_per_audio_token'
  | 'output_cost_per_audio_token'
  | 'batch_price_multiplier'
  | 'batch_input_cost_per_token'
  | 'batch_output_cost_per_token';

// Catalog metadata key -> form field.
export const MODEL_METADATA_FIELDS: Record<string, MetadataAutofillField> = {
  input_cost_per_token: 'input_cost_per_token',
  output_cost_per_token: 'output_cost_per_token',
  input_cost_per_token_cache_hit: 'input_cost_per_token_cache_hit',
  output_cost_per_token_cache_hit: 'output_cost_per_token_cache_hit',
  input_cost_per_token_cache_write: 'input_cost_per_token_cache_write',
  input_cost_per_token_cache_write_1h: 'input_cost_per_token_cache_write_1h',
  max_tokens: 'max_context_window',
  max_input_tokens: 'max_input_tokens',
  max_output_tokens: 'max_output_tokens',
  output_vector_size: 'output_vector_size',
  input_cost_per_image: 'input_cost_per_image',
  input_cost_per_character: 'input_cost_per_character',
  output_cost_per_character: 'output_cost_per_character',
  input_cost_per_second: 'input_cost_per_second',
  output_cost_per_second: 'output_cost_per_second',
  input_cost_per_audio_token: 'input_cost_per_audio_token',
  output_cost_per_audio_token: 'output_cost_per_audio_token',
  batch_price_multiplier: 'batch_price_multiplier',
  batch_input_cost_per_token: 'batch_input_cost_per_token',
  batch_output_cost_per_token: 'batch_output_cost_per_token',
};

/** Values the form filled from metadata, keyed by field. */
export type AutofilledValues = Partial<Record<MetadataAutofillField, string>>;

export interface MetadataAutofillResult {
  form: ModelFormValues;
  autofilled: AutofilledValues;
}

/**
 * Applies the selected model's known metadata to the form.
 *
 * A field is owned by auto-fill while it still holds the value auto-fill put
 * there. Owned fields follow the selected model: they take the new model's
 * value, or are cleared when the new model has none. Fields the user typed
 * (or that came from a saved model) are never changed, except empty ones,
 * which are filled.
 */
export function applyKnownMetadata(
  current: ModelFormValues,
  previouslyAutofilled: AutofilledValues,
  metadata: ProviderModelOption['known_metadata'],
): MetadataAutofillResult {
  const next = { ...current };
  const autofilled: AutofilledValues = {};
  let changed = false;

  for (const [metadataKey, field] of Object.entries(MODEL_METADATA_FIELDS)) {
    const rawValue = metadata?.[metadataKey];
    const value = rawValue == null ? '' : String(rawValue);
    const currentValue = next[field];
    const owned = previouslyAutofilled[field] !== undefined && previouslyAutofilled[field] === currentValue;

    if (!owned && currentValue) continue;
    if (value) autofilled[field] = value;
    if (currentValue !== value) {
      next[field] = value;
      changed = true;
    }
  }

  return { form: changed ? next : current, autofilled };
}
