import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Search } from 'lucide-react';
import { FilterChip } from '../usage/UsageFilterChips';

interface ChipMenuProps {
  label: string;
  /** The current value; the chip then shows "Label is value" and a click removes it. */
  value?: string;
  onClear: () => void;
  width?: string;
  /** Hide the "click a name in any row" tip when rows cannot set this filter. */
  rowHint?: boolean;
  children: (close: () => void) => ReactNode;
}

/** A Usage-style "+ Label" chip that opens a small menu. The menu's content mounts only while open. */
export default function ChipMenu({ label, value, onClear, width = 'w-64', rowHint = true, children }: ChipMenuProps) {
  const [open, setOpen] = useState(false);
  // A chip near the right edge opens its menu to the left, so the menu stays on screen.
  const [alignRight, setAlignRight] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open) {
      setAlignRight(false);
      return;
    }
    const chip = ref.current?.getBoundingClientRect();
    const menu = menuRef.current?.getBoundingClientRect();
    const viewport = document.documentElement.clientWidth;
    if (chip && menu && viewport > 0) setAlignRight(chip.left + menu.width > viewport - 8 && chip.right - menu.width >= 8);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open]);

  return (
    <span ref={ref} className="relative" title={value === undefined && rowHint ? 'You can also click a name in any row to filter by it' : undefined}>
      <FilterChip
        label={label}
        value={value}
        active={value !== undefined}
        onClick={() => (value !== undefined ? onClear() : setOpen((current) => !current))}
      />
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={`Filter by ${label.toLowerCase()}`}
          className={`absolute ${alignRight ? 'right-0' : 'left-0'} top-full z-30 mt-1 grid max-h-80 overflow-y-auto rounded-lg border border-gray-200 bg-white p-1 shadow-lg ${width}`}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </span>
  );
}

export function MenuItem({
  children,
  hint,
  onClick,
  swatch,
}: {
  children: ReactNode;
  hint?: ReactNode;
  onClick: () => void;
  swatch?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-gray-800 hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
    >
      {swatch && <i aria-hidden="true" className="h-2 w-2 shrink-0 rounded-sm" style={{ background: swatch }} />}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint && <small className="shrink-0 text-[11px] text-gray-500">{hint}</small>}
    </button>
  );
}

export function MenuSearch({
  value,
  onChange,
  onSubmit,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  placeholder: string;
  label: string;
}) {
  return (
    <form
      className="relative mb-1"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit?.();
      }}
    >
      <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
      <input
        // The menu opens on a click, so focus moves straight to its search box.
        autoFocus
        type="search"
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="control-field-md w-full rounded-md border border-gray-200 pl-7 pr-2 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
      />
    </form>
  );
}

export function MenuNote({ children }: { children: ReactNode }) {
  return <p className="px-2 py-1.5 text-[11.5px] text-gray-500">{children}</p>;
}
