import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { Bot, Check, ChevronDown, CornerDownLeft, Search, Server, X } from 'lucide-react';
import type { ProviderPreset } from '../../lib/api';
import { PROVIDER_LOGOS } from '../../lib/providerLogos';
import { providerDisplayName } from '../../lib/providers';
import Button from '../Button';
import { GENERIC_PROVIDER, listProviders, matchRange, modeShortLabel, providerModeSummary } from './providerListing';
import { usePopoverDismiss } from './usePopoverDismiss';

const LOGO_SIZES = {
  sm: { box: 'h-5 w-5 rounded', img: 'h-3 w-3' },
  md: { box: 'h-6 w-6 rounded-md', img: 'h-3.5 w-3.5' },
  lg: { box: 'h-9 w-9 rounded-lg', img: 'h-5 w-5' },
} as const;

export function ProviderLogoMark({ provider, size = 'md' }: { provider: string; size?: keyof typeof LOGO_SIZES }) {
  const logo = PROVIDER_LOGOS[provider];
  const [failed, setFailed] = useState(false);
  const classes = LOGO_SIZES[size];
  const Fallback = provider === GENERIC_PROVIDER || provider === 'sglang' ? Server : Bot;
  return (
    <span className={`inline-flex shrink-0 items-center justify-center border border-gray-200 bg-white ${classes.box}`} aria-hidden="true">
      {logo && !failed
        ? <img src={logo} alt="" loading="lazy" className={`${classes.img} object-contain`} onError={() => setFailed(true)} />
        : <Fallback className={`${classes.img} text-gray-500`} />}
    </span>
  );
}

export function Highlight({ text, query }: { text: string; query: string }) {
  const range = matchRange(text, query);
  if (!range) return <>{text}</>;
  return (
    <>
      {text.slice(0, range[0])}
      <mark className="rounded-sm bg-brand-primary/15 text-inherit">{text.slice(range[0], range[1])}</mark>
      {text.slice(range[1])}
    </>
  );
}

interface ProviderPickerProps {
  id?: string;
  presets: readonly ProviderPreset[];
  value: string;
  mode: string;
  onChange: (provider: string) => void;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
}

