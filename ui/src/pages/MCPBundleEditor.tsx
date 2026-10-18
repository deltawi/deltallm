import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Boxes, Trash2 } from 'lucide-react';
import Button from '../components/Button';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/ToastProvider';
import { PageShell } from '../components/admin/shells';
import BundleDetailsCard from '../components/mcp/bundles/BundleDetailsCard';
import BundleEndpointCard from '../components/mcp/bundles/BundleEndpointCard';
import BundlePreviewCard, { type SelectedKey } from '../components/mcp/bundles/BundlePreviewCard';
import BundleServersCard from '../components/mcp/bundles/BundleServersCard';
import { useBundlePreview } from '../components/mcp/bundles/useBundlePreview';
import { mcpBundles, type MCPBundle } from '../lib/api';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import { errorMessage } from '../lib/errorMessage';
import { useApi } from '../lib/hooks';
import {
  createPayload,
  draftFromBundle,
  emptyBundleDraft,
  findNameConflicts,
  suggestBundleKey,
  updatePayload,
  validateDraft,
  type BundleDraft,
} from '../lib/mcpBundles';
import { navTrail } from '../lib/navigation';

const BUNDLES_LIST_PATH = '/mcp-servers?tab=bundles';

function trail(label: string) {
  return [...navTrail('/mcp-servers'), { label: 'Bundles', to: BUNDLES_LIST_PATH }, { label }];
}

type EditorFormProps = {
  bundle: MCPBundle | null;
  canChooseGlobal: boolean;
};

