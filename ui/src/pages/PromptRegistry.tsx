import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, FileText, Trash2 } from 'lucide-react';
import type { Column } from '../components/DataTable';
import AssetList from '../components/admin/lists/AssetList';
import { assetMetadataColumns } from '../components/admin/lists/assetMetadataColumns';
import { ListIdentity } from '../components/admin/lists/AssetListCells';
import { useAssetListSort } from '../lib/useAssetListSort';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import ManagedAssetAccessFields from '../components/ManagedAssetAccessFields';
import {
  promptRegistry,
  managedAssetAccessInput,
  type ManagedAssetGrantInput,
  type PromptTemplate,
} from '../lib/api';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';
import { IndexShell } from '../components/admin/shells';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';

export default function PromptRegistry() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { session, authMode } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const { teamOptions, organizationOptions, loading: audiencesLoading, error: audiencesError, refetch: refetchAudiences } = useManagedAssetAudienceOptions(
    session,
    isPlatformAdmin,
  );
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [grants, setGrants] = useState<ManagedAssetGrantInput[]>([]);
  const [form, setForm] = useState({
    template_key: '',
    name: '',
    description: '',
    owner_scope: '',
  });

  const pageSize = 10;
  const listSort = useAssetListSort(['name', 'versions', 'labels', 'bindings', 'created_by', 'updated_at', 'visibility'] as const, () => setPageOffset(0));
  const { data: result, loading, error, refetch } = useApi(
    (signal) => promptRegistry.listTemplates({ search, limit: pageSize, offset: pageOffset, sort_by: listSort.sortBy, sort_direction: listSort.sortDirection }, signal),
    [search, pageOffset, listSort.sortBy, listSort.sortDirection]
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPageOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const resetForm = () => {
    setForm({ template_key: '', name: '', description: '', owner_scope: '' });
    setGrants([]);
  };

  const handleCreate = async () => {
    const templateKey = form.template_key.trim();
    const name = form.name.trim();
    if (!templateKey) {
      setFormError('Template key is required.');
      return;
    }
    if (!name) {
      setFormError('Prompt name is required.');
      return;
    }
    setFormError(null);
    setCreating(true);
    try {
      const created = await promptRegistry.createTemplate({
        template_key: templateKey,
        name,
        description: form.description.trim() || null,
        ...(isPlatformAdmin ? { owner_scope: form.owner_scope.trim() || null } : {}),
        access: managedAssetAccessInput(grants),
      });
      setCreateOpen(false);
      resetForm();
      pushToast({ tone: 'success', title: 'Template created', message: `Prompt template "${created.template_key}" is ready.` });
      navigate(`/prompts/${encodeURIComponent(created.template_key)}`);
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Create failed', message: error instanceof Error ? error.message : 'Failed to create prompt template.' });
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeletingKey(deleteTarget);
    try {
      await promptRegistry.deleteTemplate(deleteTarget);
      pushToast({ tone: 'success', title: 'Template deleted', message: `Template "${deleteTarget}" was deleted.` });
      setDeleteTarget(null);
      refetch();
    } catch (error: unknown) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error instanceof Error ? error.message : 'Failed to delete prompt template.' });
    } finally {
      setDeletingKey(null);
    }
  };

  const columns: Column<PromptTemplate>[] = [
    { key: 'name', header: 'Prompt', sortKey: 'name', render: (row) => <ListIdentity name={row.name} identifier={row.template_key} onOpen={() => navigate(`/prompts/${encodeURIComponent(row.template_key)}`)} secondary={row.description ? <span title={row.description} className="block max-w-60 truncate">{row.description}</span> : undefined} actions={(!row.access || row.access.capabilities.delete) ? <button type="button" aria-label={`Delete ${row.name}`} onClick={() => setDeleteTarget(row.template_key)} disabled={deletingKey === row.template_key} className="rounded-lg p-2 md:p-1.5 hover:bg-red-50 disabled:opacity-50"><Trash2 className="h-4 w-4 text-red-500" /></button> : null} /> },
    { key: 'version_count', header: 'Versions', sortKey: 'versions', defaultDirection: 'desc' },
    { key: 'label_count', header: 'Labels', sortKey: 'labels', defaultDirection: 'desc' },
    { key: 'binding_count', header: 'Bindings', sortKey: 'bindings', defaultDirection: 'desc' },
    ...assetMetadataColumns<PromptTemplate>(),
  ];

  return (
    <IndexShell
      title="Prompt Registry"
      titleIcon={FileText}
      count={result?.pagination?.total ?? null}
      description="Manage prompt templates, versions, and labels."
      action={(
        <button
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-brand-primary px-4 py-2 text-sm font-medium text-brand-on-primary transition-colors hover:bg-brand-primary-hover"
        >
          <Plus className="h-4 w-4" />
          Create Prompt
        </button>
      )}

    >
      <AssetList columns={columns} data={result?.data || []} rowKey={(row) => row.prompt_template_id} loading={loading} error={error} onRetry={refetch} emptyMessage="No prompt templates found" search={searchInput} searchLabel="Search prompts" onSearchChange={setSearchInput} sort={listSort.sort} onSortChange={listSort.onSortChange} pagination={result?.pagination} onPageChange={setPageOffset} onRowClick={(row) => navigate(`/prompts/${encodeURIComponent(row.template_key)}`)} />
      <details className="mt-4 text-xs text-gray-500"><summary className="cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary">Prompt setup</summary><p className="mt-2">Create the prompt shell, write the system prompt and variables, then validate and register a version before you use it.</p></details>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="Create Prompt" wide>
        <div className="space-y-5">
          <div className="rounded-xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <div className="font-semibold">What happens next</div>
              <div className="mt-1 text-amber-800">
              This creates the prompt shell only. On the next page you will add the system prompt, define variables, validate the output, and register versions.
              </div>
            </div>

          <div className="space-y-4 rounded-xl border border-slate-200 p-4">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">Required to create</h3>
              <p className="mt-1 text-xs text-slate-500">You only need a stable key and a human-friendly name.</p>
            </div>

            {formError && !formError.startsWith('Select a ') ? (
              <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{formError}</div>
            ) : null}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Template Key</label>
              <div className="flex rounded-lg border border-gray-300 focus-within:ring-2 focus-within:ring-brand-primary">
                {!isPlatformAdmin ? (
                  <span className="flex items-center rounded-l-lg border-r border-gray-200 bg-gray-50 px-3 font-mono text-xs text-gray-500">
                    prm-XXXX-
                  </span>
                ) : null}
                <input
                  value={form.template_key}
                  onChange={(event) => {
                    setForm({ ...form, template_key: event.target.value });
                    if (formError) setFormError(null);
                  }}
                  placeholder="support.reply"
                  maxLength={isPlatformAdmin ? 201 : 128}
                  data-autofocus="true"
                  className="min-w-0 flex-1 rounded-r-lg px-3 py-2 text-sm focus:outline-none"
                />
              </div>
              <p className="mt-1 text-xs text-gray-500">
                {isPlatformAdmin
                  ? 'Use a stable key that labels, bindings, and requests can reference.'
                  : 'A random four-character code will make the complete prompt key unique.'}
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Prompt Name</label>
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Support Reply Prompt"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
              />
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 p-4">
            <ManagedAssetAccessFields
              grants={grants}
              teamOptions={teamOptions}
              organizationOptions={organizationOptions}
              allowPublic={isPlatformAdmin}
              audiencesLoading={audiencesLoading}
              audiencesError={audiencesError}
              onRetryAudiences={refetchAudiences}
              error={formError?.startsWith('Select a ') ? formError : null}
              onChange={(nextGrants) => { setGrants(nextGrants); if (formError) setFormError(null); }}
            />
          </div>

          <details className="rounded-xl border border-slate-200 px-4 py-3">
            <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900">Optional metadata</summary>
            <div className="mt-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                  placeholder="Used for customer support responses."
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                />
              </div>
              {isPlatformAdmin ? (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Owner Scope</label>
                  <input
                    value={form.owner_scope}
                    onChange={(event) => setForm({ ...form, owner_scope: event.target.value })}
                    placeholder="platform / team:ops / org:acme"
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                  />
                </div>
              ) : null}
            </div>
          </details>

          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setCreateOpen(false)} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="rounded-lg bg-brand-primary px-3 py-2 text-sm text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
            >
              {creating ? 'Creating...' : 'Create and Continue'}
            </button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete prompt template"
        description={deleteTarget ? `Delete template "${deleteTarget}"? This removes all versions and labels. Any consumer page using this prompt will stop resolving it.` : ''}
        confirmLabel="Delete Template"
        destructive
        confirming={!!deletingKey}
        onConfirm={handleDelete}
        onClose={() => {
          if (!deletingKey) setDeleteTarget(null);
        }}
      />
    </IndexShell>
  );
}
