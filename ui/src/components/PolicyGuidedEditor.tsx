import {
  supportsContextRouting,
  withGuidedPolicyStrategy,
  type PolicyGuidedValues,
  type PolicyMemberOption,
} from '../lib/routeGroups';
import PolicySelectorEditor from './route-groups/PolicySelectorEditor';
import RouteGroupSelectorEditor from './route-groups/RouteGroupSelectorEditor';
import PolicyStrategyPicker from './route-groups/PolicyStrategyPicker';
import PolicyMemberPicker from './route-groups/PolicyMemberPicker';

interface PolicyGuidedEditorProps {
  routeGroupId?: string;
  values: PolicyGuidedValues;
  onChange: (next: PolicyGuidedValues) => void;
  strategyOptions: string[];
  memberOptions: PolicyMemberOption[];
  workloadMode: string;
}

export default function PolicyGuidedEditor({
  routeGroupId,
  values,
  onChange,
  strategyOptions,
  memberOptions,
  workloadMode,
}: PolicyGuidedEditorProps) {
  const contextRoutingSupported = supportsContextRouting(workloadMode);
  const updateValue = <K extends keyof PolicyGuidedValues>(
    key: K,
    value: PolicyGuidedValues[K],
  ) => onChange({ ...values, [key]: value });

  return (
    <div className="space-y-6">
      <PolicyStrategyPicker value={values.strategy} options={strategyOptions} onChange={(strategy) => onChange(withGuidedPolicyStrategy(values, strategy))} />
      <PolicyMemberPicker values={values} members={memberOptions} onChange={onChange} />
      <details className="rounded-xl border border-gray-200 p-3" open={values.selector.state === 'enabled' || undefined}>
        <summary className="cursor-pointer text-sm font-semibold text-gray-800">Advanced settings</summary>
        <div className="mt-4 space-y-3">
      <details className="rounded-xl border border-gray-200 p-3" open={values.selector.state === 'enabled' || undefined}>
        <summary className="cursor-pointer text-sm font-semibold text-gray-800">Model selector (optional)</summary>
        <div className="mt-4">
          {routeGroupId
            ? <RouteGroupSelectorEditor routeGroupId={routeGroupId} values={values} onChange={onChange} members={memberOptions} workloadMode={workloadMode} />
            : <PolicySelectorEditor values={values} onChange={onChange} members={memberOptions} workloadMode={workloadMode} />}
        </div>
      </details>
      <details className="rounded-xl border border-slate-200 px-3 py-3">
        <summary className="cursor-pointer list-none select-none text-sm font-semibold text-slate-900">
          Timeouts and retry
        </summary>
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Global Timeout (ms)</span>
            <input
              type="number"
              min="1"
              step="1"
              value={values.timeoutMs}
              onChange={(event) => updateValue('timeoutMs', event.target.value)}
              placeholder="10000"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Additional retries</span>
            <input
              type="number"
              min="0"
              step="1"
              value={values.retryMaxAttempts}
              onChange={(event) => updateValue('retryMaxAttempts', event.target.value)}
              placeholder="2"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
          </label>
          <label className="space-y-1 md:col-span-2">
            <span className="text-xs font-medium text-slate-700">Retryable Errors</span>
            <input
              value={values.retryableErrors}
              onChange={(event) => updateValue('retryableErrors', event.target.value)}
              placeholder="timeout,rate_limit,generic"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
            <span className="block text-xs text-slate-500">
              The retry budget is shared across the whole request. Allowed: timeout, rate_limit, context_window_exceeded, content_policy_violation, generic.
            </span>
          </label>
        </div>
      </details>

      {contextRoutingSupported ? <details className="rounded-xl border border-slate-200 px-3 py-3">
        <summary className="cursor-pointer list-none select-none text-sm font-semibold text-slate-900">
          Context capacity
        </summary>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          Route from the final transformed request size using each deployment&apos;s model context metadata.
        </p>
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Routing behavior</span>
            <select
              value={values.contextMode}
              onChange={(event) => updateValue(
                'contextMode',
                event.target.value as PolicyGuidedValues['contextMode'],
              )}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            >
              <option value="disabled">Disabled</option>
              <option value="eligible-only">Eligible instances only</option>
              <option value="smallest-sufficient">Prefer smallest sufficient</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Missing capacity metadata</span>
            <select
              value={values.contextUnknownCapacity}
              disabled={values.contextMode === 'disabled'}
              onChange={(event) => updateValue(
                'contextUnknownCapacity',
                event.target.value as PolicyGuidedValues['contextUnknownCapacity'],
              )}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm disabled:bg-slate-50 disabled:text-slate-400 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            >
              <option value="allow">Allow (compatible)</option>
              <option value="exclude">Exclude</option>
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Default output tokens</span>
            <input
              type="number"
              min="0"
              step="1"
              value={values.contextDefaultOutputTokens}
              disabled={values.contextMode === 'disabled'}
              onChange={(event) => updateValue('contextDefaultOutputTokens', event.target.value)}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-50 disabled:text-slate-400 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Safety margin tokens</span>
            <input
              type="number"
              min="0"
              step="1"
              value={values.contextSafetyMarginTokens}
              disabled={values.contextMode === 'disabled'}
              onChange={(event) => updateValue('contextSafetyMarginTokens', event.target.value)}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:bg-slate-50 disabled:text-slate-400 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
          </label>
        </div>
      </details> : (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3">
          <p className="text-sm font-semibold text-slate-900">Context capacity unavailable</p>
          <p className="mt-1 text-xs leading-5 text-slate-600">
            Context routing is available only for chat and embedding route groups. This group uses {workloadMode}.
            {values.contextMode !== 'disabled'
              ? ' Set context to null in the JSON editor before saving this policy.'
              : ''}
          </p>
        </div>
      )}
        </div>
      </details>
    </div>
  );
}