function BundleEditorForm({ bundle, canChooseGlobal }: EditorFormProps) {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const isEdit = bundle !== null;
  const readOnly = isEdit && !bundle.capabilities.can_mutate;
  const [draft, setDraft] = useState<BundleDraft>(() => (
    bundle ? draftFromBundle(bundle) : emptyBundleDraft(canChooseGlobal ? 'global' : 'organization')
  ));
  const [keyEdited, setKeyEdited] = useState(isEdit);
  const [selectedKey, setSelectedKey] = useState<SelectedKey | null>(null);
  const [saveProblems, setSaveProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const { serverTools, preview, loading: previewLoading, error: previewError } = useBundlePreview(
    draft,
    bundle?.mcp_bundle_id ?? null,
    selectedKey?.token ?? null,
  );
  const toolsByServer = useMemo(
    () => Object.fromEntries(Object.values(serverTools).map((server) => [server.mcp_server_id, server.tools.map((tool) => tool.name)])),
    [serverTools],
  );
  const conflictNames = useMemo(
    () => new Set(findNameConflicts(draft.members, toolsByServer).map((conflict) => conflict.name)),
    [draft.members, toolsByServer],
  );

  const change = useCallback((patch: Partial<BundleDraft>) => {
    setDraft((current) => {
      const next = { ...current, ...patch };
      if (!keyEdited && patch.name !== undefined) next.bundle_key = suggestBundleKey(patch.name);
      return next;
    });
    setSaveProblems([]);
  }, [keyEdited]);

  const memberProblems = draft.members
    .filter((member) => !member.all_tools && member.tool_names.length === 0)
    .map((member) => `Select at least one tool of "${member.server_key}".`);
  const serverErrors = [...memberProblems, ...(preview?.errors ?? []), ...(previewError ? [previewError] : [])];

  const save = async () => {
    const problems = validateDraft(draft, toolsByServer);
    if (problems.length > 0) {
      setSaveProblems(problems);
      return;
    }
    setSaving(true);
    try {
      if (bundle) await mcpBundles.update(bundle.mcp_bundle_id, updatePayload(draft));
      else await mcpBundles.create(createPayload(draft));
      pushToast({ tone: 'success', title: isEdit ? 'Bundle saved' : 'Bundle created', message: `"${draft.bundle_key}" is ready at /mcp/${draft.bundle_key}.` });
      navigate(BUNDLES_LIST_PATH);
    } catch (error) {
      setSaveProblems([errorMessage(error, 'Could not save the bundle.')]);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!bundle) return;
    setDeleting(true);
    try {
      await mcpBundles.delete(bundle.mcp_bundle_id);
      pushToast({ tone: 'success', title: 'Bundle deleted', message: `"${bundle.bundle_key}" was removed.` });
      navigate(BUNDLES_LIST_PATH);
    } catch (error) {
      pushToast({ tone: 'error', title: 'Delete failed', message: errorMessage(error, 'Could not delete the bundle.') });
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  return (
    <PageShell
      breadcrumbs={trail(isEdit ? bundle.name : 'Create bundle')}
      title={isEdit ? bundle.name : 'Create bundle'}
      icon={Boxes}
      description="Show the tools of several MCP servers at one endpoint. A bundle does not give access."
      actions={isEdit && !readOnly ? (
        <Button variant="danger-outline" onClick={() => setConfirmDelete(true)}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
          Delete
        </Button>
      ) : null}
    >
      {readOnly ? (
        <div className="mb-4 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600">
          You can see this bundle, but you cannot change it.
        </div>
      ) : null}
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <BundleDetailsCard
            draft={draft}
            isEdit={isEdit}
            readOnly={readOnly}
            canChooseGlobal={canChooseGlobal}
            onChange={change}
            onKeyEdited={() => setKeyEdited(true)}
          />
          <BundleServersCard
            members={draft.members}
            serverTools={serverTools}
            conflictNames={conflictNames}
            readOnly={readOnly}
            errors={serverErrors}
            onChange={(members) => change({ members })}
          />
        </div>
        <div className="space-y-6 lg:sticky lg:top-6 lg:self-start">
          <BundleEndpointCard bundleKey={draft.bundle_key} />
          <BundlePreviewCard
            selected={selectedKey}
            result={preview?.key && preview.key.api_key === selectedKey?.token ? preview.key : null}
            loading={previewLoading}
            onSelect={setSelectedKey}
          />
        </div>
      </div>
      {saveProblems.length > 0 ? (
        <ul role="alert" aria-label="Save problems" className="mt-6 space-y-1 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {saveProblems.map((problem) => <li key={problem}>{problem}</li>)}
        </ul>
      ) : null}
      <div className="mt-6 flex justify-end gap-2 border-t border-gray-200 pt-4">
        <Button variant="outline" onClick={() => navigate(BUNDLES_LIST_PATH)}>Cancel</Button>
        {!readOnly ? <Button loading={saving} onClick={() => void save()}>Save bundle</Button> : null}
      </div>
      <ConfirmDialog
        open={confirmDelete}
        title="Delete bundle"
        description={`Delete "${bundle?.bundle_key ?? ''}"? Clients that use /mcp/${bundle?.bundle_key ?? ''} get "not found". The servers and their tools do not change.`}
        confirmLabel="Delete bundle"
        destructive
        confirming={deleting}
        onConfirm={() => void remove()}
        onClose={() => setConfirmDelete(false)}
      />
    </PageShell>
  );
}

function EditorSkeleton({ label }: { label: string }) {
  return (
    <PageShell breadcrumbs={trail(label)} title={label} icon={Boxes}>
      <div className="grid gap-6 lg:grid-cols-3" aria-label="Loading bundle">
        <div className="space-y-6 lg:col-span-2">
          <div className="h-48 animate-pulse rounded-2xl bg-gray-100" />
          <div className="h-72 animate-pulse rounded-2xl bg-gray-100" />
        </div>
        <div className="space-y-6">
          <div className="h-40 animate-pulse rounded-2xl bg-gray-100" />
          <div className="h-32 animate-pulse rounded-2xl bg-gray-100" />
        </div>
      </div>
    </PageShell>
  );
}

export default function MCPBundleEditor() {
  const { bundleId } = useParams<{ bundleId: string }>();
  const { authMode, session, isLoading } = useAuth();
  const canChooseGlobal = isPlatformAdminSession(authMode, session);
  const { data, error } = useApi(
    (signal) => (bundleId ? mcpBundles.get(bundleId, signal) : Promise.resolve(null)),
    [bundleId],
  );

  // The default owner of a new bundle depends on the role, so wait for the session.
  if (isLoading) return <EditorSkeleton label={bundleId ? 'Bundle' : 'Create bundle'} />;
  if (!bundleId) return <BundleEditorForm bundle={null} canChooseGlobal={canChooseGlobal} />;
  if (error) {
    return (
      <PageShell breadcrumbs={trail('Bundle')} title="Bundle" icon={Boxes}>
        <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {errorMessage(error, 'Could not load the bundle.')}
        </div>
      </PageShell>
    );
  }
  if (!data || data.mcp_bundle_id !== bundleId) return <EditorSkeleton label="Bundle" />;
  return <BundleEditorForm key={data.mcp_bundle_id} bundle={data} canChooseGlobal={canChooseGlobal} />;
}
