/**
 * Summarize a model deployment's stored prices for list views.
 *
 * Prices are stored in `model_info` per single unit (token, character,
 * second, image). Lists show them in the units people compare: per 1M
 * tokens or characters, per minute of audio, per image.
 */

export type ModelCostRate = { label: string; value: string };

export type ModelCostSummary =
  | { kind: 'unset'; rates: ModelCostRate[] }
  | { kind: 'priced'; primary: string; unit: string; rates: ModelCostRate[] };

const PER_MILLION = 1_000_000;

function readPrice(info: Record<string, unknown>, field: string): number | null {
  const raw = info[field];
  if (raw == null || raw === '') return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

/** Format a USD amount: two decimals from $1, three significant digits below. */
export function fmtUnitPrice(value: number): string {
  if (value === 0) return '$0';
  if (value >= 1) return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (value < 0.0001) return '<$0.0001';
  const rounded = Number(value.toPrecision(3));
  const decimals = Math.max(2, (String(rounded).split('.')[1] || '').length);
  return `$${rounded.toFixed(decimals)}`;
}

type RateField = { field: string; label: string; scale: number; unit: string };

const TOKEN = PER_MILLION;
const RATE_FIELDS: RateField[] = [
  { field: 'input_cost_per_token', label: 'Input', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'input_cost_per_token_cache_hit', label: 'Cached input', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'output_cost_per_token', label: 'Output', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'output_cost_per_token_cache_hit', label: 'Cached output', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'batch_input_cost_per_token', label: 'Batch input', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'batch_output_cost_per_token', label: 'Batch output', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'input_cost_per_audio_token', label: 'Audio input', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'input_cost_per_audio_token_cache_hit', label: 'Cached audio input', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'output_cost_per_audio_token', label: 'Audio output', scale: TOKEN, unit: '/ 1M tokens' },
  { field: 'input_cost_per_character', label: 'Input', scale: PER_MILLION, unit: '/ 1M characters' },
  { field: 'output_cost_per_character', label: 'Output', scale: PER_MILLION, unit: '/ 1M characters' },
  { field: 'input_cost_per_second', label: 'Input audio', scale: 60, unit: '/ minute' },
  { field: 'output_cost_per_second', label: 'Output audio', scale: 60, unit: '/ minute' },
  { field: 'input_cost_per_image', label: 'Input', scale: 1, unit: '/ image' },
  { field: 'output_cost_per_image', label: 'Image', scale: 1, unit: '/ image' },
  { field: 'cost_per_request', label: 'Request', scale: 1, unit: '/ request' },
];

type Primary = { input: string; output?: string; unit: string; inputScale: number; outputScale?: number };

const TOKENS: Primary = { input: 'input_cost_per_token', output: 'output_cost_per_token', unit: 'per 1M tokens', inputScale: TOKEN, outputScale: TOKEN };
const CHARACTERS: Primary = { input: 'input_cost_per_character', output: 'output_cost_per_character', unit: 'per 1M characters', inputScale: PER_MILLION, outputScale: PER_MILLION };
const AUDIO_TOKENS: Primary = { input: 'input_cost_per_audio_token', output: 'output_cost_per_audio_token', unit: 'per 1M audio tokens', inputScale: TOKEN, outputScale: TOKEN };
const SECONDS: Primary = { input: 'input_cost_per_second', output: 'output_cost_per_second', unit: 'per minute of audio', inputScale: 60, outputScale: 60 };
const IMAGES: Primary = { input: 'input_cost_per_image', output: 'output_cost_per_image', unit: 'per image', inputScale: 1, outputScale: 1 };
const REQUESTS: Primary = { input: 'cost_per_request', unit: 'per request', inputScale: 1 };

/** Which price leads the cell, by model type, first one set wins. The cost sort in src/services/models/model_admin_listing.py uses the same order. */
const PRIMARY_BY_MODE: Record<string, Primary[]> = {
  audio_speech: [CHARACTERS, AUDIO_TOKENS, SECONDS, TOKENS, REQUESTS],
  audio_transcription: [SECONDS, AUDIO_TOKENS, TOKENS, REQUESTS],
  image_generation: [IMAGES, TOKENS, REQUESTS],
};
const DEFAULT_PRIMARY: Primary[] = [TOKENS, AUDIO_TOKENS, CHARACTERS, SECONDS, IMAGES, REQUESTS];

export function summarizeModelCost(mode: string, modelInfo: object | null | undefined): ModelCostSummary {
  const info = (modelInfo || {}) as Record<string, unknown>;
  const rates: ModelCostRate[] = [];
  for (const rate of RATE_FIELDS) {
    const value = readPrice(info, rate.field);
    if (value == null) continue;
    const label = mode === 'realtime' && (rate.field === 'input_cost_per_token' || rate.field === 'output_cost_per_token')
      ? `Text ${rate.label.toLowerCase()}` : rate.label;
    rates.push({ label, value: `${fmtUnitPrice(value * rate.scale)} ${rate.unit}` });
  }
  const multiplier = readPrice(info, 'batch_price_multiplier');
  if (multiplier != null) rates.push({ label: 'Batch', value: `${Number((multiplier * 100).toFixed(2))}% of standard` });

  const order = PRIMARY_BY_MODE[mode] || DEFAULT_PRIMARY;
  for (const primary of order) {
    const input = readPrice(info, primary.input);
    const output = primary.output ? readPrice(info, primary.output) : null;
    if (input == null && output == null) continue;
    const inputText = input == null ? '—' : fmtUnitPrice(input * primary.inputScale);
    const text = output == null ? inputText : `${inputText} / ${fmtUnitPrice(output * (primary.outputScale ?? 1))}`;
    const free = (input ?? 0) === 0 && (output ?? 0) === 0;
    return { kind: 'priced', primary: free ? 'Free' : text, unit: unitLine(mode, info, primary), rates };
  }
  return { kind: 'unset', rates };
}

function unitLine(mode: string, info: Record<string, unknown>, primary: Primary): string {
  const parts = [mode === 'realtime' && primary === TOKENS ? 'per 1M text tokens' : primary.unit];
  if (primary === TOKENS) {
    const cached = readPrice(info, 'input_cost_per_token_cache_hit');
    if (cached != null) parts.push(`cached ${fmtUnitPrice(cached * TOKEN)}`);
    const audioIn = readPrice(info, 'input_cost_per_audio_token');
    const audioOut = readPrice(info, 'output_cost_per_audio_token');
    if (audioIn != null || audioOut != null) {
      const audio = [audioIn, audioOut].map((value) => (value == null ? '—' : fmtUnitPrice(value * TOKEN))).join(' / ');
      parts.push(`audio ${audio}`);
    }
  }
  return parts.join(' · ');
}
