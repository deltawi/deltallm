import { CheckCircle2 } from 'lucide-react';
import Card from '../Card';
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
    <Card title="Routing Override">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Override the default shuffle only when you need something more specific</h4>
          <p className="mt-1 text-xs text-slate-500">The group already works with shuffle routing. Use this only for fallback order, weighted traffic, or other advanced routing behavior.</p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-xs text-slate-600">
          Recommended order: validate the override, save a draft if needed, then publish it when you want this group to stop using default shuffle.
        </div>

        {!hasMembers && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">
            Add at least one eligible deployment before validating or publishing a policy.
          </div>
        )}

        <PolicyGuidedEditor
          values={guidedPolicy}
          onChange={onGuidedPolicyChange}
          strategyOptions={[...ROUTE_GROUP_STRATEGY_OPTIONS]}
          memberOptions={memberIds}
        />

        <div>
          <p className="mb-1 text-xs font-medium text-slate-500">Effective policy preview</p>
          <pre className="max-h-56 overflow-auto rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-mono">{guidedPreview}</pre>
        </div>

        <details open={showAdvancedJson} className="rounded-xl border border-slate-200 px-3 py-3">
          <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900" onClick={(event) => {
            event.preventDefault();
            onToggleAdvancedJson();
          }}>
            Advanced JSON
          </summary>
          {showAdvancedJson && (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-slate-500">Use this only for fields that are not covered by the guided form.</p>
              <textarea
                value={policyText}
                onChange={(event) => onPolicyTextChange(event.target.value)}
                className="w-full h-64 px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          )}
        </details>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onValidate}
            disabled={isPolicyBusy || !hasMembers}
            className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-50"
          >
            {policyAction === 'validate' ? 'Validating...' : 'Validate'}
          </button>
          <button
            type="button"
            onClick={onSaveDraft}
            disabled={isPolicyBusy || !hasMembers}
            className="px-3 py-2 text-sm rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-50"
          >
            {policyAction === 'save-draft' ? 'Saving...' : 'Save Draft'}
          </button>
          <button
            type="button"
            onClick={onPublish}
            disabled={isPolicyBusy || !hasMembers}
            className="px-3 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {policyAction === 'publish-json' ? 'Publishing...' : 'Publish'}
          </button>
        </div>

        {policyMessage && (
          <div className="flex items-start gap-2 rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-sm text-green-700">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{policyMessage}</span>
          </div>
        )}
        {policyError && <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{policyError}</div>}
      </div>
    </Card>
  );
}
