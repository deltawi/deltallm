export type TierPricingProfile =
  | 'token'
  | 'image_generation'
  | 'audio_speech'
  | 'audio_transcription'
  | 'rerank'
  | 'request'
  | 'custom';

export type PricingFieldGroup = 'token' | 'cache' | 'batch' | 'image' | 'audio' | 'request';

export type PricingFormField =
  | 'input_cost_per_token'
  | 'output_cost_per_token'
  | 'cached_input_cost_per_token'
  | 'cached_output_cost_per_token'
  | 'batch_input_cost_per_token'
  | 'batch_output_cost_per_token'
  | 'batch_price_multiplier'
  | 'input_cost_per_character'
  | 'output_cost_per_character'
  | 'input_cost_per_second'
  | 'output_cost_per_second'
  | 'input_cost_per_image'
  | 'output_cost_per_image'
  | 'input_cost_per_audio_token'
  | 'output_cost_per_audio_token'
  | 'cost_per_request';

export type TierPricingFieldDefinition = {
  formField: PricingFormField;
  payloadField: string;
  label: string;
  shortLabel: string;
  unit: string;
  group: PricingFieldGroup;
  profiles: TierPricingProfile[];
  help: string;
};

export const TIER_PRICING_PROFILES: Array<{
  value: TierPricingProfile;
  label: string;
  description: string;
}> = [
  { value: 'token', label: 'Token', description: 'Chat, embeddings, and token-metered models.' },
  { value: 'image_generation', label: 'Image', description: 'Image models billed per image or request.' },
  { value: 'audio_speech', label: 'Text-to-speech', description: 'Speech generation billed by character, audio token, second, or request.' },
  { value: 'audio_transcription', label: 'Transcription', description: 'Speech-to-text billed by second, audio token, or request.' },
  { value: 'rerank', label: 'Rerank', description: 'Rerank models billed by token or flat request price.' },
  { value: 'request', label: 'Request', description: 'Flat price per request.' },
  { value: 'custom', label: 'Custom', description: 'Show every supported pricing key.' },
];

export const TIER_PRICING_FIELDS: TierPricingFieldDefinition[] = [
  {
    formField: 'input_cost_per_token',
    payloadField: 'input_cost_per_token',
    label: 'Input token price',
    shortLabel: 'Input token',
    unit: '/token',
    group: 'token',
    profiles: ['token', 'audio_speech', 'audio_transcription', 'rerank'],
    help: 'Customer price per input or prompt token.',
  },
  {
    formField: 'output_cost_per_token',
    payloadField: 'output_cost_per_token',
    label: 'Output token price',
    shortLabel: 'Output token',
    unit: '/token',
    group: 'token',
    profiles: ['token', 'audio_speech', 'audio_transcription', 'rerank'],
    help: 'Customer price per generated token.',
  },
  {
    formField: 'cached_input_cost_per_token',
    payloadField: 'input_cost_per_token_cache_hit',
    label: 'Cached input token price',
    shortLabel: 'Cached input',
    unit: '/token',
    group: 'cache',
    profiles: ['token'],
    help: 'Price for cached prompt/input tokens when cache accounting is available.',
  },
  {
    formField: 'cached_output_cost_per_token',
    payloadField: 'output_cost_per_token_cache_hit',
    label: 'Cached output token price',
    shortLabel: 'Cached output',
    unit: '/token',
    group: 'cache',
    profiles: ['token'],
    help: 'Price for cached output tokens when cache accounting is available.',
  },
  {
    formField: 'batch_input_cost_per_token',
    payloadField: 'batch_input_cost_per_token',
    label: 'Batch input token price',
    shortLabel: 'Batch input',
    unit: '/token',
    group: 'batch',
    profiles: ['token'],
    help: 'Input token price used by batch jobs.',
  },
  {
    formField: 'batch_output_cost_per_token',
    payloadField: 'batch_output_cost_per_token',
    label: 'Batch output token price',
    shortLabel: 'Batch output',
    unit: '/token',
    group: 'batch',
    profiles: ['token'],
    help: 'Output token price used by batch jobs.',
  },
  {
    formField: 'batch_price_multiplier',
    payloadField: 'batch_price_multiplier',
    label: 'Batch price multiplier',
    shortLabel: 'Batch multiplier',
    unit: 'x',
    group: 'batch',
    profiles: ['token'],
    help: 'Multiplier applied to sync token pricing for batch jobs when explicit batch prices are not set.',
  },
  {
    formField: 'output_cost_per_image',
    payloadField: 'output_cost_per_image',
    label: 'Generated image price',
    shortLabel: 'Generated image',
    unit: '/image',
    group: 'image',
    profiles: ['image_generation'],
    help: 'Customer price per generated output image.',
  },
  {
    formField: 'input_cost_per_image',
    payloadField: 'input_cost_per_image',
    label: 'Input image price (legacy output fallback)',
    shortLabel: 'Input image',
    unit: '/image',
    group: 'image',
    profiles: ['image_generation', 'custom'],
    help: 'Price per input image. It also remains the generated-image fallback for existing configurations.',
  },
  {
    formField: 'input_cost_per_character',
    payloadField: 'input_cost_per_character',
    label: 'Input character price',
    shortLabel: 'Input char',
    unit: '/char',
    group: 'audio',
    profiles: ['audio_speech'],
    help: 'Customer price per input character for text-to-speech.',
  },
  {
    formField: 'output_cost_per_character',
    payloadField: 'output_cost_per_character',
    label: 'Output character price',
    shortLabel: 'Output char',
    unit: '/char',
    group: 'audio',
    profiles: ['audio_speech'],
    help: 'Customer price per output character when a provider reports it.',
  },
  {
    formField: 'input_cost_per_second',
    payloadField: 'input_cost_per_second',
    label: 'Input second price',
    shortLabel: 'Input sec',
    unit: '/sec',
    group: 'audio',
    profiles: ['audio_speech', 'audio_transcription'],
    help: 'Customer price per input or billable audio second.',
  },
  {
    formField: 'output_cost_per_second',
    payloadField: 'output_cost_per_second',
    label: 'Output second price',
    shortLabel: 'Output sec',
    unit: '/sec',
    group: 'audio',
    profiles: ['audio_speech', 'audio_transcription'],
    help: 'Customer price per output audio second when a provider reports it.',
  },
  {
    formField: 'input_cost_per_audio_token',
    payloadField: 'input_cost_per_audio_token',
    label: 'Input audio token price',
    shortLabel: 'Input audio token',
    unit: '/audio token',
    group: 'audio',
    profiles: ['audio_speech', 'audio_transcription'],
    help: 'Customer price per input audio token.',
  },
  {
    formField: 'output_cost_per_audio_token',
    payloadField: 'output_cost_per_audio_token',
    label: 'Output audio token price',
    shortLabel: 'Output audio token',
    unit: '/audio token',
    group: 'audio',
    profiles: ['audio_speech'],
    help: 'Customer price per output audio token.',
  },
  {
    formField: 'cost_per_request',
    payloadField: 'cost_per_request',
    label: 'Request price',
    shortLabel: 'Request',
    unit: '/request',
    group: 'request',
    profiles: ['token', 'image_generation', 'audio_speech', 'audio_transcription', 'rerank', 'request'],
    help: 'Flat customer price added once per request.',
  },
];

