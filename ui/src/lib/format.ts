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
