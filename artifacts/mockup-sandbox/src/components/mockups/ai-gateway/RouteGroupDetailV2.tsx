import { useState } from "react";
import {
  ArrowLeft,
  Brain,
  CheckCircle2,
  ChevronRight,
  Copy,
  GitBranch,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  Route,
  Server,
  Settings,
  Shuffle,
  Tag,
  Terminal,
  Trash2,
  XCircle,
  Zap,
} from "lucide-react";

const TABS = [
  { id: "models",   label: "Models",   icon: Server },
  { id: "test",     label: "Test",     icon: Terminal },
  { id: "settings", label: "Settings", icon: Settings },
  { id: "advanced", label: "Advanced", icon: Layers },
] as const;

type TabId = (typeof TABS)[number]["id"];

const GROUP = {
  group_key: "prod-chat-primary",
  name: "Production Chat",
  mode: "chat",
  routing_strategy: "weighted",
  enabled: true,
  member_count: 4,
};

const MEMBERS = [
  { deployment_id: "dep_gpt4t_01",    model_name: "gpt-4-turbo",      provider: "openai",    healthy: true,  weight: 4, priority: 1 },
  { deployment_id: "dep_gpt4o_02",    model_name: "gpt-4o",           provider: "openai",    healthy: true,  weight: 3, priority: 1 },
  { deployment_id: "dep_claude3_03",  model_name: "claude-3-sonnet",  provider: "anthropic", healthy: true,  weight: 2, priority: 2 },
  { deployment_id: "dep_llama3_04",   model_name: "llama-3-70b",      provider: "groq",      healthy: false, weight: 1, priority: 3 },
];

const POLICY = {
  version: 3,
  status: "published",
  policy_json: { strategy: "weighted", fallback: "shuffle", cooldown_seconds: 60 },
};

const PROMPT = { template_key: "support.reply", label: "production" };

const PROVIDER_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  openai:    { bg: "bg-emerald-50", text: "text-emerald-700", dot: "bg-emerald-500" },
  anthropic: { bg: "bg-violet-50",  text: "text-violet-700",  dot: "bg-violet-500"  },
  groq:      { bg: "bg-orange-50",  text: "text-orange-700",  dot: "bg-orange-500"  },
  azure:     { bg: "bg-blue-50",    text: "text-blue-700",    dot: "bg-blue-500"    },
};

