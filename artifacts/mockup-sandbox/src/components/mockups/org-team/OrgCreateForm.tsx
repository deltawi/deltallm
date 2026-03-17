import { useState } from "react";
import { Building2, X, DollarSign, Gauge, TrendingUp, Info, ChevronRight, Check, AlertCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";

/* ────────────── helpers ────────────── */
function FieldLabel({ label, required, hint }: { label: string; required?: boolean; hint?: string }) {
  return (
    <div className="flex items-center gap-1.5 mb-1.5">
      <label className="text-sm font-medium text-gray-700">{label}</label>
      {required && <span className="text-red-500 text-xs">*</span>}
      {hint && (
        <span className="group relative">
          <Info className="w-3.5 h-3.5 text-gray-400 cursor-help" />
        </span>
      )}
    </div>
  );
}

function OptionalBadge() {
  return <span className="text-xs text-gray-400 font-normal ml-1">(optional)</span>;
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 mt-1">{children}</p>
  );
}

/* ────────────── background list (faded) ────────────── */
function BgList() {
  const rows = [
    { letter: "A", name: "Acme Corp", id: "org-acme-001", pct: 37, status: "healthy" },
    { letter: "B", name: "Beta Labs", id: "org-beta-002", pct: 96, status: "warning" },
    { letter: "G", name: "Gamma Research", id: "org-gamma-003", pct: null, status: "healthy" },
    { letter: "D", name: "Delta Finance", id: "org-delta-004", pct: 21, status: "healthy" },
    { letter: "E", name: "Epsilon Ventures", id: "org-eps-005", pct: 0, status: "idle" },
    { letter: "Z", name: "Zeta Dynamics", id: "org-zeta-006", pct: 100, status: "over" },
  ];
  return (
    <div className="flex-1 bg-gray-50 overflow-hidden">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Building2 className="w-5 h-5 text-blue-600" /> Organizations
            <Badge variant="secondary" className="ml-1 text-xs">6</Badge>
          </h1>
          <Button size="sm" className="opacity-50">+ Create Organization</Button>
        </div>
      </div>
      {/* Table rows */}
      <div className="px-6 py-4">
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Organization</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Budget Usage</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Members</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-gray-100 last:border-b-0">
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-100 to-blue-200 flex items-center justify-center shrink-0">
                        <span className="text-xs font-bold text-blue-700">{r.letter}</span>
                      </div>
                      <div>
                        <span className="font-semibold text-gray-400 text-sm">{r.name}</span>
                        <p className="text-[10px] text-gray-300 font-mono">{r.id}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3.5">
                    {r.pct !== null ? (
                      <div className="w-28">
                        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div className="h-full bg-gray-300 rounded-full" style={{ width: `${r.pct}%` }} />
                        </div>
                      </div>
                    ) : <span className="text-xs text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3.5 text-sm text-gray-300">—</td>
                  <td className="px-4 py-3.5">
                    <span className="text-xs text-gray-300">View</span>
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

/* ────────────── main form ────────────── */
export function OrgCreateForm() {
  const [budgetEnabled, setBudgetEnabled] = useState(false);
  const [rpmEnabled, setRpmEnabled] = useState(false);
  const [tpmEnabled, setTpmEnabled] = useState(false);
  const [auditStorage, setAuditStorage] = useState(true);
  const [nameValue, setNameValue] = useState("");
  const [nameError, setNameError] = useState(false);

  return (
    <div className="flex h-screen font-['Inter'] overflow-hidden">
      {/* Background: faded list */}
      <div className="flex-1 flex flex-col opacity-30 pointer-events-none select-none">
        <BgList />
      </div>

      {/* Backdrop */}
      <div className="absolute inset-0 bg-gray-900/20" />

      {/* Drawer */}
      <div className="absolute right-0 top-0 h-full w-[500px] bg-white shadow-2xl flex flex-col z-10">
        {/* Drawer header */}
        <div className="flex items-start justify-between px-6 py-5 border-b border-gray-200">
          <div>
            <div className="flex items-center gap-2 text-xs text-gray-400 mb-1">
              <Building2 className="w-3.5 h-3.5" />
              <ChevronRight className="w-3 h-3" />
              <span className="text-gray-600 font-medium">New Organization</span>
            </div>
            <h2 className="text-lg font-bold text-gray-900">Create Organization</h2>
            <p className="text-xs text-gray-500 mt-0.5">Set up a new top-level tenant with its own budget and access controls.</p>
          </div>
          <button className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 mt-0.5">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">

          {/* ── Basic info ── */}
          <div>
            <SectionHeading>Basic Info</SectionHeading>
            <div className="space-y-4">
              <div>
                <FieldLabel label="Organization Name" required />
                <Input
                  value={nameValue}
                  onChange={e => { setNameValue(e.target.value); setNameError(false); }}
                  onBlur={() => setNameError(!nameValue.trim())}
                  placeholder="e.g. Acme Corp"
                  className={nameError ? "border-red-400 focus-visible:ring-red-400" : ""}
                />
                {nameError && (
                  <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3" /> Name is required
                  </p>
                )}
                <p className="text-xs text-gray-400 mt-1">Must be unique across the platform.</p>
              </div>
            </div>
          </div>

          <Separator />

          {/* ── Budget ── */}
          <div>
            <SectionHeading>Budget &amp; Spend Limits</SectionHeading>
            <div className="space-y-4">
              {/* Budget toggle row */}
              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
                <div className="flex items-center gap-2.5">
                  <DollarSign className="w-4 h-4 text-green-600" />
                  <div>
                    <p className="text-sm font-medium text-gray-800">Budget limit</p>
                    <p className="text-xs text-gray-500">Cap total spend for this org</p>
                  </div>
                </div>
                <button
                  onClick={() => setBudgetEnabled(!budgetEnabled)}
                  className={`relative w-10 h-5.5 rounded-full transition-colors ${budgetEnabled ? "bg-blue-600" : "bg-gray-200"}`}
                  style={{ height: "22px", width: "40px" }}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${budgetEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              {budgetEnabled && (
                <div className="ml-1 pl-3 border-l-2 border-blue-200">
                  <FieldLabel label="Monthly budget" />
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-medium">$</span>
                    <Input placeholder="5000.00" className="pl-7" type="number" min="0" step="0.01" />
                  </div>
                  <p className="text-xs text-gray-400 mt-1">Requests will be rejected once spend reaches this amount.</p>
                </div>
              )}

              {/* RPM */}
              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
                <div className="flex items-center gap-2.5">
                  <Gauge className="w-4 h-4 text-purple-600" />
                  <div>
                    <p className="text-sm font-medium text-gray-800">RPM limit</p>
                    <p className="text-xs text-gray-500">Max requests per minute</p>
                  </div>
                </div>
                <button
                  onClick={() => setRpmEnabled(!rpmEnabled)}
                  className={`relative rounded-full transition-colors ${rpmEnabled ? "bg-blue-600" : "bg-gray-200"}`}
                  style={{ height: "22px", width: "40px" }}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${rpmEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              {rpmEnabled && (
                <div className="ml-1 pl-3 border-l-2 border-purple-200">
                  <FieldLabel label="Requests per minute" />
                  <Input placeholder="500" type="number" min="1" />
                  <p className="text-xs text-gray-400 mt-1">Shared across all teams and API keys in this org.</p>
                </div>
              )}

              {/* TPM */}
              <div className="flex items-center justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
                <div className="flex items-center gap-2.5">
                  <TrendingUp className="w-4 h-4 text-indigo-600" />
                  <div>
                    <p className="text-sm font-medium text-gray-800">TPM limit</p>
                    <p className="text-xs text-gray-500">Max tokens per minute</p>
                  </div>
                </div>
                <button
                  onClick={() => setTpmEnabled(!tpmEnabled)}
                  className={`relative rounded-full transition-colors ${tpmEnabled ? "bg-blue-600" : "bg-gray-200"}`}
                  style={{ height: "22px", width: "40px" }}
                >
                  <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${tpmEnabled ? "translate-x-5" : "translate-x-0.5"}`} />
                </button>
              </div>
              {tpmEnabled && (
                <div className="ml-1 pl-3 border-l-2 border-indigo-200">
                  <FieldLabel label="Tokens per minute" />
                  <Input placeholder="200000" type="number" min="1" />
                  <p className="text-xs text-gray-400 mt-1">Applies across input + output tokens.</p>
                </div>
              )}
            </div>
          </div>

          <Separator />

          {/* ── Settings ── */}
          <div>
            <SectionHeading>Settings</SectionHeading>
            <div className="flex items-start justify-between p-3 rounded-lg bg-gray-50 border border-gray-200">
              <div>
                <p className="text-sm font-medium text-gray-800">Audit content storage</p>
                <p className="text-xs text-gray-500 mt-0.5 max-w-xs leading-relaxed">Store request and response payloads in audit logs for compliance review.</p>
              </div>
              <button
                onClick={() => setAuditStorage(!auditStorage)}
                className={`relative rounded-full transition-colors shrink-0 ml-4`}
                style={{ height: "22px", width: "40px", background: auditStorage ? "#2563eb" : "#d1d5db" }}
              >
                <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${auditStorage ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
            </div>
            {auditStorage && (
              <div className="mt-2 flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                <Info className="w-3.5 h-3.5 shrink-0" />
                <span>Enabling this increases storage usage and may have billing implications. Ensure it aligns with your data retention policy.</span>
              </div>
            )}
          </div>

        </div>

        {/* Footer */}
        <div className="border-t border-gray-200 px-6 py-4 bg-white">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-gray-400">
              {!nameValue.trim() ? (
                <span className="flex items-center gap-1 text-amber-600"><AlertCircle className="w-3 h-3" /> Fill in required fields to continue</span>
              ) : (
                <span className="flex items-center gap-1 text-green-600"><Check className="w-3 h-3" /> Ready to create</span>
              )}
            </p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" className="text-xs">Cancel</Button>
              <Button
                size="sm"
                className={`text-xs ${nameValue.trim() ? "bg-blue-600 hover:bg-blue-700" : "opacity-50 cursor-not-allowed"}`}
              >
                Create Organization
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
