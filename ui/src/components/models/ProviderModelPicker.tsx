import { useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Check, ChevronDown } from 'lucide-react';
import type { ProviderModelOption } from '../../lib/api';
import { Highlight } from './ProviderPicker';
import { filterModelOptions, modelOptionFacts } from './providerListing';
import { usePopoverDismiss } from './usePopoverDismiss';
import { fieldClass } from './form/formStyles';

interface ProviderModelPickerProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly ProviderModelOption[];
  providerLabel: string;
  placeholder: string;
  loading?: boolean;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
  /** Shown under the list, e.g. how to load the live list. */
  footer?: ReactNode;
}

/**
 * The provider model field. It takes any model ID, and it shows the known
 * models of the provider with their context window and price.
 */
export default function ProviderModelPicker({
  id,
  value,
  onChange,
  options,
  providerLabel,
  placeholder,
  loading = false,
  disabled = false,
  invalid = false,
  describedBy,
  footer,
}: ProviderModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  usePopoverDismiss(wrapperRef, open, () => setOpen(false));

  // An exact match shows the whole list, so a picked model does not hide the others.
  const exact = options.some((option) => option.id === value.trim());
  const visible = useMemo(() => (exact ? [...options] : filterModelOptions(options, value)), [exact, options, value]);
  const highlight = exact ? '' : value;
  const activeOption = active >= 0 ? visible[active] : undefined;
  const showList = open && !disabled && (visible.length > 0 || loading);

  const pick = (option: ProviderModelOption) => {
    onChange(option.id);
    setOpen(false);
    setActive(-1);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) { setOpen(true); return; }
      if (visible.length === 0) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((current) => (current < 0 ? (step > 0 ? 0 : visible.length - 1) : (current + step + visible.length) % visible.length));
    } else if (event.key === 'Enter' && open && activeOption) {
      event.preventDefault();
      pick(activeOption);
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === 'Tab') {
      setOpen(false);
    }
  };

  return (
    <div ref={wrapperRef} className="relative">
      <input
        id={id}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList && activeOption ? `${listId}-${active}` : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        value={value}
        placeholder={placeholder}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
        onChange={(event) => { onChange(event.target.value); setOpen(true); setActive(-1); }}
        onKeyDown={onKeyDown}
        className={`${fieldClass(invalid)} pr-8 ${value ? 'font-mono' : ''}`}
      />
      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
      {showList ? (
        <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-30 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl ring-1 ring-black/5">
          <div className="flex items-center justify-between gap-3 px-3 pb-1 pt-2.5 text-[11px] font-medium text-gray-400" aria-hidden="true">
            <span className="uppercase tracking-wide">Models from {providerLabel}</span>
            <span className="hidden sm:inline">Context · per 1M in / out</span>
          </div>
          <div id={listId} role="listbox" aria-label={`Models from ${providerLabel}`} className="max-h-72 overflow-y-auto overscroll-contain p-1.5 pt-0">
            {loading && visible.length === 0 ? <div className="px-2 py-2 text-sm text-gray-500">Loading models...</div> : null}
            {visible.map((option, index) => {
              const facts = modelOptionFacts(option);
              const selected = option.id === value.trim();
              return (
                <div
                  key={option.id}
                  id={`${listId}-${index}`}
                  role="option"
                  aria-selected={selected}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseMove={() => setActive(index)}
                  onClick={() => pick(option)}
                  className={`flex min-h-9 cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 ${index === active ? 'bg-gray-100' : ''}`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-sm text-gray-900"><Highlight text={option.id} query={highlight} /></span>
                    {option.label && option.label !== option.id ? <span className="block truncate text-xs text-gray-500">{option.label}</span> : null}
                  </span>
                  {facts.context ? <span className="shrink-0 text-xs text-gray-400">{facts.context}</span> : null}
                  {facts.price ? <span className="hidden shrink-0 text-right text-xs tabular-nums text-gray-500 sm:inline">{facts.price}</span> : null}
                  <Check className={`h-4 w-4 shrink-0 text-brand-primary ${selected ? '' : 'invisible'}`} aria-hidden="true" />
                </div>
              );
            })}
          </div>
          {footer ? <div className="border-t border-gray-100 bg-gray-50 px-3 py-2 text-[11px] text-gray-500">{footer}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
