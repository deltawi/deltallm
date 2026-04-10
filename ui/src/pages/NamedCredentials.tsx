import { useEffect, useMemo, useState } from 'react';
import { ArrowRightLeft, KeyRound, Link2, Pencil, Plus, Search, ShieldCheck, Trash2 } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import Modal from '../components/Modal';
import NamedCredentialForm from '../components/NamedCredentialForm';
import { ContentCard, IndexShell } from '../components/admin/shells';
import DataTable from '../components/DataTable';
import { models, namedCredentials, type InlineCredentialGroup, type NamedCredential } from '../lib/api';
import { customUpstreamAuthHeaderLabel, providerDisplayName, supportsCustomUpstreamAuthProvider } from '../lib/providers';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';

function connectionSummary(credential: NamedCredential): string {
  const config = credential.connection_config || {};
  if (typeof config.api_base === 'string' && config.api_base.trim()) {
    return config.api_base;
  }
  const customAuthHeader = supportsCustomUpstreamAuthProvider(credential.provider)
    ? customUpstreamAuthHeaderLabel(config)
    : null;
  if (customAuthHeader) {
    return `Custom auth header: ${customAuthHeader}`;
  }
  if (typeof config.region === 'string' && config.region.trim()) {
    return `Region ${config.region}`;
  }
  return credential.credentials_present ? 'Credentials configured' : 'No connection data';
}

