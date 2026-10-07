import { parseOutputTpm } from './outputTpm';

export interface ModelOutputRow { model: string; limit: string }

export function modelOutputRows(limits?: Record<string, number> | null): ModelOutputRow[] {
  return Object.entries(limits || {}).map(([model, limit]) => ({ model, limit: String(limit) }));
}

export function modelOutputPayload(rows: ModelOutputRow[]): Record<string, number> | null {
  if (rows.length > 64) throw new Error('Use at most 64 model output limits.');
  const entries: Array<[string, number]> = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const model = row.model;
    if (!model.trim() || model !== model.trim() || new TextEncoder().encode(model).length > 256
      || model.includes('*') || Array.from(model).some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) {
      throw new Error('Enter an exact model ID, up to 256 bytes, without wildcards or spaces at its ends.');
    }
    if (seen.has(model)) throw new Error(`Only one output limit is allowed for ${model}.`);
    seen.add(model);
    const limit = parseOutputTpm(row.limit);
    if (limit === null) throw new Error(`Enter an output TPM limit for ${model}, or remove the row.`);
    entries.push([model, limit]);
  }
  if (!entries.length) return null;
  const limits = Object.fromEntries(entries);
  const storedBytes = new TextEncoder().encode(JSON.stringify(limits)).length + entries.length * 2 - 1;
  if (storedBytes > 32768) throw new Error('Model output limits exceed 32 KiB. Use shorter model IDs or fewer rows.');
  return limits;
}
