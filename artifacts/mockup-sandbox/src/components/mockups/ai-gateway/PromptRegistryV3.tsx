/**
 * Variation C — "Owner Scope Library"
 * Hypothesis: At scale, ownership is the primary navigation dimension.
 * Users manage prompts that belong to platforms, orgs, and teams.
 * The registry is a governed asset library — templates belong somewhere.
 * Left sidebar scope tree; main panel shows compact table for selected scope.
 */
import { useState } from "react";
import { Building2, ChevronRight, FileText, Hash, Layers, Plus, Tag, Trash2, Users } from "lucide-react";

const SCOPES = [
  { id: "all",             label: "All Prompts",    icon: Layers,   count: 6 },
  { id: "platform",        label: "Platform",       icon: Layers,   count: 0 },
  { id: "org:acme",        label: "Acme Corp",      icon: Building2,count: 2 },
  { id: "team:engineering",label: "Engineering",    icon: Users,    count: 1 },
  { id: "team:sales",      label: "Sales",          icon: Users,    count: 1 },
  { id: "unscoped",        label: "Unscoped",       icon: FileText, count: 2 },
];

const TEMPLATES = [
  { template_key: "support.reply",      name: "Support Reply",         version_count: 4, label_count: 2, binding_count: 3, published: true,  labels: ["production","staging"], owner_scope: "org:acme",         updated_at: "2 hrs ago"  },
  { template_key: "legal.summary",      name: "Legal Contract Summary",version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                       owner_scope: "org:acme",         updated_at: "1 wk ago"   },
  { template_key: "code.review",        name: "Code Review",           version_count: 2, label_count: 1, binding_count: 1, published: true,  labels: ["production"],           owner_scope: "team:engineering", updated_at: "1 day ago"  },
  { template_key: "sales.outreach",     name: "Sales Outreach",        version_count: 3, label_count: 2, binding_count: 2, published: true,  labels: ["production","a/b-test"],owner_scope: "team:sales",       updated_at: "5 days ago" },
  { template_key: "docs.summarize",     name: "Document Summariser",   version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                       owner_scope: null,               updated_at: "3 days ago" },
  { template_key: "onboarding.welcome", name: "Onboarding Welcome",    version_count: 2, label_count: 1, binding_count: 4, published: true,  labels: ["production"],           owner_scope: null,               updated_at: "2 wks ago"  },
];

function ScopeIcon({ id }: { id: string }) {
  const scope = SCOPES.find((s) => s.id === id);
  if (!scope) return null;
  const Icon = scope.icon;
  return <Icon className="h-3.5 w-3.5" />;
}

