import type { PolicyGuidedValues } from '../lib/routeGroups';

interface PolicyGuidedEditorProps {
  values: PolicyGuidedValues;
  onChange: (next: PolicyGuidedValues) => void;
  strategyOptions: string[];
  memberOptions: string[];
}

const POLICY_MODES: Array<{ value: PolicyGuidedValues['mode']; label: string; description: string }> = [
  { value: 'fallback', label: 'Fallback', description: 'Try members in order until one succeeds.' },
  { value: 'weighted', label: 'Weighted split', description: 'Distribute traffic by weight across the selected members.' },
  { value: 'conditional', label: 'Conditional', description: 'Use advanced JSON when routing depends on request metadata.' },
  { value: 'adaptive', label: 'Adaptive', description: 'Use health and constraints to steer traffic dynamically.' },
];

export default function PolicyGuidedEditor({
  values,
  onChange,
  strategyOptions,
  memberOptions,
}: PolicyGuidedEditorProps) {
  const updateValue = <K extends keyof PolicyGuidedValues>(key: K, value: PolicyGuidedValues[K]) => {
    onChange({ ...values, [key]: value });
  };

  const toggleMember = (deploymentId: string) => {
    const has = values.memberIds.includes(deploymentId);
    updateValue(
      'memberIds',
      has ? values.memberIds.filter((item) => item !== deploymentId) : [...values.memberIds, deploymentId]
    );
  };

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium text-slate-900">Traffic pattern</p>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {POLICY_MODES.map((option) => {
            const active = values.mode === option.value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => updateValue('mode', option.value)}
                className={`rounded-xl border px-3 py-3 text-left transition-colors ${
                  active ? 'border-blue-300 bg-blue-50 text-blue-900' : 'border-slate-200 bg-white hover:bg-slate-50 text-slate-800'
                }`}
              >
                <div className="text-sm font-semibold">{option.label}</div>
                <div className="mt-1 text-xs text-slate-500">{option.description}</div>
              </button>
            );
          })}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Strategy</label>
        <select
          value={values.strategy}
          onChange={(event) => updateValue('strategy', event.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {strategyOptions.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-slate-500">Choose how the router should pick among eligible members when this override is active.</p>
      </div>

      <div className="space-y-1">
        <p className="text-sm font-medium text-gray-700">Members included in this policy</p>
        <div className="max-h-48 overflow-y-auto rounded-xl border border-gray-200 p-2 space-y-1">
          {memberOptions.length === 0 ? (
            <p className="px-1 py-2 text-sm text-gray-500">Add group members first.</p>
          ) : (
            memberOptions.map((deploymentId) => (
              <label key={deploymentId} className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-gray-700 hover:bg-slate-50">
                <input
                  type="checkbox"
                  checked={values.memberIds.includes(deploymentId)}
                  onChange={() => toggleMember(deploymentId)}
                  className="rounded border-gray-300"
                />
                <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{deploymentId}</code>
              </label>
            ))
          )}
        </div>
      </div>

      <details className="rounded-xl border border-slate-200 px-3 py-3">
        <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900">Timeouts and retry</summary>
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="space-y-1">
            <span className="text-sm font-medium text-gray-700">Global Timeout (ms)</span>
            <input
              value={values.timeoutMs}
              onChange={(event) => updateValue('timeoutMs', event.target.value)}
              placeholder="10000"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <label className="space-y-1">
            <span className="text-sm font-medium text-gray-700">Retry Max Attempts</span>
            <input
              value={values.retryMaxAttempts}
              onChange={(event) => updateValue('retryMaxAttempts', event.target.value)}
              placeholder="2"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <label className="space-y-1 md:col-span-2">
            <span className="text-sm font-medium text-gray-700">Retryable Errors</span>
            <input
              value={values.retryableErrors}
              onChange={(event) => updateValue('retryableErrors', event.target.value)}
              placeholder="timeout,5xx,rate_limit"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <span className="block text-xs text-slate-500">Comma-separated error classes to retry.</span>
          </label>
        </div>
      </details>
    </div>
  );
}
