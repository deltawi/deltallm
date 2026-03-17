import { CheckCircle2, Code2, ListChecks } from 'lucide-react';
import PolicyGuidedEditor from '../PolicyGuidedEditor';
import { ROUTE_GROUP_STRATEGY_OPTIONS, type PolicyAction, type PolicyGuidedValues } from '../../lib/routeGroups';

interface RouteGroupPolicyEditorCardProps {
  guidedPolicy: PolicyGuidedValues;
  memberIds: string[];
  guidedPreview: string;
  policyText: string;
  policyMessage: string | null;
  policyError: string | null;
  isPolicyBusy: boolean;
  policyAction: PolicyAction;
  showAdvancedJson: boolean;
  hasMembers: boolean;
  onToggleAdvancedJson: () => void;
  onGuidedPolicyChange: (next: PolicyGuidedValues) => void;
  onPolicyTextChange: (value: string) => void;
  onValidate: () => void;
  onSaveDraft: () => void;
  onPublish: () => void;
}

export default function RouteGroupPolicyEditorCard({
  guidedPolicy,
  memberIds,
  guidedPreview,
  policyText,
  policyMessage,
  policyError,
  isPolicyBusy,
  policyAction,
  showAdvancedJson,
  hasMembers,
  onToggleAdvancedJson,
  onGuidedPolicyChange,
  onPolicyTextChange,
  onValidate,
  onSaveDraft,
  onPublish,
}: RouteGroupPolicyEditorCardProps) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4">
      {/* Header row */}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Routing Policy</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            Override the default shuffle only when you need specific routing behavior — weighted splits, ordered fallback, or rate-limit awareness.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            onClick={onValidate}
            disabled={isPolicyBusy || !hasMembers}
            className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            {policyAction === 'validate' ? 'Validating…' : 'Validate'}
          </button>
          <button
            type="button"
            onClick={onSaveDraft}
            disabled={isPolicyBusy || !hasMembers}
            className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            {policyAction === 'save-draft' ? 'Saving…' : 'Save Draft'}
          </button>
          <button
            type="button"
            onClick={onPublish}
            disabled={isPolicyBusy || !hasMembers}
            className="rounded-xl bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {policyAction === 'publish-json' ? 'Publishing…' : 'Publish'}
          </button>
        </div>
      </div>

      {!hasMembers && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
          Add at least one eligible deployment to the Models tab before validating or publishing a policy.
        </div>
      )}

      {/* Guided editor */}
      <PolicyGuidedEditor
        values={guidedPolicy}
        onChange={onGuidedPolicyChange}
        strategyOptions={[...ROUTE_GROUP_STRATEGY_OPTIONS]}
        memberOptions={memberIds}
      />

      {/* Preview + JSON toggle */}
      <div className="mt-4 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-gray-500">Effective policy preview</p>
          <button
            type="button"
            onClick={onToggleAdvancedJson}
            className="inline-flex items-center gap-1 text-xs text-gray-400 hover:text-gray-700"
          >
            {showAdvancedJson ? <><ListChecks className="h-3.5 w-3.5" /> Hide JSON</> : <><Code2 className="h-3.5 w-3.5" /> Edit raw JSON</>}
          </button>
        </div>

        {showAdvancedJson ? (
          <textarea
            value={policyText}
            onChange={(e) => onPolicyTextChange(e.target.value)}
            rows={8}
            className="w-full rounded-xl border border-gray-200 bg-gray-950 px-4 py-3 font-mono text-xs text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        ) : (
          <pre className="max-h-52 overflow-auto rounded-xl border border-gray-200 bg-gray-950 px-4 py-3 text-xs font-mono text-gray-100">
            {guidedPreview}
          </pre>
        )}
      </div>

      {/* Feedback */}
      {policyMessage && (
        <div className="mt-3 flex items-start gap-2 rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-700">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{policyMessage}</span>
        </div>
      )}
      {policyError && (
        <div className="mt-3 rounded-xl border border-red-100 bg-red-50 px-3 py-2.5 text-sm text-red-700">{policyError}</div>
      )}
    </div>
  );
}
