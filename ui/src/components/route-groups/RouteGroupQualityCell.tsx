import { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { RouteGroupMemberQuality } from '../../lib/api';
import {
  formatFirstToken,
  formatOutputSpeed,
  formatQualityScore,
  qualityScore,
  qualityTone,
  sentLastText,
  type QualityTone,
} from '../../lib/routeGroupQuality';

const TOOLTIP_OFFSET_PX = 8;
const TOOLTIP_WIDTH_PX = 272;
const VIEWPORT_PADDING_PX = 12;

const TONE_CLASS: Record<QualityTone, string> = {
  good: 'bg-emerald-50 text-emerald-700',
  warn: 'bg-amber-50 text-amber-700',
  bad: 'bg-red-50 text-red-700',
};

function QualityTooltip({ anchorRef, tooltipId, title, quality }: {
  anchorRef: React.RefObject<HTMLButtonElement | null>;
  tooltipId: string;
  title: string;
  quality: RouteGroupMemberQuality;
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

  const rows: Array<[string, string, boolean]> = [
    ['Calls', String(quality.calls), false],
    ['Provider errors', String(quality.provider_errors), quality.provider_errors > 0],
    ['Bad tool calls', String(quality.bad_tool_calls), quality.bad_tool_calls > 0],
    ['Empty answers', String(quality.empty_answers), quality.empty_answers > 0],
    ['Bad JSON', String(quality.bad_json), quality.bad_json > 0],
    ['First token (avg)', formatFirstToken(quality.avg_first_token_ms), false],
    ['Output speed', formatOutputSpeed(quality.output_tokens_per_second), false],
  ];
  const sentLast = sentLastText(quality.sent_last_reason);

  return createPortal(
    <div
      ref={tooltipRef}
      id={tooltipId}
      role="tooltip"
      className="pointer-events-none fixed z-[100] rounded-lg bg-gray-900 px-3 py-2.5 text-left shadow-lg"
      style={{ width: TOOLTIP_WIDTH_PX, left: position?.left ?? -9999, top: position?.top ?? -9999 }}
    >
      <p className="mb-1.5 text-xs font-semibold text-white">{title}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        {rows.map(([label, value, warn]) => (
          <div key={label} className="contents">
            <dt className="text-gray-400">{label}</dt>
            <dd className={`text-right tabular-nums ${warn ? 'text-amber-300' : 'text-white'}`}>{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-[11px] leading-snug text-gray-400">
        {sentLast ?? 'Failed answers are retried on another model when the group allows retries.'}
      </p>
    </div>,
    document.body,
  );
}

/** Quality score for one group member, with the details on hover, focus or tap. */
export default function RouteGroupQualityCell({ quality, modelLabel, windowLabel }: {
  quality: RouteGroupMemberQuality | undefined;
  modelLabel: string;
  windowLabel: string;
}) {
  const anchorRef = useRef<HTMLButtonElement | null>(null);
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const score = quality ? qualityScore(quality) : null;
  if (!quality || score === null) return <span className="text-xs text-gray-400">No calls yet</span>;

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        aria-describedby={open ? tooltipId : undefined}
        aria-label={`${modelLabel} quality ${formatQualityScore(score)}, ${quality.calls} calls`}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex cursor-default items-center gap-1.5 whitespace-nowrap rounded text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary"
      >
        <span className={`rounded-full px-1.5 py-0.5 font-medium tabular-nums ${TONE_CLASS[qualityTone(score)]}`}>{formatQualityScore(score)}</span>
        <span className="tabular-nums text-gray-500">{quality.calls.toLocaleString()} calls</span>
        {quality.sent_last_reason ? <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">Sent last</span> : null}
      </button>
      {open ? <QualityTooltip anchorRef={anchorRef} tooltipId={tooltipId} title={`${modelLabel} · ${windowLabel}`} quality={quality} /> : null}
    </>
  );
}