/** A searchable provider select: a field button that opens a grouped, filterable list. */
export default function ProviderPicker({ id, presets, value, mode, onChange, disabled = false, invalid = false, describedBy }: ProviderPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const optionId = (provider: string) => `${listId}-${provider}`;
  const listing = useMemo(() => listProviders(presets, query, mode, value), [presets, query, mode, value]);
  const { options, sections, unsupported } = listing;
  const activeOption = options[Math.min(active, options.length - 1)];
  // With no match, the generic provider is the one choice, and Enter picks it.
  const fallbackAvailable = options.length === 0
    && presets.some((preset) => preset.provider === GENERIC_PROVIDER && (!mode || preset.supported_modes.includes(mode)));

  const close = (restoreFocus: boolean) => {
    setOpen(false);
    setQuery('');
    if (restoreFocus) triggerRef.current?.focus();
  };
  usePopoverDismiss(wrapperRef, open, () => close(false));

  const openList = () => {
    if (disabled) return;
    const selectedIndex = listProviders(presets, '', mode, value).options.findIndex((preset) => preset.provider === value);
    setActive(Math.max(0, selectedIndex));
    setOpen(true);
  };

  useEffect(() => {
    if (!open || !activeOption) return;
    const node = listRef.current?.querySelector<HTMLElement>(`[id="${optionId(activeOption.provider)}"]`);
    node?.scrollIntoView?.({ block: 'nearest' });
    // optionId only depends on listId, which never changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeOption]);

  const pick = (provider: string) => {
    onChange(provider);
    close(true);
  };

  const onSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (options.length === 0) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((current) => (Math.min(current, options.length - 1) + step + options.length) % options.length);
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      setActive(event.key === 'Home' ? 0 : Math.max(0, options.length - 1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (activeOption) pick(activeOption.provider);
      else if (fallbackAvailable) pick(GENERIC_PROVIDER);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close(true);
    } else if (event.key === 'Tab') {
      close(false);
    }
  };

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      openList();
    }
  };

  return (
    <div ref={wrapperRef} className="relative">
      <button
        ref={triggerRef}
        id={id}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onClick={() => (open ? close(false) : openList())}
        onKeyDown={onTriggerKeyDown}
        className={`control-field flex w-full items-center gap-2 rounded-lg border bg-white px-3 text-left transition focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500 ${
          open
            ? 'border-blue-400 ring-2 ring-brand-primary/30'
            : invalid
              ? 'border-red-300 focus:ring-red-500/30'
              : 'border-gray-200 hover:border-gray-300 focus:border-blue-400 focus:ring-brand-primary/30'
        }`}
      >
        {value ? <ProviderLogoMark provider={value} size="sm" /> : null}
        <span className={`min-w-0 flex-1 truncate ${value ? 'text-gray-900' : 'text-gray-400'}`}>
          {value ? providerDisplayName(value) : 'Select provider'}
        </span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      {open ? (
        <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-30 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl ring-1 ring-black/5 md:right-auto md:w-[max(100%,25rem)]">
          <div className="flex items-center gap-2 border-b border-gray-100 px-3">
            <Search className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
            <input
              autoFocus
              role="combobox"
              aria-label="Search providers"
              aria-expanded="true"
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={activeOption ? optionId(activeOption.provider) : undefined}
              value={query}
              onChange={(event) => { setQuery(event.target.value); setActive(0); }}
              onKeyDown={onSearchKeyDown}
              placeholder="Search providers..."
              className="control-field min-w-0 flex-1 bg-transparent text-gray-900 placeholder:text-gray-400 focus:outline-none"
            />
            {query ? (
              <button type="button" onClick={() => { setQuery(''); setActive(0); }} aria-label="Clear provider search" className="rounded p-1 text-gray-400 hover:bg-gray-100">
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ) : null}
          </div>
          <div ref={listRef} id={listId} role="listbox" aria-label="Providers" className="max-h-80 overflow-y-auto overscroll-contain p-1.5">
            {sections.map((section) => (
              <div key={section.group.id} role="group" aria-label={section.group.label} className="pb-1">
                <div className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-gray-400" aria-hidden="true">
                  {section.group.label}
                </div>
                {section.presets.map((preset) => {
                  const selected = preset.provider === value;
                  const isActive = preset.provider === activeOption?.provider;
                  return (
                    <div
                      key={preset.provider}
                      id={optionId(preset.provider)}
                      role="option"
                      aria-selected={selected}
                      onMouseDown={(event) => event.preventDefault()}
                      onMouseMove={() => setActive(options.indexOf(preset))}
                      onClick={() => pick(preset.provider)}
                      className={`flex h-10 cursor-pointer items-center gap-2.5 rounded-lg px-2 ${isActive ? 'bg-gray-100' : ''}`}
                    >
                      <ProviderLogoMark provider={preset.provider} />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-gray-900">
                        <Highlight text={providerDisplayName(preset.provider)} query={query} />
                      </span>
                      <span className="hidden truncate text-xs text-gray-400 sm:inline">{providerModeSummary(preset.supported_modes)}</span>
                      <Check className={`h-4 w-4 shrink-0 text-brand-primary ${selected ? '' : 'invisible'}`} aria-hidden="true" />
                    </div>
                  );
                })}
              </div>
            ))}
            {options.length === 0 ? (
              <div className="px-3 py-6 text-center">
                <p className="text-sm text-gray-700">{query.trim() ? `No provider matches "${query.trim()}".` : 'No provider supports this model type.'}</p>
                {fallbackAvailable ? (
                  <>
                    <p className="mt-1 text-xs text-gray-500">Any OpenAI-style API works with the generic provider. Press Enter to use it.</p>
                    <Button size="sm" variant="outline" className="mt-3" onMouseDown={(event) => event.preventDefault()} onClick={() => pick(GENERIC_PROVIDER)}>Use OpenAI-compatible</Button>
                  </>
                ) : null}
              </div>
            ) : null}
            {unsupported.length > 0 ? (
              <p className="mx-2 mt-1 border-t border-gray-100 pb-1 pt-2 text-xs text-gray-400">
                {unsupported.length} {unsupported.length === 1 ? 'provider is' : 'providers are'} not shown because {unsupported.length === 1 ? 'it does' : 'they do'} not support {modeShortLabel(mode)}: {unsupported.map((preset) => providerDisplayName(preset.provider)).join(', ')}.
              </p>
            ) : null}
          </div>
          <div className="hidden items-center gap-3 border-t border-gray-100 bg-gray-50 px-3 py-2 text-[11px] text-gray-500 md:flex" aria-hidden="true">
            <span className="inline-flex items-center gap-1"><kbd className="rounded border border-gray-200 bg-white px-1 font-sans">↑</kbd><kbd className="rounded border border-gray-200 bg-white px-1 font-sans">↓</kbd> move</span>
            <span className="inline-flex items-center gap-1"><kbd className="rounded border border-gray-200 bg-white px-1"><CornerDownLeft className="inline h-3 w-3" /></kbd> select</span>
            <span className="inline-flex items-center gap-1"><kbd className="rounded border border-gray-200 bg-white px-1 font-sans">esc</kbd> close</span>
            <span className="ml-auto">{presets.length} providers</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
