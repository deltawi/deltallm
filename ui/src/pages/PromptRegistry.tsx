import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Sparkles, Trash2 } from 'lucide-react';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import ConfirmDialog from '../components/ConfirmDialog';
import { promptRegistry } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';
import { ContentCard, IndexShell } from '../components/admin/shells';

export default function PromptRegistry() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [form, setForm] = useState({
    template_key: '',
    name: '',
    description: '',
    owner_scope: '',
  });

  const pageSize = 10;
  const { data: result, loading, refetch } = useApi(
    () => promptRegistry.listTemplates({ search, limit: pageSize, offset: pageOffset }),
    [search, pageOffset]
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPageOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const resetForm = () => setForm({ template_key: '', name: '', description: '', owner_scope: '' });

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
        owner_scope: form.owner_scope.trim() || null,
      });
      setCreateOpen(false);
      resetForm();
      pushToast({ tone: 'success', title: 'Template created', message: `Prompt template "${created.template_key}" is ready.` });
      navigate(`/prompts/${created.template_key}`);
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Create failed', message: error?.message || 'Failed to create prompt template.' });
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
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete prompt template.' });
    } finally {
      setDeletingKey(null);
    }
  };

  const columns = [
    { key: 'template_key', header: 'Template Key', render: (row: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{row.template_key}</code> },
    { key: 'name', header: 'Name' },
    { key: 'description', header: 'Description', render: (row: any) => row.description || <span className="text-gray-400">—</span> },
    { key: 'version_count', header: 'Versions' },
    { key: 'label_count', header: 'Labels' },
    {
      key: 'actions',
      header: '',
      render: (row: any) => (
        <div className="flex justify-end" onClick={(event) => event.stopPropagation()}>
          <button
            onClick={() => setDeleteTarget(row.template_key)}
            disabled={deletingKey === row.template_key}
            className="p-1.5 hover:bg-red-50 rounded-lg disabled:opacity-50"
            title="Delete template"
          >
            <Trash2 className="w-4 h-4 text-red-500" />
          </button>
        </div>
      ),
    },
  ];

  return (
    <IndexShell
      title="Prompt Registry"
      count={result?.pagination?.total ?? null}
      description="Create the prompt shell first, add the system prompt and variables, then validate and register a usable version."
      action={(
        <button
          onClick={() => setCreateOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          Create Prompt
        </button>
      )}
      intro={(
        <div className="rounded-2xl border border-blue-100 bg-gradient-to-br from-amber-50 via-white to-slate-50 px-5 py-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-xs font-medium text-amber-700 shadow-sm ring-1 ring-amber-100">
                <Sparkles className="h-3.5 w-3.5" />
                Recommended setup order
              </div>
              <p className="mt-3 text-sm text-slate-700">
                Keep the first pass linear: create the prompt shell, write the system prompt, then validate and register a version before using it elsewhere.
              </p>
            </div>
            <div className="grid gap-2 text-sm text-slate-700 sm:grid-cols-3">
              <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 1</div>
                <div className="mt-1 font-medium">Create shell</div>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 2</div>
                <div className="mt-1 font-medium">Author version</div>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Step 3</div>
                <div className="mt-1 font-medium">Validate and register</div>
              </div>
            </div>
          </div>
        </div>
      )}
      toolbar={(
        <input
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder="Search prompts..."
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 sm:w-80"
        />
      )}
    >
      <ContentCard>
        <DataTable
          columns={columns}
          data={result?.data || []}
          loading={loading}
          emptyMessage="No prompt templates found"
          pagination={result?.pagination}
          onPageChange={setPageOffset}
          onRowClick={(row: any) => navigate(`/prompts/${row.template_key}`)}
        />
      </ContentCard>

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

            {formError && <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{formError}</div>}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Template Key</label>
              <input
                value={form.template_key}
                onChange={(event) => {
                  setForm({ ...form, template_key: event.target.value });
                  if (formError) setFormError(null);
                }}
                placeholder="support.reply"
                data-autofocus="true"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="mt-1 text-xs text-gray-500">Use a stable key that labels, bindings, and requests can reference.</p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Prompt Name</label>
              <input
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Support Reply Prompt"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
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
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Owner Scope</label>
                <input
                  value={form.owner_scope}
                  onChange={(event) => setForm({ ...form, owner_scope: event.target.value })}
                  placeholder="platform / team:ops / org:acme"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          </details>

          <div className="flex justify-end gap-2 pt-2">
            <button onClick={() => setCreateOpen(false)} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50">
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating}
              className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
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
