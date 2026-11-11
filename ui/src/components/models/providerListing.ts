import type { ProviderModelOption, ProviderPreset } from '../../lib/api';
import { providerDisplayName } from '../../lib/providers';

export interface ProviderGroup {
  id: string;
  label: string;
  providers: readonly string[];
}

// The order in a group is the order in the list. A provider that is in no
// group goes to the "Other providers" group, so a new backend preset always shows.
export const PROVIDER_GROUPS: readonly ProviderGroup[] = [
  {
    id: 'labs',
    label: 'Model labs and clouds',
    providers: ['openai', 'anthropic', 'gemini', 'azure_openai', 'bedrock', 'mistral', 'deepseek', 'qwen', 'zai', 'minimax', 'tencent', 'perplexity', 'elevenlabs'],
  },
  { id: 'platforms', label: 'Inference platforms', providers: ['openrouter', 'groq', 'together', 'fireworks', 'deepinfra'] },
  { id: 'self_hosted', label: 'Self-hosted', providers: ['vllm', 'sglang', 'ollama', 'lmstudio'] },
  { id: 'generic', label: 'Any other endpoint', providers: ['openai_compatible'] },
];

const OTHER_GROUP: ProviderGroup = { id: 'other', label: 'Other providers', providers: [] };

export const GENERIC_PROVIDER = 'openai_compatible';

const MODE_SHORT_LABELS: Record<string, string> = {
  chat: 'Chat',
  embedding: 'Embeddings',
  image_generation: 'Images',
  audio_speech: 'Speech',
  audio_transcription: 'Transcription',
  realtime: 'Realtime',
  rerank: 'Rerank',
};
const MODE_ORDER = Object.keys(MODE_SHORT_LABELS);

export function modeShortLabel(mode: string): string {
  return MODE_SHORT_LABELS[mode] || mode;
}

/** "Chat, Embeddings, Images +2": the model types a provider supports, short. */
export function providerModeSummary(modes: readonly string[], limit = 3): string {
  const labels = [...modes]
    .sort((a, b) => rank(a) - rank(b))
    .map(modeShortLabel);
  if (labels.length <= limit) return labels.join(', ');
  return `${labels.slice(0, limit).join(', ')} +${labels.length - limit}`;
}

function rank(mode: string): number {
  const index = MODE_ORDER.indexOf(mode);
  return index < 0 ? MODE_ORDER.length : index;
}

export function matchesProvider(preset: ProviderPreset, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [preset.provider, providerDisplayName(preset.provider), preset.api_base || '']
    .some((value) => value.toLowerCase().includes(needle));
}

export interface ProviderSection {
  group: ProviderGroup;
  presets: ProviderPreset[];
}

export interface ProviderListing {
  sections: ProviderSection[];
  /** Matching providers that do not support the selected model type. */
  unsupported: ProviderPreset[];
  /** The selectable providers in display order, for keyboard movement. */
  options: ProviderPreset[];
}

/**
 * Groups the provider presets for the picker. It keeps the providers that
 * match the search and support the model type. The selected provider stays
 * in the list even when it does not support the type, so the field can show it.
 */
export function listProviders(
  presets: readonly ProviderPreset[],
  query: string,
  mode: string,
  selected = '',
): ProviderListing {
  const known = new Set(PROVIDER_GROUPS.flatMap((group) => group.providers));
  const byProvider = new Map(presets.map((preset) => [preset.provider, preset]));
  const groups = [
    ...PROVIDER_GROUPS,
    { ...OTHER_GROUP, providers: presets.map((preset) => preset.provider).filter((id) => !known.has(id)).sort() },
  ];
  const unsupported: ProviderPreset[] = [];
  const sections: ProviderSection[] = [];
  for (const group of groups) {
    const items: ProviderPreset[] = [];
    for (const id of group.providers) {
      const preset = byProvider.get(id);
      if (!preset || !matchesProvider(preset, query)) continue;
      if (!mode || preset.supported_modes.includes(mode) || preset.provider === selected) items.push(preset);
      else unsupported.push(preset);
    }
    if (items.length > 0) sections.push({ group, presets: items });
  }
  return { sections, unsupported, options: sections.flatMap((section) => section.presets) };
}

/** Index of the first [start, end) match of the query in the text, or null. */
export function matchRange(text: string, query: string): [number, number] | null {
  const needle = query.trim().toLowerCase();
  if (!needle) return null;
  const start = text.toLowerCase().indexOf(needle);
  return start < 0 ? null : [start, start + needle.length];
}

export function filterModelOptions(options: readonly ProviderModelOption[], query: string): ProviderModelOption[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...options];
  return options.filter((option) => (
    option.id.toLowerCase().includes(needle) || option.label.toLowerCase().includes(needle)
  ));
}

const COMPACT_TOKENS = [
  [1_000_000, 'M'],
  [1_000, 'K'],
] as const;

/** 128000 -> "128K", 1048576 -> "1M". */
export function compactTokenCount(value: number | null | undefined): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
  for (const [unit, suffix] of COMPACT_TOKENS) {
    if (value >= unit) {
      const scaled = value / unit;
      return `${Number.isInteger(scaled) ? scaled : Math.round(scaled * 10) / 10}${suffix}`;
    }
  }
  return String(value);
}

function perMillionLabel(perToken: number | null | undefined): string | null {
  if (typeof perToken !== 'number' || !Number.isFinite(perToken) || perToken < 0) return null;
  const perMillion = Math.round(perToken * 1_000_000 * 10_000) / 10_000;
  return `$${perMillion.toFixed(perMillion < 0.01 && perMillion > 0 ? 4 : 2)}`;
}

/** Context and price facts for one suggested provider model, when known. */
export function modelOptionFacts(option: ProviderModelOption): { context: string | null; price: string | null } {
  const metadata = option.known_metadata || {};
  const context = compactTokenCount(metadata.max_input_tokens ?? metadata.max_tokens);
  const input = perMillionLabel(metadata.input_cost_per_token);
  const output = perMillionLabel(metadata.output_cost_per_token);
  const price = input && output ? `${input} / ${output}` : input;
  return { context, price };
}
