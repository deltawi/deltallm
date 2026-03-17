/**
 * Variation A — "Readiness Pipeline"
 * Hypothesis: Users primarily care about "which prompts need action?"
 * The registry is a workflow tool, not a catalog.
 * Templates move through 3 stages: Draft → Ready → Active.
 * Columns surface exactly what's blocking each template from going live.
 */
import { useState } from "react";
import { AlertTriangle, CheckCircle2, Plus, Tag, Trash2, X, Zap } from "lucide-react";

const ALL_TEMPLATES = [
  { template_key: "docs.summarize",    name: "Document Summariser",       description: "Summarises long docs into executive summaries.",              version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                       owner_scope: null,              updated_at: "3 days ago"  },
  { template_key: "legal.summary",     name: "Legal Contract Summary",    description: "Extracts key clauses and red flags from contracts.",          version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                       owner_scope: "org:acme",        updated_at: "1 week ago"  },
  { template_key: "code.review",       name: "Code Review",               description: "Reviews PRs for readability, correctness, and style.",        version_count: 2, label_count: 1, binding_count: 1, published: true,  labels: ["production"],           owner_scope: "team:engineering", updated_at: "1 day ago"  },
  { template_key: "sales.outreach",    name: "Sales Outreach",            description: "Personalised outreach emails based on CRM context.",          version_count: 3, label_count: 2, binding_count: 2, published: true,  labels: ["production", "a/b-test"], owner_scope: "team:sales",    updated_at: "5 days ago"  },
  { template_key: "support.reply",     name: "Support Reply",             description: "Customer support assistant for inbound tickets.",             version_count: 4, label_count: 2, binding_count: 3, published: true,  labels: ["production", "staging"], owner_scope: "org:acme",      updated_at: "2 hours ago" },
  { template_key: "onboarding.welcome",name: "Onboarding Welcome",        description: "Personalised welcome messages for new users by role.",        version_count: 2, label_count: 1, binding_count: 4, published: true,  labels: ["production"],           owner_scope: null,              updated_at: "2 weeks ago" },
];

type Stage = "draft" | "ready" | "active";

function classify(t: typeof ALL_TEMPLATES[0]): Stage {
  if (t.binding_count > 0 && t.published) return "active";
  if (t.published && t.label_count > 0) return "ready";
  return "draft";
}

const COLUMNS: { id: Stage; label: string; hint: string; accent: string; bg: string; border: string; dot: string }[] = [
  { id: "draft",  label: "Draft",  hint: "Needs a published version + label before it can be bound.",  accent: "text-amber-700",  bg: "bg-amber-50",   border: "border-amber-200", dot: "bg-amber-400"  },
  { id: "ready",  label: "Ready",  hint: "Published and labeled — waiting to be bound to a consumer.", accent: "text-blue-700",   bg: "bg-blue-50",    border: "border-blue-200",  dot: "bg-blue-500"   },
  { id: "active", label: "Active", hint: "Published, labeled, and bound to at least one consumer.",    accent: "text-emerald-700",bg: "bg-emerald-50", border: "border-emerald-200",dot: "bg-emerald-500"},
];

