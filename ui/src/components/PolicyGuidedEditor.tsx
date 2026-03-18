import { useState, useEffect } from 'react';
import {
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
import type { PolicyGuidedValues } from '../lib/routeGroups';

const STRATEGY_META: Record<string, { icon: React.ElementType; label: string }> = {
  'simple-shuffle':         { icon: Zap,       label: 'Simple Shuffle' },
  'weighted':               { icon: GitBranch,  label: 'Weighted Split' },
  'least-busy':             { icon: BarChart2,  label: 'Least Busy' },
  'latency-based-routing':  { icon: Clock,      label: 'Latency-Based' },
  'cost-based-routing':     { icon: DollarSign, label: 'Cost-Based' },
  'usage-based-routing':    { icon: Layers,     label: 'Usage-Based' },
  'tag-based-routing':      { icon: Tag,        label: 'Tag-Based' },
  'priority-based-routing': { icon: ListChecks, label: 'Priority-Based' },
  'rate-limit-aware':       { icon: Shield,     label: 'Rate-Limit Aware' },
};

interface PolicyGuidedEditorProps {
  values: PolicyGuidedValues;
  onChange: (next: PolicyGuidedValues) => void;
  strategyOptions: string[];
  memberOptions: string[];
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
  const [weights, setWeights] = useState<Record<string, number>>(() =>
    Object.fromEntries(
      memberOptions.map((id) => [id, Math.floor(100 / Math.max(memberOptions.length, 1))])
    )
  );

  useEffect(() => {
    setWeights((prev) => {
      const next = { ...prev };
      memberOptions.forEach((id) => {
        if (!(id in next)) next[id] = 0;
      });
      return next;
    });
  }, [memberOptions]);

  const updateValue = <K extends keyof PolicyGuidedValues>(key: K, val: PolicyGuidedValues[K]) => {
    onChange({ ...values, [key]: val });
  };

  const setStrategy = (s: string) => {
    onChange({ ...values, strategy: s });
  };

  const toggleMember = (id: string) => {
    const has = values.memberIds.includes(id);
    updateValue('memberIds', has ? values.memberIds.filter((m) => m !== id) : [...values.memberIds, id]);
  };

  const showWeights = values.strategy === 'weighted';

  return (
    <div className="space-y-6">

      {/* ── Routing Strategy ── */}
      <div>
        <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400 mb-3">
          Routing Strategy
        </p>
        <div className="flex flex-wrap gap-2">
          {strategyOptions.map((s) => {
            const meta = STRATEGY_META[s] ?? { icon: Zap, label: s };
            const Icon = meta.icon;
            const sel = values.strategy === s;
            return (
              <button
                key={s}
                type="button"
                onClick={() => setStrategy(s)}
                className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${
                  sel
                    ? 'border-blue-600 bg-blue-50 text-blue-700 shadow-sm'
                    : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'
                }`}
              >
                <Icon className={`h-4 w-4 ${sel ? 'text-blue-600' : 'text-slate-400'}`} />
                {meta.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Target Deployments ── */}
      <div>
        <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400 mb-3">
          Target Deployments
        </p>
        {memberOptions.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-200 px-4 py-5 text-center text-sm text-slate-500">
            Add group members in the Models tab first.
          </p>
        ) : (
          <div className="space-y-2">
            {memberOptions.map((id, i) => {
              const included = values.memberIds.includes(id);
              const w = weights[id] ?? 0;
              return (
                <div
                  key={id}
                  className={`flex items-center gap-4 rounded-lg border bg-white p-3 shadow-sm transition-all ${
                    included ? 'border-slate-200' : 'border-slate-100 opacity-50'
                  }`}
                >
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-medium text-slate-600">
                    {i + 1}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-mono text-slate-900 truncate">{id}</p>
                  </div>
                  {showWeights && included && (
                    <div className="flex items-center gap-3 w-44 shrink-0">
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={w}
                        onChange={(e) => setWeights((prev) => ({ ...prev, [id]: Number(e.target.value) }))}
                        className="w-full accent-blue-600"
                      />
                      <div className="relative">
                        <input
                          type="number"
                          value={w}
                          onChange={(e) => setWeights((prev) => ({ ...prev, [id]: Number(e.target.value) }))}
                          className="w-16 rounded-md border border-slate-200 py-1 pl-2 pr-6 text-sm text-right focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none"
                        />
                        <span className="absolute right-2 top-1.5 text-xs text-slate-400">%</span>
                      </div>
                    </div>
                  )}
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={included}
                    onClick={() => toggleMember(id)}
                    className={`h-5 w-5 shrink-0 rounded border-2 flex items-center justify-center transition-colors ${
                      included ? 'border-blue-600 bg-blue-600' : 'border-slate-300 bg-white hover:border-blue-400'
                    }`}
                  >
                    {included && <CheckIcon />}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Timeouts & Retry (collapsible) ── */}
      <details className="rounded-xl border border-slate-200 px-3 py-3">
        <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900 select-none">
          Timeouts and retry
        </summary>
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Global Timeout (ms)</span>
            <input
              value={values.timeoutMs}
              onChange={(e) => updateValue('timeoutMs', e.target.value)}
              placeholder="10000"
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-700">Retry Max Attempts</span>
            <input
              value={values.retryMaxAttempts}
              onChange={(e) => updateValue('retryMaxAttempts', e.target.value)}
              placeholder="2"
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
            />
          </label>
          <label className="space-y-1 md:col-span-2">
            <span className="text-xs font-medium text-slate-700">Retryable Errors</span>
            <input
              value={values.retryableErrors}
              onChange={(e) => updateValue('retryableErrors', e.target.value)}
              placeholder="timeout,5xx,rate_limit"
              className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
            />
            <span className="block text-xs text-slate-500">Comma-separated error classes to retry.</span>
          </label>
        </div>
      </details>

    </div>
  );
}
