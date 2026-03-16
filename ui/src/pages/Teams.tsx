import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { teams, organizations } from '../lib/api';
import { buildParentScopedAssetTargets, buildScopedSelectableTargets } from '../lib/assetAccess';
import Modal from '../components/Modal';
import UserSearchSelect from '../components/UserSearchSelect';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import {
  Plus, Users, Trash2, UserPlus, Pencil, Search, ChevronRight,
  Building2, Shield, AlertOctagon, Gauge, MoreHorizontal, Filter, X,
} from 'lucide-react';

/* ─────────────── small visual helpers ─────────────── */

function MemberDots({ count }: { count: number }) {
  const colors = ['bg-blue-400', 'bg-violet-400', 'bg-emerald-400', 'bg-amber-400', 'bg-pink-400'];
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

function MiniBar({ spend, budget }: { spend: number; budget: number | null }) {
  if (!budget) return <span className="text-xs text-gray-400">Unlimited</span>;
  const pct = Math.min(100, (spend / budget) * 100);
  const color = pct > 90 ? 'bg-red-500' : pct > 75 ? 'bg-amber-500' : 'bg-blue-500';
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

type StatusFilter = 'all' | 'active' | 'blocked' | 'over_budget';

const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'over_budget', label: 'Over budget' },
];

/* ─────────────── page ─────────────── */

