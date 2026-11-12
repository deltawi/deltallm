import type { ModelFormValues } from '../../modelFormShared';

// The price fields that each model type shows. Validation uses the same lists,
// so a hidden price never blocks Save.

export type PriceField = {
  [K in keyof ModelFormValues]: ModelFormValues[K] extends string ? K : never;
}[keyof ModelFormValues];

export interface PriceSpec {
  field: PriceField;
  label: string;
  placeholder?: string;
  perMillion?: boolean;
  help?: string;
}

const token = (field: PriceField, label: string, placeholder = '0.00', help?: string): PriceSpec => (
  { field, label: `${label} per 1M tokens`, placeholder, perMillion: true, help }
);

export const MAIN_PRICES: Partial<Record<ModelFormValues['mode'], PriceSpec[]>> = {
  chat: [
    token('input_cost_per_token', 'Input', '2.50'),
    token('output_cost_per_token', 'Output', '10.00'),
    token('input_cost_per_token_cache_hit', 'Cached input', 'Optional'),
  ],
  embedding: [
    token('input_cost_per_token', 'Input', '0.02'),
    token('input_cost_per_token_cache_hit', 'Cached input', 'Optional'),
  ],
  image_generation: [
    { field: 'input_cost_per_image', label: 'Price per image', placeholder: '0.04' },
  ],
  audio_speech: [
    { field: 'input_cost_per_character', label: 'Input per character', placeholder: '0.000015' },
    { field: 'output_cost_per_character', label: 'Output per character', placeholder: '0.000015' },
    { field: 'input_cost_per_second', label: 'Input per second', placeholder: '0.00025' },
    { field: 'output_cost_per_second', label: 'Output per second', placeholder: '0.00025' },
    token('input_cost_per_audio_token', 'Input audio', '100.00'),
    token('output_cost_per_audio_token', 'Output audio', '100.00'),
  ],
  audio_transcription: [
    { field: 'input_cost_per_second', label: 'Input per second', placeholder: '0.0001', help: 'The main price for duration-based billing.' },
    { field: 'output_cost_per_second', label: 'Output per second', placeholder: '0', help: 'Only for providers that bill output audio time.' },
  ],
  rerank: [
    token('input_cost_per_token', 'Input', '2.00', 'Counts the tokens of the query and the documents.'),
  ],
};

export const CACHE_PRICES: PriceSpec[] = [
  token('output_cost_per_token_cache_hit', 'Cached output', 'Optional'),
  token('input_cost_per_token_cache_write', 'Cache write', 'Empty uses input price'),
  token('input_cost_per_token_cache_write_1h', 'Cache write (1 hour)', 'Empty uses cache write price'),
];

export const BATCH_PRICES: PriceSpec[] = [
  token('batch_input_cost_per_token', 'Batch input', 'Optional'),
  token('batch_output_cost_per_token', 'Batch output', 'Optional'),
];

/** Realtime token prices, per 1M tokens. */
export const REALTIME_TOKEN_PRICES = [
  ['input_cost_per_token', 'Input text'],
  ['output_cost_per_token', 'Output text'],
  ['input_cost_per_token_cache_hit', 'Cached input text'],
  ['input_cost_per_audio_token', 'Input audio'],
  ['output_cost_per_audio_token', 'Output audio'],
  ['input_cost_per_audio_token_cache_hit', 'Cached input audio'],
] as const satisfies readonly (readonly [PriceField, string])[];

/** The realtime price fields for the session type and usage format. */
export function realtimePriceFields(form: Pick<ModelFormValues, 'realtime_profile' | 'realtime_usage_type'>): PriceField[] {
  if (form.realtime_profile === 'transcription' && form.realtime_usage_type === 'duration') return ['input_cost_per_second'];
  return REALTIME_TOKEN_PRICES
    .map(([field]) => field)
    .filter((field) => form.realtime_profile !== 'transcription' || field !== 'output_cost_per_audio_token');
}

/** The price fields that the form shows for the model type. */
export function visiblePriceFields(form: ModelFormValues): PriceField[] {
  if (form.mode === 'realtime') return realtimePriceFields(form);
  const fields = (MAIN_PRICES[form.mode] || []).map((spec) => spec.field);
  if (form.mode === 'chat') fields.push(...CACHE_PRICES.map((spec) => spec.field));
  if (form.mode === 'chat' || form.mode === 'embedding') fields.push(...BATCH_PRICES.map((spec) => spec.field));
  return fields;
}
