import { Building2, Users, DollarSign, Plus, Search, MoreHorizontal, TrendingUp, ChevronRight, Gauge, Shield, AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const orgs = [
  { id: "org-acme-001", name: "Acme Corp", teams: 6, members: 42, spend: 1842.5, budget: 5000, rpm: 500, tpm: 200000, asset_count: 12, status: "healthy" },
  { id: "org-beta-002", name: "Beta Labs", teams: 3, members: 18, spend: 4780.0, budget: 5000, rpm: 200, tpm: 80000, asset_count: 5, status: "warning" },
  { id: "org-gamma-003", name: "Gamma Research", teams: 9, members: 67, spend: 720.1, budget: null, rpm: null, tpm: null, asset_count: 20, status: "healthy" },
  { id: "org-delta-004", name: "Delta Finance", teams: 4, members: 29, spend: 210.0, budget: 1000, rpm: 100, tpm: 50000, asset_count: 8, status: "healthy" },
  { id: "org-eps-005", name: "Epsilon Ventures", teams: 2, members: 8, spend: 0, budget: 500, rpm: 50, tpm: 20000, asset_count: 3, status: "idle" },
  { id: "org-zeta-006", name: "Zeta Dynamics", teams: 7, members: 55, spend: 3100, budget: 3000, rpm: 300, tpm: 120000, asset_count: 15, status: "over" },
];

function BudgetRing({ spend, budget }: { spend: number; budget: number | null }) {
  if (!budget) return <span className="text-xs text-gray-400 font-medium">Unlimited</span>;
  const pct = Math.min(100, (spend / budget) * 100);
  const r = 18;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const color = pct > 95 ? "#ef4444" : pct > 80 ? "#f59e0b" : "#3b82f6";
  return (
    <div className="flex items-center gap-2.5">
      <div className="relative w-11 h-11 shrink-0">
        <svg viewBox="0 0 44 44" className="w-11 h-11 -rotate-90">
          <circle cx="22" cy="22" r={r} fill="none" stroke="#e5e7eb" strokeWidth="3.5" />
          <circle cx="22" cy="22" r={r} fill="none" stroke={color} strokeWidth="3.5"
            strokeDasharray={`${dash} ${circ}`} strokeLinecap="round" />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-[9px] font-bold text-gray-700">
          {Math.round(pct)}%
        </span>
      </div>
      <div>
        <p className="text-xs font-semibold text-gray-800">${spend.toLocaleString(undefined, { maximumFractionDigits: 0 })}</p>
        <p className="text-[10px] text-gray-400">of ${budget.toLocaleString()}</p>
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const map: Record<string, string> = {
    healthy: "bg-emerald-500",
    warning: "bg-amber-500",
    over: "bg-red-500",
    idle: "bg-gray-300",
  };
  return <span className={`inline-block w-2 h-2 rounded-full ${map[status] || "bg-gray-300"}`} />;
}

function RateTag({ value, unit }: { value: number | null; unit: string }) {
  if (value == null) return <span className="text-xs text-gray-400">—</span>;
  return (
    <span className="inline-flex items-center gap-0.5 text-xs font-medium text-gray-600 bg-gray-100 px-2 py-0.5 rounded-full">
      {value.toLocaleString()} <span className="text-gray-400">{unit}</span>
    </span>
  );
}

export function OrganizationsList() {
  return (
    <div className="min-h-screen bg-gray-50 font-['Inter']">
      {/* Top bar */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs text-gray-400 mb-1">
              <span>Platform</span><ChevronRight className="w-3 h-3" /><span className="text-gray-600 font-medium">Organizations</span>
            </div>
            <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
              <Building2 className="w-5 h-5 text-blue-600" /> Organizations
              <Badge variant="secondary" className="ml-1 text-xs">{orgs.length}</Badge>
            </h1>
          </div>
          <Button size="sm" className="flex items-center gap-1.5">
            <Plus className="w-4 h-4" /> Create Organization
          </Button>
        </div>

        <div className="flex items-center gap-3 mt-4">
          <div className="relative w-72">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <Input placeholder="Search organizations…" className="pl-8 h-8 text-xs" />
          </div>
          <div className="flex gap-1.5 ml-auto">
            {["All", "Healthy", "Over budget", "Idle"].map((f) => (
              <button key={f} className={`px-3 py-1 text-xs rounded-full transition-colors ${f === "All" ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-500 hover:bg-gray-100"}`}>
                {f}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Summary strip */}
      <div className="bg-white border-b border-gray-100 px-6 py-3 flex gap-8">
        {[
          { label: "Total spend", value: "$10,652", icon: DollarSign, color: "text-green-600" },
          { label: "Active teams", value: "31", icon: Users, color: "text-blue-600" },
          { label: "Models granted", value: "—", icon: Shield, color: "text-purple-600" },
          { label: "Over budget", value: "1", icon: AlertCircle, color: "text-red-500" },
        ].map((s) => (
          <div key={s.label} className="flex items-center gap-2">
            <s.icon className={`w-3.5 h-3.5 ${s.color}`} />
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
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Organization</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Budget Usage</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Rate Limits</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Members</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Trend</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {orgs.map((org, i) => (
                <tr key={org.id} className={`border-b border-gray-100 hover:bg-blue-50/40 cursor-pointer transition-colors ${i === orgs.length - 1 ? "border-b-0" : ""}`}>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-100 to-blue-200 flex items-center justify-center shrink-0">
                        <span className="text-xs font-bold text-blue-700">{org.name[0]}</span>
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-gray-900 text-sm">{org.name}</span>
                          <StatusDot status={org.status} />
                        </div>
                        <code className="text-[10px] text-gray-400 font-mono">{org.id}</code>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <BudgetRing spend={org.spend} budget={org.budget} />
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-1.5">
                        <Gauge className="w-3 h-3 text-gray-400" />
                        <RateTag value={org.rpm} unit="RPM" />
                      </div>
                      <div className="flex items-center gap-1.5">
                        <TrendingUp className="w-3 h-3 text-gray-400" />
                        <RateTag value={org.tpm} unit="TPM" />
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className="flex items-center gap-1 text-gray-700">
                        <Users className="w-3.5 h-3.5 text-gray-400" />
                        <span className="text-sm font-medium">{org.members}</span>
                      </div>
                      <span className="text-gray-300">·</span>
                      <div className="flex items-center gap-1 text-gray-500 text-xs">
                        <Building2 className="w-3.5 h-3.5 text-gray-400" />
                        {org.teams} teams
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    <span className={`text-xs font-semibold ${org.trend.startsWith("+") && org.trend !== "+0%" ? "text-emerald-600" : "text-gray-400"}`}>
                      {org.trend}
                    </span>
                    <p className="text-[10px] text-gray-400">this week</p>
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-1">
                      <button className="px-2.5 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors">
                        View
                      </button>
                      <button className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400">
                        <MoreHorizontal className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-t border-gray-200">
            <span className="text-xs text-gray-500">Showing 6 of 6 organizations</span>
            <div className="flex gap-1">
              <button className="px-3 py-1 text-xs text-gray-400 border border-gray-200 rounded-md disabled:opacity-40">Previous</button>
              <button className="px-3 py-1 text-xs text-blue-600 border border-blue-200 bg-blue-50 rounded-md font-medium">1</button>
              <button className="px-3 py-1 text-xs text-gray-500 border border-gray-200 rounded-md">Next</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
