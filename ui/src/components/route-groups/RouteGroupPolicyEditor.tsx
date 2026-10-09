import { Code2, GitBranch, ListChecks } from 'lucide-react';
import Button from '../Button';
import PolicyGuidedEditor from '../PolicyGuidedEditor';
import PolicyPublishControl from './PolicyPublishControl';
import PolicySelectorSummary from './PolicySelectorSummary';
import { effectiveRouteGroupStrategy, routeGroupStrategyLabel, routeGroupStrategyOptions, type PolicyAction, type PolicyGuidedValues } from '../../lib/routeGroups';
import type { RouteGroupMemberDetail, RoutePolicy } from '../../lib/api';

export interface RouteGroupPolicyEditorProps {
  routeGroupId: string;
  workloadMode: string;
  guidedPolicy: PolicyGuidedValues;
  members: RouteGroupMemberDetail[];
  guidedPreview: string;
  simulationPolicy: Record<string, unknown> | null;
  canSimulate: boolean;
  canWrite: boolean;
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
  policies: RoutePolicy[];
  loadingPolicies: boolean;
  hasPoliciesError: boolean;
  defaultStrategy?: string | null;
}

export default function RouteGroupPolicyEditor(props: RouteGroupPolicyEditorProps) {
  const published = props.policies.find((policy) => policy.status === 'published');
  const draft = props.policies.find((policy) => policy.status === 'draft');
  const activeStrategy = effectiveRouteGroupStrategy(published?.policy_json ?? null, props.defaultStrategy);
  const activeStrategyLabel = activeStrategy ? routeGroupStrategyLabel(activeStrategy) : 'Group default';
  const policyUnavailable = props.hasPoliciesError || props.loadingPolicies;
  const disabled = !props.canWrite || props.isPolicyBusy || !props.hasMembers || policyUnavailable;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-primary/20 bg-brand-primary-soft px-4 py-3">
      <div className="flex items-center gap-3">
        <GitBranch className="h-5 w-5 shrink-0 text-brand-primary-ink" />
        <div>
          <p className="text-sm font-semibold text-gray-900">{props.hasPoliciesError ? 'Live policy unavailable' : props.loadingPolicies ? 'Loading live policy…' : `${activeStrategyLabel}${published ? ` · Published v${published.version}` : activeStrategy ? ' · Group default' : ''}`}</p>
          <p className="text-xs text-gray-500">{props.hasPoliciesError ? 'Retry the page to load the saved policy.' : 'Draft edits do not change live routing until you publish.'}</p>
        </div>
      </div>
      {published && <PolicySelectorSummary policy={published.policy_json} label={`Active selector (v${published.version})`} />}
    </div>
    <section aria-label="Routing policy editor" className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-4 sm:px-5">
        <div>
          <div className="flex items-center gap-2"><h2 className="text-base font-semibold text-gray-900">Routing policy</h2><span className="rounded-full bg-brand-primary-soft px-2 py-0.5 text-xs font-medium text-brand-primary-ink">{draft ? `Draft v${draft.version}` : 'Draft editor'}</span></div>
          <p className="mt-1 text-xs text-gray-500">Choose how requests use the group deployments.</p>
        </div>
        <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-1" aria-label="Policy editor mode">
          <Button size="sm" variant="ghost" aria-pressed={!props.showAdvancedJson} disabled={props.isPolicyBusy} className={!props.showAdvancedJson ? 'bg-white text-brand-primary-ink' : ''} onClick={() => props.showAdvancedJson && props.onToggleAdvancedJson()}><ListChecks className="h-3.5 w-3.5" />Guided</Button>
          <Button size="sm" variant="ghost" aria-pressed={props.showAdvancedJson} disabled={props.isPolicyBusy} className={props.showAdvancedJson ? 'bg-white text-brand-primary-ink' : ''} onClick={() => !props.showAdvancedJson && props.onToggleAdvancedJson()}><Code2 className="h-3.5 w-3.5" />JSON</Button>
        </div>
      </header>
      <div className="space-y-4 p-4 sm:p-5">
        {!props.hasMembers && <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Add at least one deployment in Models before you validate or publish.</p>}
        {!props.canWrite && <p className="rounded-lg bg-gray-50 p-3 text-sm text-gray-600">You can view this policy. You do not have permission to change it.</p>}
        {!props.showAdvancedJson ? <>
          <fieldset disabled={!props.canWrite || props.isPolicyBusy || !props.canSimulate || policyUnavailable}>
            <PolicyGuidedEditor routeGroupId={props.routeGroupId} values={props.guidedPolicy} onChange={props.onGuidedPolicyChange} strategyOptions={routeGroupStrategyOptions(props.guidedPolicy.strategy)} memberOptions={props.members} workloadMode={props.workloadMode} />
          </fieldset>
          <details className="rounded-lg border border-gray-200 p-3"><summary className="cursor-pointer text-xs font-medium text-gray-600">Effective policy preview</summary><pre className="mt-3 overflow-x-auto rounded-lg bg-gray-950 p-4 font-mono text-xs leading-5 text-green-400">{props.guidedPreview}</pre></details>
        </> : <textarea aria-label="Policy JSON (import or export)" value={props.policyText} disabled={!props.canWrite || props.isPolicyBusy || policyUnavailable} onChange={(event) => props.onPolicyTextChange(event.target.value)} rows={14} spellCheck={false} className="w-full rounded-lg bg-gray-950 p-4 font-mono text-sm leading-6 text-green-400 focus:outline-none focus:ring-2 focus:ring-brand-primary" />}
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 bg-gray-50 p-4 sm:px-5">
        <div className="min-w-0 flex-1 text-xs text-gray-500">
          {props.policyError ? <p role="alert" className="text-red-700">{props.policyError}</p> : props.policyMessage ? <p role="status" className="text-emerald-700">{props.policyMessage}</p> : <p>Changes take effect when you publish.</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="ghost" disabled={disabled} loading={props.policyAction === 'validate'} onClick={props.onValidate}>Validate</Button>
          <Button size="sm" variant="secondary" disabled={disabled} loading={props.policyAction === 'save-draft'} onClick={props.onSaveDraft}>Save draft</Button>
          <PolicyPublishControl policy={props.simulationPolicy} activePolicy={published?.policy_json ?? null} busy={props.isPolicyBusy} disabled={disabled || !props.canSimulate} onPublish={props.onPublish} />
        </div>
      </footer>
    </section>
  </div>;
}
