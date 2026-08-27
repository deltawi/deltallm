import {
  ArrowDown,
  ArrowUp,
  BarChart2,
  Clock,
  DollarSign,
  GitBranch,
  Layers,
  ListChecks,
  Shield,
  Tag,
  Zap,
} from 'lucide-react';
import {
  withGuidedPolicyStrategy,
  type PolicyGuidedValues,
  type PolicyMemberOption,
} from '../lib/routeGroups';

const STRATEGY_META: Record<string, { icon: React.ElementType; label: string }> = {
  'simple-shuffle': { icon: Zap, label: 'Simple Shuffle' },
  weighted: { icon: GitBranch, label: 'Weighted Split' },
  'least-busy': { icon: BarChart2, label: 'Least Busy' },
  'latency-based-routing': { icon: Clock, label: 'Latency-Based' },
  'cost-based-routing': { icon: DollarSign, label: 'Cost-Based' },
  'usage-based-routing': { icon: Layers, label: 'Usage-Based' },
  'tag-based-routing': { icon: Tag, label: 'Tag-Based (Legacy)' },
  'priority-based-routing': { icon: ListChecks, label: 'Priority-Based' },
  'rate-limit-aware': { icon: Shield, label: 'Rate-Limit Aware' },
};

interface PolicyGuidedEditorProps {
  values: PolicyGuidedValues;
  onChange: (next: PolicyGuidedValues) => void;
  strategyOptions: string[];
  memberOptions: PolicyMemberOption[];
}

