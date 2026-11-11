import { useId, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { PanelCard } from '../../admin/shells';

export function FieldLabel({ htmlFor, label, required = false, action }: {
  htmlFor?: string;
  label: string;
  required?: boolean;
  action?: ReactNode;
}) {
  return (
    <div className="mb-1 flex items-center justify-between gap-2">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-gray-600">
        {label}
        {required ? <span className="ml-0.5 text-red-500" aria-hidden="true">*</span> : null}
      </label>
      {action}
    </div>
  );
}

export function FieldError({ id, message }: { id?: string; message?: string | null }) {
  if (!message) return null;
  return <p id={id} className="mt-1 text-xs text-red-600">{message}</p>;
}

export function FormPanel({ title, description, actions, children }: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <PanelCard>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {description ? <p className="mt-0.5 text-xs text-gray-500">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <div className="mt-4 space-y-4">{children}</div>
    </PanelCard>
  );
}

/** A text button that shows or hides more fields in place. */
export function Disclosure({ label, children, defaultOpen = false, forceOpen = false }: {
  label: string;
  children: ReactNode;
  defaultOpen?: boolean;
  /** Keeps the content open, for example while a field in it has an error. */
  forceOpen?: boolean;
}) {
  const [userOpen, setOpen] = useState(defaultOpen);
  const open = userOpen || forceOpen;
  const id = useId();
  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
        className="inline-flex items-center gap-1 rounded text-xs font-medium text-gray-600 hover:text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      >
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
        {label}
      </button>
      {open ? <div id={id} className="mt-3 space-y-4">{children}</div> : null}
    </div>
  );
}

/** A card of optional settings. Each row opens its fields in place. */
export function SettingsList({ title, description, children }: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      <div className="px-5 pb-3 pt-5">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        {description ? <p className="mt-0.5 text-xs text-gray-500">{description}</p> : null}
      </div>
      <ul className="divide-y divide-gray-100 border-t border-gray-100">{children}</ul>
    </section>
  );
}

export function SettingsRow({ title, description, value, hasError = false, children }: {
  title: string;
  description: string;
  /** The current state in a few words, e.g. "None" or "2 rules". */
  value: string;
  /** A field in the row has an error: the row stays open so the error shows. */
  hasError?: boolean;
  children: ReactNode;
}) {
  const [userOpen, setOpen] = useState(false);
  const open = userOpen || hasError;
  const id = useId();
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((current) => !current)}
        className="flex w-full items-center gap-4 px-5 py-3 text-left hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-gray-900">{title}</span>
          <span className="block truncate text-xs text-gray-500">{description}</span>
        </span>
        <span className={`hidden shrink-0 text-xs sm:inline ${hasError ? 'text-red-600' : 'text-gray-400'}`}>{hasError ? 'Check the fields' : value}</span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      {open ? <div id={id} role="region" aria-label={title} className="space-y-4 px-5 pb-5 pt-1">{children}</div> : null}
    </li>
  );
}
