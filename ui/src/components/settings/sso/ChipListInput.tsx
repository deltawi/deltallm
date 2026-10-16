import { useState } from 'react';
import { X } from 'lucide-react';

/** A list of short values shown as chips. Enter or a comma adds the typed text with `add`. */
export default function ChipListInput({
  id,
  values,
  onChange,
  add,
  placeholder,
  ariaDescribedBy,
}: {
  id: string;
  values: string[];
  onChange: (values: string[]) => void;
  add: (current: string[], raw: string) => string[];
  placeholder?: string;
  ariaDescribedBy?: string;
}) {
  const [draft, setDraft] = useState('');
  const commit = () => {
    if (!draft.trim()) return;
    onChange(add(values, draft));
    setDraft('');
  };
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1.5 focus-within:bg-white focus-within:ring-2 focus-within:ring-brand-primary">
      {values.map((value) => (
        <span key={value} className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-0.5 font-mono text-xs text-gray-800 ring-1 ring-gray-200">
          {value}
          <button type="button" onClick={() => onChange(values.filter((item) => item !== value))} aria-label={`Remove ${value}`} className="text-gray-400 hover:text-gray-700">
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <input
        id={id}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ',') {
            event.preventDefault();
            commit();
          } else if (event.key === 'Backspace' && !draft && values.length) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={commit}
        placeholder={values.length ? '' : placeholder}
        aria-describedby={ariaDescribedBy}
        className="min-w-[8rem] flex-1 bg-transparent px-1 py-0.5 font-mono text-xs focus:outline-none"
      />
    </div>
  );
}
