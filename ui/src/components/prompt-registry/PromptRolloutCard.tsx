import Card from '../Card';
import type { PromptLabel, PromptVersion } from '../../lib/api';

interface LabelForm {
  label: string;
  version: string;
  require_approval: boolean;
  approved_by: string;
}

interface PromptRolloutCardProps {
  versions: PromptVersion[];
  labels: PromptLabel[];
  labelForm: LabelForm;
  assigningLabel: boolean;
  onLabelFormChange: (next: LabelForm) => void;
  onAssignLabel: () => void;
}

export default function PromptRolloutCard({
  versions,
  labels,
  labelForm,
  assigningLabel,
  onLabelFormChange,
  onAssignLabel,
}: PromptRolloutCardProps) {
  const hasVersions = versions.length > 0;

  return (
    <Card title="3. Validate & Register">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Give a version a stable rollout handle</h4>
          <p className="mt-1 text-xs text-slate-500">Labels like `production` and `staging` are the stable names consuming pages will reference when they bind this prompt.</p>
        </div>

        <div className="space-y-3 rounded-xl border border-slate-200 p-4">
          <div>
            <div className="text-sm font-semibold text-slate-900">Promote label</div>
            <div className="mt-1 text-xs text-slate-500">Pick a version and give it a stable label. Group pages will bind to that label later.</div>
          </div>

          {!hasVersions && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">
              Create a version first. Labels only point to immutable versions.
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Label</label>
              <input
                value={labelForm.label}
                onChange={(event) => onLabelFormChange({ ...labelForm, label: event.target.value })}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Version</label>
              <select
                value={labelForm.version}
                onChange={(event) => onLabelFormChange({ ...labelForm, version: event.target.value })}
                disabled={!hasVersions}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
              >
                <option value="">Select version</option>
                {versions.map((version) => (
                  <option key={version.prompt_version_id} value={version.version}>
                    v{version.version} ({version.status})
                  </option>
                ))}
              </select>
            </div>
          </div>

          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={labelForm.require_approval}
              onChange={(event) => onLabelFormChange({ ...labelForm, require_approval: event.target.checked })}
              className="mt-0.5 rounded border-gray-300"
            />
            <span>
              Require approval for this promotion
              <span className="block text-xs text-gray-500">Use this when production label moves need explicit sign-off.</span>
            </span>
          </label>

          {labelForm.require_approval && (
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Approved By</label>
              <input
                value={labelForm.approved_by}
                onChange={(event) => onLabelFormChange({ ...labelForm, approved_by: event.target.value })}
                placeholder="name@company.com"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
              />
            </div>
          )}

          <div className="flex justify-end">
            <button
              type="button"
              onClick={onAssignLabel}
              disabled={assigningLabel || !hasVersions}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {assigningLabel ? 'Registering...' : 'Register Label'}
            </button>
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
            {labels.length === 0 ? (
              <span>No labels assigned yet.</span>
            ) : (
              labels.map((label) => (
                <div key={label.prompt_label_id}>
                  <strong>{label.label}</strong> {'->'} v{label.version}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