function CheckIcon() {
  return (
    <svg className="h-3 w-3 text-white" viewBox="0 0 12 12" fill="none">
      <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function PolicyGuidedEditor({
  values,
  onChange,
  strategyOptions,
  memberOptions,
}: PolicyGuidedEditorProps) {
  const enabledMemberIds = memberOptions
    .filter((member) => member.enabled)
    .map((member) => member.deployment_id);
  const optionsById = new Map(memberOptions.map((member) => [member.deployment_id, member]));
  const orderedOptions = [
    ...values.memberIds.flatMap((deploymentId) => {
      const option = optionsById.get(deploymentId);
      return option ? [option] : [];
    }),
    ...memberOptions.filter((member) => !values.memberIds.includes(member.deployment_id)),
  ];

  const updateValue = <K extends keyof PolicyGuidedValues>(
    key: K,
    value: PolicyGuidedValues[K],
  ) => onChange({ ...values, [key]: value });

  const selectExplicitMembers = () => {
    onChange({
      ...values,
      memberSelection: 'explicit',
      memberIds: values.memberSelection === 'inherit' ? enabledMemberIds : values.memberIds,
    });
  };

  const toggleMember = (deploymentId: string) => {
    const startingIds = values.memberSelection === 'inherit'
      ? enabledMemberIds
      : values.memberIds;
    const included = startingIds.includes(deploymentId);
    onChange({
      ...values,
      memberSelection: 'explicit',
      memberIds: included
        ? startingIds.filter((item) => item !== deploymentId)
        : [...startingIds, deploymentId],
    });
  };

  const updateWeight = (deploymentId: string, weight: string) => {
    onChange({
      ...values,
      memberSelection: 'explicit',
      memberIds: values.memberSelection === 'inherit' ? enabledMemberIds : values.memberIds,
      memberWeights: { ...values.memberWeights, [deploymentId]: weight },
    });
  };

  const moveMember = (deploymentId: string, delta: -1 | 1) => {
    const currentIndex = values.memberIds.indexOf(deploymentId);
    const nextIndex = currentIndex + delta;
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= values.memberIds.length) return;
    const memberIds = [...values.memberIds];
    [memberIds[currentIndex], memberIds[nextIndex]] = [
      memberIds[nextIndex],
      memberIds[currentIndex],
    ];
    onChange({ ...values, memberSelection: 'explicit', memberIds });
  };

  const showWeights = values.strategy === 'weighted';
  const showOrder = values.strategy === 'priority-based-routing'
    && values.memberSelection === 'explicit';

  return (
    <div className="space-y-6">
      <div>
        <p className="mb-3 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
          Routing strategy
        </p>
        <div className="flex flex-wrap gap-2">
          {strategyOptions.map((strategy) => {
            const meta = STRATEGY_META[strategy] ?? { icon: Zap, label: strategy };
            const Icon = meta.icon;
            const selected = values.strategy === strategy;
            return (
              <button
                key={strategy}
                type="button"
                onClick={() => onChange(withGuidedPolicyStrategy(values, strategy))}
                className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${
                  selected
                    ? 'border-brand-primary bg-blue-50 text-blue-700 shadow-sm'
                    : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'
                }`}
              >
                <Icon className={`h-4 w-4 ${selected ? 'text-brand-primary-ink' : 'text-slate-400'}`} />
                {meta.label}
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">
            Target deployments
          </p>
          <div className="flex rounded-lg border border-slate-200 bg-slate-100 p-1">
            <button
              type="button"
              onClick={() => onChange({
                ...values,
                memberSelection: 'inherit',
                memberIds: enabledMemberIds,
              })}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                values.memberSelection === 'inherit'
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500'
              }`}
            >
              Inherit enabled
            </button>
            <button
              type="button"
              onClick={selectExplicitMembers}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                values.memberSelection === 'explicit'
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500'
              }`}
            >
              Choose subset
            </button>
          </div>
        </div>
        {values.memberSelection === 'inherit' && (
          <p className="mb-3 text-xs text-slate-500">
            The policy follows the group&apos;s enabled membership. Editing a weight or checkbox creates an explicit subset.
          </p>
        )}
        {memberOptions.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-200 px-4 py-5 text-center text-sm text-slate-500">
            Add group members in the Models tab first.
          </p>
        ) : (
          <div className="space-y-2">
            {orderedOptions.map((member) => {
              const deploymentId = member.deployment_id;
              const included = values.memberIds.includes(deploymentId) && member.enabled;
              const selectedIndex = values.memberIds.indexOf(deploymentId);
              return (
                <div
                  key={deploymentId}
                  className={`flex flex-col gap-3 rounded-lg border bg-white p-3 shadow-sm transition-all md:flex-row md:items-center ${
                    included ? 'border-slate-200' : 'border-slate-100 opacity-60'
                  }`}
                >
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-medium text-slate-600">
                      {selectedIndex >= 0 ? selectedIndex + 1 : '—'}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-sm text-slate-900">{deploymentId}</p>
                      {!member.enabled && <p className="text-xs text-amber-700">Disabled in group membership</p>}
                    </div>
                  </div>
                  {showWeights && included && (
                    <label className="flex items-center gap-2 md:w-36">
                      <span className="text-xs font-medium text-slate-500">Weight</span>
                      <input
                        type="number"
                        min="1"
                        step="1"
                        value={values.memberWeights[deploymentId] || ''}
                        placeholder={member.weight == null ? 'inherit' : String(member.weight)}
                        onChange={(event) => updateWeight(deploymentId, event.target.value)}
                        className="min-w-0 flex-1 rounded-md border border-slate-200 px-2 py-1 text-right text-sm focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
                        aria-label={`Weight for ${deploymentId}`}
                      />
                    </label>
                  )}
                  {showOrder && included && (
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => moveMember(deploymentId, -1)}
                        disabled={selectedIndex <= 0}
                        className="rounded-md border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-30"
                        aria-label={`Move ${deploymentId} earlier`}
                      >
                        <ArrowUp className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => moveMember(deploymentId, 1)}
                        disabled={selectedIndex === values.memberIds.length - 1}
                        className="rounded-md border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 disabled:opacity-30"
                        aria-label={`Move ${deploymentId} later`}
                      >
                        <ArrowDown className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={included}
                    disabled={!member.enabled}
                    onClick={() => toggleMember(deploymentId)}
                    className={`flex h-5 w-5 shrink-0 items-center justify-center self-end rounded border-2 transition-colors md:self-auto ${
                      included
                        ? 'border-brand-primary bg-brand-primary'
                        : 'border-slate-300 bg-white hover:border-blue-400'
                    } disabled:cursor-not-allowed`}
                  >
                    {included && <CheckIcon />}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

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
            <span className="text-xs font-medium text-slate-700">Retry Max Attempts</span>
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
              Allowed: timeout, rate_limit, context_window_exceeded, content_policy_violation, generic.
            </span>
          </label>
        </div>
      </details>
    </div>
  );
}
