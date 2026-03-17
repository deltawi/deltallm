import { useState } from "react";
import {
  BookOpen,
  ChevronRight,
  FileText,
  Hash,
  Plus,
  Search,
  Sparkles,
  Tag,
  Trash2,
  X,
} from "lucide-react";

const TEMPLATES = [
  {
    template_key:  "support.reply",
    name:          "Support Reply",
    description:   "Customer support assistant for handling inbound tickets and crafting empathetic responses.",
    version_count: 4,
    label_count:   2,
    binding_count: 3,
    published: true,
    labels:    ["production", "staging"],
    updated_at: "2 hours ago",
    owner_scope: "org:acme",
  },
  {
    template_key:  "code.review",
    name:          "Code Review",
    description:   "Reviews pull requests for readability, correctness, and style. Outputs structured feedback.",
    version_count: 2,
    label_count:   1,
    binding_count: 1,
    published: true,
    labels:    ["production"],
    updated_at: "1 day ago",
    owner_scope: "team:engineering",
  },
  {
    template_key:  "docs.summarize",
    name:          "Document Summariser",
    description:   "Summarises long documents into bullet-pointed executive summaries.",
    version_count: 1,
    label_count:   0,
    binding_count: 0,
    published: false,
    labels:    [],
    updated_at: "3 days ago",
    owner_scope: null,
  },
  {
    template_key:  "sales.outreach",
    name:          "Sales Outreach",
    description:   "Personalised outreach emails based on CRM context and product positioning.",
    version_count: 3,
    label_count:   2,
    binding_count: 2,
    published: true,
    labels:    ["production", "a/b-test"],
    updated_at: "5 days ago",
    owner_scope: "team:sales",
  },
  {
    template_key:  "legal.summary",
    name:          "Legal Contract Summary",
    description:   "Extracts key clauses, obligations, and red flags from legal contracts.",
    version_count: 1,
    label_count:   0,
    binding_count: 0,
    published: false,
    labels:    [],
    updated_at: "1 week ago",
    owner_scope: "org:acme",
  },
  {
    template_key:  "onboarding.welcome",
    name:          "Onboarding Welcome",
    description:   "Generates personalised welcome messages for new users based on their role.",
    version_count: 2,
    label_count:   1,
    binding_count: 4,
    published: true,
    labels:    ["production"],
    updated_at: "2 weeks ago",
    owner_scope: null,
  },
];

function TemplateCard({ t, onDelete }: { t: typeof TEMPLATES[0]; onDelete: () => void }) {
  return (
    <div className="group relative flex flex-col gap-3 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm transition hover:border-blue-200 hover:shadow-md cursor-pointer">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-bold text-gray-900">{t.name}</span>
            {t.published
              ? <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">published</span>
              : <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">draft</span>}
          </div>
          <code className="text-[11px] text-gray-400 font-mono">{t.template_key}</code>
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="shrink-0 rounded-lg p-1.5 text-gray-300 opacity-0 transition hover:bg-red-50 hover:text-red-400 group-hover:opacity-100"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Description */}
      {t.description && (
        <p className="text-xs leading-relaxed text-gray-500 line-clamp-2">{t.description}</p>
      )}

      {/* Labels */}
      {t.labels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {t.labels.map((label) => (
            <span key={label} className="inline-flex items-center gap-1 rounded-full border border-violet-100 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
              <Tag className="h-2.5 w-2.5" /> {label}
            </span>
          ))}
        </div>
      )}

      {/* Stats row */}
      <div className="flex items-center justify-between border-t border-gray-100 pt-3">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1 text-[11px] text-gray-500">
            <FileText className="h-3 w-3" /> {t.version_count}v
          </span>
          <span className="flex items-center gap-1 text-[11px] text-gray-500">
            <Tag className="h-3 w-3" /> {t.label_count} labels
          </span>
          <span className="flex items-center gap-1 text-[11px] text-gray-500">
            <Hash className="h-3 w-3" /> {t.binding_count} bindings
          </span>
        </div>
        <div className="flex items-center gap-1">
          {t.owner_scope && (
            <span className="text-[10px] text-gray-400">{t.owner_scope}</span>
          )}
          <ChevronRight className="h-3.5 w-3.5 text-gray-300 group-hover:text-blue-400 transition" />
        </div>
      </div>

      <div className="text-[10px] text-gray-300">Updated {t.updated_at}</div>
    </div>
  );
}

function CreateDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form, setForm] = useState({ template_key: "", name: "", description: "", owner_scope: "" });
  const [showOptional, setShowOptional] = useState(false);
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/20" onClick={onClose} />
      <div className="flex w-[440px] flex-col border-l border-gray-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Create Prompt Template</h2>
            <p className="text-xs text-gray-500">Shell only — you'll add the prompt body on the next page.</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-gray-100">
            <X className="h-4 w-4 text-gray-400" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          <div className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm">
            <div className="font-semibold text-amber-800">What happens next</div>
            <div className="mt-1 text-amber-700 text-xs">Creates the shell only. You'll write the system prompt, define variables, and validate before registering a version.</div>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Template Key <span className="text-red-500">*</span></label>
            <input
              value={form.template_key}
              onChange={(e) => setForm({ ...form, template_key: e.target.value })}
              placeholder="support.reply"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
            <p className="mt-1 text-xs text-gray-400">Stable key used by labels, bindings, and API calls.</p>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Prompt Name <span className="text-red-500">*</span></label>
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Support Reply Prompt"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
          </div>

          <div>
            <button onClick={() => setShowOptional(!showOptional)} className="flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-700">
              <ChevronRight className={`h-4 w-4 transition ${showOptional ? "rotate-90" : ""}`} />
              Optional metadata
            </button>
            {showOptional && (
              <div className="mt-3 space-y-3">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Description</label>
                  <textarea
                    value={form.description}
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                    placeholder="Used for customer support responses."
                    rows={2}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Owner Scope</label>
                  <input
                    value={form.owner_scope}
                    onChange={(e) => setForm({ ...form, owner_scope: e.target.value })}
                    placeholder="platform / team:ops / org:acme"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
                  />
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="border-t border-gray-200 px-5 py-4 flex gap-2 justify-end">
          <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">Cancel</button>
          <button className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700">Create and Continue →</button>
        </div>
      </div>
    </div>
  );
}

export function PromptRegistryList() {
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const filtered = TEMPLATES.filter(
    (t) =>
      t.template_key.includes(search.toLowerCase()) ||
      t.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-gray-50">
      <CreateDrawer open={createOpen} onClose={() => setCreateOpen(false)} />

      {/* Header */}
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Prompt Registry</h1>
            <p className="mt-0.5 text-sm text-gray-500">Manage versioned prompt templates — author, label, and bind them to model groups or API keys.</p>
          </div>
          <button
            onClick={() => setCreateOpen(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-amber-700"
          >
            <Plus className="h-4 w-4" /> Create Prompt
          </button>
        </div>
      </div>

      {/* Setup guide */}
      <div className="mx-6 mt-4 overflow-hidden rounded-2xl border border-amber-100 bg-gradient-to-br from-amber-50 via-white to-slate-50">
        <div className="flex items-center gap-4 px-5 py-4">
          <div className="flex-1">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs font-semibold text-amber-700 shadow-sm ring-1 ring-amber-100">
              <Sparkles className="h-3 w-3" /> Recommended setup order
            </div>
            <p className="mt-2 text-sm text-slate-600">Create the shell, write the system prompt, validate, then label a version as <em>production</em> before binding it.</p>
          </div>
          <div className="hidden sm:flex items-center gap-2">
            {["Create shell", "Author version", "Validate & register"].map((step, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-center shadow-sm">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">Step {i + 1}</div>
                  <div className="mt-0.5 text-xs font-semibold text-slate-700">{step}</div>
                </div>
                {i < 2 && <ChevronRight className="h-4 w-4 shrink-0 text-slate-300" />}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Search + grid */}
      <div className="px-6 py-4 space-y-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search prompts..."
            className="w-full rounded-xl border border-gray-200 bg-white py-2.5 pl-9 pr-4 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          {filtered.map((t) => (
            <TemplateCard key={t.template_key} t={t} onDelete={() => {}} />
          ))}
        </div>

        {filtered.length === 0 && (
          <div className="rounded-2xl border border-gray-200 bg-white py-12 text-center text-sm text-gray-400">No prompt templates found.</div>
        )}

        <div className="flex items-center justify-between text-xs text-gray-400 px-1">
          <span>{filtered.length} templates</span>
          <div className="flex items-center gap-2">
            <button className="rounded-lg border border-gray-200 px-3 py-1.5 text-gray-500 hover:bg-gray-50 disabled:opacity-40" disabled>← Prev</button>
            <span className="text-gray-500">Page 1 of 1</span>
            <button className="rounded-lg border border-gray-200 px-3 py-1.5 text-gray-500 hover:bg-gray-50 disabled:opacity-40" disabled>Next →</button>
          </div>
        </div>
      </div>
    </div>
  );
}
