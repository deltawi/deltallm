import { useState } from "react";
import {
  ArrowRight,
  Brain,
  CheckCircle2,
  ChevronRight,
  GitBranch,
  Layers,
  Mic,
  Plus,
  Search,
  Shuffle,
  Sparkles,
  Trash2,
  X,
  Zap,
} from "lucide-react";

const MODE_ICONS: Record<string, any> = {
  chat: Brain,
  embedding: Zap,
  audio_speech: Mic,
  image_generation: Layers,
  rerank: GitBranch,
};

const MODE_COLORS: Record<string, string> = {
  chat:             "bg-blue-50 text-blue-700 border-blue-100",
  embedding:        "bg-violet-50 text-violet-700 border-violet-100",
  audio_speech:     "bg-orange-50 text-orange-700 border-orange-100",
  image_generation: "bg-pink-50 text-pink-700 border-pink-100",
  rerank:           "bg-teal-50 text-teal-700 border-teal-100",
};

const ROUTING_LABELS: Record<string, string> = {
  shuffle: "Shuffle",
  weighted: "Weighted",
  least_busy: "Least Busy",
  latency_based: "Latency",
  cost_based: "Cost",
};

const GROUPS = [
  { group_key: "prod-chat-primary",    name: "Production Chat",       mode: "chat",       routing_strategy: "weighted",    enabled: true,  member_count: 4, healthy: 4 },
  { group_key: "support-fallback",     name: "Support Fallback",      mode: "chat",       routing_strategy: "shuffle",     enabled: true,  member_count: 3, healthy: 2 },
  { group_key: "embed-search",         name: "Semantic Search",       mode: "embedding",  routing_strategy: "least_busy",  enabled: true,  member_count: 2, healthy: 2 },
  { group_key: "code-assist-gpt4",     name: "Code Assist (GPT-4)",   mode: "chat",       routing_strategy: "latency_based", enabled: false, member_count: 2, healthy: 1 },
  { group_key: "tts-primary",          name: "Text-to-Speech",        mode: "audio_speech", routing_strategy: "shuffle",   enabled: true,  member_count: 1, healthy: 1 },
  { group_key: "rerank-cohere",        name: "Rerank — Cohere",       mode: "rerank",     routing_strategy: "shuffle",     enabled: true,  member_count: 1, healthy: 1 },
];

function ModeChip({ mode }: { mode: string }) {
  const Icon = MODE_ICONS[mode] || Layers;
  const color = MODE_COLORS[mode] || "bg-gray-50 text-gray-700 border-gray-200";
  const label = mode.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${color}`}>
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

function HealthBar({ healthy, total }: { healthy: number; total: number }) {
  const pct = total > 0 ? (healthy / total) * 100 : 0;
  const color = pct === 100 ? "bg-emerald-500" : pct > 50 ? "bg-amber-400" : "bg-red-400";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs font-semibold ${pct === 100 ? "text-emerald-600" : pct > 50 ? "text-amber-600" : "text-red-500"}`}>
        {healthy}/{total}
      </span>
    </div>
  );
}

function CreateDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form, setForm] = useState({ group_key: "", name: "", mode: "chat" });
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/20" onClick={onClose} />
      <div className="flex w-[420px] flex-col border-l border-gray-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Create Model Group</h2>
            <p className="text-xs text-gray-500">Add the shell — you'll configure members on the next page.</p>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 hover:bg-gray-100">
            <X className="h-4 w-4 text-gray-400" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm">
            <div className="font-semibold text-blue-800">What happens next</div>
            <div className="mt-1 text-blue-700 text-xs">Creates the group shell. On the next page you'll add deployments, configure routing, and optionally bind a prompt.</div>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Group Key <span className="text-red-500">*</span></label>
            <input
              value={form.group_key}
              onChange={(e) => setForm({ ...form, group_key: e.target.value })}
              placeholder="prod-chat-primary"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="mt-1 text-xs text-gray-400">Stable key used by clients, policies, and bindings.</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Workload Type</label>
              <select
                value={form.mode}
                onChange={(e) => setForm({ ...form, mode: e.target.value })}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {["chat", "embedding", "audio_speech", "image_generation", "rerank"].map((m) => (
                  <option key={m} value={m}>{m.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Display Name</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Production Chat"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        </div>

        <div className="border-t border-gray-200 px-5 py-4 flex gap-2 justify-end">
          <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
            Create and continue →
          </button>
        </div>
      </div>
    </div>
  );
}

export function RouteGroupsList() {
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const filtered = GROUPS.filter(
    (g) =>
      g.group_key.includes(search.toLowerCase()) ||
      (g.name || "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-gray-50">
      <CreateDrawer open={createOpen} onClose={() => setCreateOpen(false)} />

      {/* Header */}
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Model Groups</h1>
            <p className="mt-0.5 text-sm text-gray-500">Group deployments behind a single key — clients call the group, DeltaLLM routes traffic.</p>
          </div>
          <button
            onClick={() => setCreateOpen(true)}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" /> Create Group
          </button>
        </div>
      </div>

      {/* Setup guide banner */}
      <div className="mx-6 mt-4 overflow-hidden rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 via-white to-slate-50">
        <div className="flex items-center gap-4 px-5 py-4">
          <div className="flex-1">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-xs font-semibold text-blue-700 shadow-sm ring-1 ring-blue-100">
              <Sparkles className="h-3 w-3" /> Recommended setup order
            </div>
            <p className="mt-2 text-sm text-slate-600">Create the group shell, add members, then start with default shuffle. Upgrade to a routing policy only when you need it.</p>
          </div>
          <div className="hidden sm:flex items-center gap-2">
            {["Create shell", "Add members", "Use default shuffle"].map((step, i) => (
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

      {/* Search + list */}
      <div className="px-6 py-4 space-y-3">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search model groups..."
            className="w-full rounded-xl border border-gray-200 bg-white py-2.5 pl-9 pr-4 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          {/* Table header */}
          <div className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] items-center gap-4 border-b border-gray-100 bg-gray-50 px-4 py-2.5 text-[10px] font-semibold uppercase tracking-widest text-gray-400">
            <div>Group</div>
            <div className="w-24 text-center">Mode</div>
            <div className="w-28 text-center">Health</div>
            <div className="w-24 text-center">Routing</div>
            <div className="w-16 text-center">Traffic</div>
            <div className="w-8" />
          </div>

          {filtered.map((g, i) => {
            const RoutingIcon = g.routing_strategy === "shuffle" ? Shuffle : GitBranch;
            return (
              <div
                key={g.group_key}
                className={`group grid grid-cols-[1fr_auto_auto_auto_auto_auto] items-center gap-4 px-4 py-3 transition hover:bg-blue-50/40 cursor-pointer ${i < filtered.length - 1 ? "border-b border-gray-100" : ""}`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-gray-900 text-sm truncate">{g.name || g.group_key}</span>
                    {!g.enabled && (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-gray-500">paused</span>
                    )}
                  </div>
                  <code className="text-[11px] text-gray-400 font-mono">{g.group_key}</code>
                </div>

                <div className="w-24 text-center">
                  <ModeChip mode={g.mode} />
                </div>

                <div className="w-28 flex justify-center">
                  <HealthBar healthy={g.healthy} total={g.member_count} />
                </div>

                <div className="w-24 flex justify-center">
                  <span className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                    <RoutingIcon className="h-3 w-3" />
                    {ROUTING_LABELS[g.routing_strategy || "shuffle"] || "Shuffle"}
                  </span>
                </div>

                <div className="w-16 flex justify-center">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${g.enabled ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-500"}`}>
                    {g.enabled ? "Live" : "Off"}
                  </span>
                </div>

                <div className="w-8 flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition">
                  <button className="rounded-lg p-1 hover:bg-red-50" onClick={(e) => e.stopPropagation()}>
                    <Trash2 className="h-3.5 w-3.5 text-red-400" />
                  </button>
                  <ArrowRight className="h-3.5 w-3.5 text-gray-300" />
                </div>
              </div>
            );
          })}

          {filtered.length === 0 && (
            <div className="px-6 py-10 text-center text-sm text-gray-400">No model groups found.</div>
          )}
        </div>

        <div className="flex items-center justify-between text-xs text-gray-400 px-1">
          <span>{filtered.length} groups</span>
          <div className="flex items-center gap-2">
            <button className="rounded-lg border border-gray-200 px-3 py-1.5 hover:bg-gray-50 text-gray-500 disabled:opacity-40" disabled>← Prev</button>
            <span className="text-gray-500">Page 1 of 1</span>
            <button className="rounded-lg border border-gray-200 px-3 py-1.5 hover:bg-gray-50 text-gray-500 disabled:opacity-40" disabled>Next →</button>
          </div>
        </div>
      </div>
    </div>
  );
}
