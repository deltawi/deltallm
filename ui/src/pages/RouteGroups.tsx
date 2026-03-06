import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Sparkles, Trash2 } from 'lucide-react';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import { routeGroups } from '../lib/api';
import { useApi } from '../lib/hooks';
import { ROUTE_GROUP_MODE_OPTIONS } from '../lib/routeGroups';
import { useToast } from '../components/ToastProvider';

export default function RouteGroups() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [form, setForm] = useState({
    group_key: '',
    name: '',
    mode: 'chat',
  });

  const pageSize = 10;
  const { data: result, loading, refetch } = useApi(
    () => routeGroups.list({ search, limit: pageSize, offset: pageOffset }),
    [search, pageOffset]
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPageOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const resetForm = () =>
    setForm({
      group_key: '',
      name: '',
      mode: 'chat',
    });

  const handleCreate = async () => {
    const groupKey = form.group_key.trim();
    if (!groupKey) {
      setFormError('Group key is required.');
      return;
    }
    setFormError(null);
    setCreating(true);
    try {
      const created = await routeGroups.create({
        group_key: groupKey,
        name: form.name.trim() || null,
        mode: form.mode,
      });
      setCreateOpen(false);
      resetForm();
      pushToast({ tone: 'success', title: 'Route group created', message: `Group "${created.group_key}" is ready for configuration.` });
      navigate(`/route-groups/${created.group_key}`);
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Create failed', message: error?.message || 'Failed to create route group.' });
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeletingKey(deleteTarget);
    try {
      await routeGroups.delete(deleteTarget);
      pushToast({ tone: 'success', title: 'Route group deleted', message: `Group "${deleteTarget}" was deleted.` });
      setDeleteTarget(null);
      refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete route group.' });
    } finally {
      setDeletingKey(null);
    }
  };

  const columns = [
    { key: 'group_key', header: 'Group Key', render: (row: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{row.group_key}</code> },
    { key: 'name', header: 'Name', render: (row: any) => row.name || <span className="text-gray-400">—</span> },
    { key: 'mode', header: 'Type', render: (row: any) => <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-700">{row.mode}</span> },
    { key: 'routing_strategy', header: 'Routing', render: (row: any) => row.routing_strategy || <span className="text-gray-400">Default shuffle</span> },
    { key: 'member_count', header: 'Members' },
    {
      key: 'enabled',
      header: 'Traffic',
      render: (row: any) => (
        <span className={`text-xs px-2 py-0.5 rounded ${row.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'}`}>
          {row.enabled ? 'Live' : 'Off'}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (row: any) => (
        <div className="flex justify-end" onClick={(event) => event.stopPropagation()}>
          <button
            onClick={() => setDeleteTarget(row.group_key)}
            disabled={deletingKey === row.group_key}
            className="p-1.5 hover:bg-red-50 rounded-lg disabled:opacity-50"
            title="Delete group"
          >
            <Trash2 className="w-4 h-4 text-red-500" />
          </button>
        </div>
      ),
    },
  ];

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Model Groups</h1>
          <p className="mt-1 text-sm text-gray-500">Create the group first, add members second, and rely on default shuffle until you need an advanced override.</p>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          <Plus className="w-4 h-4" />
          Create Model Group
        </button>
      </div>

      <Card className="mb-5 border-blue-100 bg-gradient-to-br from-blue-50 via-white to-slate-50">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-xs font-medium text-blue-700 shadow-sm ring-1 ring-blue-100">
              <Sparkles className="h-3.5 w-3.5" />
              Recommended setup order
            </div>
            <p className="mt-3 text-sm text-slate-700">
              Keep the first pass simple: create the group shell, add members, and start with default shuffle routing.
            </p>
          </div>
          <div className="grid gap-2 text-sm text-slate-700 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 1</div>
              <div className="mt-1 font-medium">Create shell</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 2</div>
              <div className="mt-1 font-medium">Add members</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 3</div>
              <div className="mt-1 font-medium">Use default shuffle</div>
            </div>
          </div>
        </div>
      </Card>

      <Card>
        <div className="px-4 pt-3 pb-2">
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search model groups..."
            className="w-full sm:w-80 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        {result === null && !loading && (
          <div className="px-4 pb-2">
            <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">Failed to load route groups.</div>
          </div>
        )}
        <DataTable
          columns={columns}
          data={result?.data || []}
          loading={loading}
          emptyMessage="No model groups found"
          pagination={result?.pagination}
          onPageChange={setPageOffset}
          onRowClick={(row: any) => navigate(`/route-groups/${row.group_key}`)}
        />
      </Card>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Create Model Group" wide>
        <div className="space-y-5">
          <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
            <div className="font-semibold">What happens next</div>
            <div className="mt-1 text-blue-800">
              This creates the group shell only. On the next page you will add members, review the request example, and optionally open advanced routing or prompt settings.
            </div>
          </div>

          <div className="space-y-4 rounded-xl border border-slate-200 p-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">Required to create</h3>
              <p className="mt-1 text-xs text-slate-500">Only the stable key and workload type are required. Routing starts as shuffle by default.</p>
            </div>

            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Group Key</label>
              <input
                value={form.group_key}
                onChange={(event) => {
                  setForm({ ...form, group_key: event.target.value });
                  if (formError) setFormError(null);
                }}
                placeholder="support-primary"
                data-autofocus="true"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="mt-1 text-xs text-gray-500">Use a stable key that clients, policies, and bindings can target.</p>
            </div>

            {formError && <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{formError}</div>}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Workload Type</label>
                <select
                  value={form.mode}
                  onChange={(event) => setForm({ ...form, mode: event.target.value })}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {ROUTE_GROUP_MODE_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Display Name</label>
                <input
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  placeholder="Support Primary"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setCreateOpen(false)} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {creating ? 'Creating...' : 'Create and continue'}
            </button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete route group"
        description={deleteTarget ? `Delete route group "${deleteTarget}"? This removes members and policy history references for this group.` : ''}
        confirmLabel="Delete Group"
        destructive
        confirming={!!deletingKey}
        onConfirm={handleDelete}
        onClose={() => {
          if (!deletingKey) setDeleteTarget(null);
        }}
      />
    </div>
  );
}
