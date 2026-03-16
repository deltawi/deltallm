import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { callableTargets, organizations } from '../lib/api';
import { buildCatalogAssetTargets } from '../lib/assetAccess';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { Plus, Pencil, Building2, Users } from 'lucide-react';

function BudgetBar({ spend, max_budget }: { spend: number; max_budget: number | null }) {
  if (!max_budget) return <span className="text-gray-400 text-xs">No limit</span>;
  const pct = Math.min(100, (spend / max_budget) * 100);
  return (
    <div className="w-24">
      <div className="flex justify-between text-xs mb-0.5">
        <span>${spend.toFixed(2)}</span>
        <span className="text-gray-400">${max_budget}</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-blue-500'}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function RateLimit({ value, unit }: { value: number | null | undefined; unit: string }) {
  if (value == null) return <span className="text-gray-400 text-xs">No limit</span>;
  return <span className="text-xs font-medium">{Number(value).toLocaleString()} {unit}</span>;
}

export default function Organizations() {
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = userRole === 'platform_admin';
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 10;
  const { data: result, loading, refetch } = useApi(() => organizations.list({ search, limit: pageSize, offset: pageOffset }), [search, pageOffset]);
  const items = result?.data || [];
  const pagination = result?.pagination;
  const [assetSearchInput, setAssetSearchInput] = useState('');
  const [assetSearch, setAssetSearch] = useState('');
  const [assetPageOffset, setAssetPageOffset] = useState(0);
  const [assetTargetType, setAssetTargetType] = useState<'all' | 'model' | 'route_group'>('all');
  const assetPageSize = 50;

  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPageOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);
  useEffect(() => {
    const t = setTimeout(() => { setAssetSearch(assetSearchInput); setAssetPageOffset(0); }, 250);
    return () => clearTimeout(t);
  }, [assetSearchInput]);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState({
    organization_name: '',
    max_budget: '',
    rpm_limit: '',
    tpm_limit: '',
    audit_content_storage_enabled: false,
    select_all_current_assets: false,
    selected_callable_keys: [] as string[],
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const { data: editAssetAccess, loading: editAssetAccessLoading } = useApi(
    () => (editItem ? organizations.assetAccess(editItem.organization_id, { include_targets: false }) : Promise.resolve(null)),
    [editItem?.organization_id],
  );
  const { data: callableTargetPage, loading: callableTargetPageLoading } = useApi(
    () => (
      isPlatformAdmin && (showCreate || !!editItem) && !form.select_all_current_assets
        ? callableTargets.list({
            search: assetSearch || undefined,
            target_type: assetTargetType === 'all' ? undefined : assetTargetType,
            limit: assetPageSize,
            offset: assetPageOffset,
          })
        : Promise.resolve({
            data: [],
            pagination: { total: 0, limit: assetPageSize, offset: 0, has_more: false },
          })
    ),
    [isPlatformAdmin, showCreate, editItem?.organization_id, form.select_all_current_assets, assetSearch, assetTargetType, assetPageOffset],
  );

  const resetForm = () => {
    setForm({
      organization_name: '',
      max_budget: '',
      rpm_limit: '',
      tpm_limit: '',
      audit_content_storage_enabled: false,
      select_all_current_assets: false,
      selected_callable_keys: [],
    });
    setError(null);
    setAssetSearchInput('');
    setAssetSearch('');
    setAssetPageOffset(0);
    setAssetTargetType('all');
  };

  useEffect(() => {
    if (!editItem || !editAssetAccess) return;
    setForm((current) => ({
      ...current,
      select_all_current_assets:
        editAssetAccess.summary.selectable_total > 0 &&
        editAssetAccess.summary.selected_total === editAssetAccess.summary.selectable_total,
      selected_callable_keys: editAssetAccess.selected_callable_keys || [],
    }));
  }, [editItem, editAssetAccess]);

  const handleSave = async () => {
    setError(null);
    setSaving(true);
    try {
      const payload = {
        organization_name: form.organization_name || undefined,
        max_budget: form.max_budget ? Number(form.max_budget) : undefined,
        rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
        tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
        audit_content_storage_enabled: !!form.audit_content_storage_enabled,
      };
      if (editItem) {
        await organizations.update(editItem.organization_id, payload);
        if (isPlatformAdmin) {
          await organizations.updateAssetAccess(editItem.organization_id, {
            selected_callable_keys: form.select_all_current_assets ? [] : form.selected_callable_keys,
            select_all_selectable: form.select_all_current_assets,
          });
        }
        setPageError(null);
      } else {
        const created = await organizations.create({
          ...payload,
          callable_target_bindings: form.select_all_current_assets
            ? []
            : form.selected_callable_keys.map((callable_key) => ({ callable_key })),
        });
        if (isPlatformAdmin && form.select_all_current_assets) {
          let assetAccessError: string | null = null;
          try {
            await organizations.updateAssetAccess(created.organization_id, {
              selected_callable_keys: [],
              select_all_selectable: true,
            });
          } catch (err: any) {
            assetAccessError = err?.message || 'Organization created, but asset access could not be updated. Open the organization again to finish access setup.';
          }
          setPageError(assetAccessError);
        } else {
          setPageError(null);
        }
      }
      setShowCreate(false);
      setEditItem(null);
      resetForm();
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to save organization');
    } finally {
      setSaving(false);
    }
  };

  const openEdit = (row: any) => {
    setPageError(null);
    setForm({
      organization_name: row.organization_name || '',
      max_budget: row.max_budget != null ? String(row.max_budget) : '',
      rpm_limit: row.rpm_limit != null ? String(row.rpm_limit) : '',
      tpm_limit: row.tpm_limit != null ? String(row.tpm_limit) : '',
      audit_content_storage_enabled: !!row.audit_content_storage_enabled,
      select_all_current_assets: false,
      selected_callable_keys: [],
    });
    setAssetSearchInput('');
    setAssetSearch('');
    setAssetPageOffset(0);
    setAssetTargetType('all');
    setEditItem(row);
  };

  const assetTargets = buildCatalogAssetTargets((callableTargetPage?.data || []) as any[], form.selected_callable_keys);
  const assetPagePagination = callableTargetPage?.pagination;

  const columns = [
    { key: 'organization_name', header: 'Name', render: (r: any) => (
      <div className="flex items-center gap-2">
        <Building2 className="w-4 h-4 text-blue-500" />
        <span className="font-medium">{r.organization_name || r.organization_id}</span>
      </div>
    ) },
    { key: 'organization_id', header: 'Org ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.organization_id}</code> },
    { key: 'team_count', header: 'Teams', render: (r: any) => <span className="flex items-center gap-1"><Users className="w-3.5 h-3.5 text-gray-400" /> {r.team_count || 0}</span> },
    { key: 'budget', header: 'Budget', render: (r: any) => <BudgetBar spend={r.spend || 0} max_budget={r.max_budget} /> },
    { key: 'rpm_limit', header: 'RPM', render: (r: any) => <RateLimit value={r.rpm_limit} unit="req/min" /> },
    { key: 'tpm_limit', header: 'TPM', render: (r: any) => <RateLimit value={r.tpm_limit} unit="tok/min" /> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <button onClick={(e) => { e.stopPropagation(); openEdit(r); }} className="p-1.5 hover:bg-gray-100 rounded-lg"><Pencil className="w-4 h-4 text-gray-500" /></button>
      ),
    },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Organizations</h1>
          <p className="text-sm text-gray-500 mt-1">Manage organizations, budgets, and runtime asset ceilings</p>
        </div>
        {isPlatformAdmin && (
          <button onClick={() => { setPageError(null); resetForm(); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
            <Plus className="w-4 h-4" /> Create Organization
          </button>
        )}
      </div>
      {pageError && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{pageError}</div>
      )}
      <Card>
        <div className="px-4 pt-3 pb-2">
          <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="Search organizations..." className="w-full sm:w-72 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </div>
        <DataTable columns={columns} data={items} loading={loading} emptyMessage="No organizations created yet" onRowClick={(r) => navigate(`/organizations/${r.organization_id}`)} pagination={pagination} onPageChange={setPageOffset} />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={() => { setPageError(null); setShowCreate(false); setEditItem(null); resetForm(); }} title={editItem ? 'Edit Organization' : 'Create Organization'}>
        <div className="space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Organization Name</label>
            <input value={form.organization_name} onChange={(e) => setForm({ ...form, organization_name: e.target.value })} placeholder="Acme Corp" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
            <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} placeholder="1000" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={form.rpm_limit} onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })} placeholder="100" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              <p className="text-xs text-gray-400 mt-1">Requests per minute</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={form.tpm_limit} onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })} placeholder="100000" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              <p className="text-xs text-gray-400 mt-1">Tokens per minute</p>
            </div>
          </div>
          <label className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg bg-gray-50">
            <input
              type="checkbox"
              checked={!!form.audit_content_storage_enabled}
              onChange={(e) => setForm({ ...form, audit_content_storage_enabled: e.target.checked })}
              className="mt-0.5"
            />
            <span className="text-sm text-gray-700">
              Store request and response payload content in audit logs for this organization.
            </span>
          </label>
          {isPlatformAdmin && (
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <label className={`rounded-lg border px-3 py-2 text-sm ${form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                  <div className="flex items-start gap-2">
                    <input
                      type="radio"
                      name="organization-asset-strategy"
                      checked={form.select_all_current_assets}
                      onChange={() => setForm((current) => ({ ...current, select_all_current_assets: true, selected_callable_keys: [] }))}
                      disabled={saving}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block font-medium text-gray-900">Allow all current assets</span>
                      <span className="block text-xs text-gray-500">Grant every model and route group that exists right now, without loading the full catalog in the browser.</span>
                    </span>
                  </div>
                </label>
                <label className={`rounded-lg border px-3 py-2 text-sm ${!form.select_all_current_assets ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white'}`}>
                  <div className="flex items-start gap-2">
                    <input
                      type="radio"
                      name="organization-asset-strategy"
                      checked={!form.select_all_current_assets}
                      onChange={() => setForm((current) => ({ ...current, select_all_current_assets: false }))}
                      disabled={saving}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block font-medium text-gray-900">Choose a subset</span>
                      <span className="block text-xs text-gray-500">Manually pick the models and route groups this organization can use.</span>
                    </span>
                  </div>
                </label>
              </div>
              {form.select_all_current_assets ? (
                <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-3 text-xs text-blue-800">
                  {editItem && editAssetAccess
                    ? `This organization currently has ${editAssetAccess.summary.selected_total} of ${editAssetAccess.summary.selectable_total} assets granted. Saving with this option will align it to all current assets in the callable catalog.`
                    : 'Saving will grant every currently available model and route group to this organization.'}
                </div>
              ) : (
                <AssetAccessEditor
                  title="Allowed Assets"
                  description="Choose the models and route groups this organization is allowed to use. Teams, keys, and users can only narrow from this set."
                  mode="grant"
                  targets={assetTargets}
                  selectedKeys={form.selected_callable_keys}
                  onSelectedKeysChange={(selected_callable_keys) => setForm({ ...form, selected_callable_keys })}
                  loading={callableTargetPageLoading || (!!editItem && editAssetAccessLoading)}
                  disabled={saving}
                  searchValue={assetSearchInput}
                  onSearchValueChange={setAssetSearchInput}
                  targetTypeFilter={assetTargetType}
                  onTargetTypeFilterChange={(next) => {
                    setAssetTargetType(next);
                    setAssetPageOffset(0);
                  }}
                  pagination={assetPagePagination}
                  onPageChange={setAssetPageOffset}
                  primaryActionLabel="Allow all current assets"
                  onPrimaryAction={() => setForm((current) => ({ ...current, select_all_current_assets: true, selected_callable_keys: [] }))}
                  secondaryActionLabel={form.selected_callable_keys.length > 0 ? 'Clear selection' : undefined}
                  onSecondaryAction={form.selected_callable_keys.length > 0 ? () => setForm((current) => ({ ...current, selected_callable_keys: [] })) : undefined}
                />
              )}
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => { setPageError(null); setShowCreate(false); setEditItem(null); resetForm(); }} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSave} disabled={saving || (!!editItem && editAssetAccessLoading)} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : editItem ? 'Save Changes' : 'Create'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
