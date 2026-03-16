/**
 * Variation D — "Usage Triage Dashboard"
 * Hypothesis: Operators primarily need to know "what's deployed, what's stale, what needs fixing?"
 * The registry is an ops tool. Templates are ranked by usage (binding_count).
 * A 3-segment health indicator per row shows published / labeled / bound status at a glance.
 * Inline action shortcuts surface without navigating away.
 */
import { useState } from "react";
import { AlertTriangle, CheckCircle2, Hash, Plus, Tag, Trash2, XCircle } from "lucide-react";

const TEMPLATES = [
  { template_key: "onboarding.welcome", name: "Onboarding Welcome",        description: "Personalised welcome messages by role.",                      version_count: 2, label_count: 1, binding_count: 4, published: true,  labels: ["production"],            owner_scope: null,               updated_at: "2 weeks ago" },
  { template_key: "support.reply",      name: "Support Reply",             description: "Customer support assistant for inbound tickets.",             version_count: 4, label_count: 2, binding_count: 3, published: true,  labels: ["production", "staging"], owner_scope: "org:acme",          updated_at: "2 hours ago"  },
  { template_key: "sales.outreach",     name: "Sales Outreach",            description: "Personalised outreach emails from CRM context.",              version_count: 3, label_count: 2, binding_count: 2, published: true,  labels: ["production", "a/b-test"],owner_scope: "team:sales",        updated_at: "5 days ago"  },
  { template_key: "code.review",        name: "Code Review",               description: "Reviews PRs for readability, correctness, and style.",        version_count: 2, label_count: 1, binding_count: 1, published: true,  labels: ["production"],            owner_scope: "team:engineering",  updated_at: "1 day ago"   },
  { template_key: "docs.summarize",     name: "Document Summariser",       description: "Summarises long docs into executive summaries.",              version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                        owner_scope: null,               updated_at: "3 days ago"  },
  { template_key: "legal.summary",      name: "Legal Contract Summary",    description: "Extracts key clauses and red flags from contracts.",          version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                        owner_scope: "org:acme",          updated_at: "1 week ago"  },
];

type ActionShortcut = null | "publish" | "label" | "bind";

function HealthDots({ published, labeled, bound }: { published: boolean; labeled: boolean; bound: boolean }) {
  const dot = (filled: boolean, label: string, color: string) => (
    <div className="flex flex-col items-center gap-1" title={label}>
      <div className={`h-2.5 w-2.5 rounded-full border-2 ${filled ? `${color} border-transparent` : "border-gray-200 bg-white"}`} />
      <span className="text-[8px] uppercase tracking-widest text-gray-400">{label.slice(0, 3)}</span>
    </div>
  );
  return (
    <div className="flex items-center gap-1.5">
      {dot(published, "Published", "bg-emerald-500")}
      <div className={`h-px w-3 ${labeled ? "bg-emerald-300" : "bg-gray-200"}`} />
      {dot(labeled, "Labeled", "bg-violet-500")}
      <div className={`h-px w-3 ${bound ? "bg-emerald-300" : "bg-gray-200"}`} />
      {dot(bound, "Bound", "bg-blue-500")}
    </div>
  );
}

function UsageBar({ count, max }: { count: number; max: number }) {
  const pct = max > 0 ? (count / max) * 100 : 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-gray-100">
        <div
          className={`h-full rounded-full transition-all ${count === 0 ? "bg-gray-200" : count >= 3 ? "bg-blue-500" : "bg-blue-300"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-xs font-bold ${count === 0 ? "text-gray-300" : "text-blue-600"}`}>{count}</span>
    </div>
  );
}

function ActionInline({ t, shortcut, onSet }: { t: typeof TEMPLATES[0]; shortcut: ActionShortcut; onSet: (v: ActionShortcut) => void }) {
  if (!t.published && shortcut === "publish") {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2">
        <span className="text-xs text-emerald-700 flex-1">Publish version {t.version_count} to mark it production-ready.</span>
        <button className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-700">Publish v{t.version_count}</button>
        <button onClick={() => onSet(null)} className="text-gray-400 hover:text-gray-600 text-xs">×</button>
      </div>
    );
  }
  if (t.label_count === 0 && shortcut === "label") {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2">
        <input placeholder="production" className="flex-1 rounded-lg border border-violet-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-violet-400" />
        <span className="text-xs text-violet-600">→ v{t.version_count}</span>
        <button className="rounded-lg bg-violet-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-violet-700">Assign</button>
        <button onClick={() => onSet(null)} className="text-gray-400 hover:text-gray-600 text-xs">×</button>
      </div>
    );
  }
  if (t.binding_count === 0 && shortcut === "bind") {
    return (
      <div className="mt-2 flex items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2">
        <select className="flex-1 rounded-lg border border-blue-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400">
          <option>Select a model group...</option>
          <option>prod-chat-primary</option>
          <option>support-fallback</option>
        </select>
        <button className="rounded-lg bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-blue-700">Bind</button>
        <button onClick={() => onSet(null)} className="text-gray-400 hover:text-gray-600 text-xs">×</button>
      </div>
    );
  }
  return null;
}

