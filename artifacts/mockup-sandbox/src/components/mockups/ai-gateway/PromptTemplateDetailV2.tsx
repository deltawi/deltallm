import { useState } from "react";
import {
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  Clock,
  Copy,
  FileText,
  Hash,
  Layers,
  Pencil,
  Play,
  Plus,
  Settings,
  Tag,
  Trash2,
  X,
  Zap,
} from "lucide-react";

const TABS = [
  { id: "versions",  label: "Versions",  icon: FileText  },
  { id: "labels",    label: "Labels",    icon: Tag       },
  { id: "test",      label: "Test",      icon: Play      },
  { id: "settings",  label: "Settings",  icon: Settings  },
] as const;

type TabId = (typeof TABS)[number]["id"];

const TEMPLATE = {
  template_key: "support.reply",
  name: "Support Reply",
  description: "Customer support assistant for handling inbound tickets and crafting empathetic, on-brand responses.",
  owner_scope: "org:acme",
  version_count: 4,
  label_count: 2,
  binding_count: 3,
};

const VERSIONS = [
  { version: 4, status: "draft",     created_at: "2 hours ago",  published_at: null,          published_by: null },
  { version: 3, status: "published", created_at: "1 day ago",    published_at: "1 day ago",   published_by: "alice@acme.com" },
  { version: 2, status: "archived",  created_at: "1 week ago",   published_at: "1 week ago",  published_by: "alice@acme.com" },
  { version: 1, status: "archived",  created_at: "2 weeks ago",  published_at: "2 weeks ago", published_by: "bob@acme.com" },
];

const LABELS = [
  { label: "production", version: 3, created_at: "1 day ago" },
  { label: "staging",    version: 4, created_at: "2 hours ago" },
];

const STATUS_STYLES: Record<string, string> = {
  draft:     "bg-amber-100 text-amber-700",
  published: "bg-emerald-100 text-emerald-700",
  archived:  "bg-gray-100 text-gray-500",
};

function StatBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{label}</span>
      <span className="text-sm font-bold text-gray-900">{value}</span>
    </div>
  );
}

function JourneyStep({ n, label, done, hint }: { n: number; label: string; done: boolean; hint: string }) {
  return (
    <div className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 ${done ? "border-emerald-200 bg-emerald-50" : "border-gray-200 bg-white"}`}>
      <div className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${done ? "bg-emerald-500 text-white" : "bg-gray-200 text-gray-500"}`}>
        {done ? "✓" : n}
      </div>
      <div>
        <div className={`text-xs font-semibold ${done ? "text-emerald-800" : "text-gray-700"}`}>{label}</div>
        <div className={`text-[10px] ${done ? "text-emerald-600" : "text-gray-400"}`}>{hint}</div>
      </div>
    </div>
  );
}

