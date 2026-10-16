import { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { summarizeModelCost, type ModelCostRate } from '../../lib/modelCost';

const TOOLTIP_OFFSET_PX = 8;
const TOOLTIP_WIDTH_PX = 264;
const VIEWPORT_PADDING_PX = 12;

type TooltipPosition = { left: number; top: number };

function CostTooltip({ anchorRef, tooltipId, note, rates }: {
  anchorRef: React.RefObject<HTMLDivElement | null>;
  tooltipId: string;
  note: string;
  rates: ModelCostRate[];
}) {
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState<TooltipPosition | null>(null);

  useLayoutEffect(() => {
    const update = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const height = tooltipRef.current?.offsetHeight ?? 0;
      const maxLeft = window.innerWidth - VIEWPORT_PADDING_PX - TOOLTIP_WIDTH_PX;
      const left = Math.max(VIEWPORT_PADDING_PX, Math.min(rect.left, maxLeft));
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

  return createPortal(
    <div
      ref={tooltipRef}
      id={tooltipId}
      role="tooltip"
      className="pointer-events-none fixed z-[100] rounded-lg bg-gray-900 px-3 py-2.5 text-left shadow-lg"
      style={{ width: TOOLTIP_WIDTH_PX, left: position?.left ?? -9999, top: position?.top ?? -9999 }}
    >
      {rates.length > 0 ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
          {rates.map((rate) => (
            <div key={`${rate.label}-${rate.value}`} className="contents">
              <dt className="text-gray-400">{rate.label}</dt>
              <dd className="text-right tabular-nums text-white">{rate.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <p className={`text-[11px] leading-snug text-gray-400 ${rates.length > 0 ? 'mt-2' : ''}`}>{note}</p>
    </div>,
    document.body,
  );
}

export default function ModelCostCell({ mode, modelInfo }: { mode: string; modelInfo: object | null | undefined }) {
  const summary = summarizeModelCost(mode, modelInfo);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const handlers = {
    onMouseEnter: () => setOpen(true),
    onMouseLeave: () => setOpen(false),
    onFocus: () => setOpen(true),
    onBlur: () => setOpen(false),
  };

  if (summary.kind === 'unset') {
    return <>
      <div ref={anchorRef} tabIndex={0} aria-describedby={open ? tooltipId : undefined} {...handlers}
        className="inline-flex cursor-default items-center gap-1.5 whitespace-nowrap rounded text-xs font-medium text-amber-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-hidden="true" />Not set
      </div>
      {open ? <CostTooltip anchorRef={anchorRef} tooltipId={tooltipId} rates={summary.rates}
        note="No standard price set. Requests to this model are recorded at $0." /> : null}
    </>;
  }

  return <>
    <div ref={anchorRef} tabIndex={0} aria-describedby={open ? tooltipId : undefined} {...handlers}
      className="max-w-36 cursor-default rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary">
      <div className="whitespace-nowrap text-sm font-medium tabular-nums text-gray-900">{summary.primary}</div>
      <div className="truncate text-xs text-gray-400">{summary.unit}</div>
    </div>
    {open ? <CostTooltip anchorRef={anchorRef} tooltipId={tooltipId} rates={summary.rates}
      note="What the provider charges the gateway. Customer prices come from tiers." /> : null}
  </>;
}