export function PromptRegistryV3() {
  const [selectedScope, setSelectedScope] = useState("all");
  const [showCreate, setShowCreate] = useState(false);

  const visible = TEMPLATES.filter((t) => {
    if (selectedScope === "all") return true;
    if (selectedScope === "unscoped") return !t.owner_scope;
    return t.owner_scope === selectedScope;
  });

  const selectedLabel = SCOPES.find((s) => s.id === selectedScope)?.label || "All";

  return (
    <div className="flex min-h-screen bg-gray-50">
      {/* Sidebar scope tree */}
      <div className="flex w-52 shrink-0 flex-col border-r border-gray-200 bg-white">
        <div className="border-b border-gray-100 px-4 py-4">
          <h1 className="text-sm font-bold text-gray-900">Prompt Registry</h1>
          <p className="text-[11px] text-gray-400 mt-0.5">Browse by owner</p>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {/* All */}
          {SCOPES.slice(0, 1).map((s) => {
            const Icon = s.icon;
            const active = selectedScope === s.id;
            return (
              <button key={s.id} onClick={() => setSelectedScope(s.id)}
                className={`flex w-full items-center gap-2.5 px-4 py-2 text-left text-sm transition ${active ? "bg-amber-50 text-amber-700 font-semibold" : "text-gray-600 hover:bg-gray-50"}`}
              >
                <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? "text-amber-500" : "text-gray-400"}`} />
                <span className="flex-1 text-xs">{s.label}</span>
                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${active ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-400"}`}>{s.count}</span>
              </button>
            );
          })}

          {/* Divider */}
          <div className="mx-4 my-2 border-t border-gray-100" />
          <div className="px-4 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">Organizations</div>
          {SCOPES.slice(2, 3).map((s) => {
            const Icon = s.icon;
            const active = selectedScope === s.id;
            return (
              <button key={s.id} onClick={() => setSelectedScope(s.id)}
                className={`flex w-full items-center gap-2.5 px-4 py-2 text-left transition ${active ? "bg-amber-50 text-amber-700 font-semibold" : "text-gray-600 hover:bg-gray-50"}`}
              >
                <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? "text-amber-500" : "text-gray-400"}`} />
                <span className="flex-1 text-xs">{s.label}</span>
                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${active ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-400"}`}>{s.count}</span>
              </button>
            );
          })}

          <div className="mx-4 my-2 border-t border-gray-100" />
          <div className="px-4 pb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">Teams</div>
          {SCOPES.slice(3, 5).map((s) => {
            const Icon = s.icon;
            const active = selectedScope === s.id;
            return (
              <button key={s.id} onClick={() => setSelectedScope(s.id)}
                className={`flex w-full items-center gap-2.5 px-4 py-2 pl-6 text-left transition ${active ? "bg-amber-50 text-amber-700 font-semibold" : "text-gray-600 hover:bg-gray-50"}`}
              >
                <Icon className={`h-3 w-3 shrink-0 ${active ? "text-amber-500" : "text-gray-400"}`} />
                <span className="flex-1 text-xs">{s.label}</span>
                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${active ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-400"}`}>{s.count}</span>
              </button>
            );
          })}

          <div className="mx-4 my-2 border-t border-gray-100" />
          {SCOPES.slice(5).map((s) => {
            const Icon = s.icon;
            const active = selectedScope === s.id;
            return (
              <button key={s.id} onClick={() => setSelectedScope(s.id)}
                className={`flex w-full items-center gap-2.5 px-4 py-2 text-left transition ${active ? "bg-amber-50 text-amber-700 font-semibold" : "text-gray-600 hover:bg-gray-50"}`}
              >
                <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? "text-amber-500" : "text-gray-400"}`} />
                <span className="flex-1 text-xs">{s.label}</span>
                <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${active ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-400"}`}>{s.count}</span>
              </button>
            );
          })}
        </div>

        <div className="border-t border-gray-100 p-3">
          <button onClick={() => setShowCreate(true)} className="flex w-full items-center gap-2 rounded-xl bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-700">
            <Plus className="h-3.5 w-3.5" /> New Prompt
          </button>
        </div>
      </div>

      {/* Main panel */}
      <div className="flex-1 overflow-auto">
        {/* Panel header */}
        <div className="border-b border-gray-200 bg-white px-5 py-3.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-800">
              <ScopeIcon id={selectedScope} />
              {selectedLabel}
              <span className="text-gray-400 font-normal">· {visible.length} prompts</span>
            </div>
          </div>
        </div>

        {/* Table */}
        <div className="p-4">
          <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
            <div className="grid gap-4 border-b border-gray-100 bg-gray-50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400"
              style={{ gridTemplateColumns: "1fr 100px 80px 80px 80px 80px 32px" }}>
              <div>Prompt</div>
              <div className="text-center">Labels</div>
              <div className="text-center">Versions</div>
              <div className="text-center">Bindings</div>
              <div className="text-center">Status</div>
              <div className="text-center">Updated</div>
              <div />
            </div>

            {visible.map((t, i) => (
              <div
                key={t.template_key}
                className={`group grid items-center gap-4 px-4 py-3 hover:bg-amber-50/30 cursor-pointer transition ${i < visible.length - 1 ? "border-b border-gray-100" : ""}`}
                style={{ gridTemplateColumns: "1fr 100px 80px 80px 80px 80px 32px" }}
              >
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-gray-900 leading-snug">{t.name}</div>
                  <code className="text-[10px] text-gray-400 font-mono">{t.template_key}</code>
                </div>

                <div className="flex flex-wrap justify-center gap-1">
                  {t.labels.length > 0
                    ? t.labels.map((l) => <span key={l} className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold text-violet-600">{l}</span>)
                    : <span className="text-[10px] text-gray-300">—</span>}
                </div>

                <div className="text-center text-sm font-semibold text-gray-700">{t.version_count}</div>
                <div className="text-center text-sm font-semibold text-gray-700">{t.binding_count}</div>

                <div className="flex justify-center">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${t.published ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
                    {t.published ? "published" : "draft"}
                  </span>
                </div>

                <div className="text-center text-[11px] text-gray-400">{t.updated_at}</div>

                <div className="flex items-center justify-end gap-0.5 opacity-0 group-hover:opacity-100 transition">
                  <button className="rounded p-1 hover:bg-red-50" onClick={(e) => e.stopPropagation()}>
                    <Trash2 className="h-3 w-3 text-red-400" />
                  </button>
                  <ChevronRight className="h-3.5 w-3.5 text-gray-300" />
                </div>
              </div>
            ))}

            {visible.length === 0 && (
              <div className="py-12 text-center text-sm text-gray-400">
                No prompts in <strong>{selectedLabel}</strong>
                <div className="mt-2"><button onClick={() => setShowCreate(true)} className="text-xs text-amber-600 hover:underline">Create one →</button></div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-[400px] rounded-2xl border border-gray-200 bg-white p-6 shadow-2xl">
            <h2 className="mb-4 text-base font-semibold text-gray-900">New Prompt Template</h2>
            <div className="space-y-3">
              <div><label className="mb-1 block text-xs font-medium text-gray-700">Template Key *</label><input placeholder="support.reply" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" /></div>
              <div><label className="mb-1 block text-xs font-medium text-gray-700">Name *</label><input placeholder="Support Reply" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" /></div>
              <div><label className="mb-1 block text-xs font-medium text-gray-700">Owner Scope</label>
                <select className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500">
                  <option value="">Unscoped</option>
                  <option>org:acme</option>
                  <option>team:engineering</option>
                  <option>team:sales</option>
                </select>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setShowCreate(false)} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-600">Cancel</button>
              <button className="rounded-lg bg-amber-600 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-700">Create →</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
