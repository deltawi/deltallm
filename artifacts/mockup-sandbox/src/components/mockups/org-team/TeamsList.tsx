import { Users, Plus, Search, ChevronRight, MoreHorizontal, Building2, Shield, AlertOctagon, Gauge, Filter } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const teams = [
  { id: "team-001", name: "Engineering", org: "Acme Corp", orgId: "org-acme-001", members: 14, spend: 920, budget: 2000, rpm: 200, tpm: 100000, blocked: false, asset_mode: "restrict", asset_count: 6 },
  { id: "team-002", name: "Data Science", org: "Acme Corp", orgId: "org-acme-001", members: 8, spend: 740, budget: 1500, rpm: 150, tpm: 75000, blocked: false, asset_mode: "restrict", asset_count: 4 },
  { id: "team-003", name: "ML Platform", org: "Beta Labs", orgId: "org-beta-002", members: 5, spend: 3800, budget: 4000, rpm: 100, tpm: 50000, blocked: false, asset_mode: "inherit", asset_count: null },
  { id: "team-004", name: "Frontend", org: "Gamma Research", orgId: "org-gamma-003", members: 6, spend: 120, budget: null, rpm: null, tpm: null, blocked: false, asset_mode: "inherit", asset_count: null },
  { id: "team-005", name: "Security", org: "Delta Finance", orgId: "org-delta-004", members: 3, spend: 0, budget: 200, rpm: 20, tpm: 10000, blocked: true, asset_mode: "restrict", asset_count: 2 },
  { id: "team-006", name: "Research", org: "Gamma Research", orgId: "org-gamma-003", members: 12, spend: 450, budget: null, rpm: 300, tpm: 150000, blocked: false, asset_mode: "inherit", asset_count: null },
  { id: "team-007", name: "Ops", org: "Acme Corp", orgId: "org-acme-001", members: 4, spend: 52.5, budget: 300, rpm: 30, tpm: 15000, blocked: false, asset_mode: "restrict", asset_count: 3 },
];

