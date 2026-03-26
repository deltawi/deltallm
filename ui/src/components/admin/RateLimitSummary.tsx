import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

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

type TooltipPosition = {
  left: number;
  top: number;
};

const TOOLTIP_OFFSET_PX = 8;
const TOOLTIP_MAX_WIDTH_PX = 220;
const VIEWPORT_PADDING_PX = 12;

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

function RateLimitTooltip({
  anchorRef,
  items,
  open,
  tooltipId,
}: {
  anchorRef: React.RefObject<HTMLSpanElement | null>;
  items: RateLimitItem[];
  open: boolean;
  tooltipId: string;
}) {
  const [position, setPosition] = useState<TooltipPosition | null>(null);

  useEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }

    const updatePosition = () => {
      if (typeof window === 'undefined') {
        return;
      }
      const anchor = anchorRef.current;
      if (!anchor) {
        return;
      }

      const rect = anchor.getBoundingClientRect();
      const preferredCenter = rect.left + rect.width / 2;
      const minCenter = VIEWPORT_PADDING_PX + TOOLTIP_MAX_WIDTH_PX / 2;
      const maxCenter = window.innerWidth - VIEWPORT_PADDING_PX - TOOLTIP_MAX_WIDTH_PX / 2;
      const clampedCenter = Math.min(Math.max(preferredCenter, minCenter), maxCenter);

      setPosition({
        left: clampedCenter,
        top: rect.bottom + TOOLTIP_OFFSET_PX,
      });
    };

    updatePosition();
    window.addEventListener('resize', updatePosition);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      window.removeEventListener('resize', updatePosition);
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [anchorRef, open]);

  if (!open || !position || typeof document === 'undefined') {
    return null;
  }

  return createPortal(
    <div
      id={tooltipId}
      role="tooltip"
      className="pointer-events-none fixed z-[100] w-max min-w-[160px] max-w-[220px] rounded-lg border border-gray-200 bg-gray-900 px-3 py-2 text-left shadow-lg"
      style={{
        left: `${position.left}px`,
        top: `${position.top}px`,
        transform: 'translateX(-50%)',
      }}
    >
      <div className="space-y-1">
        {items.map((item) => (
          <div key={item.key} className="flex items-center justify-between gap-3 text-[11px] text-white">
            <span className="font-medium">{item.label}</span>
            <span className="text-gray-200">{formatRateLimitValue(item.value)}</span>
          </div>
        ))}
      </div>
    </div>,
    document.body,
  );
}

export default function RateLimitSummary(props: RateLimitSummaryProps) {
  const items = buildRateLimitItems(props);
  const visibleCount = props.visibleCount ?? 2;
  const visibleItems = items.slice(0, visibleCount);
  const hiddenItems = items.slice(visibleCount);
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const tooltipId = useId();
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const hoverLabel = useMemo(
    () => hiddenItems.map((item) => `${formatRateLimitValue(item.value)} ${item.label}`).join('\n'),
    [hiddenItems],
  );

  if (items.length === 0) {
    return <span className="text-xs text-gray-400">—</span>;
  }

  return (
    <div className="flex max-w-[240px] flex-wrap items-center gap-1.5">
      {visibleItems.map((item) => (
        <RateLimitChip key={item.key} label={item.label} value={item.value} />
      ))}
      {hiddenItems.length > 0 ? (
        <>
          <span
            ref={triggerRef}
            tabIndex={0}
            title={hoverLabel}
            aria-describedby={tooltipOpen ? tooltipId : undefined}
            onMouseEnter={() => setTooltipOpen(true)}
            onMouseLeave={() => setTooltipOpen(false)}
            onFocus={() => setTooltipOpen(true)}
            onBlur={() => setTooltipOpen(false)}
            className="inline-flex cursor-default items-center rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-500"
          >
            +{hiddenItems.length} more
          </span>
          <RateLimitTooltip anchorRef={triggerRef} items={hiddenItems} open={tooltipOpen} tooltipId={tooltipId} />
        </>
      ) : null}
    </div>
  );
}
