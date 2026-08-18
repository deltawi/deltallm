import { X } from 'lucide-react';
import { useEffect, useId, useRef } from 'react';
import type { TierFormValues } from '../../lib/tiers';

type TierFormDrawerProps = {
  open: boolean;
  title: string;
  values: TierFormValues;
  saving: boolean;
  error: string | null;
  submitLabel: string;
  onChange: (values: TierFormValues) => void;
  onClose: () => void;
  onSubmit: () => void;
};

export default function TierFormDrawer({
  open,
  title,
  values,
  saving,
  error,
  submitLabel,
  onChange,
  onClose,
  onSubmit,
}: TierFormDrawerProps) {
  const dialogRef = useRef<HTMLFormElement | null>(null);
  const closeRef = useRef(onClose);
  const savingRef = useRef(saving);
  const titleId = useId();
  const tierKeyId = useId();
  const nameId = useId();
  const descriptionId = useId();

  useEffect(() => {
    closeRef.current = onClose;
    savingRef.current = saving;
  }, [onClose, saving]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const focusableElements = () => {
      const selector = 'button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])';
      return Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(selector) || [])
        .filter((element) => !element.hasAttribute('disabled') && element.offsetParent !== null);
    };
    requestAnimationFrame(() => focusableElements()[0]?.focus());

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !savingRef.current) {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = focusableElements();
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [open]);

  if (!open) return null;

  const inputClassName = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500';

  return (
    <div className="fixed inset-0 z-50 flex">
      <button
        type="button"
        aria-label="Close tier form"
        disabled={saving}
        className="flex-1 bg-black/20 disabled:cursor-default"
        onClick={onClose}
      />
      <form
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex w-full max-w-[460px] shrink-0 flex-col border-l border-gray-200 bg-white shadow-xl"
        onSubmit={(event) => {
          event.preventDefault();
          if (!saving) onSubmit();
        }}
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 id={titleId} className="text-base font-semibold text-gray-900">{title}</h2>
            <p className="mt-0.5 text-xs text-gray-500">Define the reusable package admins can assign to organizations.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            aria-label="Close tier form"
            className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {error ? (
            <div role="alert" className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <div>
            <label htmlFor={tierKeyId} className="mb-1 block text-sm font-medium text-gray-700">Tier key</label>
            <input
              id={tierKeyId}
              value={values.tier_key}
              onChange={(event) => onChange({ ...values, tier_key: event.target.value })}
              placeholder="enterprise"
              disabled={saving}
              className={inputClassName}
            />
            <p className="mt-1 text-xs text-gray-400">Stable identifier used in logs, spend metadata, and assignment history.</p>
          </div>

          <div>
            <label htmlFor={nameId} className="mb-1 block text-sm font-medium text-gray-700">Display name</label>
            <input
              id={nameId}
              value={values.name}
              onChange={(event) => onChange({ ...values, name: event.target.value })}
              placeholder="Enterprise"
              disabled={saving}
              className={inputClassName}
            />
          </div>

          <div>
            <label htmlFor={descriptionId} className="mb-1 block text-sm font-medium text-gray-700">Description</label>
            <textarea
              id={descriptionId}
              value={values.description}
              onChange={(event) => onChange({ ...values, description: event.target.value })}
              rows={4}
              placeholder="High-throughput package with committed capacity."
              disabled={saving}
              className={inputClassName}
            />
          </div>

          <label className={`flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 ${saving ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'}`}>
            <span>
              <span className="block text-sm font-medium text-gray-800">Enabled</span>
              <span className="block text-xs text-gray-500">End or disable every live and scheduled assignment before disabling a tier.</span>
            </span>
            <input
              type="checkbox"
              checked={values.enabled}
              onChange={(event) => onChange({ ...values, enabled: event.target.checked })}
              disabled={saving}
              className="h-4 w-4 rounded border-gray-300 text-brand-primary-ink focus:ring-brand-primary"
            />
          </label>
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-brand-primary px-4 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
          >
            {saving ? 'Saving...' : submitLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
