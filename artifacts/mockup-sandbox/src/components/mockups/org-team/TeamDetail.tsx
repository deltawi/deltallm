import { useState } from "react";
import { Users, ArrowLeft, ChevronRight, Pencil, UserPlus, Shield, Gauge, DollarSign, AlertOctagon, CheckCircle2, TrendingUp, Building2, MoreHorizontal, Lock, Unlock, Info, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

const team = {
  id: "team-002",
  name: "Data Science",
  org_id: "org-acme-001",
  org_name: "Acme Corp",
  spend: 740,
  budget: 1500,
  rpm: 150,
  tpm: 75000,
  blocked: false,
  asset_mode: "restrict" as "inherit" | "restrict",
  asset_count: 4,
  asset_total: 12,
  created_at: "Feb 3, 2025",
};

const members = [
  { id: "acc-001", name: "Alice Chen", email: "alice@acme.com", role: "team_admin", spend: 280, initials: "AC", color: "bg-violet-100 text-violet-700" },
  { id: "acc-002", name: "Bob Reyes", email: "bob@acme.com", role: "team_developer", spend: 320, initials: "BR", color: "bg-blue-100 text-blue-700" },
  { id: "acc-003", name: "Carol White", email: "carol@acme.com", role: "team_developer", spend: 90, initials: "CW", color: "bg-emerald-100 text-emerald-700" },
  { id: "acc-004", name: "David Kim", email: "david@acme.com", role: "team_viewer", spend: 50, initials: "DK", color: "bg-amber-100 text-amber-700" },
  { id: "acc-005", name: "Emma Liu", email: "emma@acme.com", role: "team_viewer", spend: 0, initials: "EL", color: "bg-pink-100 text-pink-700" },
  { id: "acc-006", name: "Frank Mora", email: "frank@acme.com", role: "team_developer", spend: 0, initials: "FM", color: "bg-teal-100 text-teal-700" },
  { id: "acc-007", name: "Grace Park", email: "grace@acme.com", role: "team_viewer", spend: 0, initials: "GP", color: "bg-rose-100 text-rose-700" },
  { id: "acc-008", name: "Hiro Tanaka", email: "hiro@acme.com", role: "team_developer", spend: 0, initials: "HT", color: "bg-cyan-100 text-cyan-700" },
];

const ROLE_LABELS: Record<string, { label: string; color: string }> = {
  team_admin:     { label: "Admin",     color: "bg-indigo-100 text-indigo-700" },
  team_developer: { label: "Developer", color: "bg-blue-100 text-blue-700" },
  team_viewer:    { label: "Viewer",    color: "bg-gray-100 text-gray-600" },
};

const grantedAssets = [
  { key: "gpt-4o", type: "chat", provider: "OpenAI" },
  { key: "gpt-4o-mini", type: "chat", provider: "OpenAI" },
  { key: "claude-3-5-sonnet", type: "chat", provider: "Anthropic" },
  { key: "text-embedding-3-large", type: "embedding", provider: "OpenAI" },
];

const inheritedButBlocked = [
  { key: "dall-e-3", type: "image", provider: "OpenAI" },
  { key: "gemini-1.5-pro", type: "chat", provider: "Google" },
  { key: "whisper-1", type: "audio", provider: "OpenAI" },
  { key: "claude-3-haiku", type: "chat", provider: "Anthropic" },
  { key: "mistral-large", type: "chat", provider: "Mistral" },
  { key: "text-embedding-ada-002", type: "embedding", provider: "OpenAI" },
  { key: "tts-1", type: "audio", provider: "OpenAI" },
  { key: "cohere-rerank", type: "rerank", provider: "Cohere" },
];

export function TeamDetail() {
  const [tab, setTab] = useState("overview");
  const spendPct = Math.round((team.spend / team.budget) * 100);
  const assetPct = Math.round((team.asset_count / team.asset_total) * 100);

  return (
    <div className="min-h-screen bg-gray-50 font-['Inter']">
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="px-6 py-4">
          <div className="flex items-center gap-1.5 text-xs text-gray-400 mb-3">
            <button className="hover:text-gray-700 flex items-center gap-1"><ArrowLeft className="w-3 h-3" /> Teams</button>
            <ChevronRight className="w-3 h-3" />
            <button className="hover:text-gray-700 flex items-center gap-1">
              <Building2 className="w-3 h-3" /> {team.org_name}
            </button>
            <ChevronRight className="w-3 h-3" />
            <span className="text-gray-600 font-medium">{team.name}</span>
          </div>

          <div className="flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-indigo-500 to-indigo-700 flex items-center justify-center shadow-sm shrink-0">
                <Users className="w-6 h-6 text-white" />
              </div>
              <div>
                <div className="flex items-center gap-2.5">
                  <h1 className="text-xl font-bold text-gray-900">{team.name}</h1>
                  {team.blocked ? (
                    <span className="flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-red-100 text-red-700">
                      <AlertOctagon className="w-3.5 h-3.5" /> Blocked
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs text-emerald-600 font-medium">
                      <CheckCircle2 className="w-3.5 h-3.5" /> Active
                    </span>
                  )}
                  <Badge variant="secondary" className="text-xs text-gray-500">
                    <Building2 className="w-3 h-3 mr-1 inline" />{team.org_name}
                  </Badge>
                </div>
                <div className="flex items-center gap-3 mt-1">
                  <code className="text-xs text-gray-400 font-mono bg-gray-100 px-1.5 py-0.5 rounded">{team.id}</code>
                  <span className="text-xs text-gray-400">Created {team.created_at}</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" className="flex items-center gap-1.5 text-xs">
                <Pencil className="w-3.5 h-3.5" /> Edit
              </Button>
            </div>
          </div>

          {/* Metrics strip */}
          <div className="mt-5 grid grid-cols-4 gap-4">
            {[
              { label: "Budget used", value: `${spendPct}%`, sub: `$${team.spend} of $${team.budget}`, icon: DollarSign, color: spendPct > 80 ? "text-amber-500" : "text-green-500", bg: spendPct > 80 ? "bg-amber-50" : "bg-green-50" },
              { label: "Members", value: String(members.length), sub: "in this team", icon: Users, color: "text-indigo-600", bg: "bg-indigo-50" },
              { label: "RPM Limit", value: team.rpm.toLocaleString(), sub: "requests / min", icon: Gauge, color: "text-purple-600", bg: "bg-purple-50" },
              { label: "Asset access", value: `${team.asset_count}/${team.asset_total}`, sub: "from org ceiling", icon: Shield, color: "text-blue-600", bg: "bg-blue-50" },
            ].map((m) => (
              <div key={m.label} className="flex items-center gap-3 px-4 py-3 bg-white rounded-xl border border-gray-200">
                <div className={`p-2 rounded-lg ${m.bg}`}>
                  <m.icon className={`w-4 h-4 ${m.color}`} />
                </div>
                <div>
                  <p className="text-lg font-bold text-gray-900 leading-none">{m.value}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{m.label}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-5 -mb-px">
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList className="bg-transparent p-0">
                {["overview", "members", "assets"].map((t) => (
                  <TabsTrigger key={t} value={t} className="text-sm">
                    {t === "assets" ? "Asset Access" : t.charAt(0).toUpperCase() + t.slice(1)}
                    {t === "members" && <Badge variant="secondary" className="ml-1.5 text-[10px] py-0 px-1.5">{members.length}</Badge>}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="px-6 py-5">
        {tab === "overview" && (
          <div className="grid grid-cols-3 gap-5">
            <div className="col-span-2 space-y-5">
              {/* Spend card */}
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold text-gray-900">Budget &amp; Spend</h3>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${spendPct > 80 ? "bg-amber-100 text-amber-700" : "bg-green-100 text-green-700"}`}>
                    {spendPct}% used
                  </span>
                </div>
                <Progress value={spendPct} className={`h-2 mb-4 ${spendPct > 80 ? "[&>div]:bg-amber-500" : ""}`} />
                <div className="flex justify-between text-sm mb-4">
                  <div><p className="text-2xl font-bold text-gray-900">${team.spend.toLocaleString()}</p><p className="text-xs text-gray-400">Current spend</p></div>
                  <div className="text-right"><p className="text-lg font-semibold text-gray-500">${(team.budget - team.spend).toLocaleString()}</p><p className="text-xs text-gray-400">Remaining</p></div>
                </div>
                <Separator className="mb-4" />
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-xs text-gray-500 flex items-center gap-1 mb-1"><Gauge className="w-3.5 h-3.5" /> RPM Limit</p>
                    <p className="text-sm font-semibold">{team.rpm.toLocaleString()} <span className="text-xs font-normal text-gray-400">req/min</span></p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 flex items-center gap-1 mb-1"><TrendingUp className="w-3.5 h-3.5" /> TPM Limit</p>
                    <p className="text-sm font-semibold">{team.tpm.toLocaleString()} <span className="text-xs font-normal text-gray-400">tok/min</span></p>
                  </div>
                </div>
              </div>

              {/* Top spenders preview */}
              <div className="bg-white rounded-xl border border-gray-200">
                <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-gray-900">Top Spenders</h3>
                  <button className="text-xs text-blue-600 hover:underline" onClick={() => setTab("members")}>View all →</button>
                </div>
                <div className="divide-y divide-gray-100">
                  {members.filter(m => m.spend > 0).slice(0, 3).map((m) => (
                    <div key={m.id} className="flex items-center gap-3 px-5 py-3 hover:bg-gray-50">
                      <div className={`w-7 h-7 rounded-full ${m.color} flex items-center justify-center text-xs font-bold shrink-0`}>{m.initials}</div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-800 truncate">{m.name}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <div className="w-24 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div className="h-full bg-indigo-400 rounded-full" style={{ width: `${Math.min(100, (m.spend / team.spend) * 100)}%` }} />
                        </div>
                        <span className="text-xs font-medium text-gray-700 w-12 text-right">${m.spend}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Team Info</h4>
                <div className="space-y-2.5 text-sm">
                  <div className="flex justify-between"><span className="text-gray-500">Organization</span><span className="font-medium text-blue-600 text-xs">{team.org_name}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">Created</span><span className="font-medium text-gray-800 text-xs">{team.created_at}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">Status</span>
                    <span className="text-xs font-medium text-emerald-600 flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> Active</span>
                  </div>
                </div>
              </div>

              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <div className="flex items-center gap-1.5 mb-3">
                  <Shield className="w-3.5 h-3.5 text-indigo-600" />
                  <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Asset Access</h4>
                  <Info className="w-3.5 h-3.5 text-gray-400" />
                </div>
                <div className="flex items-center gap-2 mb-3">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${team.asset_mode === "restrict" ? "bg-indigo-100 text-indigo-700" : "bg-gray-100 text-gray-600"}`}>
                    {team.asset_mode === "restrict" ? <><Lock className="w-3 h-3 inline mr-0.5" />Restricted</> : <><Unlock className="w-3 h-3 inline mr-0.5" />Inherited</>}
                  </span>
                </div>
                {team.asset_mode === "restrict" && (
                  <>
                    <div className="flex justify-between text-xs mb-1.5 text-gray-600">
                      <span>{team.asset_count} assets selected</span>
                      <span className="text-gray-400">of {team.asset_total}</span>
                    </div>
                    <Progress value={assetPct} className="h-1.5 mb-2" />
                  </>
                )}
                <button className="text-xs text-blue-600 hover:underline font-medium" onClick={() => setTab("assets")}>
                  Manage asset access →
                </button>
              </div>
            </div>
          </div>
        )}

        {tab === "members" && (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50">
              <h3 className="text-sm font-semibold text-gray-900">Members ({members.length})</h3>
              <Button size="sm" className="flex items-center gap-1.5 h-8 text-xs bg-indigo-600 hover:bg-indigo-700">
                <UserPlus className="w-3.5 h-3.5" /> Add Member
              </Button>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Member</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Team Role</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Spend</th>
                  <th className="px-5 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {members.map((m, i) => {
                  const role = ROLE_LABELS[m.role] || { label: m.role, color: "bg-gray-100 text-gray-700" };
                  return (
                    <tr key={m.id} className={`hover:bg-gray-50 ${i < members.length - 1 ? "border-b border-gray-100" : ""}`}>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <Avatar className="w-8 h-8">
                            <AvatarFallback className={`text-xs font-bold ${m.color}`}>{m.initials}</AvatarFallback>
                          </Avatar>
                          <div>
                            <p className="font-medium text-gray-900 text-sm">{m.name}</p>
                            <p className="text-xs text-gray-400">{m.email}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-3.5">
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${role.color}`}>{role.label}</span>
                      </td>
                      <td className="px-5 py-3.5">
                        {m.spend > 0 ? (
                          <div className="flex items-center gap-2">
                            <div className="w-20 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                              <div className="h-full bg-indigo-400 rounded-full" style={{ width: `${Math.min(100, (m.spend / team.spend) * 100)}%` }} />
                            </div>
                            <span className="text-xs text-gray-700">${m.spend}</span>
                          </div>
                        ) : <span className="text-xs text-gray-400">—</span>}
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <div className="flex items-center gap-1 justify-end">
                          <button className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400"><MoreHorizontal className="w-4 h-4" /></button>
                          <button className="p-1.5 hover:bg-red-50 rounded-lg transition-colors"><Trash2 className="w-4 h-4 text-red-400" /></button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {tab === "assets" && (
          <div className="grid grid-cols-3 gap-5">
            <div className="col-span-2">
              <div className="bg-white rounded-xl border border-gray-200 mb-4">
                <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Lock className="w-4 h-4 text-indigo-600" />
                    <h3 className="text-sm font-semibold text-gray-900">Granted to this team</h3>
                    <Badge variant="secondary" className="text-xs">{grantedAssets.length}</Badge>
                  </div>
                </div>
                <div className="p-3 space-y-1.5">
                  {grantedAssets.map((a) => (
                    <div key={a.key} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-indigo-50 border border-indigo-100">
                      <div className="flex items-center gap-2.5">
                        <CheckCircle2 className="w-4 h-4 text-indigo-600" />
                        <span className="text-sm font-medium text-gray-800">{a.key}</span>
                        <span className="text-xs px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600">{a.type}</span>
                        <span className="text-xs text-gray-400">{a.provider}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bg-white rounded-xl border border-gray-200">
                <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Lock className="w-4 h-4 text-gray-400" />
                    <h3 className="text-sm font-semibold text-gray-900 text-opacity-60">Blocked from this team</h3>
                    <Badge variant="secondary" className="text-xs">{inheritedButBlocked.length}</Badge>
                  </div>
                  <span className="text-xs text-gray-400">Available in org, not granted here</span>
                </div>
                <div className="p-3 space-y-1.5">
                  {inheritedButBlocked.map((a) => (
                    <div key={a.key} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-gray-50 border border-gray-200 opacity-50">
                      <div className="flex items-center gap-2.5">
                        <Lock className="w-4 h-4 text-gray-400" />
                        <span className="text-sm font-medium text-gray-500">{a.key}</span>
                        <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">{a.type}</span>
                        <span className="text-xs text-gray-400">{a.provider}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Access Policy</h4>
                <div className="space-y-2 text-sm">
                  <div className="flex items-start gap-2 p-2.5 rounded-lg border-2 border-indigo-500 bg-indigo-50">
                    <Lock className="w-4 h-4 text-indigo-600 mt-0.5 shrink-0" />
                    <div>
                      <p className="text-xs font-semibold text-indigo-800">Restricted</p>
                      <p className="text-[10px] text-indigo-700 mt-0.5">Only the 4 selected assets are accessible. Org ceiling: 12 assets.</p>
                    </div>
                  </div>
                  <button className="w-full mt-2 px-3 py-2 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 flex items-center gap-1.5">
                    <Unlock className="w-3.5 h-3.5" /> Switch to Inherited
                  </button>
                </div>
              </div>
              <Button size="sm" className="w-full bg-indigo-600 hover:bg-indigo-700 text-xs h-8">
                Edit Asset Selection
              </Button>
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
                <p className="text-xs text-blue-800 leading-relaxed">
                  <strong>Restricted mode:</strong> API keys and users in this team can only call models in the granted set. This narrows the org's ceiling — it never expands it.
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
