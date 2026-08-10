import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { CalendarDays } from 'lucide-react';

import {
  REPORTING_RANGE_OPTIONS,
  compactReportingRangeSelection,
  resolveReportingRange,
  todayIsoDate,
  validateCustomReportingRange,
  type CompactReportingRangeSelection,
  type ReportingRangeKey,
  type ReportingRangeSelectionKey,
} from '../lib/reportingRange';

interface ReportingRangeControlProps {
  value: ReportingRangeSelectionKey;
  startDate?: string;
  endDate?: string;
  onPresetChange: (key: ReportingRangeKey) => void;
  onCustomApply?: (startDate: string, endDate: string) => void;
  allowCustom?: boolean;
  disabled?: boolean;
  ariaLabel: string;
  desktopBreakpoint?: 'md' | 'xl';
  customLabel?: string;
}

const breakpointClasses = {
  md: {
    root: 'w-full md:w-auto',
    desktop: 'hidden md:flex',
    compact: 'flex md:hidden',
  },
  xl: {
    root: 'w-full xl:w-auto',
    desktop: 'hidden xl:flex',
    compact: 'flex xl:hidden',
  },
} as const;

export default function ReportingRangeControl({
  value,
  startDate,
  endDate,
  onPresetChange,
  onCustomApply,
  allowCustom = false,
  disabled = false,
  ariaLabel,
  desktopBreakpoint = 'md',
  customLabel,
}: ReportingRangeControlProps) {
  const fallbackRange = resolveReportingRange('30d');
  const initialStartDate = startDate ?? fallbackRange.startDate ?? '';
  const initialEndDate = endDate ?? fallbackRange.endDate ?? '';
  const [customOpen, setCustomOpen] = useState(false);
  const [draftStartDate, setDraftStartDate] = useState(initialStartDate);
  const [draftEndDate, setDraftEndDate] = useState(initialEndDate);
  const [customTouched, setCustomTouched] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const startInputRef = useRef<HTMLInputElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const dialogId = useId();
  const dialogDescriptionId = useId();
  const maxDate = todayIsoDate();
  const validationError = validateCustomReportingRange(draftStartDate, draftEndDate);
  const visibility = breakpointClasses[desktopBreakpoint];

  const restoreOpenerFocus = useCallback(() => {
    const opener = openerRef.current;
    if (!opener) return;
    window.requestAnimationFrame(() => opener.focus());
  }, []);

  const closeCustom = useCallback((restoreFocus = false) => {
    setCustomOpen(false);
    setDraftStartDate(initialStartDate);
    setDraftEndDate(initialEndDate);
    setCustomTouched(false);
    if (restoreFocus) restoreOpenerFocus();
  }, [initialEndDate, initialStartDate, restoreOpenerFocus]);

  useEffect(() => {
    if (!customOpen) return;

    startInputRef.current?.focus();
    const handlePointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        closeCustom(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeCustom(true);
      }
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [closeCustom, customOpen]);

  const openCustom = (opener?: HTMLElement) => {
    openerRef.current = opener ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    setDraftStartDate(initialStartDate);
    setDraftEndDate(initialEndDate);
    setCustomTouched(false);
    setCustomOpen(true);
  };

  const handleSelection = (selection: CompactReportingRangeSelection, opener?: HTMLElement) => {
    if (selection === 'custom-current') return;
    if (selection === 'custom-action') {
      openCustom(opener);
      return;
    }
    closeCustom(false);
    onPresetChange(selection);
  };

  const handleApply = () => {
    if (validationError || !onCustomApply) return;
    onCustomApply(draftStartDate, draftEndDate);
    setCustomOpen(false);
    setCustomTouched(false);
    restoreOpenerFocus();
  };

  return (
    <div ref={rootRef} className={`relative min-w-0 ${visibility.root}`}>
      <div
        className={`${visibility.desktop} rounded-lg border border-gray-200 bg-gray-100 p-1`}
        role="group"
        aria-label={ariaLabel}
      >
        {REPORTING_RANGE_OPTIONS.map((option) => {
          const selected = value === option.key && !customOpen;
          return (
            <button
              key={option.key}
              type="button"
              aria-pressed={selected}
              disabled={disabled}
              onClick={() => handleSelection(option.key)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50 ${
                selected ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
              }`}
            >
              {option.shortLabel}
            </button>
          );
        })}
        {allowCustom && (
          <button
            type="button"
            aria-pressed={value === 'custom' || customOpen}
            aria-expanded={customOpen}
            aria-controls={dialogId}
            disabled={disabled}
            onClick={(event) => openCustom(event.currentTarget)}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50 ${
              value === 'custom' || customOpen ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
            }`}
          >
            <CalendarDays className="h-3.5 w-3.5" />
            Custom
          </button>
        )}
      </div>

      <label className={`${visibility.compact} min-w-0 items-center gap-2`}>
        <span className="shrink-0 text-sm font-medium text-gray-600">Period</span>
        <select
          value={compactReportingRangeSelection(value, customOpen)}
          disabled={disabled}
          aria-expanded={customOpen}
          aria-controls={allowCustom ? dialogId : undefined}
          onChange={(event) => handleSelection(
            event.target.value as CompactReportingRangeSelection,
            event.currentTarget,
          )}
          className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400"
        >
          {REPORTING_RANGE_OPTIONS.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
          {allowCustom && value === 'custom' && (
            <option value="custom-current">Custom · {customLabel ?? 'applied range'}</option>
          )}
          {allowCustom && <option value="custom-action">{value === 'custom' ? 'Edit custom range…' : 'Custom range…'}</option>}
        </select>
      </label>

      {allowCustom && customOpen && (
        <div
          id={dialogId}
          role="dialog"
          aria-label="Custom reporting range"
          aria-describedby={dialogDescriptionId}
          className="absolute left-0 z-30 mt-2 w-full min-w-[18rem] rounded-xl border border-gray-200 bg-white p-4 shadow-xl sm:left-auto sm:right-0 sm:w-[25rem]"
        >
          <div className="mb-3">
            <p className="text-sm font-semibold text-gray-900">Custom reporting range</p>
            <p id={dialogDescriptionId} className="mt-0.5 text-xs text-gray-500">Dates are inclusive and use UTC.</p>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium text-gray-600">
              Start date
              <input
                ref={startInputRef}
                type="date"
                value={draftStartDate}
                max={maxDate}
                onChange={(event) => {
                  setDraftStartDate(event.target.value);
                  setCustomTouched(true);
                }}
                className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-normal text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
            <label className="text-xs font-medium text-gray-600">
              End date
              <input
                type="date"
                value={draftEndDate}
                max={maxDate}
                onChange={(event) => {
                  setDraftEndDate(event.target.value);
                  setCustomTouched(true);
                }}
                className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-normal text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </label>
          </div>

          <div className="mt-3 min-h-5 text-xs text-rose-600" role={customTouched && validationError ? 'alert' : undefined}>
            {customTouched && validationError ? validationError : null}
          </div>

          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => closeCustom(true)}
              className="rounded-lg px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={Boolean(validationError)}
              onClick={handleApply}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-blue-300"
            >
              Apply range
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
