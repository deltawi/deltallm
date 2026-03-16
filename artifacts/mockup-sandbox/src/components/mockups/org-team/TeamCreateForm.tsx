import { useState } from "react";
import { Users, X, DollarSign, Gauge, TrendingUp, Info, ChevronRight, Check, AlertCircle, Building2, Shield, Lock, Unlock, AlertOctagon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";

const orgs = [
  { id: "org-acme-001", name: "Acme Corp", budget: 5000, spend: 1842, rpm: 500, tpm: 200000 },
  { id: "org-beta-002", name: "Beta Labs", budget: 5000, spend: 4780, rpm: 200, tpm: 80000 },
  { id: "org-gamma-003", name: "Gamma Research", budget: null, spend: 720, rpm: null, tpm: null },
  { id: "org-delta-004", name: "Delta Finance", budget: 1000, spend: 210, rpm: 100, tpm: 50000 },
];

function FieldLabel({ label, required, hint }: { label: string; required?: boolean; hint?: string }) {
  return (
    <div className="flex items-center gap-1.5 mb-1.5">
      <label className="text-sm font-medium text-gray-700">{label}</label>
      {required && <span className="text-red-500 text-xs">*</span>}
      {hint && <Info className="w-3.5 h-3.5 text-gray-400 cursor-help" />}
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 mt-1">{children}</p>;
}

function InheritBadge({ value, unit }: { value: number | null; unit: string }) {
  if (!value) return <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">Unlimited from org</span>;
  return (
    <span className="text-xs bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full">
      Org limit: {value.toLocaleString()} {unit}
    </span>
  );
}

/* ─── Background faded team list ─── */
function BgTeamList() {
  const rows = [
    { name: "Engineering", org: "Acme Corp", members: 14, spend: 920, budget: 2000 },
    { name: "Data Science", org: "Acme Corp", members: 8, spend: 740, budget: 1500 },
    { name: "ML Platform", org: "Beta Labs", members: 5, spend: 3800, budget: 4000 },
    { name: "Frontend", org: "Gamma Research", members: 6, spend: 120, budget: null },
    { name: "Security", org: "Delta Finance", members: 3, spend: 0, budget: 200, blocked: true },
  ];
  return (
    <div className="flex-1 bg-gray-50 overflow-hidden">
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Users className="w-5 h-5 text-indigo-600" /> Teams
            <Badge variant="secondary" className="ml-1 text-xs">{rows.length}</Badge>
          </h1>
          <Button size="sm" className="opacity-50 bg-indigo-600">+ Create Team</Button>
        </div>
      </div>
      <div className="px-6 py-4">
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Team</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Org</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Budget</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name} className="border-b border-gray-100 last:border-b-0">
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-2.5">
                      <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                        <Users className="w-3.5 h-3.5 text-indigo-300" />
                      </div>
                      <span className="font-semibold text-gray-400 text-sm">{r.name}</span>
                      {r.blocked && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-50 text-red-300 font-semibold">Blocked</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3.5 text-sm text-gray-300">{r.org}</td>
                  <td className="px-4 py-3.5">
                    {r.budget ? (
                      <div className="w-24 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                        <div className="h-full bg-gray-200 rounded-full" style={{ width: `${Math.min(100, (r.spend / r.budget) * 100)}%` }} />
                      </div>
                    ) : <span className="text-xs text-gray-300">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ─── Main component ─── */
export function TeamCreateForm() {
  const [selectedOrg, setSelectedOrg] = useState(orgs[0]);
  const [nameValue, setNameValue] = useState("");
  const [nameError, setNameError] = useState(false);
  const [budgetEnabled, setBudgetEnabled] = useState(false);
  const [rpmEnabled, setRpmEnabled] = useState(false);
  const [tpmEnabled, setTpmEnabled] = useState(false);
  const [assetMode, setAssetMode] = useState<"inherit" | "restrict">("inherit");
  const [blockedEnabled, setBlockedEnabled] = useState(false);

  const org = selectedOrg;
  const remainingBudget = org.budget ? org.budget - org.spend : null;

  return (
    <div className="flex h-screen font-['Inter'] overflow-hidden relative">
      {/* Background faded */}
      <div className="flex-1 flex flex-col opacity-30 pointer-events-none select-none">
        <BgTeamList />
      </div>

      {/* Backdrop */}
      <div className="absolute inset-0 bg-gray-900/20" />

      {/* Drawer */}
      <div className="absolute right-0 top-0 h-full w-[520px] bg-white shadow-2xl flex flex-col z-10">
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-5 border-b border-gray-200">
          <div>
            <div className="flex items-center gap-2 text-xs text-gray-400 mb-1">
              <Users className="w-3.5 h-3.5" />
              <ChevronRight className="w-3 h-3" />
              <span className="text-gray-600 font-medium">New Team</span>
            </div>
            <h2 className="text-lg font-bold text-gray-900">Create Team</h2>
            <p className="text-xs text-gray-500 mt-0.5">Teams group users and API keys under a shared budget and access policy.</p>
          </div>
          <button className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 mt-0.5">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">

          {/* ── Basic info ── */}
          <div>
            <SectionHeading>Basic Info</SectionHeading>
            <div className="space-y-4">
              {/* Org selector */}
              <div>
                <FieldLabel label="Organization" required />
                <div className="relative">
                  <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                  <select
                    className="w-full pl-8 pr-4 py-2 text-sm border border-gray-300 rounded-md bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 appearance-none"
                    value={selectedOrg.id}
                    onChange={e => setSelectedOrg(orgs.find(o => o.id === e.target.value) ?? orgs[0])}
                  >
                    {orgs.map(o => (
                      <option key={o.id} value={o.id}>{o.name}</option>
                    ))}
                  </select>
                </div>
                {/* Org context strip */}
                <div className="mt-2 flex items-center gap-3 px-3 py-2 rounded-lg bg-blue-50 border border-blue-200 text-xs text-blue-700">
                  <span className="font-medium">{org.name}</span>
                  <span className="text-blue-400">·</span>
                  <span>Budget: {org.budget ? `$${org.budget.toLocaleString()}` : "Unlimited"}</span>
                  {org.rpm && <><span className="text-blue-400">·</span><span>RPM: {org.rpm.toLocaleString()}</span></>}
                </div>
              </div>

              {/* Name */}
              <div>
                <FieldLabel label="Team Name" required />
                <Input
                  value={nameValue}
                  onChange={e => { setNameValue(e.target.value); setNameError(false); }}
                  onBlur={() => setNameError(!nameValue.trim())}
                  placeholder="e.g. Engineering, Data Science…"
                  className={nameError ? "border-red-400 focus-visible:ring-red-400" : ""}
                />
                {nameError && (
                  <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3" /> Name is required
                  </p>
                )}
                <p className="text-xs text-gray-400 mt-1">Unique within the selected organization.</p>
              </div>
            </div>
          </div>

          <Separator />

          {/* ── Budget ── */}
          <div>
            <SectionHeading>Budget &amp; Rate Limits</SectionHeading>
            <p className="text-xs text-gray-500 mb-3">
              Limits are <strong>sub-limits</strong> of the org ceiling — they can narrow but never exceed it.
            </p>
            <div className="space-y-4">
              {/* Budget */}
              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
                <div className="flex items-center gap-2.5">
                  <DollarSign className="w-4 h-4 text-green-600" />
                  <div>
                    <p className="text-sm font-medium text-gray-800">Budget limit</p>
                    <InheritBadge value={remainingBudget} unit="remaining" />
                  </div>
                </div>
                <button
                  onClick={() => setBudgetEnabled(!budgetEnabled)}
                  className="relative rounded-full transition-colors"
                  style={{ height: "22px", width: "40px", background: budgetEnabled ? "#2563eb" : "#d1d5db" }}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${budgetEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              {budgetEnabled && (
                <div className="ml-1 pl-3 border-l-2 border-green-200">
                  <FieldLabel label="Budget cap ($)" />
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
                    <Input placeholder={remainingBudget ? `max ${remainingBudget.toLocaleString()}` : "0.00"} className="pl-7" type="number" min="0" />
                  </div>
                  {org.budget && (
                    <p className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                      <Info className="w-3 h-3" /> Cannot exceed org's remaining ${remainingBudget?.toLocaleString()}.
                    </p>
                  )}
                </div>
              )}

              {/* RPM */}
              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
                <div className="flex items-center gap-2.5">
                  <Gauge className="w-4 h-4 text-purple-600" />
                  <div>
                    <p className="text-sm font-medium text-gray-800">RPM limit</p>
                    <InheritBadge value={org.rpm} unit="RPM" />
                  </div>
                </div>
                <button
                  onClick={() => setRpmEnabled(!rpmEnabled)}
                  className="relative rounded-full transition-colors"
                  style={{ height: "22px", width: "40px", background: rpmEnabled ? "#2563eb" : "#d1d5db" }}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${rpmEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              {rpmEnabled && (
                <div className="ml-1 pl-3 border-l-2 border-purple-200">
                  <FieldLabel label="Requests per minute" />
                  <Input placeholder={org.rpm ? `max ${org.rpm.toLocaleString()}` : "unlimited"} type="number" min="1" />
                </div>
              )}

              {/* TPM */}
              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
                <div className="flex items-center gap-2.5">
                  <TrendingUp className="w-4 h-4 text-indigo-600" />
                  <div>
                    <p className="text-sm font-medium text-gray-800">TPM limit</p>
                    <InheritBadge value={org.tpm} unit="TPM" />
                  </div>
                </div>
                <button
                  onClick={() => setTpmEnabled(!tpmEnabled)}
                  className="relative rounded-full transition-colors"
                  style={{ height: "22px", width: "40px", background: tpmEnabled ? "#2563eb" : "#d1d5db" }}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${tpmEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              {tpmEnabled && (
                <div className="ml-1 pl-3 border-l-2 border-indigo-200">
                  <FieldLabel label="Tokens per minute" />
                  <Input placeholder={org.tpm ? `max ${org.tpm.toLocaleString()}` : "unlimited"} type="number" min="1" />
                </div>
              )}
            </div>
          </div>

          <Separator />

          {/* ── Asset access ── */}
          <div>
            <SectionHeading>Asset Access</SectionHeading>
            <p className="text-xs text-gray-500 mb-3">Choose whether this team inherits the org's asset access or uses a custom subset.</p>
            <div className="grid grid-cols-2 gap-2.5">
              {(["inherit", "restrict"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => setAssetMode(mode)}
                  className={`flex flex-col items-start gap-1.5 p-3.5 rounded-xl border-2 text-left transition-all ${
                    assetMode === mode
                      ? mode === "inherit"
                        ? "border-blue-500 bg-blue-50"
                        : "border-indigo-500 bg-indigo-50"
                      : "border-gray-200 bg-white hover:border-gray-300"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {mode === "inherit"
                      ? <Unlock className={`w-4 h-4 ${assetMode === mode ? "text-blue-600" : "text-gray-400"}`} />
                      : <Lock className={`w-4 h-4 ${assetMode === mode ? "text-indigo-600" : "text-gray-400"}`} />}
                    <span className={`text-sm font-semibold ${
                      assetMode === mode
                        ? mode === "inherit" ? "text-blue-800" : "text-indigo-800"
                        : "text-gray-700"
                    }`}>{mode === "inherit" ? "Inherit" : "Restrict"}</span>
                    {assetMode === mode && (
                      <span className={`ml-auto w-4 h-4 rounded-full flex items-center justify-center ${mode === "inherit" ? "bg-blue-600" : "bg-indigo-600"}`}>
                        <Check className="w-2.5 h-2.5 text-white" />
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-500 leading-relaxed">
                    {mode === "inherit"
                      ? "Use all assets available to the org (default)."
                      : "Pick a specific subset from the org's allowed assets."}
                  </p>
                </button>
              ))}
            </div>
            {assetMode === "restrict" && (
              <div className="mt-2.5 flex items-center gap-2 px-3 py-2 rounded-lg bg-indigo-50 border border-indigo-200 text-xs text-indigo-700">
                <Shield className="w-3.5 h-3.5 shrink-0" />
                <span>After creating the team, you can configure which assets to allow from the Team Detail page.</span>
              </div>
            )}
          </div>

          <Separator />

          {/* ── Advanced ── */}
          <div>
            <SectionHeading>Advanced</SectionHeading>
            <div className="flex items-start justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
              <div className="flex items-start gap-2.5">
                <AlertOctagon className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-medium text-gray-800">Block team</p>
                  <p className="text-xs text-gray-500 mt-0.5">Immediately block all requests from this team's API keys.</p>
                </div>
              </div>
              <button
                onClick={() => setBlockedEnabled(!blockedEnabled)}
                className="relative rounded-full transition-colors shrink-0 ml-4"
                style={{ height: "22px", width: "40px", background: blockedEnabled ? "#ef4444" : "#d1d5db" }}
              >
                <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${blockedEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
            </div>
            {blockedEnabled && (
              <div className="mt-2 flex items-center gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                <span>The team will be created in a blocked state. All API requests will be rejected until you unblock it.</span>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-gray-200 px-6 py-4 bg-white">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs">
              {!nameValue.trim() ? (
                <span className="flex items-center gap-1 text-amber-600"><AlertCircle className="w-3 h-3" /> Fill in required fields</span>
              ) : (
                <span className="flex items-center gap-1 text-green-600"><Check className="w-3 h-3" /> Ready to create</span>
              )}
            </p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" className="text-xs">Cancel</Button>
              <Button
                size="sm"
                className={`text-xs ${nameValue.trim() ? "bg-indigo-600 hover:bg-indigo-700" : "opacity-50 cursor-not-allowed"}`}
              >
                Create Team
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