function ProviderPill({ provider }: { provider: string }) {
  const c = PROVIDER_COLORS[provider] || { bg: "bg-gray-100", text: "text-gray-700", dot: "bg-gray-400" };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ${c.bg} ${c.text}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${c.dot}`} />
      {provider.charAt(0).toUpperCase() + provider.slice(1)}
    </span>
  );
}

function StatBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{label}</span>
      <span className="text-sm font-bold text-gray-900">{value}</span>
    </div>
  );
}

function ModelsTab() {
  const [adding, setAdding] = useState(false);
  const totalWeight = MEMBERS.reduce((s, m) => s + (m.weight || 0), 0);

  return (
    <div className="space-y-4">
      {/* Add member row */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">{MEMBERS.length} deployments · {MEMBERS.filter(m => m.healthy).length} healthy</p>
        <button
          onClick={() => setAdding(!adding)}
          className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700"
        >
          <Plus className="h-3.5 w-3.5" /> Add Deployment
        </button>
      </div>

      {adding && (
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-4">
          <h4 className="mb-3 text-sm font-semibold text-blue-900">Add a deployment to this group</h4>
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <label className="mb-1 block text-xs font-medium text-gray-700">Search Deployments</label>
              <input placeholder="Search by model name..." className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-700">Weight</label>
              <input type="number" placeholder="1" className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white">Add</button>
            <button onClick={() => setAdding(false)} className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600">Cancel</button>
          </div>
        </div>
      )}

      {/* Member list */}
      <div className="overflow-hidden rounded-2xl border border-gray-200">
        <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-4 border-b border-gray-100 bg-gray-50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
          <div>Deployment</div>
          <div className="w-24 text-center">Provider</div>
          <div className="w-24 text-center">Weight</div>
          <div className="w-16 text-center">Status</div>
          <div className="w-8" />
        </div>

        {MEMBERS.map((m, i) => {
          const weightPct = totalWeight > 0 ? Math.round((m.weight / totalWeight) * 100) : 0;
          return (
            <div key={m.deployment_id} className={`grid grid-cols-[1fr_auto_auto_auto_auto] items-center gap-4 px-4 py-3 ${i < MEMBERS.length - 1 ? "border-b border-gray-100" : ""} hover:bg-gray-50`}>
              <div>
                <div className="text-sm font-semibold text-gray-900">{m.model_name}</div>
                <code className="text-[11px] text-gray-400 font-mono">{m.deployment_id}</code>
              </div>

              <div className="w-24 flex justify-center">
                <ProviderPill provider={m.provider} />
              </div>

              <div className="w-24 flex flex-col items-center gap-1">
                <div className="h-1.5 w-16 overflow-hidden rounded-full bg-gray-100">
                  <div className="h-full rounded-full bg-blue-400" style={{ width: `${weightPct}%` }} />
                </div>
                <span className="text-[11px] font-semibold text-gray-600">{m.weight} ({weightPct}%)</span>
              </div>

              <div className="w-16 flex justify-center">
                {m.healthy
                  ? <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-600"><CheckCircle2 className="h-3.5 w-3.5" /> Healthy</span>
                  : <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-red-500"><XCircle className="h-3.5 w-3.5" /> Down</span>}
              </div>

              <div className="w-8 flex justify-end">
                <button className="rounded-lg p-1 text-gray-300 hover:bg-red-50 hover:text-red-400">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TestTab() {
  const [lang, setLang] = useState<"curl" | "python">("curl");
  const curlCode = `curl https://api.deltallm.io/v1/chat/completions \\
  -H "Authorization: Bearer $DELTALLM_API_KEY" \\
  -d '{"model": "${GROUP.group_key}", "messages": [{"role": "user", "content": "Hello!"}]}'`;
  const pyCode = `from openai import OpenAI
client = OpenAI(base_url="https://api.deltallm.io/v1", api_key=os.environ["DELTALLM_API_KEY"])
response = client.chat.completions.create(model="${GROUP.group_key}", messages=[{"role":"user","content":"Hello!"}])`;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-gray-100 bg-gray-50 p-4">
        <div className="mb-2 flex items-center justify-between">
          <h4 className="text-sm font-semibold text-gray-700">Test This Group</h4>
          <div className="flex gap-1 rounded-lg border border-gray-200 bg-white p-0.5">
            {(["curl", "python"] as const).map((t) => (
              <button key={t} onClick={() => setLang(t)} className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${lang === t ? "bg-blue-600 text-white" : "text-gray-500"}`}>
                {t === "curl" ? "cURL" : "Python"}
              </button>
            ))}
          </div>
        </div>
        <pre className="overflow-x-auto rounded-xl bg-gray-950 px-4 py-3 text-xs leading-relaxed text-gray-100">
          {lang === "curl" ? curlCode : pyCode}
        </pre>
      </div>

      <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 text-sm">
        <div className="flex items-start gap-3">
          <Brain className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
          <div>
            <div className="font-semibold text-blue-800">How this group routes traffic</div>
            <div className="mt-1 text-blue-700">
              Requests to <code className="rounded bg-white px-1 py-0.5 text-xs">{GROUP.group_key}</code> are distributed across {MEMBERS.length} deployments using <strong>weighted</strong> routing. Policy v{POLICY.version} is active.
            </div>
          </div>
        </div>
      </div>

      {PROMPT && (
        <div className="rounded-2xl border border-violet-100 bg-violet-50 p-4 text-sm">
          <div className="flex items-start gap-3">
            <Tag className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" />
            <div>
              <div className="font-semibold text-violet-800">Prompt bound</div>
              <div className="mt-1 text-violet-700">
                Requests resolve prompt <code className="rounded bg-white px-1 py-0.5 text-xs">{PROMPT.template_key}</code> @ label <code className="rounded bg-white px-1 py-0.5 text-xs">{PROMPT.label}</code>.
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsTab() {
  const [form, setForm] = useState({ name: GROUP.name, mode: GROUP.mode, enabled: GROUP.enabled });
  return (
    <div className="space-y-4 max-w-lg">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Display Name</label>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Workload Mode</label>
          <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
            {["chat", "embedding", "audio_speech", "image_generation", "rerank"].map((m) => (
              <option key={m} value={m}>{m.replace(/_/g, " ")}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-gray-900">Live Traffic</div>
          <div className="text-xs text-gray-500">Disabling this stops all requests to the group.</div>
        </div>
        <div
          className={`relative h-6 w-10 cursor-pointer rounded-full transition-colors ${form.enabled ? "bg-blue-600" : "bg-gray-300"}`}
          onClick={() => setForm({ ...form, enabled: !form.enabled })}
        >
          <span className={`absolute top-[3px] h-[18px] w-[18px] rounded-full bg-white shadow-sm transition-transform ${form.enabled ? "translate-x-[19px]" : "translate-x-[3px]"}`} />
        </div>
      </div>

      <div className="flex justify-end">
        <button className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700">Save Settings</button>
      </div>
    </div>
  );
}

function AdvancedTab() {
  const [policyText, setPolicyText] = useState(JSON.stringify(POLICY.policy_json, null, 2));

  return (
    <div className="space-y-5">
      {/* Prompt Binding */}
      <div className="rounded-2xl border border-gray-200 bg-white p-4">
        <h3 className="mb-3 text-sm font-semibold text-gray-900">Prompt Binding</h3>
        <div className="flex items-center justify-between rounded-xl border border-violet-100 bg-violet-50 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-violet-900">{PROMPT.template_key}</div>
            <div className="text-xs text-violet-600">label: <strong>{PROMPT.label}</strong> · priority 100 · enabled</div>
          </div>
          <button className="rounded-lg p-1.5 text-violet-400 hover:bg-red-50 hover:text-red-400">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="mt-3 flex gap-2">
          <input placeholder="template_key" className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          <input placeholder="production" className="w-28 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          <button className="rounded-xl bg-violet-600 px-3 py-2 text-xs font-semibold text-white hover:bg-violet-700">Bind</button>
        </div>
      </div>

      {/* Policy Editor */}
      <div className="rounded-2xl border border-gray-200 bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Routing Policy</h3>
            <p className="text-xs text-gray-500">Version {POLICY.version} · <span className="text-emerald-600 font-medium">Published</span></p>
          </div>
          <div className="flex gap-2">
            <button className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50">Validate</button>
            <button className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50">Save Draft</button>
            <button className="rounded-xl bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700">Publish</button>
          </div>
        </div>
        <textarea
          value={policyText}
          onChange={(e) => setPolicyText(e.target.value)}
          rows={6}
          className="w-full rounded-xl border border-gray-200 bg-gray-950 px-4 py-3 font-mono text-xs text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {/* Version history */}
      <div className="rounded-2xl border border-gray-200 bg-white p-4">
        <h3 className="mb-3 text-sm font-semibold text-gray-900">Policy History</h3>
        <div className="space-y-2">
          {[3, 2, 1].map((v) => (
            <div key={v} className="flex items-center justify-between rounded-xl border border-gray-100 px-4 py-2.5">
              <div className="flex items-center gap-3">
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${v === 3 ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-500"}`}>
                  {v === 3 ? "published" : "archived"}
                </span>
                <span className="text-sm font-medium text-gray-700">Version {v}</span>
              </div>
              {v !== 3 && (
                <button className="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-500 hover:bg-gray-50">Roll back</button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function RouteGroupDetailV2() {
  const [activeTab, setActiveTab] = useState<TabId>("models");
  const healthyCount = MEMBERS.filter((m) => m.healthy).length;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top bar */}
      <div className="border-b border-gray-200 bg-white px-6 py-3">
        <button className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800">
          <ArrowLeft className="h-4 w-4" /> Back to Model Groups
        </button>
      </div>

      {/* Hero header */}
      <div className="relative overflow-hidden border-b border-gray-200 bg-white">
        <div className="absolute inset-0 bg-gradient-to-br from-blue-50 via-white to-slate-50 opacity-70" />
        <div className="absolute right-0 top-0 h-40 w-40 rounded-full bg-blue-100/40 blur-3xl" />

        <div className="relative px-6 pb-5 pt-6">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 px-2.5 py-1 text-xs font-semibold text-blue-700">
              <Brain className="h-3.5 w-3.5" /> Chat
            </span>
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${GROUP.enabled ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-500"}`}>
              {GROUP.enabled ? <><CheckCircle2 className="h-3.5 w-3.5" /> Live</> : "Off"}
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">
              <Shuffle className="h-3.5 w-3.5" /> Weighted routing
            </span>
          </div>

          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{GROUP.name}</h1>
              <p className="mt-0.5 text-sm text-gray-500">
                Group key: <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-700">{GROUP.group_key}</code>
              </p>
            </div>
            <div className="flex gap-2">
              <button className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 shadow-sm hover:bg-gray-50">
                <Pencil className="h-4 w-4" /> Edit
              </button>
              <button className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-500 shadow-sm hover:bg-red-50">
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-6 divide-x divide-gray-100">
            <StatBadge label="Members" value={String(GROUP.member_count)} />
            <div className="pl-6"><StatBadge label="Healthy" value={`${healthyCount}/${GROUP.member_count}`} /></div>
            <div className="pl-6"><StatBadge label="Policy" value={`v${POLICY.version} published`} /></div>
            <div className="pl-6"><StatBadge label="Prompt" value={PROMPT.template_key} /></div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="px-6 pb-8 pt-0">
        <div className="mb-4 flex gap-1 border-b border-gray-200">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition ${activeTab === id ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}
            >
              <Icon className="h-4 w-4" /> {label}
            </button>
          ))}
        </div>

        <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          {activeTab === "models"   && <ModelsTab />}
          {activeTab === "test"     && <TestTab />}
          {activeTab === "settings" && <SettingsTab />}
          {activeTab === "advanced" && <AdvancedTab />}
        </div>
      </div>
    </div>
  );
}