function MiniBar({ spend, budget }: { spend: number; budget: number | null }) {
  if (!budget) return <span className="text-xs text-gray-400">Unlimited</span>;
  const pct = Math.min(100, (spend / budget) * 100);
  const color = pct > 90 ? "bg-red-500" : pct > 75 ? "bg-amber-500" : "bg-blue-500";
  return (
    <div className="w-28">
      <div className="flex justify-between text-[10px] mb-1">
        <span className="font-medium text-gray-600">${spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
        <span className="text-gray-400">${budget.toLocaleString()}</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function MemberDots({ count }: { count: number }) {
  const colors = ["bg-blue-400", "bg-violet-400", "bg-emerald-400", "bg-amber-400", "bg-pink-400"];
  const shown = Math.min(count, 4);
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex -space-x-1.5">
        {Array.from({ length: shown }).map((_, i) => (
          <div key={i} className={`w-5 h-5 rounded-full border-2 border-white ${colors[i % colors.length]}`} />
        ))}
      </div>
      <span className="text-xs text-gray-600 font-medium">{count}</span>
    </div>
  );
}

export function TeamsList() {
  return (
    <div className="min-h-screen bg-gray-50 font-['Inter']">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-1.5 text-xs text-gray-400 mb-1">
              <span>Platform</span><ChevronRight className="w-3 h-3" /><span className="text-gray-600 font-medium">Teams</span>
            </div>
            <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
              <Users className="w-5 h-5 text-indigo-600" /> Teams
              <Badge variant="secondary" className="ml-1 text-xs">{teams.length}</Badge>
            </h1>
          </div>
          <Button size="sm" className="flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Create Team
          </Button>
        </div>

        <div className="flex items-center gap-3 mt-4">
          <div className="relative w-64">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <Input placeholder="Search teams…" className="pl-8 h-8 text-xs" />
          </div>
          <div className="relative">
            <select className="appearance-none pl-3 pr-8 py-1.5 text-xs border border-gray-200 rounded-lg bg-white text-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500">
              <option>All organizations</option>
              <option>Acme Corp</option>
              <option>Beta Labs</option>
              <option>Gamma Research</option>
            </select>
          </div>
          <button className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50">
            <Filter className="w-3.5 h-3.5" /> Filters
          </button>
          <div className="flex gap-1.5 ml-auto">
            {["All", "Active", "Blocked", "Over budget"].map((f) => (
              <button key={f} className={`px-3 py-1 text-xs rounded-full transition-colors ${f === "All" ? "bg-indigo-50 text-indigo-700 font-medium" : "text-gray-500 hover:bg-gray-100"}`}>
                {f}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="bg-white border-b border-gray-100 px-6 py-3 flex gap-8">
        {[
          { label: "Total teams", value: String(teams.length) },
          { label: "Total members", value: String(teams.reduce((a, t) => a + t.members, 0)) },
          { label: "Blocked", value: String(teams.filter(t => t.blocked).length) },
          { label: "Inherit asset access", value: String(teams.filter(t => t.asset_mode === "inherit").length) },
        ].map((s) => (
          <div key={s.label} className="flex items-center gap-2">
            <span className="text-xs text-gray-500">{s.label}</span>
            <span className="text-xs font-semibold text-gray-900">{s.value}</span>
          </div>
        ))}
      </div>

      {/* Table */}
      <div className="px-6 py-4">
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Team</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Organization</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Members</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Budget</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Rate Limits</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Assets</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {teams.map((t, i) => (
                <tr key={t.id} className={`border-b border-gray-100 hover:bg-indigo-50/30 cursor-pointer transition-colors ${i === teams.length - 1 ? "border-b-0" : ""} ${t.blocked ? "opacity-60" : ""}`}>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${t.blocked ? "bg-red-50" : "bg-indigo-50"}`}>
                        <Users className={`w-4 h-4 ${t.blocked ? "text-red-500" : "text-indigo-600"}`} />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-gray-900 text-sm">{t.name}</span>
                          {t.blocked && (
                            <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-red-100 text-red-700">
                              <AlertOctagon className="w-3 h-3" /> Blocked
                            </span>
                          )}
                        </div>
                        <code className="text-[10px] text-gray-400 font-mono">{t.id}</code>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-1.5">
                      <Building2 className="w-3 h-3 text-gray-400" />
                      <span className="text-xs text-blue-600 hover:underline cursor-pointer font-medium">{t.org}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <MemberDots count={t.members} />
                  </td>
                  <td className="px-4 py-3.5">
                    <MiniBar spend={t.spend} budget={t.budget} />
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-1 text-[10px] text-gray-500">
                        <Gauge className="w-3 h-3" />
                        {t.rpm ? <span className="font-medium text-gray-700">{t.rpm.toLocaleString()}</span> : <span className="text-gray-400">—</span>}
                        <span className="text-gray-400">RPM</span>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-1.5">
                      <Shield className={`w-3.5 h-3.5 ${t.asset_mode === "restrict" ? "text-indigo-500" : "text-gray-400"}`} />
                      <span className={`text-xs font-medium ${t.asset_mode === "restrict" ? "text-indigo-700" : "text-gray-500"}`}>
                        {t.asset_mode === "restrict" ? `${t.asset_count} assets` : "Inherited"}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-1">
                      <button className="px-2.5 py-1 text-xs font-medium text-indigo-600 bg-indigo-50 rounded-lg hover:bg-indigo-100 transition-colors">View</button>
                      <button className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400"><MoreHorizontal className="w-4 h-4" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-t border-gray-200">
            <span className="text-xs text-gray-500">Showing {teams.length} of {teams.length} teams</span>
            <div className="flex gap-1">
              <button className="px-3 py-1 text-xs text-gray-400 border border-gray-200 rounded-md disabled:opacity-40">Previous</button>
              <button className="px-3 py-1 text-xs text-indigo-600 border border-indigo-200 bg-indigo-50 rounded-md font-medium">1</button>
              <button className="px-3 py-1 text-xs text-gray-500 border border-gray-200 rounded-md">Next</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
