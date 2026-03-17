import { useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Brain,
  CheckCircle2,
  ChevronRight,
  Clock,
  Code2,
  Copy,
  DollarSign,
  ExternalLink,
  Gauge,
  Hash,
  Key,
  Layers,
  Pencil,
  RefreshCw,
  Route,
  Server,
  Shield,
  Tag,
  Terminal,
  Trash2,
  TrendingUp,
  Zap,
} from "lucide-react";

const TAB_LIST = [
  { id: "overview", label: "Overview", icon: Layers },
  { id: "runtime", label: "Runtime", icon: Zap },
  { id: "routing", label: "Routing", icon: Route },
  { id: "costs", label: "Costs", icon: DollarSign },
  { id: "usage", label: "API Usage", icon: Terminal },
] as const;

type TabId = (typeof TAB_LIST)[number]["id"];

const PROVIDER_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  openai:     { bg: "bg-emerald-50",  text: "text-emerald-700",  dot: "bg-emerald-500"  },
  anthropic:  { bg: "bg-violet-50",   text: "text-violet-700",   dot: "bg-violet-500"   },
  groq:       { bg: "bg-orange-50",   text: "text-orange-700",   dot: "bg-orange-500"   },
  azure:      { bg: "bg-blue-50",     text: "text-blue-700",     dot: "bg-blue-500"     },
  bedrock:    { bg: "bg-amber-50",    text: "text-amber-700",    dot: "bg-amber-500"    },
  gemini:     { bg: "bg-sky-50",      text: "text-sky-700",      dot: "bg-sky-500"      },
  mistral:    { bg: "bg-rose-50",     text: "text-rose-700",     dot: "bg-rose-500"     },
  cohere:     { bg: "bg-indigo-50",   text: "text-indigo-700",   dot: "bg-indigo-500"   },
};

const MODEL = {
  model_name: "gpt-4-turbo",
  deployment_id: "dep_8f2a19bc4e7c",
  provider: "openai",
  healthy: true,
  mode: "chat",
  deltallm_params: {
    model: "gpt-4-turbo-preview",
    api_base: "https://api.openai.com/v1",
    api_version: null,
    api_key: "sk-openai••••••••••••••••••••••••••••••••••abcd",
    timeout: 120,
    max_tokens: 4096,
    stream_timeout: 60,
    rpm: 10000,
    tpm: 2000000,
    weight: 3,
  },
  model_info: {
    mode: "chat",
    max_tokens: 128000,
    max_input_tokens: 128000,
    max_output_tokens: 4096,
    input_cost_per_token: 0.00001,
    output_cost_per_token: 0.00003,
    batch_price_multiplier: 0.5,
    batch_input_cost_per_token: 0.000005,
    batch_output_cost_per_token: 0.000015,
    weight: 3,
    priority: 1,
    rpm_limit: 10000,
    tpm_limit: 2000000,
    tags: ["production", "gpt4", "high-capacity"],
    default_params: { temperature: 0.7, top_p: 0.95, frequency_penalty: 0 },
  },
  health: {
    consecutive_failures: 0,
    in_cooldown: false,
    last_success_at: Math.floor(Date.now() / 1000) - 42,
    last_error_at: null,
    last_error: null,
  },
};

function StatBadge({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{label}</span>
      <span className={`text-sm font-semibold text-gray-900 ${mono ? "font-mono" : ""}`}>{value}</span>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  sub,
  accent = "blue",
}: {
  icon: any;
  label: string;
  value: string;
  sub?: string;
  accent?: "blue" | "green" | "amber" | "violet";
}) {
  const accents = {
    blue:   { wrap: "bg-blue-50 border-blue-100",   icon: "bg-blue-100 text-blue-600",   val: "text-blue-900"   },
    green:  { wrap: "bg-emerald-50 border-emerald-100", icon: "bg-emerald-100 text-emerald-600", val: "text-emerald-900" },
    amber:  { wrap: "bg-amber-50 border-amber-100", icon: "bg-amber-100 text-amber-600", val: "text-amber-900"  },
    violet: { wrap: "bg-violet-50 border-violet-100", icon: "bg-violet-100 text-violet-600", val: "text-violet-900" },
  };
  const a = accents[accent];
  return (
    <div className={`rounded-2xl border p-4 ${a.wrap}`}>
      <div className={`mb-3 inline-flex rounded-xl p-2 ${a.icon}`}>
        <Icon className="h-4 w-4" />
      </div>
      <div className={`text-xl font-bold leading-none ${a.val}`}>{value}</div>
      <div className="mt-1.5 text-xs font-medium text-gray-500">{label}</div>
      {sub && <div className="mt-1 text-[11px] text-gray-400">{sub}</div>}
    </div>
  );
}