function TemplateRow({ t, maxBindings }: { t: typeof TEMPLATES[0]; maxBindings: number }) {
  const [shortcut, setShortcut] = useState<ActionShortcut>(null);
  const published = t.published;
  const labeled = t.label_count > 0;
  const bound = t.binding_count > 0;
  const healthy = published && labeled && bound;
  const needsPublish = !published;
  const needsLabel = published && !labeled;
  const needsBind = published && labeled && !bound;

  return (
    <div className={`group px-4 py-3.5 transition hover:bg-gray-50 ${!healthy ? "border-l-2 border-amber-300" : ""}`}>
      <div className="grid items-center gap-4" style={{ gridTemplateColumns: "auto 1fr 120px 72px auto" }}>
        {/* Health dots */}
        <HealthDots published={published} labeled={labeled} bound={bound} />

        {/* Name + meta */}
        <div className="min-w-0 cursor-pointer">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">{t.name}</span>
            {!healthy && (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                <AlertTriangle className="h-2.5 w-2.5" /> needs attention
              </span>
            )}
            {healthy && (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">
                <CheckCircle2 className="h-2.5 w-2.5" /> active
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <code className="text-[10px] text-gray-400 font-mono">{t.template_key}</code>
            {t.labels.map((l) => <span key={l} className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold text-violet-600">{l}</span>)}
            {t.owner_scope && <span className="text-[10px] text-gray-300">{t.owner_scope}</span>}
          </div>
        </div>

        {/* Usage bar */}
        <div className="flex flex-col items-start gap-0.5">
          <span className="text-[9px] uppercase tracking-widest text-gray-400">Bindings</span>
          <UsageBar count={t.binding_count} max={maxBindings} />
        </div>

        {/* Versions */}
        <div className="text-center text-xs font-semibold text-gray-600">
          <div className="text-[9px] uppercase tracking-widest text-gray-400 mb-0.5">Versions</div>
          {t.version_count}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
          {needsPublish && (
            <button onClick={() => setShortcut(s => s === "publish" ? null : "publish")}
              className="rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-700 hover:bg-emerald-100">
              Publish
            </button>
          )}
          {needsLabel && (
            <button onClick={() => setShortcut(s => s === "label" ? null : "label")}
              className="rounded-lg border border-violet-200 bg-violet-50 px-2 py-1 text-[10px] font-semibold text-violet-700 hover:bg-violet-100">
              Label
            </button>
          )}
          {needsBind && (
            <button onClick={() => setShortcut(s => s === "bind" ? null : "bind")}
              className="rounded-lg border border-blue-200 bg-blue-50 px-2 py-1 text-[10px] font-semibold text-blue-700 hover:bg-blue-100">
              Bind
            </button>
          )}
          <button className="rounded-lg p-1 text-gray-300 hover:bg-red-50 hover:text-red-400">
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* Inline action shortcut */}
      <ActionInline t={t} shortcut={shortcut} onSet={setShortcut} />
    </div>
  );
}

const SORT_OPTIONS = ["Usage (high → low)", "Recently updated", "Alphabetical", "Needs attention first"];

export function PromptRegistryV4() {
  const [sort, setSort] = useState(SORT_OPTIONS[0]);

  const maxBindings = Math.max(...TEMPLATES.map((t) => t.binding_count));

  const sorted = [...TEMPLATES].sort((a, b) => {
    if (sort === SORT_OPTIONS[0]) return b.binding_count - a.binding_count;
    if (sort === SORT_OPTIONS[2]) return a.name.localeCompare(b.name);
    if (sort === SORT_OPTIONS[3]) {
      const healthScore = (t: typeof TEMPLATES[0]) => (t.published ? 1 : 0) + (t.label_count > 0 ? 1 : 0) + (t.binding_count > 0 ? 1 : 0);
      return healthScore(a) - healthScore(b);
    }
    return 0;
  });

  const active = TEMPLATES.filter((t) => t.binding_count > 0 && t.published && t.label_count > 0).length;
  const needsAttention = TEMPLATES.filter((t) => !t.published || t.label_count === 0 || t.binding_count === 0).length;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Prompt Registry</h1>
            <p className="text-sm text-gray-500 mt-0.5">Health-ranked view — surface stale and unbound prompts before they cause issues.</p>
          </div>
          <button className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-amber-700">
            <Plus className="h-4 w-4" /> New Prompt
          </button>
        </div>
      </div>

      {/* Summary stat strip */}
      <div className="mx-6 mt-4 grid grid-cols-4 gap-3">
        {[
          { label: "Total",          value: TEMPLATES.length,  color: "text-gray-900" },
          { label: "Active",         value: active,             color: "text-emerald-600" },
          { label: "Needs attention",value: needsAttention,     color: "text-amber-600" },
          { label: "Unbound",        value: TEMPLATES.filter(t => t.binding_count === 0).length, color: "text-red-500" },
        ].map((s) => (
          <div key={s.label} className="rounded-2xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
            <div className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{s.label}</div>
            <div className={`mt-1 text-2xl font-bold ${s.color}`}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="mx-6 mt-3 flex items-center gap-6 text-[11px] text-gray-400">
        <span className="font-semibold uppercase tracking-widest">Health indicator:</span>
        {[
          { color: "bg-emerald-500", label: "Published" },
          { color: "bg-violet-500", label: "Labeled" },
          { color: "bg-blue-500", label: "Bound" },
        ].map((s) => (
          <span key={s.label} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${s.color}`} />
            {s.label}
          </span>
        ))}
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full border-2 border-gray-200 bg-white" /> Missing</span>
      </div>

      {/* List */}
      <div className="px-6 py-3">
        {/* Sort bar */}
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs text-gray-400">{TEMPLATES.length} prompts</span>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">Sort:</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-amber-500"
            >
              {SORT_OPTIONS.map((o) => <option key={o}>{o}</option>)}
            </select>
          </div>
        </div>

        <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm divide-y divide-gray-100">
          {sorted.map((t) => (
            <TemplateRow key={t.template_key} t={t} maxBindings={maxBindings} />
          ))}
        </div>
      </div>
    </div>
  );
}
