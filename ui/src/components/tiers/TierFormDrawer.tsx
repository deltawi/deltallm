import { X } from 'lucide-react';
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
  if (!open) return null;

  const inputClassName = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500';

  return (
    <div className="fixed inset-0 z-50 flex">
      <button
        type="button"
        aria-label="Close tier form"
        disabled={saving}
        className="flex-1 bg-black/20 disabled:cursor-default"
        onClick={onClose}
      />
      <div className="flex w-full max-w-[460px] shrink-0 flex-col border-l border-gray-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">{title}</h2>
            <p className="mt-0.5 text-xs text-gray-500">Define the reusable package admins can assign to organizations.</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {error ? (
            <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Tier key</label>
            <input
              value={values.tier_key}
              onChange={(event) => onChange({ ...values, tier_key: event.target.value })}
              placeholder="enterprise"
              disabled={saving}
              className={inputClassName}
            />
            <p className="mt-1 text-xs text-gray-400">Stable identifier used in logs, spend metadata, and assignment history.</p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Display name</label>
            <input
              value={values.name}
              onChange={(event) => onChange({ ...values, name: event.target.value })}
              placeholder="Enterprise"
              disabled={saving}
              className={inputClassName}
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Description</label>
            <textarea
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
              <span className="block text-xs text-gray-500">Disabled tiers stay visible but cannot be used for new rollout.</span>
            </span>
            <input
              type="checkbox"
              checked={values.enabled}
              onChange={(event) => onChange({ ...values, enabled: event.target.checked })}
              disabled={saving}
              className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
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
            type="button"
            onClick={onSubmit}
            disabled={saving}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? 'Saving...' : submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
