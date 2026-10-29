import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Download } from 'lucide-react';
import { errorMessage } from '../lib/errorMessage';

export interface ExportOption<Key extends string> {
  key: Key;
  label: string;
}

interface ExportMenuProps<Key extends string> {
  options: ReadonlyArray<ExportOption<Key>>;
  /** One line under the options, for example the row limit. */
  note: string;
  onExport: (key: Key) => Promise<void>;
}

export default function ExportMenu<Key extends string>({ options, note, onExport }: ExportMenuProps<Key>) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const run = (key: Key) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    onExport(key)
      .then(() => setOpen(false), (reason: unknown) => setError(errorMessage(reason, 'Export failed.')))
      .finally(() => setBusy(false));
  };
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="true"
        disabled={busy}
        onClick={() => setOpen((current) => !current)}
        className="control-md inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white text-gray-700 shadow-sm hover:bg-gray-50 hover:text-gray-900 disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      >
        <Download className="h-4 w-4 text-gray-500" aria-hidden="true" />
        {busy ? 'Exporting' : 'Export'}
        <ChevronDown className="h-3 w-3 text-gray-500" aria-hidden="true" />
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 grid w-60 rounded-lg border border-gray-200 bg-white p-1 shadow-lg">
          {options.map((option) => (
            <button
              key={option.key}
              type="button"
              disabled={busy}
              onClick={() => run(option.key)}
              className="rounded-md px-2 py-1.5 text-left text-xs text-gray-800 hover:bg-gray-50 disabled:opacity-60"
            >
              {option.label}
            </button>
          ))}
          <p className="px-2 py-1.5 text-[11.5px] text-gray-500">{note}</p>
          {error && <p role="alert" className="px-2 pb-1.5 text-[11.5px] text-rose-700">{error}</p>}
        </div>
      )}
    </div>
  );
}
