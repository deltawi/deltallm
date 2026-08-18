import { Save, X } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Tier } from '../../lib/api';
import { isAssignableTierVersion } from '../../lib/tiers';

export type AssignmentForm = {
  assignment_id: string | null;
  tier_id: string;
  tier_version_id: string;
  assignment_type: string;
  enabled: boolean;
  weight: string;
  starts_at: string;
  ends_at: string;
};

type OrganizationTierAssignmentDrawerProps = {
  form: AssignmentForm;
  tierOptions: Tier[];
  versionOptions: Array<{ tier_version_id: string; version_number: number; status: string }>;
  requireActiveVersion: boolean;
  saving: boolean;
  error: string | null;
  onChange: (form: AssignmentForm) => void;
  onClose: () => void;
  onSave: () => void;
};

export default function OrganizationTierAssignmentDrawer({
  form,
  tierOptions,
  versionOptions,
  requireActiveVersion,
  saving,
  error,
  onChange,
  onClose,
  onSave,
}: OrganizationTierAssignmentDrawerProps) {
  const inputClassName = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-500';
  const selectedTier = tierOptions.find((tier) => tier.tier_id === form.tier_id);
  const enabledTierConflict = form.enabled && selectedTier?.enabled === false;

  return (
    <div className="fixed inset-0 z-50 flex">
      <button type="button" aria-label="Close assignment form" onClick={onClose} disabled={saving} className="flex-1 bg-black/20 disabled:cursor-default" />
      <div className="flex w-full max-w-[460px] shrink-0 flex-col border-l border-gray-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">{form.assignment_id ? 'Edit Assignment' : 'Assign Tier'}</h2>
            <p className="mt-0.5 text-xs text-gray-500">Changes invalidate effective organization policy after saving.</p>
          </div>
          <button type="button" onClick={onClose} disabled={saving} className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {error ? <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}
          <Field label="Tier">
            <select value={form.tier_id} onChange={(event) => onChange({ ...form, tier_id: event.target.value, tier_version_id: '' })} disabled={saving} className={inputClassName}>
              <option value="">Select tier</option>
              {tierOptions.map((tier) => (
                <option key={tier.tier_id} value={tier.tier_id} disabled={form.enabled && !tier.enabled}>
                  {tier.name} ({tier.tier_key}){tier.enabled ? '' : ' - disabled'}
                </option>
              ))}
            </select>
            {enabledTierConflict ? <p className="mt-1 text-xs text-red-600">Enabled assignments require an enabled tier.</p> : null}
          </Field>
          <Field label="Version">
            <select value={form.tier_version_id} onChange={(event) => onChange({ ...form, tier_version_id: event.target.value })} disabled={saving} className={inputClassName}>
              <option value="">Use active version</option>
              {versionOptions.map((version) => {
                const optionDisabled = !isAssignableTierVersion(version, requireActiveVersion);
                return (
                  <option key={version.tier_version_id} value={version.tier_version_id} disabled={optionDisabled}>
                    v{version.version_number} {version.status}
                  </option>
                );
              })}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Type">
              <select value={form.assignment_type} onChange={(event) => onChange({ ...form, assignment_type: event.target.value })} disabled={saving} className={inputClassName}>
                <option value="primary">Primary</option>
                <option value="addon">Add-on</option>
                <option value="override">Override</option>
              </select>
            </Field>
            <Field label="Weight">
              <input value={form.weight} onChange={(event) => onChange({ ...form, weight: event.target.value })} disabled={saving} className={inputClassName} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Starts at">
              <input type="datetime-local" value={form.starts_at} onChange={(event) => onChange({ ...form, starts_at: event.target.value })} disabled={saving} className={inputClassName} />
            </Field>
            <Field label="Ends at">
              <input type="datetime-local" value={form.ends_at} onChange={(event) => onChange({ ...form, ends_at: event.target.value })} disabled={saving} className={inputClassName} />
            </Field>
          </div>
          <label className={`flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 ${saving ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'}`}>
            <span className="text-sm font-medium text-gray-800">Enabled</span>
            <input type="checkbox" checked={form.enabled} onChange={(event) => onChange({ ...form, enabled: event.target.checked })} disabled={saving} className="h-4 w-4 rounded border-gray-300 text-brand-primary-ink focus:ring-brand-primary" />
          </label>
        </div>
        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-4">
          <button type="button" onClick={onClose} disabled={saving} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50">Cancel</button>
          <button type="button" onClick={onSave} disabled={saving || enabledTierConflict} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-4 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50">
            <Save className="h-4 w-4" />
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-gray-500">{label}</span>
      {children}
    </label>
  );
}
