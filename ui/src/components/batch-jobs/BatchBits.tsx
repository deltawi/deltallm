import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, Copy, MoreHorizontal } from 'lucide-react';
import type { BatchProgress, BatchTone } from '../../lib/batchJobs';

const TONE_TEXT: Record<BatchTone, string> = {
  ok: 'text-emerald-700',
  bad: 'text-rose-700',
  run: 'text-blue-700',
  warn: 'text-amber-700',
  idle: 'text-gray-500',
};

const TONE_DOT: Record<BatchTone, string> = {
  ok: 'bg-emerald-600',
  bad: 'bg-rose-600',
  run: 'bg-blue-600 motion-safe:animate-pulse',
  warn: 'bg-amber-500',
  idle: 'bg-gray-300',
};

/** A dot and a word. The word carries the meaning, so color is never the only signal. */
export function StatusText({ label, tone }: { label: string; tone: BatchTone }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[12.5px] font-medium ${TONE_TEXT[tone]}`}>
      <span aria-hidden="true" className={`h-[7px] w-[7px] shrink-0 rounded-full ${TONE_DOT[tone]}`} />
      {label}
    </span>
  );
}

const SEGMENTS = [
  { key: 'done', className: 'bg-emerald-600', label: 'done' },
  { key: 'failed', className: 'bg-rose-600', label: 'failed' },
  { key: 'running', className: 'bg-blue-600', label: 'running' },
  { key: 'cancelled', className: 'bg-gray-400', label: 'cancelled' },
] as const;

/** One stacked bar: done, failed, running and cancelled lines. Waiting lines are the empty track. */
export function ProgressBar({ progress, className = 'h-1.5 w-[92px]' }: { progress: BatchProgress; className?: string }) {
  const total = Math.max(1, progress.total);
  return (
    <span
      role="img"
      aria-label={`${progress.percent}% finished: ${progress.done.toLocaleString()} done, ${progress.failed.toLocaleString()} failed, ${progress.running.toLocaleString()} running, ${progress.waiting.toLocaleString()} waiting`}
      className={`flex shrink-0 overflow-hidden rounded-full bg-gray-100 ${className}`}
    >
      {SEGMENTS.map((segment) => {
        const value = progress[segment.key];
        return value > 0 ? (
          <i key={segment.key} className={`block h-full ${segment.className}`} style={{ width: `${(value / total) * 100}%` }} />
        ) : null;
      })}
    </span>
  );
}

export function ProgressLegend({ progress, short = false }: { progress: BatchProgress; short?: boolean }) {
  const rows: Array<[string, number, string]> = [
    ['bg-emerald-600', progress.done, 'done'],
    ['bg-rose-600', progress.failed, 'failed'],
    ['bg-blue-600', progress.running, 'running'],
    ['bg-gray-200', progress.waiting, 'waiting'],
  ];
  if (progress.cancelled > 0) rows.push(['bg-gray-400', progress.cancelled, 'cancelled']);
  return (
    <span className="flex flex-wrap gap-x-3.5 gap-y-1 text-xs tabular-nums text-gray-600">
      {rows.map(([swatch, value, label]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className={`h-2 w-2 rounded-sm ${swatch}`} />
          {value.toLocaleString()}
          {short ? <span className="sr-only">{label}</span> : ` ${label}`}
        </span>
      ))}
    </span>
  );
}

export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={(event) => {
        event.stopPropagation();
        navigator.clipboard?.writeText(value).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => undefined,
        );
      }}
      className="inline-flex rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
    </button>
  );
}

export function EndpointTag({ label }: { label: string }) {
  return <span className="rounded bg-gray-100 px-1.5 py-px text-[11px] font-medium text-gray-600">{label}</span>;
}

export interface ActionMenuItem {
  label: string;
  onSelect: () => void;
  danger?: boolean;
  hint?: string;
}

/** A small "more actions" menu. Escape and a click outside close it, and focus goes back to the button. */
export function ActionMenu({ label, items, trigger, ghost = false }: { label: string; items: ActionMenuItem[]; trigger?: ReactNode; ghost?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (restoreFocus: boolean) => {
      setOpen(false);
      if (restoreFocus) buttonRef.current?.focus();
    };
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) close(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        close(true);
      }
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey, true);
    ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open]);

  if (items.length === 0) return null;
  return (
    <span ref={ref} className="relative inline-flex" onClick={(event) => event.stopPropagation()}>
      <button
        ref={buttonRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className={`inline-flex items-center justify-center rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${ghost ? '' : 'border border-gray-200 bg-white'}`}
      >
        {trigger ?? <MoreHorizontal className="h-4 w-4" aria-hidden="true" />}
      </button>
      {open && (
        <div
          role="menu"
          aria-label={label}
          onBlur={(event) => {
            // Tab out of the menu closes it.
            if (!ref.current?.contains(event.relatedTarget as Node | null)) setOpen(false);
          }}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
            event.preventDefault();
            const entries = Array.from(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
            const index = entries.indexOf(document.activeElement as HTMLElement);
            const next = event.key === 'ArrowDown' ? index + 1 : index - 1;
            entries[(next + entries.length) % entries.length]?.focus();
          }}
          className="absolute right-0 top-full z-30 mt-1 grid w-56 rounded-lg border border-gray-200 bg-white p-1 text-left shadow-lg"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
              className={`rounded-md px-2 py-1.5 text-left text-xs hover:bg-gray-50 focus:bg-gray-50 focus:outline-none ${item.danger ? 'text-rose-700' : 'text-gray-800'}`}
            >
              {item.label}
              {item.hint && <span className="block text-[11px] text-gray-500">{item.hint}</span>}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