export default function Teams() {
  const navigate = useNavigate();

  /* search / pagination */
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 20;

  /* filters */
  const [orgFilter, setOrgFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  /* data */
  const { data: result, loading, refetch } = useApi(
    () => teams.list({ search, limit: pageSize, offset: pageOffset, organization_id: orgFilter || undefined }),
    [search, pageOffset, orgFilter],
  );
  const items: any[] = result?.data || [];
  const pagination = result?.pagination;

  const { data: orgResult } = useApi(() => organizations.list({ limit: 500 }), []);
  const orgList: any[] = orgResult?.data || [];

  /* org name lookup */
  const orgNameMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const o of orgList) m[o.organization_id] = o.organization_name || o.organization_id;
    return m;
  }, [orgList]);

  /* debounced search */
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  /* client-side status filter */
  const filteredItems = useMemo(() => {
    if (statusFilter === 'blocked') return items.filter((t) => t.blocked);
    if (statusFilter === 'over_budget') return items.filter((t) => t.max_budget && (t.spend || 0) > t.max_budget);
    if (statusFilter === 'active') return items.filter((t) => !t.blocked);
    return items;
  }, [items, statusFilter]);

  /* summary stats */
  const totalMembers = items.reduce((s, t) => s + (t.member_count || 0), 0);
  const blockedCount = items.filter((t) => t.blocked).length;
  const inheritCount = items.filter((t) => !t.blocked).length; // placeholder until API returns mode

  /* ── create / edit modal ── */
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState({
    team_alias: '',
    organization_id: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    asset_access_mode: 'inherit' as 'inherit' | 'restrict',
    selected_callable_keys: [] as string[],
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);

  const resetForm = () => setForm({
    team_alias: '', organization_id: '', max_budget: '', rpm_limit: '', tpm_limit: '',
    asset_access_mode: 'inherit', selected_callable_keys: [],
  });

  /* asset access helpers */
  const selectedOrganizationId = form.organization_id.trim();
  const usesParentPreview = !editItem || selectedOrganizationId !== (editItem.organization_id || '');

  const { data: editAssetAccess } = useApi(
    () => (editItem ? teams.assetAccess(editItem.team_id, { include_targets: false }) : Promise.resolve(null)),
    [editItem?.team_id],
  );
  const { data: editAssetAccessTargets, loading: editAssetAccessTargetsLoading } = useApi(
    () => (editItem && !usesParentPreview && form.asset_access_mode === 'restrict'
      ? teams.assetAccess(editItem.team_id, { include_targets: true })
      : Promise.resolve(null)),
    [editItem?.team_id, usesParentPreview, form.asset_access_mode],
  );
  const { data: parentOrgAssetVisibility, loading: parentOrgAssetVisibilityLoading } = useApi(
    () => ((showCreate || !!editItem) && usesParentPreview && form.asset_access_mode === 'restrict' && selectedOrganizationId
      ? organizations.assetVisibility(selectedOrganizationId)
      : Promise.resolve(null)),
    [showCreate, editItem?.team_id, selectedOrganizationId, usesParentPreview, form.asset_access_mode],
  );

  useEffect(() => {
    if (!editItem || !editAssetAccess) return;
    setForm((c) => ({
      ...c,
      asset_access_mode: editAssetAccess.mode === 'restrict' ? 'restrict' : 'inherit',
      selected_callable_keys: editAssetAccess.selected_callable_keys || [],
    }));
  }, [editItem, editAssetAccess]);

  const handleOrganizationChange = (organizationId: string) => {
    setForm((c) => {
      const changed = c.organization_id !== organizationId;
      return { ...c, organization_id: organizationId, asset_access_mode: changed ? 'inherit' : c.asset_access_mode, selected_callable_keys: changed ? [] : c.selected_callable_keys };
    });
  };

  const assetTargets = usesParentPreview
    ? buildParentScopedAssetTargets(parentOrgAssetVisibility?.callable_targets?.items || [], form.selected_callable_keys, form.asset_access_mode)
    : buildScopedSelectableTargets(editAssetAccessTargets?.selectable_targets || [], form.selected_callable_keys, form.asset_access_mode);
  const assetAccessLoading = form.asset_access_mode !== 'restrict' ? false
    : usesParentPreview ? parentOrgAssetVisibilityLoading
    : editAssetAccessTargetsLoading;

  const handleSave = async () => {
    setError(null);
    setSaving(true);
    try {
      const payload = {
        team_alias: form.team_alias || undefined,
        organization_id: form.organization_id || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
      };
      if (editItem) {
        await teams.update(editItem.team_id, payload);
        await teams.updateAssetAccess(editItem.team_id, {
          mode: form.asset_access_mode,
          selected_callable_keys: form.asset_access_mode === 'restrict' ? form.selected_callable_keys : [],
        });
        setPageError(null);
      } else {
        const created = await teams.create(payload);
        let assetAccessError: string | null = null;
        if (form.asset_access_mode === 'restrict') {
          try {
            await teams.updateAssetAccess(created.team_id, { mode: 'restrict', selected_callable_keys: form.selected_callable_keys });
          } catch (err: any) {
            assetAccessError = err?.message || 'Team created, but asset access could not be updated.';
          }
        }
        setPageError(assetAccessError);
      }
      setShowCreate(false);
      setEditItem(null);
      resetForm();
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to save team');
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (row: any) => {
    setPageError(null);
    setForm({
      team_alias: row.team_alias || '',
      organization_id: row.organization_id || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      asset_access_mode: 'inherit',
      selected_callable_keys: [],
    });
    setEditItem(row);
  };

  const handleDelete = async (row: any) => {
    if (!confirm(`Delete team "${row.team_alias || row.team_id}"? All members will be unassigned.`)) return;
    try {
      await teams.delete(row.team_id);
      refetch();
    } catch (err: any) {
      alert(err?.message || 'Failed to delete team');
    }
  };

  /* ── members modal ── */
  const [selectedTeam, setSelectedTeam] = useState<any>(null);
  const [memberSearch, setMemberSearch] = useState('');
  const [memberForm, setMemberForm] = useState({ user_id: '', user_email: '', user_role: 'team_viewer' });

  const { data: members, refetch: refetchMembers } = useApi(
    () => selectedTeam ? teams.members(selectedTeam.team_id) : Promise.resolve([]),
    [selectedTeam?.team_id],
  );
  const { data: memberCandidates, loading: memberCandidatesLoading } = useApi(
    () => selectedTeam ? teams.memberCandidates(selectedTeam.team_id, { search: memberSearch, limit: 50 }) : Promise.resolve([]),
    [selectedTeam?.team_id, memberSearch],
  );

  const handleAddMember = async () => {
    if (!selectedTeam) return;
    await teams.addMember(selectedTeam.team_id, {
      user_id: memberForm.user_id,
      user_email: memberForm.user_email || undefined,
      user_role: memberForm.user_role,
    });
    setMemberForm({ user_id: '', user_email: '', user_role: 'team_viewer' });
    refetchMembers();
  };

  const handleRemoveMember = async (userId: string) => {
    if (!selectedTeam || !confirm('Remove this member from the team?')) return;
    await teams.removeMember(selectedTeam.team_id, userId);
    refetchMembers();
  };

  /* ── render ── */
  return (
    <div className="min-h-screen bg-gray-50">
      {/* ── Header ── */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-1.5 text-xs text-gray-400 mb-1">
              <span>Platform</span>
              <ChevronRight className="w-3 h-3" />
              <span className="text-gray-600 font-medium">Teams</span>
            </div>
            <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
              <Users className="w-5 h-5 text-indigo-600" />
              Teams
              {pagination && (
                <span className="ml-1 inline-flex items-center justify-center min-w-[1.5rem] h-6 px-2 rounded-full bg-gray-100 text-xs font-semibold text-gray-600">
                  {pagination.total}
                </span>
              )}
            </h1>
          </div>
          <button
            onClick={() => { resetForm(); setShowCreate(true); }}
            className="flex items-center gap-1.5 bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors"
          >
            <Plus className="w-4 h-4" /> Create Team
          </button>
        </div>

        {/* Search + filters row */}
        <div className="flex items-center gap-3 mt-4 flex-wrap">
          <div className="relative w-64">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search teams…"
              className="w-full pl-8 pr-3 h-8 text-xs border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* Org filter */}
          <div className="relative flex items-center gap-1.5">
            <Building2 className="absolute left-2.5 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
            <select
              value={orgFilter}
              onChange={(e) => { setOrgFilter(e.target.value); setPageOffset(0); }}
              className="appearance-none pl-8 pr-7 h-8 text-xs border border-gray-300 rounded-lg bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">All organizations</option>
              {orgList.map((o: any) => (
                <option key={o.organization_id} value={o.organization_id}>
                  {o.organization_name || o.organization_id}
                </option>
              ))}
            </select>
          </div>

          {orgFilter && (
            <button
              onClick={() => { setOrgFilter(''); setPageOffset(0); }}
              className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-800"
            >
              <X className="w-3.5 h-3.5" /> Clear
            </button>
          )}

          {/* Status tabs */}
          <div className="flex gap-1 ml-auto">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setStatusFilter(tab.key)}
                className={`px-3 py-1 text-xs rounded-full transition-colors ${
                  statusFilter === tab.key
                    ? 'bg-indigo-50 text-indigo-700 font-medium'
                    : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Stats strip ── */}
      <div className="bg-white border-b border-gray-100 px-6 py-3 flex gap-8">
        {[
          { label: 'Total teams', value: String(pagination?.total ?? items.length) },
          { label: 'Total members', value: String(totalMembers) },
          { label: 'Blocked', value: String(blockedCount) },
        ].map((s) => (
          <div key={s.label} className="flex items-center gap-2">
            <span className="text-xs text-gray-500">{s.label}</span>
            <span className="text-xs font-semibold text-gray-900">{s.value}</span>
          </div>
        ))}
      </div>

      {/* ── Page-level error ── */}
      {pageError && (
        <div className="mx-6 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {pageError}
        </div>
      )}

      {/* ── Table ── */}
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
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <tr key={i} className="border-b border-gray-100">
                    {Array.from({ length: 6 }).map((_, j) => (
                      <td key={j} className="px-4 py-3.5">
                        <div className="h-4 bg-gray-100 rounded animate-pulse w-24" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : filteredItems.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-sm text-gray-400">
                    {statusFilter !== 'all'
                      ? `No ${statusFilter.replace('_', ' ')} teams`
                      : search
                      ? 'No teams match your search'
                      : 'No teams yet — create your first team to get started.'}
                  </td>
                </tr>
              ) : (
                filteredItems.map((t: any, i: number) => (
                  <tr
                    key={t.team_id}
                    onClick={() => navigate(`/teams/${t.team_id}`)}
                    className={`border-b border-gray-100 hover:bg-indigo-50/30 cursor-pointer transition-colors ${
                      i === filteredItems.length - 1 ? 'border-b-0' : ''
                    } ${t.blocked ? 'opacity-60' : ''}`}
                  >
                    {/* Team name */}
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${t.blocked ? 'bg-red-50' : 'bg-indigo-50'}`}>
                          <Users className={`w-4 h-4 ${t.blocked ? 'text-red-500' : 'text-indigo-600'}`} />
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-gray-900 text-sm">{t.team_alias || t.team_id}</span>
                            {t.blocked && (
                              <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-red-100 text-red-700">
                                <AlertOctagon className="w-3 h-3" /> Blocked
                              </span>
                            )}
                          </div>
                          <code className="text-[10px] text-gray-400 font-mono">{t.team_id}</code>
                        </div>
                      </div>
                    </td>

                    {/* Org */}
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-1.5">
                        <Building2 className="w-3 h-3 text-gray-400 shrink-0" />
                        <span
                          onClick={(e) => { e.stopPropagation(); if (t.organization_id) navigate(`/organizations/${t.organization_id}`); }}
                          className="text-xs text-indigo-600 hover:underline cursor-pointer font-medium"
                        >
                          {orgNameMap[t.organization_id] || t.organization_id || '—'}
                        </span>
                      </div>
                    </td>

                    {/* Members */}
                    <td className="px-4 py-3.5">
                      <MemberDots count={t.member_count || 0} />
                    </td>

                    {/* Budget */}
                    <td className="px-4 py-3.5">
                      <MiniBar spend={t.spend || 0} budget={t.max_budget} />
                    </td>

                    {/* Rate limits */}
                    <td className="px-4 py-3.5">
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-1 text-[10px] text-gray-500">
                          <Gauge className="w-3 h-3 shrink-0" />
                          {t.rpm_limit != null
                            ? <><span className="font-medium text-gray-700">{Number(t.rpm_limit).toLocaleString()}</span><span className="text-gray-400">RPM</span></>
                            : <span className="text-gray-400">—</span>}
                        </div>
                        {t.tpm_limit != null && (
                          <div className="flex items-center gap-1 text-[10px] text-gray-400">
                            <span className="w-3" />
                            <span className="font-medium">{Number(t.tpm_limit).toLocaleString()}</span>
                            <span>TPM</span>
                          </div>
                        )}
                      </div>
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                        <button
                          onClick={() => openEdit(t)}
                          className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600 transition-colors"
                          title="Edit"
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => { setSelectedTeam(t); setMemberSearch(''); setMemberForm({ user_id: '', user_email: '', user_role: 'team_viewer' }); }}
                          className="p-1.5 hover:bg-gray-100 rounded-lg text-gray-400 hover:text-gray-600 transition-colors"
                          title="Manage members"
                        >
                          <Users className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDelete(t)}
                          className="p-1.5 hover:bg-red-50 rounded-lg text-gray-400 hover:text-red-500 transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>

          {/* Pagination footer */}
          <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-t border-gray-200">
            <span className="text-xs text-gray-500">
              {loading
                ? 'Loading…'
                : `Showing ${filteredItems.length} of ${pagination?.total ?? filteredItems.length} teams`}
            </span>
            {pagination && pagination.total > pageSize && (
              <div className="flex gap-1">
                <button
                  onClick={() => setPageOffset(Math.max(0, pageOffset - pageSize))}
                  disabled={pageOffset === 0}
                  className="px-3 py-1 text-xs text-gray-500 border border-gray-200 rounded-md disabled:opacity-40"
                >
                  Previous
                </button>
                {Array.from({ length: Math.ceil(pagination.total / pageSize) }).map((_, i) => {
                  const active = i * pageSize === pageOffset;
                  return (
                    <button
                      key={i}
                      onClick={() => setPageOffset(i * pageSize)}
                      className={`px-3 py-1 text-xs border rounded-md ${active ? 'text-indigo-600 border-indigo-200 bg-indigo-50 font-medium' : 'text-gray-500 border-gray-200'}`}
                    >
                      {i + 1}
                    </button>
                  );
                })}
                <button
                  onClick={() => setPageOffset(pageOffset + pageSize)}
                  disabled={!pagination.has_more}
                  className="px-3 py-1 text-xs text-gray-500 border border-gray-200 rounded-md disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Create / Edit Modal ── */}
      <Modal
        open={showCreate || !!editItem}
        onClose={() => { setShowCreate(false); setEditItem(null); resetForm(); setError(null); }}
        title={editItem ? 'Edit Team' : 'Create Team'}
      >
        <div className="space-y-4">
          {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team Name</label>
            <input
              value={form.team_alias}
              onChange={(e) => setForm({ ...form, team_alias: e.target.value })}
              placeholder="Engineering"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Organization <span className="text-red-500">*</span>
              </label>
              <select
                value={form.organization_id}
                onChange={(e) => handleOrganizationChange(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
              >
                <option value="">Select an organization</option>
                {orgList.map((o: any) => (
                  <option key={o.organization_id} value={o.organization_id}>
                    {o.organization_name || o.organization_id}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input
                type="number"
                value={form.max_budget}
                onChange={(e) => setForm({ ...form, max_budget: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input
                type="number"
                value={form.rpm_limit}
                onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })}
                placeholder="100"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <p className="text-xs text-gray-400 mt-1">Requests per minute</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input
                type="number"
                value={form.tpm_limit}
                onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })}
                placeholder="100000"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
              <p className="text-xs text-gray-400 mt-1">Tokens per minute</p>
            </div>
          </div>
          <p className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
            Team runtime access is enforced through callable-target bindings and scope policies. Use the section below to inherit the organization set or narrow it for this team.
          </p>
          <AssetAccessEditor
            title="Team Asset Access"
            description="Choose whether this team inherits the organization asset ceiling or narrows itself to a selected subset."
            mode={form.asset_access_mode}
            allowModeSelection
            onModeChange={(mode) => setForm((c) => ({
              ...c,
              asset_access_mode: mode === 'restrict' ? 'restrict' : 'inherit',
              selected_callable_keys: mode === 'restrict' ? c.selected_callable_keys : [],
            }))}
            targets={assetTargets}
            selectedKeys={form.selected_callable_keys}
            onSelectedKeysChange={(selected_callable_keys) => setForm({ ...form, selected_callable_keys })}
            loading={assetAccessLoading}
            disabled={saving || !form.organization_id}
          />
          <div className="flex justify-end gap-3 pt-2">
            <button
              onClick={() => { setShowCreate(false); setEditItem(null); resetForm(); setError(null); }}
              className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!form.organization_id || saving || assetAccessLoading}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? 'Saving…' : editItem ? 'Save Changes' : 'Create Team'}
            </button>
          </div>
        </div>
      </Modal>

      {/* ── Members Modal ── */}
      <Modal
        open={!!selectedTeam}
        onClose={() => setSelectedTeam(null)}
        title={`Team: ${selectedTeam?.team_alias || selectedTeam?.team_id || ''}`}
        wide
      >
        <div>
          <div className="mb-4 space-y-3">
            <UserSearchSelect
              search={memberSearch}
              onSearchChange={setMemberSearch}
              options={(memberCandidates || []) as any[]}
              loading={memberCandidatesLoading}
              selectedAccountId={memberForm.user_id}
              onSelect={(a: any) => setMemberForm({ ...memberForm, user_id: a.account_id, user_email: a.email || '' })}
              searchPlaceholder="Search by email or account ID"
              helperText="Results include users that already belong to this team's organization."
              emptyText="No organization members match your search."
            />
            <div className="flex flex-col sm:flex-row gap-2">
              <select
                value={memberForm.user_role}
                onChange={(e) => setMemberForm({ ...memberForm, user_role: e.target.value })}
                className="sm:w-56 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="team_viewer">Viewer</option>
                <option value="team_developer">Developer</option>
                <option value="team_admin">Admin</option>
              </select>
              <button
                onClick={handleAddMember}
                disabled={!memberForm.user_id.trim()}
                className="flex items-center justify-center gap-1 px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <UserPlus className="w-4 h-4" /> Add
              </button>
            </div>
          </div>
          <p className="text-xs text-gray-400 mb-4">Authorization and scope are managed via RBAC memberships.</p>
          <div className="rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-gray-500">User</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-gray-500">Role</th>
                  <th className="px-4 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {!members || (members as any[]).length === 0 ? (
                  <tr><td colSpan={3} className="px-4 py-8 text-center text-sm text-gray-400">No members in this team</td></tr>
                ) : (
                  (members as any[]).map((m: any) => (
                    <tr key={m.user_id} className="border-b border-gray-100 last:border-b-0">
                      <td className="px-4 py-3">
                        <div className="font-medium text-sm text-gray-900">{m.user_id}</div>
                        {m.user_email && <div className="text-xs text-gray-400">{m.user_email}</div>}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">{m.user_role}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => handleRemoveMember(m.user_id)}
                          className="p-1.5 hover:bg-red-50 rounded-lg text-gray-400 hover:text-red-500 transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </Modal>
    </div>
  );
}