function VersionsTab() {
  const [showComposer, setShowComposer] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState("You are a helpful customer support assistant for {product_name}.\n\nAlways be empathetic and professional. Address the customer's issue clearly and offer next steps.");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">{VERSIONS.length} versions · v{VERSIONS.find(v => v.status === "published")?.version} published</p>
        <button onClick={() => setShowComposer(!showComposer)} className="inline-flex items-center gap-1.5 rounded-xl bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-700">
          <Plus className="h-3.5 w-3.5" /> New Version
        </button>
      </div>

      {showComposer && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-amber-900">Compose Version 5</h4>
            <button onClick={() => setShowComposer(false)} className="rounded-lg p-1 hover:bg-amber-100"><X className="h-4 w-4 text-amber-600" /></button>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-gray-700">System Prompt</label>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={5}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
            <p className="mt-1 text-xs text-gray-400">Use <code className="bg-gray-100 px-1 rounded">{"{variable_name}"}</code> for interpolation.</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-700">Variables (comma-separated)</label>
              <input defaultValue="product_name, customer_name" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-700">Model Hints (JSON)</label>
              <input defaultValue='{"preferred_mode":"chat"}' className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-amber-500" />
            </div>
          </div>

          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
              <input type="checkbox" className="rounded" />
              Publish immediately
            </label>
            <div className="flex gap-2">
              <button onClick={() => setShowComposer(false)} className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600">Cancel</button>
              <button className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700">Create v5</button>
            </div>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {VERSIONS.map((v) => (
          <div key={v.version} className="flex items-start gap-3 rounded-2xl border border-gray-200 bg-white px-4 py-3">
            <div className="flex flex-col items-center gap-1">
              <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${v.status === "published" ? "bg-emerald-500 text-white" : v.status === "draft" ? "bg-amber-400 text-white" : "bg-gray-200 text-gray-500"}`}>
                v{v.version}
              </div>
              {v.version > 1 && <div className="w-px flex-1 bg-gray-200" style={{ minHeight: 16 }} />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-gray-900">Version {v.version}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${STATUS_STYLES[v.status]}`}>{v.status}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  {v.status === "draft" && (
                    <button className="rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-emerald-700">Publish</button>
                  )}
                  <button className="rounded-lg border border-gray-200 px-2.5 py-1 text-[11px] text-gray-500 hover:bg-gray-50">View</button>
                </div>
              </div>
              <div className="mt-1 flex items-center gap-3 text-[11px] text-gray-400">
                <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> Created {v.created_at}</span>
                {v.published_by && <span>Published by {v.published_by}</span>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function LabelsTab() {
  const [form, setForm] = useState({ label: "production", version: "" });
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-500">Labels are aliases that point to a specific version. Clients and bindings reference the label name (e.g. <code className="bg-gray-100 px-1 rounded text-xs">production</code>), not the version number directly.</p>

      <div className="overflow-hidden rounded-2xl border border-gray-200">
        <div className="grid grid-cols-[1fr_auto_auto_auto] gap-4 border-b border-gray-100 bg-gray-50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
          <div>Label</div>
          <div className="w-20 text-center">Version</div>
          <div className="w-28 text-center">Assigned</div>
          <div className="w-8" />
        </div>

        {LABELS.map((l, i) => (
          <div key={l.label} className={`grid grid-cols-[1fr_auto_auto_auto] items-center gap-4 px-4 py-3 ${i < LABELS.length - 1 ? "border-b border-gray-100" : ""}`}>
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-violet-100 px-2.5 py-1 text-xs font-semibold text-violet-700">{l.label}</span>
            </div>
            <div className="w-20 flex justify-center">
              <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-mono font-semibold text-gray-700">v{l.version}</span>
            </div>
            <div className="w-28 text-center text-xs text-gray-400">{l.created_at}</div>
            <div className="w-8 flex justify-end">
              <button className="rounded-lg p-1 text-gray-300 hover:bg-red-50 hover:text-red-400">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white p-4">
        <h4 className="mb-3 text-sm font-semibold text-gray-900">Assign Label</h4>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-700">Label</label>
            <input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-700">Target Version</label>
            <select value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500">
              <option value="">— Select version —</option>
              {VERSIONS.map((v) => <option key={v.version} value={v.version}>v{v.version} ({v.status})</option>)}
            </select>
          </div>
          <div className="flex items-end">
            <button className="w-full rounded-xl bg-amber-600 px-3 py-2 text-sm font-semibold text-white hover:bg-amber-700">Assign</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function TestTab() {
  const [variables, setVariables] = useState('{\n  "product_name": "DeltaLLM",\n  "customer_name": "Alice"\n}');
  const [label, setLabel] = useState("production");
  const [ran, setRan] = useState(false);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Resolve by Label</label>
          <select value={label} onChange={(e) => setLabel(e.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500">
            {LABELS.map((l) => <option key={l.label} value={l.label}>{l.label} → v{l.version}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Or Specific Version</label>
          <select className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500">
            <option value="">— Use label —</option>
            {VERSIONS.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}
          </select>
        </div>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Variables (JSON)</label>
        <textarea
          value={variables}
          onChange={(e) => setVariables(e.target.value)}
          rows={4}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-amber-500"
        />
      </div>

      <button onClick={() => setRan(true)} className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700">
        <Play className="h-4 w-4" /> Run Dry Test
      </button>

      {ran && (
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-emerald-800">
            <CheckCircle2 className="h-4 w-4" /> Render successful — v3 via label "production"
          </div>
          <pre className="overflow-x-auto rounded-xl bg-gray-950 px-4 py-3 text-xs text-gray-100">{`[
  {
    "role": "system",
    "content": "You are a helpful customer support assistant for DeltaLLM.\\n\\nAlways be empathetic and professional. Address the customer's issue clearly and offer next steps."
  }
]`}</pre>
        </div>
      )}
    </div>
  );
}

function SettingsTab() {
  const [form, setForm] = useState({ name: TEMPLATE.name, description: TEMPLATE.description, owner_scope: TEMPLATE.owner_scope });
  return (
    <div className="space-y-4 max-w-lg">
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Name</label>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" />
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Description</label>
        <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" />
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Owner Scope</label>
        <input value={form.owner_scope || ""} onChange={(e) => setForm({ ...form, owner_scope: e.target.value })} placeholder="platform / team:ops / org:acme" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" />
      </div>

      <div className="flex items-center justify-between pt-2">
        <button className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-50">
          <Trash2 className="h-4 w-4" /> Delete Template
        </button>
        <button className="rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700">Save Changes</button>
      </div>
    </div>
  );
}

export function PromptTemplateDetailV2() {
  const [activeTab, setActiveTab] = useState<TabId>("versions");
  const publishedVersion = VERSIONS.find((v) => v.status === "published");

  const journeySteps = [
    { label: "Create version",   done: VERSIONS.length > 0,           hint: "Shell needs at least one version." },
    { label: "Register label",   done: LABELS.length > 0,             hint: "Label 'production' before binding." },
    { label: "Run test",         done: false,                          hint: "Verify render before consumers use it." },
  ];

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top bar */}
      <div className="border-b border-gray-200 bg-white px-6 py-3">
        <button className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft className="h-4 w-4" /> Back to Prompt Registry
        </button>
      </div>

      {/* Hero header */}
      <div className="relative overflow-hidden border-b border-gray-200 bg-white">
        <div className="absolute inset-0 bg-gradient-to-br from-amber-50 via-white to-slate-50 opacity-70" />
        <div className="absolute right-0 top-0 h-40 w-40 rounded-full bg-amber-100/40 blur-3xl" />

        <div className="relative px-6 pb-5 pt-6">
          {/* Badges */}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {publishedVersion && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700">
                <CheckCircle2 className="h-3.5 w-3.5" /> v{publishedVersion.version} Published
              </span>
            )}
            {LABELS.map((l) => (
              <span key={l.label} className="inline-flex items-center gap-1.5 rounded-full bg-violet-100 px-2.5 py-1 text-xs font-semibold text-violet-700">
                <Tag className="h-3.5 w-3.5" /> {l.label}
              </span>
            ))}
            {TEMPLATE.owner_scope && (
              <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-500">{TEMPLATE.owner_scope}</span>
            )}
          </div>

          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{TEMPLATE.name}</h1>
              <p className="mt-0.5 text-sm text-gray-500">
                Key: <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-700">{TEMPLATE.template_key}</code>
              </p>
              {TEMPLATE.description && (
                <p className="mt-1.5 text-sm text-gray-500 max-w-lg">{TEMPLATE.description}</p>
              )}
            </div>
            <div className="flex gap-2 shrink-0">
              <button className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 shadow-sm hover:bg-gray-50">
                <Pencil className="h-4 w-4" /> Edit
              </button>
              <button className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-500 shadow-sm hover:bg-red-50">
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Stat strip */}
          <div className="mt-5 flex flex-wrap items-center gap-6 divide-x divide-gray-100">
            <StatBadge label="Versions" value={String(TEMPLATE.version_count)} />
            <div className="pl-6"><StatBadge label="Labels" value={String(TEMPLATE.label_count)} /></div>
            <div className="pl-6"><StatBadge label="Bindings" value={String(TEMPLATE.binding_count)} /></div>
            <div className="pl-6"><StatBadge label="Published" value={publishedVersion ? `v${publishedVersion.version}` : "None"} /></div>
          </div>
        </div>
      </div>

      {/* Journey checklist */}
      <div className="mx-6 mt-4 grid grid-cols-3 gap-2">
        {journeySteps.map((s, i) => (
          <JourneyStep key={i} n={i + 1} label={s.label} done={s.done} hint={s.hint} />
        ))}
      </div>

      {/* Tabs */}
      <div className="px-6 pb-8 pt-4">
        <div className="mb-4 flex gap-1 border-b border-gray-200">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition ${activeTab === id ? "border-amber-600 text-amber-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}
            >
              <Icon className="h-4 w-4" /> {label}
            </button>
          ))}
        </div>

        <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          {activeTab === "versions"  && <VersionsTab />}
          {activeTab === "labels"    && <LabelsTab />}
          {activeTab === "test"      && <TestTab />}
          {activeTab === "settings"  && <SettingsTab />}
        </div>
      </div>
    </div>
  );
}
