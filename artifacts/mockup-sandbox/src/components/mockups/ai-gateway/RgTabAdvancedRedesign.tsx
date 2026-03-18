import React, { useState } from 'react';
import {
  Tag, Trash2, CheckCircle2, Code2, ListChecks, RotateCcw,
  ChevronDown, Zap, GitBranch, Shield, Clock, BookOpen, ChevronRight
} from 'lucide-react';

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

function AccordionCard({
  id,
  open,
  onToggle,
  icon: Icon,
  iconBg,
  iconColor,
  title,
  subtitle,
  badge,
  borderAccent,
  children,
}: {
  id: string;
  open: boolean;
  onToggle: () => void;
  icon: React.ElementType;
  iconBg: string;
  iconColor: string;
  title: string;
  subtitle: string;
  badge?: React.ReactNode;
  borderAccent: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`rounded-xl bg-white ring-1 ring-slate-200 shadow-sm overflow-hidden transition-shadow ${open ? 'shadow-md' : ''}`}>
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-4 px-5 py-4 text-left hover:bg-slate-50/60 transition-colors group"
      >
        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${iconBg}`}>
          <Icon className={`h-4 w-4 ${iconColor}`} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="text-sm font-semibold text-slate-900">{title}</span>
            {badge}
          </div>
          <p className="text-xs text-slate-500 mt-0.5 truncate">{subtitle}</p>
        </div>
        <ChevronDown className={`h-4 w-4 text-slate-400 shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className={`border-t-2 ${borderAccent}`}>
          {children}
        </div>
      )}
    </div>
  );
}

