import { useRef, type KeyboardEvent } from 'react';
import type { LucideIcon } from 'lucide-react';

export interface SegmentedItem<T extends string> {
  value: T;
  label: string;
  icon?: LucideIcon;
  /** Shown as the button title, for a short label that needs more text. */
  title?: string;
}

interface SegmentedControlProps<T extends string> {
  items: readonly SegmentedItem<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
  className?: string;
}

/**
 * A single-choice group of buttons (radio group). Arrow keys move the choice,
 * like native radio buttons. The track is 32 px: control-segment inside p-0.5.
 */
export default function SegmentedControl<T extends string>({
  items,
  value,
  onChange,
  label,
  disabled = false,
  className = '',
}: SegmentedControlProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const selectedIndex = Math.max(0, items.findIndex((item) => item.value === value));

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1
      : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + items.length) % items.length;
    onChange(items[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={`flex w-fit max-w-full flex-wrap items-center gap-1 rounded-lg border border-gray-200 bg-white p-0.5 ${className}`.trim()}
    >
      {items.map((item, index) => {
        const Icon = item.icon;
        const checked = item.value === value;
        return (
          <button
            key={item.value}
            ref={(node) => { refs.current[index] = node; }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={index === selectedIndex ? 0 : -1}
            title={item.title}
            disabled={disabled}
            onClick={() => onChange(item.value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={`control-segment inline-flex items-center gap-1.5 rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-60 ${
              checked ? 'bg-brand-primary text-brand-on-primary shadow-sm' : 'text-gray-600 hover:bg-gray-50'
            }`}
          >
            {Icon ? <Icon className="h-3.5 w-3.5" aria-hidden="true" /> : null}
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