function TemplateCard({ t, stage }: { t: typeof ALL_TEMPLATES[0]; stage: Stage }) {
  const nextAction: Record<Stage, string> = {
    draft:  t.version_count === 0 ? "Create version" : t.label_count === 0 ? "Assign label" : "Publish version",
    ready:  "Create binding",
    active: "View bindings",
  };
  const actionColor: Record<Stage, string> = {
    draft:  "bg-amber-600 text-white hover:bg-amber-700",
    ready:  "bg-blue-600 text-white hover:bg-blue-700",
    active: "bg-emerald-600 text-white hover:bg-emerald-700",
  };

  return (
    <div className="group rounded-xl border border-gray-200 bg-white p-3.5 shadow-sm hover:border-gray-300 hover:shadow-md cursor-pointer transition">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-gray-900 leading-snug">{t.name}</div>
          <code className="text-[10px] text-gray-400 font-mono">{t.template_key}</code>
        </div>
        <button className="shrink-0 rounded-lg p-1 text-gray-300 opacity-0 group-hover:opacity-100 transition hover:bg-red-50 hover:text-red-400">
          <Trash2 className="h-3 w-3" />
        </button>
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-gray-500 line-clamp-2">{t.description}</p>

      {/* Stage-specific blocker badges */}
      {stage === "draft" && (
        <div className="mt-2.5 flex flex-wrap gap-1">
          {t.version_count === 0 && <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-semibold text-red-600"><AlertTriangle className="h-2.5 w-2.5" /> No version</span>}
          {t.version_count > 0 && !t.published && <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700"><AlertTriangle className="h-2.5 w-2.5" /> Not published</span>}
          {t.published && t.label_count === 0 && <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700"><AlertTriangle className="h-2.5 w-2.5" /> No label</span>}
        </div>
      )}
      {stage === "ready" && (
        <div className="mt-2.5 flex flex-wrap gap-1">
          {t.labels.map((l) => (
            <span key={l} className="inline-flex items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700"><Tag className="h-2.5 w-2.5" /> {l}</span>
          ))}
        </div>
      )}
      {stage === "active" && (
        <div className="mt-2.5 flex items-center gap-2">
          <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 font-semibold"><CheckCircle2 className="h-3 w-3" /> {t.binding_count} binding{t.binding_count !== 1 ? "s" : ""}</span>
          {t.labels.map((l) => (
            <span key={l} className="rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">{l}</span>
          ))}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between">
        <span className="text-[10px] text-gray-300">{t.updated_at}</span>
        <button className={`rounded-lg px-2.5 py-1 text-[11px] font-semibold transition ${actionColor[stage]}`}>
          {nextAction[stage]} →
        </button>
      </div>
    </div>
  );
}

export function PromptRegistryV1() {
  const [showCreate, setShowCreate] = useState(false);

  const byStage = (stage: Stage) => ALL_TEMPLATES.filter((t) => classify(t) === stage);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Create drawer */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex">
          <div className="flex-1 bg-black/20" onClick={() => setShowCreate(false)} />
          <div className="flex w-[380px] flex-col border-l border-gray-200 bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
              <h2 className="text-sm font-semibold text-gray-900">New Prompt Template</h2>
              <button onClick={() => setShowCreate(false)} className="rounded-lg p-1 hover:bg-gray-100"><X className="h-4 w-4 text-gray-400" /></button>
            </div>
            <div className="flex-1 p-5 space-y-4">
              <div><label className="mb-1 block text-xs font-medium text-gray-700">Template Key *</label><input placeholder="support.reply" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" /></div>
              <div><label className="mb-1 block text-xs font-medium text-gray-700">Name *</label><input placeholder="Support Reply" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" /></div>
              <div><label className="mb-1 block text-xs font-medium text-gray-700">Description</label><textarea rows={2} placeholder="What does this prompt do?" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" /></div>
            </div>
            <div className="border-t px-5 py-4 flex justify-end gap-2">
              <button onClick={() => setShowCreate(false)} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600">Cancel</button>
              <button className="rounded-lg bg-amber-600 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-700">Create →</button>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Prompt Registry</h1>
            <p className="text-sm text-gray-500 mt-0.5">Track each prompt through its readiness stages — from draft to active in production.</p>
          </div>
          <button onClick={() => setShowCreate(true)} className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-amber-700">
            <Plus className="h-4 w-4" /> New Prompt
          </button>
        </div>
      </div>

      {/* Stage summary bar */}
      <div className="mx-6 mt-4 grid grid-cols-3 gap-3">
        {COLUMNS.map((col) => {
          const count = byStage(col.id).length;
          return (
            <div key={col.id} className={`flex items-center gap-3 rounded-2xl border px-4 py-3 ${col.bg} ${col.border}`}>
              <span className={`h-2.5 w-2.5 rounded-full ${col.dot}`} />
              <div>
                <div className={`text-xs font-semibold ${col.accent}`}>{col.label}</div>
                <div className="text-lg font-bold text-gray-900">{count}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Kanban board */}
      <div className="grid grid-cols-3 gap-4 px-6 py-4">
        {COLUMNS.map((col) => {
          const cards = byStage(col.id);
          return (
            <div key={col.id}>
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${col.dot}`} />
                  <span className={`text-xs font-bold uppercase tracking-widest ${col.accent}`}>{col.label}</span>
                  <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-bold text-gray-500">{cards.length}</span>
                </div>
              </div>
              <p className="mb-3 text-[10px] text-gray-400 leading-relaxed">{col.hint}</p>
              <div className="space-y-2.5">
                {cards.map((t) => <TemplateCard key={t.template_key} t={t} stage={col.id} />)}
                {cards.length === 0 && (
                  <div className="rounded-xl border border-dashed border-gray-200 py-8 text-center text-xs text-gray-300">No templates</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