function Field({ label, value, mono = false, full = false }: { label: string; value: React.ReactNode; mono?: boolean; full?: boolean }) {
  return (
    <div className={`rounded-xl border border-gray-100 bg-white px-4 py-3 ${full ? "col-span-2" : ""}`}>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">{label}</div>
      <div className={`break-all text-sm text-gray-900 ${mono ? "font-mono text-xs" : "font-medium"}`}>{value}</div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
      onClick={() => { navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
    >
      {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-gray-950">
      <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2">
        <span className="text-xs font-medium text-gray-400">{lang}</span>
        <CopyButton text={code} />
      </div>
      <pre className="overflow-x-auto px-4 py-3 text-xs leading-relaxed text-gray-100">{code}</pre>
    </div>
  );
}

function OverviewTab() {
  const lp = MODEL.deltallm_params;
  const health = MODEL.health;
  const lastSuccess = health.last_success_at
    ? `${Math.round((Date.now() / 1000 - health.last_success_at))}s ago`
    : "—";

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Public Model Name" value={MODEL.model_name} />
        <Field label="Deployment ID" value={MODEL.deployment_id} mono />
        <Field label="Provider" value={<ProviderPill provider={MODEL.provider} />} />
        <Field label="Provider Model" value={lp.model} mono />
        <Field label="API Base" value={lp.api_base} mono full />
        <Field label="API Key" value={lp.api_key} mono full />
        <Field label="Timeout" value={`${lp.timeout}s`} />
        <Field label="Consecutive Failures" value={String(health.consecutive_failures)} />
        <Field label="Last Success" value={lastSuccess} />
        <Field label="Cooldown Active" value={health.in_cooldown ? "Yes" : "No"} />
      </div>
    </div>
  );
}

function RuntimeTab() {
  const lp = MODEL.deltallm_params;
  const mi = MODEL.model_info;
  const defaults = Object.entries(mi.default_params);

  return (
    <div className="space-y-5">
      <div>
        <h3 className="mb-3 text-sm font-semibold text-gray-700">Token Limits</h3>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Context Window" value={mi.max_tokens.toLocaleString()} />
          <Field label="Max Input Tokens" value={mi.max_input_tokens.toLocaleString()} />
          <Field label="Max Output Tokens" value={mi.max_output_tokens.toLocaleString()} />
          <Field label="Per Request Cap" value={lp.max_tokens.toLocaleString()} />
          <Field label="Stream Timeout" value={`${lp.stream_timeout}s`} />
          <Field label="Request Timeout" value={`${lp.timeout}s`} />
        </div>
      </div>
      {defaults.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold text-gray-700">Default Parameters</h3>
          <div className="grid grid-cols-2 gap-3">
            {defaults.map(([k, v]) => (
              <Field key={k} label={k} value={String(v)} mono />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RoutingTab() {
  const lp = MODEL.deltallm_params;
  const mi = MODEL.model_info;
  const tags = mi.tags;
  const totalWeight = 10;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Weight" value={String(mi.weight)} />
        <Field label="Priority" value={String(mi.priority)} />
        <Field label="RPM Limit" value={lp.rpm.toLocaleString()} />
        <Field label="TPM Limit" value={lp.tpm.toLocaleString()} />
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Weight distribution</span>
          <span className="text-xs text-gray-400">{mi.weight} / {totalWeight} total weight</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-gray-100">
          <div
            className="h-full rounded-full bg-blue-500 transition-all"
            style={{ width: `${(mi.weight / totalWeight) * 100}%` }}
          />
        </div>
        <p className="mt-1.5 text-xs text-gray-400">This deployment receives ~{Math.round((mi.weight / totalWeight) * 100)}% of routed traffic.</p>
      </div>

      <div>
        <h3 className="mb-3 text-sm font-semibold text-gray-700">Tags</h3>
        <div className="flex flex-wrap gap-2">
          {tags.map((tag) => (
            <span key={tag} className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-600">
              <Tag className="h-3 w-3 text-gray-400" />
              {tag}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function CostsTab() {
  const mi = MODEL.model_info;
  const rows = [
    { label: "Input Cost / Token",           value: `$${mi.input_cost_per_token}`,             hint: "Standard request" },
    { label: "Output Cost / Token",          value: `$${mi.output_cost_per_token}`,            hint: "Standard request" },
    { label: "Batch Input Cost / Token",     value: `$${mi.batch_input_cost_per_token}`,       hint: "Batch 50% discount" },
    { label: "Batch Output Cost / Token",    value: `$${mi.batch_output_cost_per_token}`,      hint: "Batch 50% discount" },
    { label: "Batch Price Multiplier",       value: `${mi.batch_price_multiplier}×`,           hint: "Applied to batch jobs" },
  ];

  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-2xl border border-gray-200">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-400">Field</th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-400">Rate</th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-400">Note</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {rows.map((r) => (
              <tr key={r.label} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-gray-700">{r.label}</td>
                <td className="px-4 py-3 font-mono text-xs text-gray-900">{r.value}</td>
                <td className="px-4 py-3 text-xs text-gray-400">{r.hint}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 text-sm">
        <div className="flex items-start gap-3">
          <TrendingUp className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
          <div>
            <div className="font-semibold text-blue-800">Estimated cost for 1M tokens</div>
            <div className="mt-1 text-blue-700">
              Input: <strong>${(mi.input_cost_per_token * 1_000_000).toFixed(2)}</strong> &nbsp;·&nbsp;
              Output: <strong>${(mi.output_cost_per_token * 1_000_000).toFixed(2)}</strong>
            </div>
            <div className="mt-0.5 text-xs text-blue-500">Batch pricing available at {mi.batch_price_multiplier * 100}% of standard rates.</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function UsageTab() {
  const curlCode = `curl https://api.deltallm.io/v1/chat/completions \\
  -H "Authorization: Bearer $DELTALLM_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${MODEL.model_name}",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 256
  }'`;

  const pyCode = `from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DELTALLM_API_KEY"],
    base_url="https://api.deltallm.io/v1",
)

response = client.chat.completions.create(
    model="${MODEL.model_name}",
    messages=[{"role": "user", "content": "Hello!"}],
    max_tokens=256,
)
print(response.choices[0].message.content)`;

  const jsCode = `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.DELTALLM_API_KEY,
  baseURL: "https://api.deltallm.io/v1",
});

const response = await client.chat.completions.create({
  model: "${MODEL.model_name}",
  messages: [{ role: "user", content: "Hello!" }],
  max_tokens: 256,
});
console.log(response.choices[0].message.content);`;

  const [tab, setTab] = useState<"curl" | "python" | "js">("curl");

  return (
    <div className="space-y-4">
      <div className="flex gap-1 rounded-xl border border-gray-200 bg-gray-50 p-1">
        {(["curl", "python", "js"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-lg py-1.5 text-xs font-semibold transition ${
              tab === t ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {t === "curl" ? "cURL" : t === "python" ? "Python" : "Node.js"}
          </button>
        ))}
      </div>
      {tab === "curl"   && <CodeBlock lang="Shell" code={curlCode} />}
      {tab === "python" && <CodeBlock lang="Python" code={pyCode} />}
      {tab === "js"     && <CodeBlock lang="JavaScript" code={jsCode} />}

      <div className="rounded-2xl border border-gray-200 bg-white p-4">
        <h4 className="mb-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">Quick Reference</h4>
        <div className="space-y-2 text-sm text-gray-700">
          <div className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
            <span>Clients call <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-900">{MODEL.model_name}</code>. DeltaLLM routes traffic to <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-900">{MODEL.deltallm_params.model}</code>.</span>
          </div>
          <div className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
            <span>Drop-in replacement for any OpenAI-compatible SDK — just swap the <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-900">base_url</code> and <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-900">api_key</code>.</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function ProviderPill({ provider }: { provider: string }) {
  const colors = PROVIDER_COLORS[provider] || { bg: "bg-gray-100", text: "text-gray-700", dot: "bg-gray-400" };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${colors.bg} ${colors.text}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${colors.dot}`} />
      {provider.charAt(0).toUpperCase() + provider.slice(1)}
    </span>
  );
}

export function ModelDetailV2() {
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const lp = MODEL.deltallm_params;
  const mi = MODEL.model_info;
  const health = MODEL.health;

  return (
    <div className="min-h-screen bg-gray-50 font-sans">
      {/* ── Top bar ── */}
      <div className="border-b border-gray-200 bg-white px-6 py-3">
        <button className="flex items-center gap-1.5 text-sm text-gray-500 transition hover:text-gray-800">
          <ArrowLeft className="h-4 w-4" />
          Back to Models
        </button>
      </div>

      {/* ── Hero header ── */}
      <div className="relative overflow-hidden border-b border-gray-200 bg-white">
        {/* Gradient accent */}
        <div className="absolute inset-0 bg-gradient-to-br from-blue-50 via-white to-violet-50 opacity-60" />
        <div className="absolute right-0 top-0 h-40 w-40 rounded-full bg-blue-100/40 blur-3xl" />
        <div className="absolute bottom-0 left-1/4 h-32 w-64 rounded-full bg-violet-100/30 blur-3xl" />

        <div className="relative px-6 pb-5 pt-6">
          {/* Mode chip + Status */}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 px-2.5 py-1 text-xs font-semibold text-blue-700">
              <Brain className="h-3.5 w-3.5" /> Chat
            </span>
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${MODEL.healthy ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
              {MODEL.healthy
                ? <><CheckCircle2 className="h-3.5 w-3.5" /> Healthy</>
                : <><AlertTriangle className="h-3.5 w-3.5" /> Unhealthy</>}
            </span>
            <ProviderPill provider={MODEL.provider} />
          </div>

          {/* Model name + actions */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{MODEL.model_name}</h1>
              <p className="mt-0.5 text-sm text-gray-500">
                Routes to <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-700">{lp.model}</code> via <span className="font-medium capitalize">{MODEL.provider}</span>
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 shadow-sm transition hover:bg-gray-50">
                <RefreshCw className="h-4 w-4" /> Check Health
              </button>
              <button className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-blue-700">
                <Pencil className="h-4 w-4" /> Edit
              </button>
              <button className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-600 shadow-sm transition hover:bg-red-50">
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Stat strip */}
          <div className="mt-5 flex flex-wrap items-center gap-6 divide-x divide-gray-100">
            <StatBadge label="Context Window" value={`${(mi.max_tokens / 1000).toFixed(0)}K tokens`} />
            <div className="pl-6"><StatBadge label="RPM" value={lp.rpm.toLocaleString()} /></div>
            <div className="pl-6"><StatBadge label="TPM" value={(lp.tpm / 1_000_000).toFixed(1) + "M"} /></div>
            <div className="pl-6"><StatBadge label="Weight" value={`${mi.weight} / 10`} /></div>
            <div className="pl-6"><StatBadge label="Input Cost" value={`$${mi.input_cost_per_token} / tok`} /></div>
          </div>
        </div>
      </div>

      {/* ── Tab navigation + body ── */}
      <div className="px-6 pb-8">
        {/* Tabs */}
        <div className="mb-4 flex gap-1 border-b border-gray-200">
          {TAB_LIST.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-sm font-medium transition ${
                activeTab === id
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          {activeTab === "overview" && <OverviewTab />}
          {activeTab === "runtime"  && <RuntimeTab />}
          {activeTab === "routing"  && <RoutingTab />}
          {activeTab === "costs"    && <CostsTab />}
          {activeTab === "usage"    && <UsageTab />}
        </div>
      </div>
    </div>
  );
}
