/**
 * Format a number using compact notation (K, M, B) for large values.
 * Numbers below 1,000 are returned as-is with locale formatting.
 */
export function fmtCompact(n: number | null | undefined): string {
  if (n == null) return '0';
  const v = Number(n);
  const tiers: [number, string][] = [
    [1_000_000_000, 'B'],
    [1_000_000, 'M'],
    [1_000, 'K'],
  ];
  for (let i = 0; i < tiers.length; i++) {
    const [threshold, suffix] = tiers[i];
    if (v >= threshold) {
      const scaled = v / threshold;
      const decimals = scaled >= 100 ? 0 : 1;
      const rounded = Number(scaled.toFixed(decimals));
      if (rounded >= 1000 && i > 0) {
        const [upperThreshold, upperSuffix] = tiers[i - 1];
        const upperScaled = v / upperThreshold;
        return `${upperScaled.toFixed(1).replace(/\.0$/, '')}${upperSuffix}`;
      }
      return `${scaled.toFixed(decimals).replace(/\.0$/, '')}${suffix}`;
    }
  }
  return v.toLocaleString();
}

function signedDollar(value: number, formattedAbsoluteValue: string): string {
  return `${value < 0 ? '-$' : '$'}${formattedAbsoluteValue}`;
}

/** Format spend for cards and tables while preserving meaningful micro-spend. */
export function fmtSpendValue(n: number | null | undefined): string {
  const value = Number(n ?? 0);
  if (!Number.isFinite(value) || value === 0) return '$0';

  const absoluteValue = Math.abs(value);
  if (absoluteValue >= 1_000) return signedDollar(value, fmtCompact(absoluteValue));
  if (absoluteValue >= 1) {
    return signedDollar(value, absoluteValue.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }));
  }
  if (absoluteValue >= 0.01) {
    return signedDollar(value, absoluteValue.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }));
  }
  if (absoluteValue >= 0.00000001) {
    return signedDollar(value, absoluteValue.toLocaleString('en-US', {
      minimumFractionDigits: 4,
      maximumFractionDigits: 8,
    }));
  }
  return signedDollar(value, absoluteValue.toExponential(2));
}

/** Format chart ticks without rounding a non-zero micro-spend value down to zero. */
export function fmtSpendAxis(n: number | null | undefined): string {
  const value = Number(n ?? 0);
  if (!Number.isFinite(value) || value === 0) return '$0';

  const absoluteValue = Math.abs(value);
  if (absoluteValue >= 1_000) return signedDollar(value, fmtCompact(absoluteValue));
  if (absoluteValue >= 1) {
    return signedDollar(value, absoluteValue.toLocaleString('en-US', { maximumFractionDigits: 2 }));
  }
  if (absoluteValue >= 0.01) {
    return signedDollar(value, absoluteValue.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }));
  }
  if (absoluteValue >= 0.000001) {
    return signedDollar(value, absoluteValue.toLocaleString('en-US', { maximumFractionDigits: 6 }));
  }
  return signedDollar(value, absoluteValue.toExponential(1));
}

/** Format tooltip spend with more precision than the chart axis. */
export function fmtSpendPrecise(n: number | null | undefined): string {
  const value = Number(n ?? 0);
  if (!Number.isFinite(value) || value === 0) return '$0.0000';

  const absoluteValue = Math.abs(value);
  if (absoluteValue < 0.00000001) {
    return signedDollar(value, absoluteValue.toExponential(2));
  }
  if (absoluteValue >= 1) {
    return signedDollar(value, absoluteValue.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    }));
  }
  return signedDollar(value, absoluteValue.toLocaleString('en-US', {
    minimumFractionDigits: 4,
    maximumFractionDigits: 8,
  }));
}

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

function parseUtcDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const hasTimezone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(trimmed);
  const date = new Date(hasTimezone ? trimmed : `${trimmed}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function toUtcDateTimeLocalInputValue(value: string | Date | null | undefined): string {
  const date = parseUtcDate(value);
  if (!date) return '';
  return [
    date.getUTCFullYear(),
    '-',
    pad2(date.getUTCMonth() + 1),
    '-',
    pad2(date.getUTCDate()),
    'T',
    pad2(date.getUTCHours()),
    ':',
    pad2(date.getUTCMinutes()),
  ].join('');
}

export function dateTimeLocalUtcInputToIso(value: string): string | null {
  if (!value.trim()) return null;
  const date = new Date(`${value.trim()}Z`);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

export function defaultMonthlyResetUtcInputValue(): string {
  const nextMonth = new Date();
  nextMonth.setUTCMonth(nextMonth.getUTCMonth() + 1, 1);
  nextMonth.setUTCHours(0, 0, 0, 0);
  return toUtcDateTimeLocalInputValue(nextMonth);
}

export function fmtUtcDateTime(value: string | null | undefined): string {
  const date = parseUtcDate(value);
  if (!date) return '';
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    timeZoneName: 'short',
  });
}
