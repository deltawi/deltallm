import React, { useState } from 'react';
import { Tag, Trash2, CheckCircle2, Code2, ListChecks, RotateCcw, ChevronDown, Zap, GitBranch, Shield, Clock } from 'lucide-react';

const MOCK_BINDINGS = [
  { id: "b1", template_key: "customer-support", label: "production", priority: 100, enabled: true }
];
const MOCK_TEMPLATES = [
  { id: "t1", key: "customer-support", name: "Customer Support" },
  { id: "t2", key: "code-review", name: "Code Review" },
];
const MOCK_MEMBERS = [
  { id: "dep-abc123", name: "gpt-4o / OpenAI", weight: 70 },
  { id: "dep-xyz789", name: "claude-3-5-sonnet / Anthropic", weight: 30 },
];
const MOCK_POLICIES = [
  { id: "p3", version: 3, status: "published", publisher: "admin@acme.com", date: "Mar 14, 2026" },
  { id: "p2", version: 2, status: "draft",     publisher: "dev@acme.com",   date: "Mar 12, 2026" },
  { id: "p1", version: 1, status: "archived",  publisher: "admin@acme.com", date: "Mar 10, 2026" },
];

export function RgTabAdvancedRedesign() {
  const [bindings, setBindings] = useState(MOCK_BINDINGS);
  const [bindForm, setBindForm] = useState({ template_key: '', label: '', priority: '', enabled: true });
  
  const [policyMode, setPolicyMode] = useState<'guided' | 'json'>('guided');
  const [strategy, setStrategy] = useState('weighted');
  const [isValidated, setIsValidated] = useState(false);
  const [policyJson, setPolicyJson] = useState(JSON.stringify({ strategy: "weighted", members: MOCK_MEMBERS.map(m => ({ id: m.id, weight: m.weight })) }, null, 2));

  const [rollbackVersion, setRollbackVersion] = useState<string>("");

  const handleRemoveBinding = (id: string) => {
    setBindings(bindings.filter(b => b.id !== id));
  };

  const handleValidate = () => {
    setIsValidated(true);
    setTimeout(() => setIsValidated(false), 3000);
  };

  return (
    <div className="min-h-screen bg-slate-50 p-6 space-y-5">
      <div className="mx-auto max-w-5xl rounded-xl bg-white shadow-sm ring-1 ring-slate-200 overflow-hidden">
        
        {/* SECTION 1: PROMPT BINDING */}
        <div className="p-6">
          <div className="mb-4">
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-semibold text-slate-900">Prompt Binding</h2>
              {bindings.length > 0 && (
                <span className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 ring-1 ring-inset ring-violet-600/20">
                  {bindings.length} active
                </span>
              )}
            </div>
            <p className="mt-1 text-[13px] text-slate-500">
              Gateway resolves the bound prompt automatically for every request in this group.
            </p>
          </div>

          {bindings.length === 0 ? (
            <div className="rounded-xl border border-dashed border-violet-200 bg-violet-50/50 px-4 py-6 text-center">
              <p className="text-sm font-medium text-violet-900">No prompts bound yet</p>
              <p className="mt-1 text-xs text-violet-600/70">Bind a prompt below to automatically resolve it for this route group.</p>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2 mb-6">
              {bindings.map(b => (
                <div key={b.id} className="inline-flex items-center gap-2 rounded-full bg-violet-100 pl-3 pr-1 py-1 text-sm text-violet-800 ring-1 ring-inset ring-violet-200">
                  <Tag className="h-3.5 w-3.5 text-violet-500" />
                  <span className="font-medium">{b.template_key}</span>
                  <span className="text-violet-400">/</span>
                  <span className="text-violet-600">{b.label}</span>
                  <span className="text-violet-400">/</span>
                  <span className="text-violet-600 text-xs">p={b.priority}</span>
                  <button 
                    onClick={() => handleRemoveBinding(b.id)}
                    className="ml-1 rounded-full p-1 hover:bg-violet-200 text-violet-500 hover:text-violet-900 transition-colors"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="mt-6 rounded-lg border border-slate-100 bg-slate-50 p-4">
            <p className="text-[11px] uppercase tracking-widest font-semibold text-slate-400 mb-3">Bind a prompt</p>
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[200px]">
                <label className="mb-1.5 block text-xs font-medium text-slate-700">Prompt Template</label>
                <div className="relative">
                  <select 
                    value={bindForm.template_key}
                    onChange={e => setBindForm({...bindForm, template_key: e.target.value})}
                    className="w-full appearance-none rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm"
                  >
                    <option value="" disabled>Select a template...</option>
                    {MOCK_TEMPLATES.map(t => (
                      <option key={t.id} value={t.key}>{t.name} ({t.key})</option>
                    ))}
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-4 w-4 text-slate-400" />
                </div>
              </div>
              <div className="w-40">
                <label className="mb-1.5 block text-xs font-medium text-slate-700">Label</label>
                <input 
                  type="text" 
                  placeholder="e.g. production"
                  value={bindForm.label}
                  onChange={e => setBindForm({...bindForm, label: e.target.value})}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm"
                />
              </div>
              <div className="w-24">
                <label className="mb-1.5 block text-xs font-medium text-slate-700">Priority</label>
                <input 
                  type="number" 
                  placeholder="100"
                  value={bindForm.priority}
                  onChange={e => setBindForm({...bindForm, priority: e.target.value})}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm"
                />
              </div>
              <button className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-violet-700 focus:outline-none focus:ring-2 focus:ring-violet-500 focus:ring-offset-2 transition-colors">
                Bind
              </button>
            </div>
            
            <div className="mt-4 flex items-center gap-2">
              <button 
                type="button"
                onClick={() => setBindForm({...bindForm, enabled: !bindForm.enabled})}
                className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${bindForm.enabled ? 'bg-violet-600' : 'bg-slate-200'}`}
              >
                <span className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${bindForm.enabled ? 'translate-x-4' : 'translate-x-0'}`} />
              </button>
              <span className="text-xs text-slate-600">Active (resolves for live requests)</span>
            </div>
          </div>
        </div>

        {/* SECTION 2: ROUTING POLICY */}
        <div className="border-t border-slate-100 bg-blue-950/[0.02] p-6 relative">
          
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 mb-6">
            <div>
              <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                <GitBranch className="h-5 w-5 text-blue-600" />
                Routing Policy
              </h2>
              <p className="mt-1 text-[13px] text-slate-500 max-w-xl">
                Override only when you need weighted splits, ordered fallback, or rate-limit awareness.
              </p>
            </div>
            
            <div className="flex items-center gap-2 shrink-0">
              <button 
                onClick={handleValidate}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 hover:text-slate-900 transition-colors"
              >
                Validate
              </button>
              <button className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 hover:text-slate-900 transition-colors">
                Save Draft
              </button>
              <button className="rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 transition-colors">
                Publish ↑
              </button>
            </div>
          </div>

          {isValidated && (
            <div className="mb-6 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 shadow-sm animate-in fade-in slide-in-from-top-2">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              <span className="font-medium">Policy validated ✓</span>
              <span className="text-emerald-600/80 ml-1">No errors found in the current configuration.</span>
            </div>
          )}

          <div className="mb-4 flex items-center gap-1 border-b border-slate-200">
            <button 
              onClick={() => setPolicyMode('guided')}
              className={`flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${policyMode === 'guided' ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'}`}
            >
              <ListChecks className="h-4 w-4" />
              Guided
            </button>
            <button 
              onClick={() => setPolicyMode('json')}
              className={`flex items-center gap-2 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${policyMode === 'json' ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'}`}
            >
              <Code2 className="h-4 w-4" />
              Raw JSON
            </button>
          </div>

          {policyMode === 'guided' ? (
            <div className="space-y-6">
              {/* Strategy Selector */}
              <div>
                <p className="text-[11px] uppercase tracking-widest font-semibold text-slate-400 mb-3">Routing Strategy</p>
                <div className="flex flex-wrap gap-2">
                  {[
                    { id: 'shuffle', label: 'Simple Shuffle', icon: Zap },
                    { id: 'weighted', label: 'Weighted Split', icon: GitBranch },
                    { id: 'fallback', label: 'Ordered Fallback', icon: ListChecks },
                    { id: 'ratelimit', label: 'Rate-Limit Aware', icon: Shield },
                  ].map(s => {
                    const Icon = s.icon;
                    const isSelected = strategy === s.id;
                    return (
                      <button
                        key={s.id}
                        onClick={() => setStrategy(s.id)}
                        className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${isSelected ? 'border-blue-600 bg-blue-50 text-blue-700 shadow-sm' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'}`}
                      >
                        <Icon className={`h-4 w-4 ${isSelected ? 'text-blue-600' : 'text-slate-400'}`} />
                        {s.label}
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* Members */}
              <div>
                <p className="text-[11px] uppercase tracking-widest font-semibold text-slate-400 mb-3">Target Deployments</p>
                <div className="space-y-3">
                  {MOCK_MEMBERS.map((member, i) => (
                    <div key={member.id} className="flex items-center gap-4 rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-medium text-slate-600">
                        {i + 1}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-slate-900 truncate">{member.name}</p>
                        <p className="text-xs text-slate-500 font-mono mt-0.5">{member.id}</p>
                      </div>
                      <div className="flex items-center gap-3 w-48 shrink-0">
                        <input type="range" min="0" max="100" defaultValue={member.weight} className="w-full accent-blue-600" />
                        <div className="relative">
                          <input type="number" defaultValue={member.weight} className="w-16 rounded-md border border-slate-200 py-1 pl-2 pr-6 text-sm text-right focus:border-blue-500 focus:ring-1 focus:ring-blue-500" />
                          <span className="absolute right-2 top-1.5 text-xs text-slate-400">%</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Preview */}
              <div className="mt-6">
                <p className="text-[11px] uppercase tracking-widest font-semibold text-slate-400 mb-2">Effective Policy Preview</p>
                <div className="rounded-lg bg-gray-950 p-4 shadow-inner overflow-x-auto">
                  <pre className="text-sm text-green-400 font-mono leading-relaxed">
                    {`{
  "strategy": "${strategy}",
  "members": [
    {
      "id": "dep-abc123",
      "weight": 70
    },
    {
      "id": "dep-xyz789",
      "weight": 30
    }
  ]
}`}
                  </pre>
                </div>
              </div>
            </div>
          ) : (
            <div className="mt-4">
              <p className="text-[11px] uppercase tracking-widest font-semibold text-slate-400 mb-2">Raw JSON Editor</p>
              <textarea 
                value={policyJson}
                onChange={(e) => setPolicyJson(e.target.value)}
                className="w-full h-80 rounded-lg bg-gray-950 p-4 text-sm text-green-400 font-mono leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500 shadow-inner"
                spellCheck="false"
              />
            </div>
          )}
        </div>

        {/* SECTION 3: POLICY HISTORY */}
        <div className="border-t border-slate-100 p-6">
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-[11px] uppercase tracking-widest font-semibold text-slate-400 flex items-center gap-2">
              <Clock className="h-3.5 w-3.5" />
              Policy History
            </h3>
            
            <div className="flex items-center gap-2">
              <select 
                value={rollbackVersion}
                onChange={e => setRollbackVersion(e.target.value)}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-700 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 shadow-sm"
              >
                <option value="" disabled>Select version to restore</option>
                {MOCK_POLICIES.map(p => (
                  <option key={p.id} value={p.version}>v{p.version} ({p.status})</option>
                ))}
              </select>
              <button 
                disabled={!rollbackVersion}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Rollback
              </button>
            </div>
          </div>

          <div className="pl-2">
            <div className="relative border-l-2 border-slate-200 space-y-6 pb-2">
              {MOCK_POLICIES.map((policy, i) => {
                const isPublished = policy.status === 'published';
                const isDraft = policy.status === 'draft';
                const isArchived = policy.status === 'archived';
                
                let dotColorClass = "bg-slate-300 border-slate-100";
                let badgeClass = "bg-slate-100 text-slate-600 border-slate-200";
                
                if (isPublished) {
                  dotColorClass = "bg-emerald-500 border-emerald-100";
                  badgeClass = "bg-emerald-50 text-emerald-700 border-emerald-200";
                } else if (isDraft) {
                  dotColorClass = "bg-blue-500 border-blue-100";
                  badgeClass = "bg-blue-50 text-blue-700 border-blue-200";
                }

                return (
                  <div key={policy.id} className="relative pl-6 sm:pl-8 flex flex-col sm:flex-row sm:items-center sm:justify-between group">
                    {/* Timeline Dot */}
                    <div className={`absolute -left-[7px] top-1.5 h-3 w-3 rounded-full border-2 ${dotColorClass} ring-4 ring-white`} />
                    
                    <div className="flex items-center flex-wrap gap-3">
                      <span className="text-sm font-semibold text-slate-900">Version {policy.version}</span>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border uppercase tracking-wider ${badgeClass}`}>
                        {policy.status}
                      </span>
                      <span className="text-xs text-slate-500 flex items-center gap-1.5">
                        <span className="hidden sm:inline">by</span> {policy.publisher}
                      </span>
                      <span className="text-xs text-slate-400">· {policy.date}</span>
                    </div>

                    {!isPublished && (
                      <button className="mt-2 sm:mt-0 self-start sm:self-auto text-xs font-medium text-slate-500 hover:text-slate-900 opacity-0 group-hover:opacity-100 transition-opacity">
                        Roll back to v{policy.version}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
