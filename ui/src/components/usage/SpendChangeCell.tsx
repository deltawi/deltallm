import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowDown, ArrowUp } from 'lucide-react';
import type { UsageWindow } from '../../lib/api/usageReports';
import { fmtSpendPrecise } from '../../lib/format';
import { periodChange, type PeriodChange } from '../../lib/usageExplorer';

const TOOLTIP_OFFSET_PX = 8;
const TOOLTIP_WIDTH_PX = 248;
const VIEWPORT_PADDING_PX = 12;
const HOVER_LEAVE_DELAY_MS = 150;

function shortDay(value: string): string {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${value}T00:00:00Z`),
  );
}

function dayRange(start: string | null, end: string | null): string | null {
  if (!start || !end) return null;
  return start === end ? shortDay(start) : `${shortDay(start)} to ${shortDay(end)}`;
}

function signedSpend(value: number): string {
  if (value === 0) return '$0';
  return `${value > 0 ? '+' : '−'}${fmtSpendPrecise(Math.abs(value))}`;
}

function spendChangeSummary(current: number, previous: number, change: PeriodChange | null): string {
  if (previous === 0) return current === 0 ? 'No spend in either period' : 'New: no spend in the previous period';
  if (!change) return 'No percent change: the previous period has a negative spend total';
  if (change.direction === 'flat') return 'Same spend as the previous period';
  return `Spend ${change.direction} ${change.size} on the previous period`;
}

function ChangeTooltip({ anchorRef, tooltipId, current, previous, change, period, onPointerInside }: {
  anchorRef: React.RefObject<HTMLSpanElement | null>;
  onPointerInside: (inside: boolean) => void;
  tooltipId: string;
  current: number;
  previous: number;
  change: PeriodChange | null;
  period: UsageWindow | null;
}) {
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    const update = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const height = tooltipRef.current?.offsetHeight ?? 0;
      const maxLeft = window.innerWidth - VIEWPORT_PADDING_PX - TOOLTIP_WIDTH_PX;
      const left = Math.max(VIEWPORT_PADDING_PX, Math.min(rect.right - TOOLTIP_WIDTH_PX, maxLeft));
      const above = rect.bottom + TOOLTIP_OFFSET_PX + height > window.innerHeight - VIEWPORT_PADDING_PX
        && rect.top - TOOLTIP_OFFSET_PX - height > VIEWPORT_PADDING_PX;
      setPosition({ left, top: above ? rect.top - TOOLTIP_OFFSET_PX - height : rect.bottom + TOOLTIP_OFFSET_PX });
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [anchorRef]);

  if (typeof document === 'undefined') return null;

  const previousRange = period ? dayRange(period.previous_start_date, period.previous_end_date) : null;
  const currentRange = period ? dayRange(period.start_date, period.end_date) : null;
  const changeText = change && change.direction !== 'flat'
    ? `${signedSpend(current - previous)} · ${change.direction === 'up' ? '↑' : '↓'} ${change.size}`
    : signedSpend(current - previous);

  return createPortal(
    <div
      ref={tooltipRef}
      id={tooltipId}
      role="tooltip"
      // The pointer can move onto the card to read it (WCAG 1.4.13); clicks on it do not reach the row filter.
      onMouseEnter={() => onPointerInside(true)}
      onMouseLeave={() => onPointerInside(false)}
      onClick={(event) => event.stopPropagation()}
      className="fixed z-[100] cursor-default rounded-lg bg-gray-900 px-3 py-2.5 text-left shadow-lg"
      style={{ width: TOOLTIP_WIDTH_PX, left: position?.left ?? -9999, top: position?.top ?? -9999 }}
    >
      <p className="mb-1.5 text-xs font-semibold text-white">Spend vs the previous period</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-gray-400">{previousRange ?? 'Previous period'}</dt>
        <dd className="text-right tabular-nums text-white">{fmtSpendPrecise(previous)}</dd>
        <dt className="text-gray-400">{currentRange ?? 'This period'}</dt>
        <dd className="text-right tabular-nums text-white">{fmtSpendPrecise(current)}</dd>
      </dl>
      <div className="mt-1.5 flex justify-between gap-3 border-t border-gray-700 pt-1.5 text-xs">
        <span className="text-gray-400">Change</span>
        <span className="whitespace-nowrap tabular-nums text-white">
          {previous === 0 ? (current === 0 ? '$0' : 'New') : changeText}
        </span>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Spend change against the previous window for one top-table row. Neutral gray
 * with an arrow: more spend is often just more traffic, not an error.
 */
export default function SpendChangeCell({ current, previous, period }: {
  current: number;
  previous: number | null;
  period: UsageWindow | null;
}) {
  const anchorRef = useRef<HTMLSpanElement | null>(null);
  const tooltipId = useId();
  // Hover and focus are tracked apart so leaving with the pointer keeps a focused card open.
  const [dismissed, setDismissed] = useState(false);
  const [hovered, setHoveredNow] = useState(false);
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Leaving is delayed a little so the pointer can cross the gap between the value and its card.
  const setHovered = (inside: boolean) => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
    leaveTimer.current = null;
    if (inside) {
      setHoveredNow(true);
      setDismissed(false);
    } else {
      leaveTimer.current = setTimeout(() => setHoveredNow(false), HOVER_LEAVE_DELAY_MS);
    }
  };
  useEffect(() => () => {
    if (leaveTimer.current) clearTimeout(leaveTimer.current);
  }, []);
  const [focused, setFocused] = useState(false);
  const open = (hovered || focused) && !dismissed;

  useEffect(() => {
    if (!open) return undefined;
    // Escape closes the card wherever focus is, including a hover-open card while the row has focus.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDismissed(true);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);

  if (previous === null) return <span className="text-gray-400">—</span>;

  const change = periodChange(current, previous);
  const summary = spendChangeSummary(current, previous, change);
  let content;
  if (previous === 0) {
    content = <span className="text-xs font-medium text-gray-500">{current === 0 ? '0%' : 'New'}</span>;
  } else if (!change) {
    // A negative previous total (legacy signed rows) has no meaningful ratio; the hover card still shows both amounts.
    content = <span className="text-gray-400">—</span>;
  } else if (change.direction === 'flat') {
    content = <span className="font-medium text-gray-500">0%</span>;
  } else {
    const Arrow = change.direction === 'up' ? ArrowUp : ArrowDown;
    content = (
      <span className="inline-flex items-center gap-0.5 font-medium text-gray-700">
        <Arrow className="h-3 w-3" aria-hidden="true" />
        {change.size}
      </span>
    );
  }

  return (
    <span
      ref={anchorRef}
      tabIndex={0}
      aria-describedby={open ? tooltipId : undefined}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => { setFocused(true); setDismissed(false); }}
      onBlur={() => setFocused(false)}
      onKeyDown={(event) => {
        // The row filters on Enter and Space; keys pressed on this cell only drive the card.
        if (event.key === 'Escape') setDismissed(true);
        else event.stopPropagation();
      }}
      className="inline-block rounded tabular-nums focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
    >
      <span aria-hidden="true">{content}</span>
      <span className="sr-only">{summary}</span>
      {open && (
        <ChangeTooltip
          anchorRef={anchorRef}
          tooltipId={tooltipId}
          current={current}
          previous={previous}
          change={change}
          period={period}
          onPointerInside={setHovered}
        />
      )}
    </span>
  );
}
