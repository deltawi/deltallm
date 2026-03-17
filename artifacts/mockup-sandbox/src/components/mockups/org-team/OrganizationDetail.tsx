import { useState } from "react";
import { Building2, Users, DollarSign, ArrowLeft, ChevronRight, Pencil, UserPlus, Plus, ExternalLink, Shield, Gauge, TrendingUp, CheckCircle2, AlertTriangle, MoreHorizontal, Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";

const org = {
  id: "org-acme-001",
  name: "Acme Corp",
  spend: 1842.5,
  budget: 5000,
  rpm: 500,
  tpm: 200000,
  audit_content_storage: true,
  asset_count_selected: 8,
  asset_count_total: 20,
  created_at: "Jan 12, 2025",
};

const teams = [
  { id: "team-001", name: "Engineering", members: 14, spend: 920, budget: 2000, rpm: 200, status: "healthy" },
  { id: "team-002", name: "Data Science", members: 8, spend: 740, budget: 1500, rpm: 150, status: "warning" },
  { id: "team-003", name: "Product", members: 6, spend: 130, budget: 500, rpm: 50, status: "healthy" },
  { id: "team-004", name: "Marketing", members: 4, spend: 52.5, budget: 300, rpm: 30, status: "healthy" },
  { id: "team-005", name: "Infrastructure", members: 10, spend: null, budget: null, rpm: null, status: "idle" },
];

const members = [
  { id: "acc-001", email: "alice@acme.com", name: "Alice Chen", role: "org_admin", teams: 3, initials: "AC", color: "bg-violet-100 text-violet-700" },
  { id: "acc-002", email: "bob@acme.com", name: "Bob Reyes", role: "org_member", teams: 2, initials: "BR", color: "bg-blue-100 text-blue-700" },
  { id: "acc-003", email: "carol@acme.com", name: "Carol White", role: "org_admin", teams: 1, initials: "CW", color: "bg-emerald-100 text-emerald-700" },
  { id: "acc-004", email: "david@acme.com", name: "David Kim", role: "org_member", teams: 4, initials: "DK", color: "bg-amber-100 text-amber-700" },
  { id: "acc-005", email: "emma@acme.com", name: "Emma Liu", role: "org_viewer", teams: 0, initials: "EL", color: "bg-pink-100 text-pink-700" },
];

const ROLE_LABELS: Record<string, { label: string; color: string }> = {
  org_owner:  { label: "Owner",  color: "bg-purple-100 text-purple-700" },
  org_admin:  { label: "Admin",  color: "bg-blue-100 text-blue-700" },
  org_member: { label: "Member", color: "bg-gray-100 text-gray-700" },
  org_viewer: { label: "Viewer", color: "bg-gray-50 text-gray-500" },
};

function SpendBar({ spend, budget, size = "md" }: { spend: number; budget: number | null; size?: "sm" | "md" }) {
  if (!budget) return <span className="text-xs text-gray-400">No limit</span>;
  const pct = Math.min(100, (spend / budget) * 100);
  const color = pct > 95 ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-blue-500";
  return (
    <div className={size === "sm" ? "w-28" : "w-full"}>
      <div className="flex justify-between text-xs mb-1">
        <span className="font-medium text-gray-700">${spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
        <span className="text-gray-400">/ ${budget.toLocaleString()}</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function OrganizationDetail() {
  const [tab, setTab] = useState("overview");
  const spendPct = Math.round((org.spend / org.budget) * 100);
  const assetPct = Math.round((org.asset_count_selected / org.asset_count_total) * 100);

  return (
    <div className="min-h-screen bg-gray-50 font-['Inter']">
      {/* Header */}
      <div className="bg-white border-b border-gray-200">
        <div className="px-6 py-4">
          <div className="flex items-center gap-1.5 text-xs text-gray-400 mb-3">
            <button className="hover:text-gray-700 flex items-center gap-1"><ArrowLeft className="w-3 h-3" /> Organizations</button>
            <ChevronRight className="w-3 h-3" />
            <span className="text-gray-600 font-medium">{org.name}</span>
          </div>
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center shadow-sm shrink-0">
                <span className="text-lg font-bold text-white">{org.name[0]}</span>
              </div>
              <div>
                <div className="flex items-center gap-2.5">
                  <h1 className="text-xl font-bold text-gray-900">{org.name}</h1>
                  <Badge variant="secondary" className="text-xs">Platform managed</Badge>
                  <span className="flex items-center gap-1 text-xs text-emerald-600 font-medium">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Active
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-1">
                  <code className="text-xs text-gray-400 font-mono bg-gray-100 px-1.5 py-0.5 rounded">{org.id}</code>
                  <span className="text-xs text-gray-400">Created {org.created_at}</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" className="flex items-center gap-1.5">
                <Pencil className="w-3.5 h-3.5" /> Edit
              </Button>
            </div>
          </div>

          {/* Key metrics strip */}
          <div className="mt-5 grid grid-cols-4 gap-4">
            {[
              { label: "Budget used", value: `${spendPct}%`, sub: `$${org.spend.toLocaleString()} of $${org.budget.toLocaleString()}`, icon: DollarSign, color: spendPct > 80 ? "text-amber-500" : "text-green-500", bg: spendPct > 80 ? "bg-amber-50" : "bg-green-50" },
              { label: "Teams", value: String(teams.length), sub: `${teams.filter(t => t.status !== "idle").length} active`, icon: Building2, color: "text-blue-600", bg: "bg-blue-50" },
              { label: "Members", value: String(members.length), sub: "across all teams", icon: Users, color: "text-violet-600", bg: "bg-violet-50" },
              { label: "Assets granted", value: `${org.asset_count_selected}/${org.asset_count_total}`, sub: `${assetPct}% of catalog`, icon: Shield, color: "text-indigo-600", bg: "bg-indigo-50" },
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

          {/* Tabs */}
          <div className="mt-5 -mb-px">
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList className="bg-transparent p-0">
                {["overview", "teams", "members", "assets"].map((t) => (
                  <TabsTrigger key={t} value={t} className="capitalize text-sm">
                    {t === "assets" ? "Asset Access" : t.charAt(0).toUpperCase() + t.slice(1)}
                    {t === "teams" && <Badge variant="secondary" className="ml-1.5 text-[10px] py-0 px-1.5">{teams.length}</Badge>}
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
              {/* Budget card */}
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-semibold text-gray-900">Budget &amp; Spend</h3>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${spendPct > 80 ? "bg-amber-100 text-amber-700" : "bg-green-100 text-green-700"}`}>
                    {spendPct}% used
                  </span>
                </div>
                <div className="mb-3">
                  <Progress value={spendPct} className={`h-2 ${spendPct > 80 ? "[&>div]:bg-amber-500" : ""}`} />
                </div>
                <div className="flex justify-between text-sm">
                  <div><p className="text-2xl font-bold text-gray-900">${org.spend.toLocaleString()}</p><p className="text-xs text-gray-400 mt-0.5">Current spend</p></div>
                  <div className="text-right"><p className="text-lg font-semibold text-gray-500">${(org.budget - org.spend).toFixed(2)}</p><p className="text-xs text-gray-400 mt-0.5">Remaining budget</p></div>
                </div>
                <Separator className="my-4" />
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1"><Gauge className="w-3.5 h-3.5" /> RPM Limit</div>
                    <p className="text-sm font-semibold text-gray-800">{org.rpm?.toLocaleString() ?? "Unlimited"} <span className="text-xs font-normal text-gray-400">req/min</span></p>
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-1"><TrendingUp className="w-3.5 h-3.5" /> TPM Limit</div>
                    <p className="text-sm font-semibold text-gray-800">{org.tpm?.toLocaleString() ?? "Unlimited"} <span className="text-xs font-normal text-gray-400">tok/min</span></p>
                  </div>
                </div>
              </div>

              {/* Teams quick list */}
              <div className="bg-white rounded-xl border border-gray-200">
                <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                  <h3 className="text-sm font-semibold text-gray-900">Teams</h3>
                  <Button size="sm" variant="outline" className="text-xs h-7 flex items-center gap-1">
                    <Plus className="w-3 h-3" /> Add Team
                  </Button>
                </div>
                <table className="w-full text-sm">
                  <tbody>
                    {teams.slice(0, 4).map((t, i) => (
                      <tr key={t.id} className={`hover:bg-gray-50 cursor-pointer ${i < teams.length - 1 ? "border-b border-gray-100" : ""}`}>
                        <td className="px-5 py-3">
                          <div className="flex items-center gap-2.5">
                            <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                              <Users className="w-3.5 h-3.5 text-indigo-600" />
                            </div>
                            <div>
                              <p className="font-medium text-gray-800 text-xs">{t.name}</p>
                              <p className="text-[10px] text-gray-400 font-mono">{t.id}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-5 py-3"><span className="text-xs text-gray-600 flex items-center gap-1"><Users className="w-3 h-3 text-gray-400" /> {t.members}</span></td>
                        <td className="px-5 py-3"><SpendBar spend={t.spend ?? 0} budget={t.budget} size="sm" /></td>
                        <td className="px-5 py-3 text-right">
                          <button className="text-xs text-blue-600 hover:underline flex items-center gap-1 ml-auto">Open <ExternalLink className="w-3 h-3" /></button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {teams.length > 4 && (
                  <div className="px-5 py-3 border-t border-gray-100 text-center">
                    <button className="text-xs text-blue-600 hover:underline" onClick={() => setTab("teams")}>View all {teams.length} teams →</button>
                  </div>
                )}
              </div>
            </div>

            {/* Sidebar */}
            <div className="space-y-4">
              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-900 mb-3">Settings</h3>
                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-xs font-medium text-gray-700">Audit content storage</p>
                      <p className="text-[10px] text-gray-400 mt-0.5">Stores request/response payloads in audit logs</p>
                    </div>
                    <span className={`shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full ${org.audit_content_storage ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                      {org.audit_content_storage ? "Enabled" : "Disabled"}
                    </span>
                  </div>
                </div>
              </div>

              <div className="bg-white rounded-xl border border-gray-200 p-5">
                <h3 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
                  Asset Access <Info className="w-3.5 h-3.5 text-gray-400" />
                </h3>
                <div className="mb-3">
                  <div className="flex justify-between text-xs mb-1.5">
                    <span className="text-gray-600">{org.asset_count_selected} models &amp; routes</span>
                    <span className="text-gray-400">of {org.asset_count_total}</span>
                  </div>
                  <Progress value={assetPct} className="h-1.5" />
                </div>
                <p className="text-[10px] text-gray-400 leading-relaxed">
                  Teams and API keys within this org can only use assets from this allowed set.
                </p>
                <button className="mt-3 text-xs text-blue-600 hover:underline flex items-center gap-1 font-medium">
                  Manage assets <ChevronRight className="w-3 h-3" />
                </button>
              </div>

              <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-xs font-semibold text-amber-800">Data Science team at 49% budget</p>
                    <p className="text-[10px] text-amber-700 mt-0.5">At current pace, will exhaust budget in ~6 days.</p>
                    <button className="text-[10px] text-amber-700 underline mt-1.5">View team →</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {tab === "teams" && (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50">
              <h3 className="text-sm font-semibold text-gray-900">All Teams ({teams.length})</h3>
              <Button size="sm" className="flex items-center gap-1.5 h-8 text-xs">
                <Plus className="w-3.5 h-3.5" /> Add Team
              </Button>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Name</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Members</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Budget Usage</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">RPM Limit</th>
                  <th className="px-5 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {teams.map((t, i) => (
                  <tr key={t.id} className={`hover:bg-blue-50/40 cursor-pointer ${i < teams.length - 1 ? "border-b border-gray-100" : ""}`}>
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center">
                          <Users className="w-4 h-4 text-indigo-600" />
                        </div>
                        <div>
                          <p className="font-semibold text-gray-900 text-sm">{t.name}</p>
                          <code className="text-[10px] text-gray-400 font-mono">{t.id}</code>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-sm text-gray-700">{t.members}</td>
                    <td className="px-5 py-3.5"><SpendBar spend={t.spend ?? 0} budget={t.budget} size="sm" /></td>
                    <td className="px-5 py-3.5">
                      {t.rpm ? <span className="text-xs bg-gray-100 px-2 py-0.5 rounded-full text-gray-600">{t.rpm.toLocaleString()}</span> : <span className="text-xs text-gray-400">—</span>}
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <button className="p-1.5 hover:bg-gray-100 rounded-lg"><MoreHorizontal className="w-4 h-4 text-gray-400" /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === "members" && (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 bg-gray-50">
              <h3 className="text-sm font-semibold text-gray-900">Organization Members ({members.length})</h3>
              <Button size="sm" className="flex items-center gap-1.5 h-8 text-xs">
                <UserPlus className="w-3.5 h-3.5" /> Add Member
              </Button>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Member</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Org Role</th>
                  <th className="text-left px-5 py-3 text-xs font-semibold text-gray-500">Team memberships</th>
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
                          <div className={`w-8 h-8 rounded-full ${m.color} flex items-center justify-center text-xs font-bold shrink-0`}>{m.initials}</div>
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
                        <span className="text-sm text-gray-600">{m.teams} {m.teams === 1 ? "team" : "teams"}</span>
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <button className="p-1.5 hover:bg-red-50 rounded-lg text-gray-300 hover:text-red-400 transition-colors text-xs">Remove</button>
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
            <div className="col-span-2 bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold text-gray-900">Allowed Assets</h3>
                <div className="flex gap-1.5">
                  {["All", "Models", "Route Groups"].map((f) => (
                    <button key={f} className={`px-2.5 py-1 text-xs rounded-full ${f === "All" ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-500 hover:bg-gray-100"}`}>{f}</button>
                  ))}
                </div>
              </div>
              <div className="space-y-2">
                {["gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet", "claude-3-haiku", "gemini-1.5-pro", "text-embedding-3-large", "dall-e-3", "whisper-1"].map((model, i) => (
                  <div key={model} className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-gray-50 border border-gray-200 hover:bg-blue-50/50 transition-colors">
                    <div className="flex items-center gap-2.5">
                      <Shield className="w-3.5 h-3.5 text-green-500" />
                      <span className="text-sm font-medium text-gray-800">{model}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded ${i < 4 ? "bg-blue-50 text-blue-600" : i < 6 ? "bg-purple-50 text-purple-600" : "bg-amber-50 text-amber-600"}`}>
                        {i < 4 ? "chat" : i < 6 ? "embedding" : i < 7 ? "image" : "audio"}
                      </span>
                    </div>
                    <CheckCircle2 className="w-4 h-4 text-green-500" />
                  </div>
                ))}
              </div>
              <p className="text-xs text-gray-400 mt-3 text-center">Showing 8 of 8 granted assets</p>
            </div>
            <div className="space-y-4">
              <div className="bg-white rounded-xl border border-gray-200 p-4">
                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Access Summary</h4>
                <div className="space-y-2.5">
                  <div className="flex justify-between text-sm"><span className="text-gray-500">Mode</span><span className="font-medium text-gray-800">Subset (8 selected)</span></div>
                  <div className="flex justify-between text-sm"><span className="text-gray-500">Models</span><span className="font-medium text-gray-800">6 of 16</span></div>
                  <div className="flex justify-between text-sm"><span className="text-gray-500">Route groups</span><span className="font-medium text-gray-800">2 of 4</span></div>
                </div>
              </div>
              <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
                <p className="text-xs text-blue-800 leading-relaxed">Teams, API keys, and users within this org can only use assets from this allowed set. Child scopes can narrow further but never expand beyond this ceiling.</p>
              </div>
              <Button variant="outline" size="sm" className="w-full text-xs">Edit Asset Access</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
