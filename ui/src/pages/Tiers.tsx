import { BadgeDollarSign, ChevronRight, Plus, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import StatusBadge from '../components/StatusBadge';
import IndexShell from '../components/admin/shells/IndexShell';
import TierCapacityDashboardPanel from '../components/tiers/TierCapacityDashboardPanel';
import TierFormDrawer from '../components/tiers/TierFormDrawer';
import { useToast } from '../components/ToastProvider';
import { useApi } from '../lib/hooks';
import { tierCapacity, tiers, type Tier } from '../lib/api';
import { errorMessage, formatDateTime, tierToForm } from '../lib/tiers';

export default function Tiers() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [enabled, setEnabled] = useState<'all' | 'enabled' | 'disabled'>('all');
  const [pageOffset, setPageOffset] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [form, setForm] = useState(tierToForm());
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const createRequestRef = useRef(0);
  const pageSize = 25;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPageOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const enabledParam = enabled === 'all' ? undefined : enabled === 'enabled';
  const { data: result, loading, error, refetch } = useApi(
    () => tiers.list({ search, enabled: enabledParam, limit: pageSize, offset: pageOffset }),
    [search, enabledParam, pageOffset],
  );
  const {
    data: capacityDashboard,
    loading: capacityLoading,
    error: capacityError,
    refetch: refetchCapacity,
  } = useApi(
    () => tierCapacity.dashboard({ top_org_limit: 8, pool_limit: 50 }),
    [],
  );

  const tierRows = useMemo(() => result?.data || [], [result]);
  const pagination = result?.pagination;
  const summary = useMemo(() => {
    const rows = tierRows;
    return {
      enabled: rows.filter((tier) => tier.enabled).length,
      versions: rows.reduce((total, tier) => total + Number(tier.version_count || 0), 0),
      assignments: rows.reduce((total, tier) => total + Number(tier.assignment_count || 0), 0),
    };
  }, [tierRows]);

  const openCreate = () => {
    if (saving) return;
    createRequestRef.current += 1;
    setForm(tierToForm());
    setFormError(null);
    setDrawerOpen(true);
  };

  const closeCreate = () => {
    if (saving) return;
    createRequestRef.current += 1;
    setDrawerOpen(false);
  };

  const beginCreateRequest = (): number => {
    const requestId = createRequestRef.current + 1;
    createRequestRef.current = requestId;
    return requestId;
  };

  const isCurrentCreateRequest = (requestId: number): boolean => (
    createRequestRef.current === requestId
  );

  const handleEnabledFilterChange = (nextEnabled: typeof enabled) => {
    setEnabled(nextEnabled);
    setPageOffset(0);
  };

  const handleCreate = async () => {
    if (saving) return;
    const tierKey = form.tier_key.trim();
    const name = form.name.trim();
    if (!tierKey || !name) {
      setFormError('Tier key and display name are required.');
      return;
    }
    const requestId = beginCreateRequest();
    setSaving(true);
    setFormError(null);
    try {
      const created = await tiers.create({
        tier_key: tierKey,
        name,
        description: form.description.trim() || null,
        enabled: form.enabled,
      });
      if (!isCurrentCreateRequest(requestId)) return;
      setDrawerOpen(false);
      refetch();
      pushToast({ tone: 'success', title: 'Tier created', message: `${created.name} is ready for a draft version.` });
      navigate(`/tiers/${created.tier_id}`);
    } catch (err: unknown) {
      if (!isCurrentCreateRequest(requestId)) return;
      setFormError(errorMessage(err, 'Failed to create tier.'));
    } finally {
      if (isCurrentCreateRequest(requestId)) {
        setSaving(false);
      }
    }
  };

  return (
    <IndexShell
      title="Tiers"
      titleIcon={BadgeDollarSign}
      count={pagination?.total ?? null}
      description="Manage reusable model, pricing, and capacity packages for organizations."
      action={(
        <button
          type="button"
          onClick={openCreate}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
        >
          <Plus className="h-4 w-4" />
          Create tier
        </button>
      )}
      toolbar={(
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <div className="relative max-w-md flex-1">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search tiers"
              className="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
            />
          </div>
          <div className="inline-flex rounded-lg border border-gray-200 bg-white p-1">
            {[
              ['all', 'All'],
              ['enabled', 'Enabled'],
              ['disabled', 'Disabled'],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => handleEnabledFilterChange(value as typeof enabled)}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
                  enabled === value ? 'bg-brand-primary text-brand-on-primary' : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}
      summaryItems={[
        { label: 'Enabled on page', value: summary.enabled, icon: BadgeDollarSign, iconClassName: 'text-emerald-600' },
        { label: 'Versions on page', value: summary.versions, icon: BadgeDollarSign, iconClassName: 'text-brand-primary-ink' },
        { label: 'Assignments on page', value: summary.assignments, icon: BadgeDollarSign, iconClassName: 'text-brand-secondary-ink' },
      ]}
      notice={error ? (
        <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage(error, 'Failed to load tiers.')}
        </div>
      ) : undefined}
    >
      <TierCapacityDashboardPanel
        dashboard={capacityDashboard}
        loading={capacityLoading}
        error={capacityError}
        onRefresh={refetchCapacity}
      />

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left">Tier</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">Versions</th>
                <th className="px-4 py-3 text-left">Orgs</th>
                <th className="px-4 py-3 text-left">Updated</th>
                <th className="px-4 py-3 text-right">Open</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center">
                    <div className="mx-auto h-6 w-6 animate-spin rounded-full border-b-2 border-brand-primary" />
                  </td>
                </tr>
              ) : tierRows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-sm text-gray-400">No tiers found.</td>
                </tr>
              ) : tierRows.map((tier: Tier) => (
                <tr key={tier.tier_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link
                      to={`/tiers/${tier.tier_id}`}
                      className="block rounded focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
                    >
                      <p className="font-semibold text-gray-900">{tier.name}</p>
                      <p className="font-mono text-xs text-gray-400">{tier.tier_key}</p>
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={tier.enabled ? 'enabled' : 'disabled'} label={tier.enabled ? 'Enabled' : 'Disabled'} />
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{tier.version_count}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{tier.assignment_count}</td>
                  <td className="px-4 py-3 text-xs text-gray-500">{formatDateTime(tier.updated_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      to={`/tiers/${tier.tier_id}`}
                      aria-label={`Open ${tier.name}`}
                      className="inline-flex rounded p-1 text-gray-300 hover:bg-gray-100 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {pagination && pagination.total > 0 ? (
          <div className="flex items-center justify-between border-t border-gray-100 px-4 py-3 text-xs text-gray-500">
            <span>
              Showing {pagination.offset + 1} to {Math.min(pagination.offset + pagination.limit, pagination.total)} of {pagination.total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={pageOffset === 0}
                onClick={() => setPageOffset(Math.max(0, pageOffset - pageSize))}
                className="rounded border border-gray-200 px-3 py-1.5 font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={!pagination.has_more}
                onClick={() => setPageOffset(pageOffset + pageSize)}
                className="rounded border border-gray-200 px-3 py-1.5 font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <TierFormDrawer
        open={drawerOpen}
        title="Create Tier"
        values={form}
        saving={saving}
        error={formError}
        submitLabel="Create tier"
        onChange={setForm}
        onClose={closeCreate}
        onSubmit={handleCreate}
      />
    </IndexShell>
  );
}
