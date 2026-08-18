import { ChevronDown, CircleHelp } from 'lucide-react';
import type { ReactNode } from 'react';
import { useId } from 'react';

type TierEditorAccordionProps = {
  title: string;
  summary: string;
  description: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
};

export function TierEditorAccordion({
  title,
  summary,
  description,
  open,
  onToggle,
  children,
}: TierEditorAccordionProps) {
  const generatedId = useId();
  const triggerId = `tier-editor-trigger-${generatedId}`;
  const panelId = `tier-editor-panel-${generatedId}`;

  return (
    <section className="overflow-visible rounded-xl border border-gray-200 bg-white">
      <button
        id={triggerId}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-4 rounded-xl px-4 py-3 text-left hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand-primary"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-gray-900">{title}</span>
          <span className="mt-0.5 block truncate text-xs text-gray-500">{summary}</span>
        </span>
        <ChevronDown
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>
      {open ? (
        <div
          id={panelId}
          role="region"
          aria-labelledby={triggerId}
          className="space-y-3 border-t border-gray-100 px-4 py-4"
        >
          <p className="text-xs text-gray-500">{description}</p>
          {children}
        </div>
      ) : null}
    </section>
  );
}

type TierFieldProps = {
  id: string;
  label: string;
  help?: string;
  children: ReactNode;
  className?: string;
};

export function TierField({ id, label, help, children, className = '' }: TierFieldProps) {
  return (
    <div className={className}>
      <div className="mb-1 flex min-h-4 items-center gap-1">
        <label htmlFor={id} className="text-xs font-semibold text-gray-500">{label}</label>
        {help ? <TierFieldHelp label={label} help={help} /> : null}
      </div>
      {children}
    </div>
  );
}

export function TierFieldHelp({ label, help }: { label: string; help: string }) {
  const tooltipId = `tier-field-help-${useId()}`;

  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={`${label}: ${help}`}
        aria-describedby={tooltipId}
        className="rounded text-gray-400 hover:text-brand-primary-ink focus:outline-none focus:ring-2 focus:ring-brand-primary"
      >
        <CircleHelp aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
      <span
        id={tooltipId}
        role="tooltip"
        className="pointer-events-none invisible absolute left-0 top-full z-50 mt-2 w-72 max-w-[calc(100vw-2rem)] origin-top-left rounded-lg bg-gray-900 px-3 py-2 text-left text-xs font-normal leading-5 text-white opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {help}
      </span>
    </span>
  );
}
