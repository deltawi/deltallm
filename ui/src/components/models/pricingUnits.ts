// The form keeps token prices per token, the unit the API stores. The page
// shows them per 1M tokens. The conversion moves the decimal point in the text,
// so no binary rounding changes a saved price.

const MAX_EXPONENT = 100;

const DECIMAL = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;

/** Moves the decimal point of a decimal text. Returns null when the text is not a number. */
export function shiftDecimal(value: string, places: number): string | null {
  const text = value.trim();
  if (!text) return '';
  const match = DECIMAL.exec(text);
  if (!match) return null;
  const [, sign, whole = '', fraction = '', exponent = '0'] = match;
  if (!whole && !fraction) return null;
  // A price never needs a large exponent. This also stops "1e9999999999" from
  // building a string that is too long.
  if (Math.abs(Number(exponent)) > MAX_EXPONENT) return null;
  const digits = `${whole}${fraction}`;
  const point = whole.length + Number(exponent) + places;
  let shifted: string;
  if (point <= 0) shifted = `0.${'0'.repeat(-point)}${digits}`;
  else if (point >= digits.length) shifted = `${digits}${'0'.repeat(point - digits.length)}`;
  else shifted = `${digits.slice(0, point)}.${digits.slice(point)}`;
  return `${sign === '-' ? '-' : ''}${normalize(shifted)}`;
}

function normalize(value: string): string {
  const [rawWhole, rawFraction = ''] = value.split('.');
  const whole = rawWhole.replace(/^0+(?=\d)/, '') || '0';
  const fraction = rawFraction.replace(/0+$/, '');
  return fraction ? `${whole}.${fraction}` : whole;
}

/** Per-token price text -> per-1M-tokens text. Text that is not a number stays as it is. */
export function perMillionFromPerToken(value: string): string {
  return shiftDecimal(value, 6) ?? value;
}

/** Per-1M-tokens price text -> per-token text. Text that is not a number stays as it is. */
export function perTokenFromPerMillion(value: string): string {
  return shiftDecimal(value, -6) ?? value;
}

/** True when the text is blank or a plain decimal number such as "2.5" or "2.5e-6". */
export function isPriceText(value: string): boolean {
  const text = value.trim();
  return !text || (shiftDecimal(text, 0) !== null && !text.startsWith('-'));
}

/**
 * The first price field whose text is not a number, or null. It checks the
 * given fields, or every key with "cost_per_" when no fields are given.
 */
export function firstInvalidPriceField<T extends object>(values: T, fields?: readonly (keyof T & string)[]): string | null {
  const record = values as Record<string, unknown>;
  const keys = fields ?? Object.keys(record).filter((field) => field.includes('cost_per_'));
  for (const field of keys) {
    const value = record[field];
    if (typeof value === 'string' && !isPriceText(value)) return field;
  }
  return null;
}