export default function NamedCredentials() {
  const { pushToast } = useToast();
  const [providerFilter, setProviderFilter] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [editingCredential, setEditingCredential] = useState<NamedCredential | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<NamedCredential | null>(null);
  const [convertTarget, setConvertTarget] = useState<InlineCredentialGroup | null>(null);
  const [conversionName, setConversionName] = useState('');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [converting, setConverting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const { data: presetsResponse } = useApi(() => models.providerPresets(), []);
  const { data: credentialsResponse, loading, refetch } = useApi(
    () => namedCredentials.list({ provider: providerFilter || undefined }),
    [providerFilter],
  );
  const { data: inlineReportResponse, refetch: refetchInlineReport } = useApi(
    () => namedCredentials.inlineReport(),
    [],
  );
  const { data: editingDetail } = useApi(
    () => (editingCredential ? namedCredentials.get(editingCredential.credential_id) : Promise.resolve(null)),
    [editingCredential?.credential_id],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(searchInput.trim().toLowerCase()), 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const providerPresets = presetsResponse?.data || [];
  const items = credentialsResponse?.data || [];
  const inlineGroups = inlineReportResponse?.data || [];
  const filteredItems = useMemo(
    () =>
      items.filter((item) => {
        if (!search) return true;
        return (
          item.name.toLowerCase().includes(search)
          || item.provider.toLowerCase().includes(search)
          || connectionSummary(item).toLowerCase().includes(search)
        );
      }),
    [items, search],
  );

  const usageCount = filteredItems.reduce((total, item) => total + Number(item.usage_count || 0), 0);

  const handleCreate = async (payload: { name: string; provider: string; connection_config: Record<string, unknown> }) => {
    setSaving(true);
    setFormError(null);
    try {
      await namedCredentials.create(payload);
      pushToast({ tone: 'success', title: 'Credential created', message: `"${payload.name}" is ready to use.` });
      setCreateOpen(false);
      await refetch();
      await refetchInlineReport();
    } catch (error: any) {
      setFormError(error?.message || 'Failed to create named credential.');
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async (payload: { name: string; provider: string; connection_config: Record<string, unknown> }) => {
    if (!editingCredential) return;
    setSaving(true);
    setFormError(null);
    try {
      await namedCredentials.update(editingCredential.credential_id, payload);
      pushToast({ tone: 'success', title: 'Credential updated', message: `"${payload.name}" was updated.` });
      setEditingCredential(null);
      await refetch();
      await refetchInlineReport();
    } catch (error: any) {
      setFormError(error?.message || 'Failed to update named credential.');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await namedCredentials.delete(deleteTarget.credential_id);
      pushToast({ tone: 'success', title: 'Credential deleted', message: `"${deleteTarget.name}" was removed.` });
      setDeleteTarget(null);
      await refetch();
      await refetchInlineReport();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete named credential.' });
    } finally {
      setDeleting(false);
    }
  };

  const handleConvert = async () => {
    if (!convertTarget || !conversionName.trim()) return;
    setConverting(true);
    try {
      await namedCredentials.convertInlineGroup({
        fingerprint: convertTarget.fingerprint,
        name: conversionName.trim(),
        provider: convertTarget.provider,
        deployment_ids: convertTarget.deployments.map((deployment) => deployment.deployment_id),
      });
      pushToast({
        tone: 'success',
        title: 'Inline credentials converted',
        message: `"${conversionName.trim()}" now backs ${convertTarget.deployment_count} deployment${convertTarget.deployment_count === 1 ? '' : 's'}.`,
      });
      setConvertTarget(null);
      setConversionName('');
      await refetch();
      await refetchInlineReport();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Conversion failed', message: error?.message || 'Failed to convert inline credentials.' });
    } finally {
      setConverting(false);
    }
  };

  const columns = [
    { key: 'name', header: 'Name', render: (row: NamedCredential) => <span className="font-medium">{row.name}</span> },
    { key: 'provider', header: 'Provider', render: (row: NamedCredential) => providerDisplayName(row.provider) },
    { key: 'connection', header: 'Connection', render: (row: NamedCredential) => <span className="text-xs text-gray-600">{connectionSummary(row)}</span> },
    {
      key: 'credentials_present',
      header: 'Status',
      render: (row: NamedCredential) => (
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${row.credentials_present ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-600'}`}>
          <ShieldCheck className="h-3 w-3" />
          {row.credentials_present ? 'Configured' : 'Empty'}
        </span>
      ),
    },
    {
      key: 'usage_count',
      header: 'Linked Models',
      render: (row: NamedCredential) => (
        <span className="inline-flex items-center gap-1 text-sm text-gray-700">
          <Link2 className="h-3.5 w-3.5 text-gray-400" />
          {row.usage_count || 0}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (row: NamedCredential) => (
        <div className="flex gap-1" onClick={(event) => event.stopPropagation()}>
          <button onClick={() => { setFormError(null); setEditingCredential(row); }} className="rounded-lg p-1.5 hover:bg-gray-100">
            <Pencil className="h-4 w-4 text-gray-500" />
          </button>
          <button
            onClick={() => setDeleteTarget(row)}
            disabled={Boolean(row.usage_count)}
            className="rounded-lg p-1.5 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40"
            title={row.usage_count ? 'Credential is still linked to model deployments' : 'Delete credential'}
          >
            <Trash2 className="h-4 w-4 text-red-500" />
          </button>
        </div>
      ),
    },
  ];

  return (
    <IndexShell
      title="Named Credentials"
      titleIcon={KeyRound}
      count={filteredItems.length}
      description="Store reusable provider connection credentials and attach them to model deployments."
      action={(
        <button
          type="button"
          onClick={() => { setFormError(null); setCreateOpen(true); }}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          New Credential
        </button>
      )}
      summaryItems={[
        { label: 'Configured', value: filteredItems.filter((item) => item.credentials_present).length },
        { label: 'Linked Models', value: usageCount },
        { label: 'Providers', value: new Set(filteredItems.map((item) => item.provider)).size },
      ]}
      toolbar={(
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative w-full sm:w-80">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search credentials..."
              className="h-9 w-full rounded-lg border border-gray-300 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <select
            value={providerFilter}
            onChange={(e) => setProviderFilter(e.target.value)}
            className="h-9 rounded-lg border border-gray-300 px-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">All providers</option>
            {providerPresets.map((preset) => (
              <option key={preset.provider} value={preset.provider}>
                {providerDisplayName(preset.provider)}
              </option>
            ))}
          </select>
        </div>
      )}
    >
      <ContentCard>
        <DataTable
          columns={columns}
          data={filteredItems}
          loading={loading}
          emptyMessage="No named credentials configured"
          onRowClick={(row) => { setFormError(null); setEditingCredential(row); }}
        />
      </ContentCard>

      <div className="mt-6">
        <ContentCard>
          <div className="border-b border-gray-200 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-900">Inline Credential Conversion</h2>
            <p className="mt-1 text-xs text-gray-500">
              Find repeated inline provider credentials and convert them into reusable named credentials without changing the gateway request path.
            </p>
          </div>
          <DataTable
            columns={[
              { key: 'provider', header: 'Provider', render: (row: InlineCredentialGroup) => providerDisplayName(row.provider) },
              {
                key: 'connection',
                header: 'Connection',
                render: (row: InlineCredentialGroup) => <span className="text-xs text-gray-600">{connectionSummary({ ...row, name: '', credential_id: '', provider: row.provider } as NamedCredential)}</span>,
              },
              {
                key: 'deployment_count',
                header: 'Deployments',
                render: (row: InlineCredentialGroup) => <span className="font-medium">{row.deployment_count}</span>,
              },
              {
                key: 'actions',
                header: '',
                render: (row: InlineCredentialGroup) => (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      setConvertTarget(row);
                      setConversionName(`${providerDisplayName(row.provider)} Shared`);
                    }}
                    className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
                  >
                    <ArrowRightLeft className="h-3.5 w-3.5" />
                    Convert
                  </button>
                ),
              },
            ]}
            data={inlineGroups}
            emptyMessage="No inline credential groups available for conversion"
          />
        </ContentCard>
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Create Named Credential" wide>
        <NamedCredentialForm
          providerPresets={providerPresets}
          saving={saving}
          error={formError}
          onSave={handleCreate}
          onCancel={() => setCreateOpen(false)}
        />
      </Modal>

      <Modal open={editingCredential !== null} onClose={() => setEditingCredential(null)} title="Edit Named Credential" wide>
        <div className="space-y-5">
          <NamedCredentialForm
            initialCredential={editingDetail || editingCredential}
            providerPresets={providerPresets}
            saving={saving}
            error={formError}
            onSave={handleUpdate}
            onCancel={() => setEditingCredential(null)}
          />
          {editingDetail?.linked_deployments && editingDetail.linked_deployments.length > 0 ? (
            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
              <h3 className="mb-2 text-sm font-semibold text-gray-900">Linked Deployments</h3>
              <div className="space-y-1 text-sm text-gray-600">
                {editingDetail.linked_deployments.map((deployment) => (
                  <div key={deployment.deployment_id} className="flex items-center justify-between gap-3">
                    <span>{deployment.model_name}</span>
                    <code className="rounded bg-white px-2 py-0.5 text-xs text-gray-500">{deployment.deployment_id}</code>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Named Credential"
        description={`Delete "${deleteTarget?.name || ''}"? This cannot be undone.`}
        destructive
        confirming={deleting}
        confirmLabel="Delete"
        onConfirm={() => { void handleDelete(); }}
        onClose={() => setDeleteTarget(null)}
      />

      <Modal open={convertTarget !== null} onClose={() => setConvertTarget(null)} title="Convert Inline Credentials">
        <div className="space-y-4">
          <p className="text-sm text-gray-600">
            This will create one named credential and link {convertTarget?.deployment_count || 0} deployment{convertTarget?.deployment_count === 1 ? '' : 's'} to it.
          </p>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">New Credential Name</label>
            <input
              value={conversionName}
              onChange={(e) => setConversionName(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          {convertTarget ? (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 text-xs text-gray-600">
              <div>Provider: {providerDisplayName(convertTarget.provider)}</div>
              <div className="mt-1">Deployments: {convertTarget.deployments.map((deployment) => deployment.model_name).join(', ')}</div>
            </div>
          ) : null}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setConvertTarget(null)}
              disabled={converting}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => { void handleConvert(); }}
              disabled={converting || !conversionName.trim()}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {converting ? 'Converting...' : 'Convert'}
            </button>
          </div>
        </div>
      </Modal>
    </IndexShell>
  );
}
