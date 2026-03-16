/**
 * Variation B — "Spotlight Search"
 * Hypothesis: Users spend most time finding, not browsing.
 * The registry is a retrieval tool — optimize for speed of access.
 * Search is front-and-center; results render as a compact, scannable feed
 * with an expandable inline preview of the published prompt body.
 */
import { useState } from "react";
import { ChevronDown, ChevronUp, FileText, Hash, Plus, Search, Tag, Trash2, Zap } from "lucide-react";

const TEMPLATES = [
  { template_key: "support.reply",      name: "Support Reply",             description: "Customer support assistant for handling inbound tickets and crafting empathetic, on-brand responses.", version_count: 4, label_count: 2, binding_count: 3, published: true,  labels: ["production", "staging"], owner_scope: "org:acme",         prompt_preview: 'You are a helpful customer support assistant for {product_name}.\n\nAlways be empathetic and professional. Address the customer\'s issue: {issue}', updated_at: "2 hours ago"  },
  { template_key: "code.review",        name: "Code Review",               description: "Reviews pull requests for readability, correctness, and style. Outputs structured feedback.",          version_count: 2, label_count: 1, binding_count: 1, published: true,  labels: ["production"],           owner_scope: "team:engineering", prompt_preview: "Review the following code for: correctness, readability, style adherence.\n\nCode:\n```{language}\n{code}\n```\n\nProvide structured feedback.", updated_at: "1 day ago"  },
  { template_key: "docs.summarize",     name: "Document Summariser",       description: "Summarises long documents into bullet-pointed executive summaries.",                                  version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                       owner_scope: null,               prompt_preview: null, updated_at: "3 days ago"  },
  { template_key: "sales.outreach",     name: "Sales Outreach",            description: "Personalised outreach emails based on CRM context and product positioning.",                          version_count: 3, label_count: 2, binding_count: 2, published: true,  labels: ["production", "a/b-test"], owner_scope: "team:sales",      prompt_preview: "Write a personalised outreach email for {contact_name} at {company}.\n\nContext: {crm_notes}\nProduct fit: {positioning}", updated_at: "5 days ago"  },
  { template_key: "legal.summary",      name: "Legal Contract Summary",    description: "Extracts key clauses, obligations, and red flags from legal contracts.",                             version_count: 1, label_count: 0, binding_count: 0, published: false, labels: [],                       owner_scope: "org:acme",         prompt_preview: null, updated_at: "1 week ago"  },
  { template_key: "onboarding.welcome", name: "Onboarding Welcome",        description: "Generates personalised welcome messages for new users based on their role.",                          version_count: 2, label_count: 1, binding_count: 4, published: true,  labels: ["production"],           owner_scope: null,               prompt_preview: "Welcome {user_name} to {product_name}!\n\nAs a {role}, here's what you can do first:\n{role_specific_guide}", updated_at: "2 weeks ago" },
];

const QUICK_FILTERS = ["All", "Published", "Draft", "Bound", "Unbound"];

function highlight(text: string, query: string) {
  if (!query) return <>{text}</>;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-amber-100 text-amber-800 rounded px-0.5">{text.slice(idx, idx + query.length)}</mark>
      {text.slice(idx + query.length)}
    </>
  );
}