export function RgTabAdvancedRedesign() {
  const [openSections, setOpenSections] = useState<Set<string>>(new Set(['prompt-binding']));

  const toggle = (id: string) => {
    setOpenSections(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const [bindings, setBindings] = useState(MOCK_BINDINGS);
  const [bindForm, setBindForm] = useState({ template_key: '', label: '', priority: '', enabled: true });

  const [policyMode, setPolicyMode] = useState<'guided' | 'json'>('guided');
  const [strategy, setStrategy] = useState('weighted');
  const [isValidated, setIsValidated] = useState(false);
  const [policyJson, setPolicyJson] = useState(
    JSON.stringify({ strategy: "weighted", members: MOCK_MEMBERS.map(m => ({ id: m.id, weight: m.weight })) }, null, 2)
  );

  const [rollbackVersion, setRollbackVersion] = useState<string>("");

  const handleBind = () => {
    if (!bindForm.template_key) return;
    setBindings(prev => [...prev, {
      id: `b${Date.now()}`,
      template_key: bindForm.template_key,
      label: bindForm.label || 'default',
      priority: Number(bindForm.priority) || 100,
      enabled: bindForm.enabled,
    }]);
    setBindForm({ template_key: '', label: '', priority: '', enabled: true });
  };

  const handleValidate = () => { setIsValidated(true); setTimeout(() => setIsValidated(false), 3000); };

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="max-w-3xl mx-auto space-y-3">

        {/* ── 1. PROMPT BINDING ── */}
        <AccordionCard
          id="prompt-binding"
          open={openSections.has('prompt-binding')}
          onToggle={() => toggle('prompt-binding')}
          icon={BookOpen}
          iconBg="bg-violet-100"
          iconColor="text-violet-600"
          title="Prompt Binding"
          subtitle="Gateway resolves the bound prompt automatically for every request in this group."
          borderAccent="border-violet-200"
          badge={
            bindings.length > 0 ? (
              <span className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 ring-1 ring-inset ring-violet-600/20">
                {bindings.length} active
              </span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
                none
              </span>
            )
          }
        >
          <div className="px-5 py-5 space-y-5">
            {/* Active chips */}
            {bindings.length === 0 ? (
              <div className="rounded-xl border border-dashed border-violet-200 bg-violet-50/40 px-4 py-5 text-center">
                <p className="text-sm font-medium text-violet-900">No prompts bound yet</p>
                <p className="mt-1 text-xs text-violet-600/70">Bind a prompt below to automatically inject it into every request.</p>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {bindings.map(b => (
                  <div key={b.id} className="inline-flex items-center gap-2 rounded-full bg-violet-100 pl-3 pr-1 py-1 text-sm text-violet-800 ring-1 ring-inset ring-violet-200">
                    <Tag className="h-3.5 w-3.5 text-violet-500 shrink-0" />
                    <span className="font-medium">{b.template_key}</span>
                    <span className="text-violet-400">/</span>
                    <span className="text-violet-600">{b.label}</span>
                    <span className="text-violet-400">/</span>
                    <span className="text-violet-600 text-xs">p={b.priority}</span>
                    <button
                      onClick={() => setBindings(bindings.filter(x => x.id !== b.id))}
                      className="ml-1 rounded-full p-1 hover:bg-violet-200 text-violet-500 hover:text-violet-900 transition-colors"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Bind form */}
            <div className="rounded-lg border border-slate-100 bg-slate-50 p-4 space-y-4">
              <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400">Add binding</p>
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex-1 min-w-[180px]">
                  <label className="mb-1.5 block text-xs font-medium text-slate-700">Prompt Template</label>
                  <div className="relative">
                    <select
                      value={bindForm.template_key}
                      onChange={e => setBindForm({ ...bindForm, template_key: e.target.value })}
                      className="w-full appearance-none rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm"
                    >
                      <option value="" disabled>Select a template...</option>
                      {MOCK_TEMPLATES.map(t => <option key={t.id} value={t.key}>{t.name}</option>)}
                    </select>
                    <ChevronDown className="pointer-events-none absolute right-2.5 top-2.5 h-4 w-4 text-slate-400" />
                  </div>
                </div>
                <div className="w-36">
                  <label className="mb-1.5 block text-xs font-medium text-slate-700">Label</label>
                  <input type="text" placeholder="e.g. production" value={bindForm.label}
                    onChange={e => setBindForm({ ...bindForm, label: e.target.value })}
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm" />
                </div>
                <div className="w-24">
                  <label className="mb-1.5 block text-xs font-medium text-slate-700">Priority</label>
                  <input type="number" placeholder="100" value={bindForm.priority}
                    onChange={e => setBindForm({ ...bindForm, priority: e.target.value })}
                    className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm placeholder:text-slate-400 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 shadow-sm" />
                </div>
                <button
                  onClick={handleBind}
                  className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-violet-700 transition-colors"
                >
                  Bind
                </button>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setBindForm({ ...bindForm, enabled: !bindForm.enabled })}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ${bindForm.enabled ? 'bg-violet-600' : 'bg-slate-200'}`}
                >
                  <span className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ${bindForm.enabled ? 'translate-x-4' : 'translate-x-0'}`} />
                </button>
                <span className="text-xs text-slate-600">Active — resolves for live requests</span>
              </div>
            </div>
          </div>
        </AccordionCard>

        {/* ── 2. ROUTING POLICY ── */}
        <AccordionCard
          id="routing-policy"
          open={openSections.has('routing-policy')}
          onToggle={() => toggle('routing-policy')}
          icon={GitBranch}
          iconBg="bg-blue-100"
          iconColor="text-blue-600"
          title="Routing Policy"
          subtitle="Override when you need weighted splits, ordered fallback, or rate-limit awareness."
          borderAccent="border-blue-200"
          badge={
            <span className="inline-flex items-center rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-600/20">
              Weighted Split
            </span>
          }
        >
          <div className="px-5 py-5 space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              {/* Mode tabs */}
              <div className="flex items-center gap-1 border border-slate-200 bg-slate-100 rounded-lg p-1">
                {(['guided', 'json'] as const).map(mode => (
                  <button
                    key={mode}
                    onClick={() => setPolicyMode(mode)}
                    className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
                      policyMode === mode
                        ? 'bg-white text-slate-900 shadow-sm'
                        : 'text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    {mode === 'guided' ? <><ListChecks className="h-3.5 w-3.5" />Guided</> : <><Code2 className="h-3.5 w-3.5" />Raw JSON</>}
                  </button>
                ))}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-2">
                {isValidated && (
                  <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
                    <CheckCircle2 className="h-3.5 w-3.5" />Validated ✓
                  </span>
                )}
                <button onClick={handleValidate} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 transition-colors">Validate</button>
                <button className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 transition-colors">Save Draft</button>
                <button className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 transition-colors">Publish ↑</button>
              </div>
            </div>

            {policyMode === 'guided' ? (
              <div className="space-y-5">
                <div>
                  <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400 mb-3">Routing Strategy</p>
                  <div className="flex flex-wrap gap-2">
                    {[
                      { id: 'shuffle',   label: 'Simple Shuffle',   icon: Zap },
                      { id: 'weighted',  label: 'Weighted Split',   icon: GitBranch },
                      { id: 'fallback',  label: 'Ordered Fallback', icon: ListChecks },
                      { id: 'ratelimit', label: 'Rate-Limit Aware', icon: Shield },
                    ].map(s => {
                      const Icon = s.icon;
                      const sel = strategy === s.id;
                      return (
                        <button key={s.id} onClick={() => setStrategy(s.id)}
                          className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${sel ? 'border-blue-600 bg-blue-50 text-blue-700 shadow-sm' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'}`}>
                          <Icon className={`h-4 w-4 ${sel ? 'text-blue-600' : 'text-slate-400'}`} />{s.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400 mb-3">Target Deployments</p>
                  <div className="space-y-2">
                    {MOCK_MEMBERS.map((member, i) => (
                      <div key={member.id} className="flex items-center gap-4 rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
                        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-medium text-slate-600">{i + 1}</div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-semibold text-slate-900 truncate">{member.name}</p>
                          <p className="text-xs text-slate-500 font-mono mt-0.5 truncate">{member.id}</p>
                        </div>
                        <div className="flex items-center gap-3 w-44 shrink-0">
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

                <div>
                  <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400 mb-2">Effective Policy Preview</p>
                  <div className="rounded-lg bg-gray-950 p-4 shadow-inner overflow-x-auto">
                    <pre className="text-sm text-green-400 font-mono leading-relaxed">{`{\n  "strategy": "${strategy}",\n  "members": [\n    { "id": "dep-abc123", "weight": 70 },\n    { "id": "dep-xyz789", "weight": 30 }\n  ]\n}`}</pre>
                  </div>
                </div>
              </div>
            ) : (
              <div>
                <p className="text-[10px] uppercase tracking-widest font-semibold text-slate-400 mb-2">Raw JSON Editor</p>
                <textarea value={policyJson} onChange={e => setPolicyJson(e.target.value)}
                  className="w-full h-64 rounded-lg bg-gray-950 p-4 text-sm text-green-400 font-mono leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500 shadow-inner" spellCheck={false} />
              </div>
            )}
          </div>
        </AccordionCard>

        {/* ── 3. POLICY HISTORY ── */}
        <AccordionCard
          id="policy-history"
          open={openSections.has('policy-history')}
          onToggle={() => toggle('policy-history')}
          icon={Clock}
          iconBg="bg-slate-100"
          iconColor="text-slate-500"
          title="Policy History"
          subtitle="Audit and rollback previous routing policy versions."
          borderAccent="border-slate-200"
          badge={
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />
              v3 live
            </span>
          }
        >
          <div className="px-5 py-5">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
              <p className="text-xs text-slate-500">Rollback restores a past version as the new published policy.</p>
              <div className="flex items-center gap-2">
                <div className="relative">
                  <select value={rollbackVersion} onChange={e => setRollbackVersion(e.target.value)}
                    className="appearance-none rounded-lg border border-slate-200 bg-white pl-3 pr-8 py-1.5 text-xs text-slate-700 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 shadow-sm">
                    <option value="" disabled>Select version…</option>
                    {MOCK_POLICIES.map(p => <option key={p.id} value={p.version}>v{p.version} ({p.status})</option>)}
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-2.5 top-2 h-3.5 w-3.5 text-slate-400" />
                </div>
                <button disabled={!rollbackVersion}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                  <RotateCcw className="h-3.5 w-3.5" />Rollback
                </button>
              </div>
            </div>

            <div className="relative border-l-2 border-slate-200 space-y-5 pl-6 pb-1">
              {MOCK_POLICIES.map(policy => {
                const isPublished = policy.status === 'published';
                const isDraft = policy.status === 'draft';
                const dotColor = isPublished ? 'bg-emerald-500 border-emerald-100' : isDraft ? 'bg-blue-400 border-blue-100' : 'bg-slate-300 border-slate-100';
                const badgeClass = isPublished ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : isDraft ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-slate-100 text-slate-500 border-slate-200';
                return (
                  <div key={policy.id} className="relative flex flex-col sm:flex-row sm:items-center sm:justify-between group">
                    <div className={`absolute -left-[31px] top-1 h-3.5 w-3.5 rounded-full border-2 ${dotColor} ring-4 ring-white`} />
                    <div className="flex items-center flex-wrap gap-3">
                      <span className="text-sm font-semibold text-slate-900">Version {policy.version}</span>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border uppercase tracking-wider ${badgeClass}`}>{policy.status}</span>
                      <span className="text-xs text-slate-500">by {policy.publisher}</span>
                      <span className="text-xs text-slate-400">· {policy.date}</span>
                      {isPublished && (
                        <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-700">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />LIVE
                        </span>
                      )}
                    </div>
                    {!isPublished && (
                      <button onClick={() => setRollbackVersion(String(policy.version))}
                        className="mt-2 sm:mt-0 self-start sm:self-auto text-xs font-medium text-slate-500 hover:text-slate-800 opacity-0 group-hover:opacity-100 rounded-lg border border-slate-200 px-2.5 py-1 hover:bg-slate-50 transition-all">
                        Restore v{policy.version}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </AccordionCard>

      </div>
    </div>
  );
}
