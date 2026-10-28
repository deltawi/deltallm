import { useEffect, useRef, useState } from 'react';
import { Columns3 } from 'lucide-react';
import { MenuNote } from './ChipMenu';

interface ColumnsMenuProps<T extends string> {
  /** Every optional column, in table order. */
  options: ReadonlyArray<{ key: T; label: string }>;
  columns: readonly T[];
  onColumns: (columns: T[]) => void;
  /** Leave out to hide the time format option. */
  showUtc?: boolean;
  onShowUtc?: (value: boolean) => void;
}

const CHECKBOX = 'h-3.5 w-3.5 rounded border-gray-300 text-brand-primary focus:ring-brand-primary';
const OPTION = 'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs text-gray-800 hover:bg-gray-50';

/** The icon at the right end of the table header: extra columns and the time format. */
export default function ColumnsMenu<T extends string>({ options, columns, onColumns, showUtc, onShowUtc }: ColumnsMenuProps<T>) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
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
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-label="Columns"
        title="Columns"
        onClick={() => setOpen((current) => !current)}
        className="inline-grid h-7 w-7 place-items-center rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      >
        <Columns3 className="h-4 w-4" aria-hidden="true" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 grid w-64 rounded-lg border border-gray-200 bg-white p-1 shadow-lg">
          <MenuNote>Extra columns show on wide screens</MenuNote>
          {options.map((column) => (
            <label key={column.key} className={OPTION}>
              <input
                type="checkbox"
                checked={columns.includes(column.key)}
                onChange={(event) =>
                  onColumns(
                    options.map((item) => item.key).filter((key) =>
                      key === column.key ? event.target.checked : columns.includes(key),
                    ),
                  )
                }
                className={CHECKBOX}
              />
              {column.label}
            </label>
          ))}
          {showUtc !== undefined && onShowUtc && (
            <>
              <div className="my-1 border-t border-gray-100" />
              <label className={OPTION}>
                <input type="checkbox" checked={showUtc} onChange={(event) => onShowUtc(event.target.checked)} className={CHECKBOX} />
                Show times in UTC
              </label>
            </>
          )}
          <MenuNote>Saved in this browser</MenuNote>
        </div>
      )}
    </div>
  );
}