function ResultRow({ t, query }: { t: typeof TEMPLATES[0]; query: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-gray-100 last:border-0">
      <div
        className="flex items-start gap-4 px-5 py-3.5 hover:bg-gray-50 cursor-pointer transition"
        onClick={() => {}}
      >
        {/* Status dot */}
        <div className="mt-1 flex shrink-0 flex-col items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${t.published ? "bg-emerald-500" : "bg-amber-400"}`} />
        </div>

        {/* Main content */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-gray-900">{highlight(t.name, query)}</span>
            <code className="text-[11px] text-gray-400 font-mono">{highlight(t.template_key, query)}</code>
            {t.labels.map((l) => (
              <span key={l} className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold text-violet-600">{l}</span>
            ))}
          </div>
          <p className="mt-0.5 text-xs text-gray-500 line-clamp-1">{t.description}</p>

          {/* Inline stats */}
          <div className="mt-1.5 flex items-center gap-3 text-[11px] text-gray-400">
            <span className="flex items-center gap-1"><FileText className="h-3 w-3" /> {t.version_count}v</span>
            <span className="flex items-center gap-1"><Tag className="h-3 w-3" /> {t.label_count}</span>
            <span className="flex items-center gap-1"><Hash className="h-3 w-3" /> {t.binding_count} bindings</span>
            {t.owner_scope && <span className="text-gray-300">{t.owner_scope}</span>}
            <span className="text-gray-300">· {t.updated_at}</span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex shrink-0 items-center gap-1.5">
          {t.prompt_preview && (
            <button
              onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
              className="flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-[11px] text-gray-500 hover:bg-gray-100"
            >
              Preview {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </button>
          )}
          <button className="rounded-lg p-1 text-gray-300 hover:bg-red-50 hover:text-red-400" onClick={(e) => e.stopPropagation()}>
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Expandable prompt preview */}
      {expanded && t.prompt_preview && (
        <div className="mx-5 mb-3 overflow-hidden rounded-xl border border-gray-200 bg-gray-950">
          <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">Published prompt body — v{t.version_count}</span>
            <span className="text-[10px] text-violet-400">label: {t.labels[0] || "—"}</span>
          </div>
          <pre className="overflow-x-auto px-4 py-3 text-xs leading-relaxed text-gray-200">{t.prompt_preview}</pre>
        </div>
      )}
    </div>
  );
}

export function PromptRegistryV2() {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");

  const filtered = TEMPLATES.filter((t) => {
    const matchesSearch = !query || t.name.toLowerCase().includes(query.toLowerCase()) || t.template_key.includes(query.toLowerCase()) || t.description.toLowerCase().includes(query.toLowerCase());
    const matchesFilter = filter === "All"
      || (filter === "Published" && t.published)
      || (filter === "Draft" && !t.published)
      || (filter === "Bound" && t.binding_count > 0)
      || (filter === "Unbound" && t.binding_count === 0);
    return matchesSearch && matchesFilter;
  });

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Hero search area */}
      <div className="border-b border-gray-200 bg-white">
        <div className="px-6 pt-6 pb-0">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-lg font-bold text-gray-900">Prompt Registry</h1>
            <button className="inline-flex items-center gap-1.5 rounded-xl bg-amber-600 px-3 py-2 text-sm font-medium text-white hover:bg-amber-700">
              <Plus className="h-4 w-4" /> New
            </button>
          </div>

          {/* Large search */}
          <div className="relative">
            <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-gray-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a prompt by name, key, or description..."
              autoFocus
              className="w-full rounded-2xl border-2 border-gray-200 bg-white px-5 py-3.5 pl-12 text-base font-medium shadow-sm transition focus:border-amber-400 focus:outline-none focus:ring-0"
            />
            {query && (
              <button onClick={() => setQuery("")} className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-gray-400 hover:text-gray-600">
                clear
              </button>
            )}
          </div>

          {/* Quick filters */}
          <div className="mt-3 flex gap-1.5 pb-3">
            {QUICK_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded-full px-3 py-1 text-xs font-semibold transition ${filter === f ? "bg-amber-600 text-white" : "border border-gray-200 bg-white text-gray-500 hover:bg-gray-50"}`}
              >
                {f}
              </button>
            ))}
            <span className="ml-auto self-center text-xs text-gray-400">{filtered.length} results</span>
          </div>
        </div>
      </div>

      {/* Results feed */}
      <div className="mx-6 mt-4 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        {/* Column headers */}
        <div className="grid border-b border-gray-100 bg-gray-50 px-5 py-2.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400" style={{ gridTemplateColumns: "0.5rem 1fr auto" }}>
          <div />
          <div>Prompt</div>
          <div />
        </div>

        {filtered.map((t) => (
          <ResultRow key={t.template_key} t={t} query={query} />
        ))}

        {filtered.length === 0 && (
          <div className="py-16 text-center">
            <Search className="mx-auto h-8 w-8 text-gray-200" />
            <p className="mt-3 text-sm text-gray-400">No prompts match <strong className="text-gray-600">"{query}"</strong></p>
            <button className="mt-3 text-xs text-amber-600 hover:underline" onClick={() => { setQuery(""); setFilter("All"); }}>Clear search</button>
          </div>
        )}
      </div>

      {/* Keyboard hint */}
      <p className="mt-3 px-7 text-[11px] text-gray-300">
        Click a row to open the template · Click <strong>Preview</strong> to inspect the published prompt body inline
      </p>
    </div>
  );
}
