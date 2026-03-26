type RateLimitValue = number | null | undefined;

type RateLimitSummaryProps = {
  rpm_limit?: RateLimitValue;
  tpm_limit?: RateLimitValue;
  rph_limit?: RateLimitValue;
  rpd_limit?: RateLimitValue;
  tpd_limit?: RateLimitValue;
  visibleCount?: number;
};

type RateLimitItem = {
  key: string;
  label: string;
  value: number;
};

function formatRateLimitValue(value: number): string {
  return Number(value).toLocaleString();
}

function buildRateLimitItems({
  rpm_limit,
  tpm_limit,
  rph_limit,
  rpd_limit,
  tpd_limit,
}: RateLimitSummaryProps): RateLimitItem[] {
  return [
    { key: 'rpm_limit', label: 'RPM', value: rpm_limit ?? null },
    { key: 'tpm_limit', label: 'TPM', value: tpm_limit ?? null },
    { key: 'rph_limit', label: 'RPH', value: rph_limit ?? null },
    { key: 'rpd_limit', label: 'RPD', value: rpd_limit ?? null },
    { key: 'tpd_limit', label: 'TPD', value: tpd_limit ?? null },
  ].filter((item): item is RateLimitItem => item.value != null);
}

function RateLimitChip({ label, value }: { label: string; value: number }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-700">
      <span>{formatRateLimitValue(value)}</span>
      <span className="text-gray-400">{label}</span>
    </span>
  );
}

export default function RateLimitSummary(props: RateLimitSummaryProps) {
  const items = buildRateLimitItems(props);
  const visibleCount = props.visibleCount ?? 2;
  const visibleItems = items.slice(0, visibleCount);
  const hiddenItems = items.slice(visibleCount);
  const hoverLabel = hiddenItems.map((item) => `${formatRateLimitValue(item.value)} ${item.label}`).join('\n');

  if (items.length === 0) {
    return <span className="text-xs text-gray-400">—</span>;
  }

  return (
    <div className="flex max-w-[240px] flex-wrap items-center gap-1.5">
      {visibleItems.map((item) => (
        <RateLimitChip key={item.key} label={item.label} value={item.value} />
      ))}
      {hiddenItems.length > 0 ? (
        <div className="group relative inline-flex">
          <span
            tabIndex={0}
            title={hoverLabel}
            className="inline-flex cursor-default items-center rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-500"
          >
            +{hiddenItems.length} more
          </span>
          <div className="pointer-events-none absolute left-1/2 top-full z-20 hidden w-max min-w-[160px] max-w-[220px] -translate-x-1/2 rounded-lg border border-gray-200 bg-gray-900 px-3 py-2 text-left shadow-lg group-hover:block group-focus-within:block">
            <div className="space-y-1">
              {hiddenItems.map((item) => (
                <div key={item.key} className="flex items-center justify-between gap-3 text-[11px] text-white">
                  <span className="font-medium">{item.label}</span>
                  <span className="text-gray-200">{formatRateLimitValue(item.value)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