export function pricingProfileForModelMode(mode?: string | null): TierPricingProfile {
  switch (String(mode || '').trim().toLowerCase()) {
    case 'chat':
    case 'completion':
    case 'responses':
    case 'embedding':
      return 'token';
    case 'image':
    case 'image_generation':
      return 'image_generation';
    case 'audio_speech':
    case 'speech':
    case 'text_to_speech':
      return 'audio_speech';
    case 'audio_transcription':
    case 'transcription':
    case 'speech_to_text':
      return 'audio_transcription';
    case 'rerank':
      return 'rerank';
    default:
      return 'token';
  }
}

export function pricingProfileLabel(profile: TierPricingProfile): string {
  return TIER_PRICING_PROFILES.find((item) => item.value === profile)?.label || 'Token';
}

export function pricingFieldsForProfile(profile: TierPricingProfile): TierPricingFieldDefinition[] {
  if (profile === 'custom') return [...TIER_PRICING_FIELDS];
  return TIER_PRICING_FIELDS.filter((field) => field.profiles.includes(profile));
}

export function advancedPricingFieldsForProfile(profile: TierPricingProfile): TierPricingFieldDefinition[] {
  const primary = new Set(pricingFieldsForProfile(profile).map((field) => field.payloadField));
  if (profile === 'custom') return [];
  return TIER_PRICING_FIELDS.filter((field) => !primary.has(field.payloadField));
}

export function pricingEntries(
  pricing?: Record<string, number> | null,
): Array<TierPricingFieldDefinition & { value: number }> {
  const data = pricing || {};
  return TIER_PRICING_FIELDS
    .map((field) => {
      const value = data[field.payloadField];
      return Number.isFinite(value) ? { ...field, value } : null;
    })
    .filter((entry): entry is TierPricingFieldDefinition & { value: number } => entry !== null);
}

export function formatPricingValue(value: number, unit?: string): string {
  const normalized = Number(value);
  if (!Number.isFinite(normalized)) return '-';
  return `${normalized.toLocaleString(undefined, { maximumSignificantDigits: 8 })}${unit ? ` ${unit}` : ''}`;
}

export function summarizePricing(
  pricing?: Record<string, number> | null,
  profile?: TierPricingProfile | null,
): string {
  const entries = pricingEntries(pricing);
  if (entries.length === 0) return 'No price override';
  const preferred = profile
    ? entries.filter((entry) => profile === 'custom' || entry.profiles.includes(profile))
    : entries;
  const visible = (preferred.length ? preferred : entries).slice(0, 3);
  const summary = visible.map((entry) => `${entry.shortLabel} ${formatPricingValue(entry.value, entry.unit)}`).join(' · ');
  const remaining = entries.length - visible.length;
  return remaining > 0 ? `${summary} · +${remaining}` : summary;
}

export function inferPricingProfileFromPricing(pricing?: Record<string, number> | null): TierPricingProfile {
  const fields = new Set(Object.keys(pricing || {}));
  if (fields.has('input_cost_per_image') || fields.has('output_cost_per_image')) {
    return 'image_generation';
  }
  if (fields.has('input_cost_per_character') || fields.has('output_cost_per_character')) {
    return 'audio_speech';
  }
  if (fields.has('input_cost_per_second') || fields.has('output_cost_per_second')) {
    return 'audio_transcription';
  }
  if (fields.has('input_cost_per_audio_token') || fields.has('output_cost_per_audio_token')) {
    return 'audio_transcription';
  }
  if (fields.size === 1 && fields.has('cost_per_request')) {
    return 'request';
  }
  return 'token';
}
