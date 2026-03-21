import { useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { teams, organizations } from '../lib/api';
import { buildParentScopedAssetTargets } from '../lib/assetAccess';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import ToggleSwitch from '../components/ToggleSwitch';
import { useAuth } from '../lib/auth';
import {
  Users, X, DollarSign, Gauge, TrendingUp, Info,
  ChevronRight, Check, AlertCircle, Building2,
  Shield, Lock, Unlock, AlertOctagon, Clock, CalendarDays,
} from 'lucide-react';

/* ─────────────── helpers ─────────────── */

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 mt-1">
      {children}
    </p>
  );
}

function FieldLabel({ label, required }: { label: string; required?: boolean }) {
  return (
    <div className="flex items-center gap-1.5 mb-1.5">
      <label className="text-sm font-medium text-gray-700">{label}</label>
      {required && <span className="text-red-500 text-xs">*</span>}
    </div>
  );
}

function InheritBadge({ value, unit }: { value: number | null | undefined; unit: string }) {
  if (value == null) {
    return <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">Unlimited from org</span>;
  }
  return (
    <span className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full">
      Org limit: {value.toLocaleString()} {unit}
    </span>
  );
}

/* ─────────────── faded background list ─────────────── */

function BgList({ items }: { items: any[] }) {
  return (
    <div className="flex-1 bg-gray-50 overflow-hidden pointer-events-none select-none">
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <Users className="w-5 h-5 text-indigo-600" /> Teams
            {items.length > 0 && (
              <span className="inline-flex items-center justify-center min-w-[1.5rem] h-5 px-1.5 rounded-full bg-gray-100 text-xs font-semibold text-gray-600 ml-1">
                {items.length}+
              </span>
            )}
          </h1>
          <span className="px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg opacity-50">
            + Create Team
          </span>
        </div>
      </div>
      <div className="px-6 py-4">
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Team</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Organization</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-400 uppercase tracking-wide">Budget</th>
              </tr>
            </thead>
            <tbody>
              {(items.length > 0 ? items : Array.from({ length: 5 })).map((t: any, i: number) => {
                const name = t?.team_alias || t?.team_id || '—';
                const orgName = t?.organization_name || t?.organization_id || '—';
                const pct = t?.max_budget ? Math.min(100, ((t.spend || 0) / t.max_budget) * 100) : null;
                return (
                  <tr key={t?.team_id || i} className="border-b border-gray-100 last:border-b-0">
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-2.5">
                        <div className="w-7 h-7 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
                          <Users className="w-3.5 h-3.5 text-indigo-200" />
                        </div>
                        <span className="font-semibold text-gray-300 text-sm">{name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-sm text-gray-300">{orgName}</td>
                    <td className="px-4 py-3.5">
                      {pct !== null ? (
                        <div className="w-24 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div className="h-full bg-gray-200 rounded-full" style={{ width: `${pct}%` }} />
                        </div>
                      ) : (
                        <span className="text-xs text-gray-300">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ─────────────── page ─────────────── */

export default function TeamCreate() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const preselectedOrgId = searchParams.get('organization_id') || '';
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const permissions = new Set(session?.effective_permissions || []);
  const canCreateTeam = userRole === 'platform_admin' || permissions.has('team.update');

  if (!canCreateTeam) {
    return <Navigate to="/teams" replace />;
  }

  /* background teams list */
  const { data: teamsResult } = useApi(() => teams.list({ limit: 8, offset: 0 }), []);
  const bgItems: any[] = teamsResult?.data || [];

  /* org list for selector */
  const { data: orgResult } = useApi(() => organizations.list({ limit: 500 }), []);
  const orgList: any[] = orgResult?.data || [];

  /* form state */
  const [selectedOrgId, setSelectedOrgId] = useState(preselectedOrgId);
  const [teamName, setTeamName] = useState('');
  const [nameError, setNameError] = useState(false);
  const [budgetEnabled, setBudgetEnabled] = useState(false);
  const [rpmEnabled, setRpmEnabled] = useState(false);
  const [tpmEnabled, setTpmEnabled] = useState(false);
  const [budgetValue, setBudgetValue] = useState('');
  const [rpmValue, setRpmValue] = useState('');
  const [tpmValue, setTpmValue] = useState('');
  const [rphEnabled, setRphEnabled] = useState(false);
  const [rpdEnabled, setRpdEnabled] = useState(false);
  const [tpdEnabled, setTpdEnabled] = useState(false);
  const [rphValue, setRphValue] = useState('');
  const [rpdValue, setRpdValue] = useState('');
  const [tpdValue, setTpdValue] = useState('');
  const [assetMode, setAssetMode] = useState<'inherit' | 'restrict'>('inherit');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [blocked, setBlocked] = useState(false);

  /* selected org details */
  const selectedOrg = orgList.find((o) => o.organization_id === selectedOrgId) || null;
  const remainingBudget = selectedOrg?.max_budget != null
    ? Math.max(0, selectedOrg.max_budget - (selectedOrg.spend || 0))
    : null;

  /* reset asset access when org changes */
  const handleOrgChange = (orgId: string) => {
    setSelectedOrgId(orgId);
    setAssetMode('inherit');
    setSelectedKeys([]);
  };

  /* org asset visibility for restrict mode */
  const { data: orgAssetVisibility, loading: orgAssetVisibilityLoading } = useApi(
    () => (assetMode === 'restrict' && selectedOrgId
      ? organizations.assetVisibility(selectedOrgId)
      : Promise.resolve(null)),
    [assetMode, selectedOrgId],
  );

  const assetTargets = buildParentScopedAssetTargets(
    orgAssetVisibility?.callable_targets?.items || [],
    selectedKeys,
    assetMode,
  );

  /* submit */
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isReady = teamName.trim().length > 0 && selectedOrgId.length > 0;

  const handleCreate = async () => {
    if (!teamName.trim()) { setNameError(true); return; }
    if (!selectedOrgId) { setError('Please select an organization.'); return; }
    setError(null);
    setSaving(true);
    try {
      const payload: any = {
        team_alias: teamName.trim(),
        organization_id: selectedOrgId,
        max_budget: budgetEnabled && budgetValue ? Number(budgetValue) : undefined,
        rpm_limit: rpmEnabled && rpmValue ? Number(rpmValue) : undefined,
        tpm_limit: tpmEnabled && tpmValue ? Number(tpmValue) : undefined,
        rph_limit: rphEnabled && rphValue ? Number(rphValue) : undefined,
        rpd_limit: rpdEnabled && rpdValue ? Number(rpdValue) : undefined,
        tpd_limit: tpdEnabled && tpdValue ? Number(tpdValue) : undefined,
      };

      const created = await teams.create(payload);
      let pageWarning: string | null = null;

      /* block if requested (best-effort patch) */
      if (blocked) {
        try { await teams.update(created.team_id, { blocked: true }); } catch { /* non-fatal */ }
      }

      /* apply restrict asset access if selected */
      if (assetMode === 'restrict') {
        try {
          await teams.updateAssetAccess(created.team_id, {
            mode: 'restrict',
            selected_callable_keys: selectedKeys,
          });
        } catch (err: any) {
          pageWarning = err?.message || 'Team created, but restricted asset access could not be applied.';
        }
      }

      navigate(`/teams/${created.team_id}`, {
        state: pageWarning ? { pageWarning } : undefined,
      });
    } catch (err: any) {
      setError(err?.message || 'Failed to create team');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => navigate('/teams');

  /* ─────────────── render ─────────────── */
  return (
    <div className="flex h-screen overflow-hidden relative">
      {/* Faded background */}
      <div className="flex-1 flex flex-col opacity-30">
        <BgList items={bgItems} />
      </div>

      {/* Backdrop */}
      <div className="absolute inset-0 bg-gray-900/20" onClick={handleCancel} />

      {/* Slide-over drawer */}
      <div className="absolute right-0 top-0 h-full w-[520px] bg-white shadow-2xl flex flex-col z-10">

        {/* Header */}
        <div className="flex items-start justify-between px-6 py-5 border-b border-gray-200 shrink-0">
          <div>
            <div className="flex items-center gap-1.5 text-xs text-gray-400 mb-1">
              <Users className="w-3.5 h-3.5" />
              <ChevronRight className="w-3 h-3" />
              <span className="text-gray-600 font-medium">New Team</span>
            </div>
            <h2 className="text-lg font-bold text-gray-900">Create Team</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Teams group users and API keys under a shared budget and access policy.
            </p>
          </div>
          <button
            onClick={handleCancel}
            className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 mt-0.5 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">

          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" /> {error}
            </div>
          )}

          {/* ── Basic Info ── */}
          <div>
            <SectionHeading>Basic Info</SectionHeading>
            <div className="space-y-4">

              {/* Org selector */}
              <div>
                <FieldLabel label="Organization" required />
                <div className="relative">
                  <Building2 className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                  <select
                    value={selectedOrgId}
                    onChange={(e) => handleOrgChange(e.target.value)}
                    disabled={saving}
                    className="w-full pl-8 pr-4 py-2 text-sm border border-gray-300 rounded-lg bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 appearance-none disabled:opacity-50"
                  >
                    <option value="">Select an organization…</option>
                    {orgList.map((o: any) => (
                      <option key={o.organization_id} value={o.organization_id}>
                        {o.organization_name || o.organization_id}
                      </option>
                    ))}
                  </select>
                </div>
                {selectedOrg && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg bg-indigo-50 border border-indigo-200 text-xs text-indigo-700">
                    <span className="font-semibold">{selectedOrg.organization_name || selectedOrg.organization_id}</span>
                    <span className="text-indigo-300">·</span>
                    <span>
                      Budget:{' '}
                      {selectedOrg.max_budget != null
                        ? `$${selectedOrg.max_budget.toLocaleString()} (${remainingBudget != null ? `$${remainingBudget.toLocaleString()} remaining` : '—'})`
                        : 'Unlimited'}
                    </span>
                    {selectedOrg.rpm_limit != null && (
                      <>
                        <span className="text-indigo-300">·</span>
                        <span>RPM: {selectedOrg.rpm_limit.toLocaleString()}</span>
                      </>
                    )}
                    {selectedOrg.tpm_limit != null && (
                      <>
                        <span className="text-indigo-300">·</span>
                        <span>TPM: {selectedOrg.tpm_limit.toLocaleString()}</span>
                      </>
                    )}
                    {selectedOrg.rph_limit != null && (
                      <>
                        <span className="text-indigo-300">·</span>
                        <span>RPH: {selectedOrg.rph_limit.toLocaleString()}</span>
                      </>
                    )}
                    {selectedOrg.rpd_limit != null && (
                      <>
                        <span className="text-indigo-300">·</span>
                        <span>RPD: {selectedOrg.rpd_limit.toLocaleString()}</span>
                      </>
                    )}
                    {selectedOrg.tpd_limit != null && (
                      <>
                        <span className="text-indigo-300">·</span>
                        <span>TPD: {selectedOrg.tpd_limit.toLocaleString()}</span>
                      </>
                    )}
                  </div>
                )}
              </div>

              {/* Team name */}
              <div>
                <FieldLabel label="Team Name" required />
                <input
                  value={teamName}
                  onChange={(e) => { setTeamName(e.target.value); setNameError(false); }}
                  onBlur={() => setNameError(!teamName.trim())}
                  placeholder="e.g. Engineering, Data Science…"
                  disabled={saving}
                  className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-colors disabled:opacity-50 ${
                    nameError ? 'border-red-400 focus:ring-red-400' : 'border-gray-300'
                  }`}
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

          <div className="border-t border-gray-200" />

          {/* ── Budget & Rate Limits ── */}
          <div>
            <SectionHeading>Budget &amp; Rate Limits</SectionHeading>
            <p className="text-xs text-gray-500 mb-3">
              Limits are <strong>sub-limits</strong> of the org ceiling — they can narrow but never exceed it.
            </p>
            <div className="space-y-3">

              {/* Budget */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <DollarSign className="w-4 h-4 text-green-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">Budget limit</p>
                      <InheritBadge value={remainingBudget} unit="remaining" />
                    </div>
                  </div>
                  <ToggleSwitch
                    checked={budgetEnabled}
                    onCheckedChange={setBudgetEnabled}
                    disabled={saving}
                    aria-label="Toggle budget limit"
                  />
                </div>
                {budgetEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-green-200">
                    <FieldLabel label="Budget cap ($)" />
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
                      <input
                        value={budgetValue}
                        onChange={(e) => setBudgetValue(e.target.value)}
                        placeholder={remainingBudget != null ? `max ${remainingBudget.toLocaleString()}` : '0.00'}
                        type="number"
                        min="0"
                        step="0.01"
                        className="w-full pl-7 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>
                    {selectedOrg?.max_budget != null && (
                      <p className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                        <Info className="w-3 h-3" /> Cannot exceed org's remaining ${remainingBudget?.toLocaleString()}.
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* RPM */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <Gauge className="w-4 h-4 text-purple-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">RPM limit</p>
                      <InheritBadge value={selectedOrg?.rpm_limit} unit="RPM" />
                    </div>
                  </div>
                  <ToggleSwitch
                    checked={rpmEnabled}
                    onCheckedChange={setRpmEnabled}
                    disabled={saving}
                    aria-label="Toggle RPM limit"
                  />
                </div>
                {rpmEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-purple-200">
                    <FieldLabel label="Requests per minute" />
                    <input
                      value={rpmValue}
                      onChange={(e) => setRpmValue(e.target.value)}
                      placeholder={selectedOrg?.rpm_limit ? `max ${selectedOrg.rpm_limit.toLocaleString()}` : 'unlimited'}
                      type="number"
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                )}
              </div>

              {/* TPM */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <TrendingUp className="w-4 h-4 text-indigo-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">TPM limit</p>
                      <InheritBadge value={selectedOrg?.tpm_limit} unit="TPM" />
                    </div>
                  </div>
                  <ToggleSwitch
                    checked={tpmEnabled}
                    onCheckedChange={setTpmEnabled}
                    disabled={saving}
                    aria-label="Toggle TPM limit"
                  />
                </div>
                {tpmEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-indigo-200">
                    <FieldLabel label="Tokens per minute" />
                    <input
                      value={tpmValue}
                      onChange={(e) => setTpmValue(e.target.value)}
                      placeholder={selectedOrg?.tpm_limit ? `max ${selectedOrg.tpm_limit.toLocaleString()}` : 'unlimited'}
                      type="number"
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                )}
              </div>

              {/* RPH */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <Clock className="w-4 h-4 text-teal-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">RPH limit</p>
                      <InheritBadge value={selectedOrg?.rph_limit} unit="RPH" />
                    </div>
                  </div>
                  <ToggleSwitch
                    checked={rphEnabled}
                    onCheckedChange={setRphEnabled}
                    disabled={saving}
                    aria-label="Toggle RPH limit"
                  />
                </div>
                {rphEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-teal-200">
                    <FieldLabel label="Requests per hour" />
                    <input
                      value={rphValue}
                      onChange={(e) => setRphValue(e.target.value)}
                      placeholder={selectedOrg?.rph_limit ? `max ${selectedOrg.rph_limit.toLocaleString()}` : 'unlimited'}
                      type="number"
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-teal-500"
                    />
                  </div>
                )}
              </div>

              {/* RPD */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <CalendarDays className="w-4 h-4 text-amber-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">RPD limit</p>
                      <InheritBadge value={selectedOrg?.rpd_limit} unit="RPD" />
                    </div>
                  </div>
                  <ToggleSwitch
                    checked={rpdEnabled}
                    onCheckedChange={setRpdEnabled}
                    disabled={saving}
                    aria-label="Toggle RPD limit"
                  />
                </div>
                {rpdEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-amber-200">
                    <FieldLabel label="Requests per day" />
                    <input
                      value={rpdValue}
                      onChange={(e) => setRpdValue(e.target.value)}
                      placeholder={selectedOrg?.rpd_limit ? `max ${selectedOrg.rpd_limit.toLocaleString()}` : 'unlimited'}
                      type="number"
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
                    />
                  </div>
                )}
              </div>

              {/* TPD */}
              <div className="p-3 rounded-lg bg-gray-50 border border-gray-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    <TrendingUp className="w-4 h-4 text-rose-600" />
                    <div>
                      <p className="text-sm font-medium text-gray-800">TPD limit</p>
                      <InheritBadge value={selectedOrg?.tpd_limit} unit="TPD" />
                    </div>
                  </div>
                  <ToggleSwitch
                    checked={tpdEnabled}
                    onCheckedChange={setTpdEnabled}
                    disabled={saving}
                    aria-label="Toggle TPD limit"
                  />
                </div>
                {tpdEnabled && (
                  <div className="ml-6 pl-3 border-l-2 border-rose-200">
                    <FieldLabel label="Tokens per day" />
                    <input
                      value={tpdValue}
                      onChange={(e) => setTpdValue(e.target.value)}
                      placeholder={selectedOrg?.tpd_limit ? `max ${selectedOrg.tpd_limit.toLocaleString()}` : 'unlimited'}
                      type="number"
                      min="1"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-rose-500"
                    />
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="border-t border-gray-200" />

          {/* ── Asset Access ── */}
          <div>
            <SectionHeading>Asset Access</SectionHeading>
            <p className="text-xs text-gray-500 mb-3">
              Choose whether this team inherits the org's asset access or uses a custom subset.
            </p>
            <div className="grid grid-cols-2 gap-2.5 mb-3">
              {(['inherit', 'restrict'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => { setAssetMode(mode); if (mode === 'inherit') setSelectedKeys([]); }}
                  disabled={saving}
                  className={`flex flex-col items-start gap-1.5 p-3.5 rounded-xl border-2 text-left transition-all disabled:opacity-50 ${
                    assetMode === mode
                      ? mode === 'inherit'
                        ? 'border-blue-500 bg-blue-50'
                        : 'border-indigo-500 bg-indigo-50'
                      : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <div className="flex items-center gap-2 w-full">
                    {mode === 'inherit'
                      ? <Unlock className={`w-4 h-4 ${assetMode === mode ? 'text-blue-600' : 'text-gray-400'}`} />
                      : <Lock className={`w-4 h-4 ${assetMode === mode ? 'text-indigo-600' : 'text-gray-400'}`} />}
                    <span className={`text-sm font-semibold ${
                      assetMode === mode
                        ? mode === 'inherit' ? 'text-blue-800' : 'text-indigo-800'
                        : 'text-gray-700'
                    }`}>
                      {mode === 'inherit' ? 'Inherit' : 'Restrict'}
                    </span>
                    {assetMode === mode && (
                      <span className={`ml-auto w-4 h-4 rounded-full flex items-center justify-center shrink-0 ${
                        mode === 'inherit' ? 'bg-blue-600' : 'bg-indigo-600'
                      }`}>
                        <Check className="w-2.5 h-2.5 text-white" />
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-500 leading-relaxed">
                    {mode === 'inherit'
                      ? 'Use all assets available to the org (default).'
                      : 'Pick a specific subset from the org\'s allowed assets.'}
                  </p>
                </button>
              ))}
            </div>

            {assetMode === 'inherit' && (
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-blue-50 border border-blue-200 text-xs text-blue-700">
                <Shield className="w-3.5 h-3.5 shrink-0" />
                <span>This team will automatically have access to all assets visible to the selected organization.</span>
              </div>
            )}

            {assetMode === 'restrict' && !selectedOrgId && (
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-700">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                <span>Select an organization first to browse its available assets.</span>
              </div>
            )}

            {assetMode === 'restrict' && selectedOrgId && (
              <AssetAccessEditor
                title="Allowed Assets"
                description="Choose which of the organization's assets this team can access."
                mode="restrict"
                targets={assetTargets}
                selectedKeys={selectedKeys}
                onSelectedKeysChange={setSelectedKeys}
                loading={orgAssetVisibilityLoading}
                disabled={saving}
                secondaryActionLabel={selectedKeys.length > 0 ? 'Clear selection' : undefined}
                onSecondaryAction={selectedKeys.length > 0 ? () => setSelectedKeys([]) : undefined}
              />
            )}
          </div>

          <div className="border-t border-gray-200" />

          {/* ── Advanced ── */}
          <div>
            <SectionHeading>Advanced</SectionHeading>
            <div className="p-3 rounded-lg bg-gray-50 border border-gray-200">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-start gap-2.5">
                  <AlertOctagon className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-medium text-gray-800">Block team</p>
                    <p className="text-xs text-gray-500 mt-0.5">
                      Immediately block all requests from this team's API keys.
                    </p>
                  </div>
                </div>
                <ToggleSwitch
                  checked={blocked}
                  onCheckedChange={setBlocked}
                  activeColor="#ef4444"
                  disabled={saving}
                  aria-label="Toggle blocked state"
                />
              </div>
              {blocked && (
                <div className="mt-3 flex items-center gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  <span>The team will be created in a blocked state. All API requests will be rejected until you unblock it.</span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-gray-200 px-6 py-4 bg-white shrink-0">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs">
              {!isReady ? (
                <span className="flex items-center gap-1 text-amber-600">
                  <AlertCircle className="w-3 h-3" />
                  {!selectedOrgId ? 'Select an organization to continue' : 'Fill in required fields to continue'}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-green-600">
                  <Check className="w-3 h-3" /> Ready to create
                </span>
              )}
            </p>
            <div className="flex gap-2">
              <button
                onClick={handleCancel}
                disabled={saving}
                className="px-3 py-1.5 text-xs font-medium text-gray-700 border border-gray-300 bg-white rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={saving || !isReady}
                className="px-3 py-1.5 text-xs font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving ? 'Creating…' : 'Create Team'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
