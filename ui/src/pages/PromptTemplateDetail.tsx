import { useMemo, useState, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, FilePlus2, FileText, FlaskConical, History, Link2, Settings } from 'lucide-react';
import Button from '../components/Button';
import ConfirmDialog from '../components/ConfirmDialog';
import JourneyChecklist from '../components/JourneyChecklist';
import ManagedAssetAccessSummary from '../components/ManagedAssetAccessSummary';
import { useToast } from '../components/ToastProvider';
import { IconTabs, RecordDetailShell } from '../components/admin/shells';
import { CopyIdentifier, CreatedBy } from '../components/admin/lists/AssetListCells';
import PromptLabelCards from '../components/prompt-registry/PromptLabelCards';
import PromptLabelDialog, { type PromptLabelDialogStart, type PromptLabelMove } from '../components/prompt-registry/PromptLabelDialog';
import PromptOverviewTab from '../components/prompt-registry/PromptOverviewTab';
import PromptSettingsTab, { type PromptTemplateDetailsForm } from '../components/prompt-registry/PromptSettingsTab';
import PromptTestTab from '../components/prompt-registry/PromptTestTab';
import PromptUsageTab from '../components/prompt-registry/PromptUsageTab';
import PromptVersionEditor from '../components/prompt-registry/PromptVersionEditor';
import PromptVersionsTab from '../components/prompt-registry/PromptVersionsTab';
import { promptRegistry, type PromptLabel, type PromptVersionCreateInput } from '../lib/api';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import { useBranding } from '../lib/brandingContext';
import { fmtLocalDay } from '../lib/format';
import { useApi } from '../lib/hooks';
import { bindingsForLabel, latestVersion, PRODUCTION_LABEL, resolveReference, sortLabels, type PromptReference } from '../lib/promptVersions';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';

type TabId = 'prompt' | 'versions' | 'test' | 'usage' | 'settings';
type Busy = 'version' | 'label' | 'remove' | 'details' | 'delete' | null;

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export default function PromptTemplateDetail() {
  const { templateKey } = useParams<{ templateKey: string }>();
  // A new key starts a fresh page state, so tabs and dialogs never carry over between prompts.
  return templateKey ? <PromptTemplateDetailView key={templateKey} templateKey={templateKey} /> : null;
}

function PromptTemplateDetailView({ templateKey }: { templateKey: string }) {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { branding } = useBranding();
  const { session, authMode } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const audiences = useManagedAssetAudienceOptions(session, isPlatformAdmin);
  const detail = useApi((signal) => promptRegistry.getTemplate(templateKey, signal), [templateKey]);

  const [tab, setTab] = useState<TabId>('prompt');
  const [viewRef, setViewRef] = useState<PromptReference | null>(null);
  const [testRef, setTestRef] = useState<PromptReference | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [compareWith, setCompareWith] = useState<number | null | undefined>(undefined);
  const [editorBase, setEditorBase] = useState<number | null | undefined>(undefined);
  const [labelDialog, setLabelDialog] = useState<PromptLabelDialogStart | null>(null);
  const [labelToRemove, setLabelToRemove] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState<Busy>(null);

  const template = detail.data?.template ?? null;
  const versions = useMemo(() => [...(detail.data?.versions ?? [])].sort((a, b) => b.version - a.version), [detail.data]);
  const labels = useMemo(() => sortLabels(detail.data?.labels ?? []), [detail.data]);
  const bindings = useMemo(() => detail.data?.bindings ?? [], [detail.data]);
  const latest = latestVersion(versions);
  const production = labels.find((label) => label.label === PRODUCTION_LABEL) ?? null;
  const defaultRef: PromptReference = labels[0] ? { label: labels[0].label } : { version: latest?.version ?? 0 };
  const shownRef = viewRef && resolveReference(viewRef, versions, labels) ? viewRef : defaultRef;
  const testedRef = testRef && resolveReference(testRef, versions, labels) ? testRef : defaultRef;
  const selected = versions.find((item) => item.version === selectedVersion)?.version ?? latest?.version ?? 0;
  const compared = compareWith === undefined ? versions.find((item) => item.version < selected)?.version ?? null : compareWith;
  const targetOptions = { teamOptions: audiences.teamOptions, organizationOptions: audiences.organizationOptions };
  const canWrite = !template?.access || Boolean(template.access.capabilities.write);
  const canDelete = !template?.access || Boolean(template.access.capabilities.delete);

  if (!template) {
    if (detail.loading || (!detail.data && !detail.error)) {
      return <div className="flex min-h-[300px] items-center justify-center p-6"><div className="h-8 w-8 animate-spin rounded-full border-b-2 border-brand-primary" role="status" aria-label="Loading prompt" /></div>;
    }
    return (
      <div className="space-y-4 p-6">
        <button onClick={() => navigate('/prompts')} className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700"><ArrowLeft className="h-4 w-4" /> Back to Prompt Registry</button>
        <p role="alert" className="text-sm text-gray-600">{errorText(detail.error, 'Prompt template not found.')}</p>
      </div>
    );
  }

  const run = async (kind: Exclude<Busy, null>, action: () => Promise<void>) => {
    if (busy) return;
    setBusy(kind);
    try {
      await action();
    } finally {
      setBusy(null);
    }
  };

  const createVersion = (payload: PromptVersionCreateInput, pointLabel: string | null) => run('version', async () => {
    let created;
    try {
      created = await promptRegistry.createVersion(template.template_key, payload);
    } catch (error) {
      pushToast({ tone: 'error', title: 'Version not saved', message: errorText(error, 'Could not save the version.') });
      return;
    }
    let labelError: string | null = null;
    if (pointLabel) {
      try {
        await promptRegistry.assignLabel(template.template_key, { label: pointLabel, version: created.version });
      } catch (error) {
        labelError = errorText(error, 'Could not move the label.');
      }
    }
    pushToast({ tone: 'success', title: `Saved v${created.version}`, message: pointLabel && !labelError ? `${pointLabel} now points to v${created.version}.` : 'No label points to it yet.' });
    if (labelError) pushToast({ tone: 'error', title: `${pointLabel} did not move`, message: labelError });
    setEditorBase(undefined);
    setTab('versions');
    setSelectedVersion(created.version);
    setCompareWith(undefined);
    detail.refetch();
  });

  const moveLabel = (move: PromptLabelMove) => run('label', async () => {
    try {
      await promptRegistry.assignLabel(template.template_key, {
        label: move.label,
        version: move.version,
        require_approval: Boolean(move.approvedBy),
        approved_by: move.approvedBy || null,
      });
      pushToast({ tone: 'success', title: 'Label moved', message: `${move.label} now points to v${move.version}.` });
      setLabelDialog(null);
      detail.refetch();
    } catch (error) {
      pushToast({ tone: 'error', title: 'Label not moved', message: errorText(error, 'Could not move the label.') });
    }
  });

  const removeLabel = (label: string) => run('remove', async () => {
    try {
      await promptRegistry.deleteLabel(template.template_key, label);
      pushToast({ tone: 'success', title: 'Label removed', message: `${label} no longer points to a version.` });
      setLabelToRemove(null);
      detail.refetch();
    } catch (error) {
      pushToast({ tone: 'error', title: 'Label not removed', message: errorText(error, 'Could not remove the label.') });
    }
  });

  const saveDetails = (form: PromptTemplateDetailsForm) => run('details', async () => {
    try {
      await promptRegistry.updateTemplate(template.template_key, {
        name: form.name.trim(),
        description: form.description.trim() || null,
        ...(isPlatformAdmin ? { owner_scope: form.owner_scope.trim() || null } : {}),
      });
      pushToast({ tone: 'success', title: 'Details saved', message: 'The name and description were saved.' });
      detail.refetch();
    } catch (error) {
      pushToast({ tone: 'error', title: 'Details not saved', message: errorText(error, 'Could not save the details.') });
    }
  });

  const deleteTemplate = () => run('delete', async () => {
    try {
      await promptRegistry.deleteTemplate(template.template_key);
      pushToast({ tone: 'success', title: 'Prompt deleted', message: `${template.template_key} was deleted.` });
      navigate('/prompts');
    } catch (error) {
      pushToast({ tone: 'error', title: 'Prompt not deleted', message: errorText(error, 'Could not delete the prompt.') });
    }
  });

  const openVersion = (version: number) => { setTab('versions'); setSelectedVersion(version); setCompareWith(undefined); };
  const testVersion = (version: number) => { setTab('test'); setTestRef({ version }); };
  const promote = (label: PromptLabel) => setLabelDialog({ label: PRODUCTION_LABEL, version: label.version, confirm: Boolean(production) });

  const tabs = [
    { id: 'prompt' as const, label: 'Prompt', icon: FileText },
    { id: 'versions' as const, label: 'Versions', icon: History, count: versions.length },
    { id: 'test' as const, label: 'Test', icon: FlaskConical },
    ...(isPlatformAdmin ? [{ id: 'usage' as const, label: 'Used by', icon: Link2, count: bindings.length }] : []),
    { id: 'settings' as const, label: 'Settings', icon: Settings },
  ];
  const noVersions = (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white px-4 py-8 text-center">
      <p className="text-sm text-gray-600">This prompt has no versions yet.</p>
      {canWrite ? <Button size="sm" className="mt-3" onClick={() => setEditorBase(null)}><FilePlus2 className="h-3.5 w-3.5" aria-hidden="true" /> Write the first version</Button> : null}
    </div>
  );
  const panels: Record<TabId, ReactNode> = {
    prompt: versions.length ? <PromptOverviewTab versions={versions} labels={labels} reference={shownRef} onReferenceChange={setViewRef} onOpenVersion={openVersion} /> : noVersions,
    versions: versions.length ? (
      <PromptVersionsTab versions={versions} labels={labels} selected={selected} compareWith={compared} canWrite={canWrite}
        onSelect={(version) => { setSelectedVersion(version); setCompareWith(undefined); }} onCompareChange={setCompareWith}
        onPointLabel={(version) => setLabelDialog({ label: production ? PRODUCTION_LABEL : null, version })}
        onNewFrom={(version) => setEditorBase(version)} onTest={testVersion} />
    ) : noVersions,
    test: versions.length ? (
      <PromptTestTab templateKey={template.template_key} versions={versions} labels={labels} reference={testedRef} onReferenceChange={setTestRef}
        initialValues={{ product_name: branding.instance_name }} />
    ) : noVersions,
    usage: <PromptUsageTab bindings={bindings} labels={labels} targetOptions={targetOptions} />,
    settings: (
      <PromptSettingsTab key={`${template.name}\n${template.description ?? ''}\n${template.owner_scope ?? ''}`} template={template} canWrite={canWrite} canDelete={canDelete}
        isPlatformAdmin={isPlatformAdmin} bindingCount={bindings.length} saving={busy === 'details'} onSave={saveDetails}
        onAccessSaved={() => detail.refetch()} onDelete={() => setConfirmDelete(true)}
        teamOptions={audiences.teamOptions} organizationOptions={audiences.organizationOptions} audiencesLoading={audiences.loading}
        audiencesError={audiences.error} onRetryAudiences={audiences.refetch} />
    ),
  };
  const removeUsers = labelToRemove ? bindingsForLabel(bindings, labelToRemove) : [];

  return (
    <RecordDetailShell
      backAction={(
        <button onClick={() => navigate('/prompts')} className="flex items-center gap-2 text-sm text-gray-500 transition-colors hover:text-gray-700">
          <ArrowLeft className="h-4 w-4" /> Back to Prompt Registry
        </button>
      )}
      header={(
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="break-words text-2xl font-bold text-gray-900">{template.name}</h1>
              <div className="rounded-md bg-gray-100 px-2"><CopyIdentifier value={template.template_key} display={template.template_key} label="Template key" /></div>
            </div>
            {template.description ? <p className="max-w-3xl text-sm text-gray-600">{template.description}</p> : null}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
              {template.access ? <ManagedAssetAccessSummary access={template.access} teamOptions={audiences.teamOptions} organizationOptions={audiences.organizationOptions} /> : null}
              <span className="flex items-center gap-1">Created by <CreatedBy row={template} /></span>
              <span>Updated {fmtLocalDay(template.updated_at)}</span>
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            {versions.length ? <Button variant="outline" onClick={() => setTab('test')}><FlaskConical className="h-4 w-4" aria-hidden="true" /> Test</Button> : null}
            {canWrite ? <Button onClick={() => setEditorBase(latest?.version ?? null)}><FilePlus2 className="h-4 w-4" aria-hidden="true" /> New version</Button> : null}
          </div>
        </div>
      )}
      intro={(
        <div className="space-y-4">
          {detail.error ? <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">Could not refresh this prompt. The page shows the last loaded data. {errorText(detail.error, '')}</p> : null}
          {!production ? (
            <JourneyChecklist
              title="Set up this prompt"
              description="Bindings and API calls get the version that a label points to. They use the production label unless they name another one."
              steps={[
                { label: 'Write a version', done: versions.length > 0, hint: 'Write the prompt and its {variables}. Saved versions cannot change.' },
                { label: 'Point production at a version', done: false, hint: 'Test the version, then point the production label at it.' },
              ]}
            />
          ) : null}
          {versions.length ? (
            <PromptLabelCards labels={labels} latestVersion={latest?.version ?? null} bindings={bindings} showUsage={isPlatformAdmin} canWrite={canWrite}
              targetOptions={targetOptions} onView={(label) => { setTab('prompt'); setViewRef({ label }); }}
              onChangeVersion={(label) => setLabelDialog({ label, version: labels.find((item) => item.label === label)?.version ?? selected })}
              onPromote={promote} onAdd={() => setLabelDialog({ label: null, version: latest?.version ?? 0 })} />
          ) : null}
        </div>
      )}
    >
      <div>
        <IconTabs id="prompt-tabs" label="Prompt sections" active={tab} onChange={setTab} items={tabs} />
        {tabs.map(({ id }) => (
          <div key={id} role="tabpanel" id={`prompt-tabs-panel-${id}`} aria-labelledby={`prompt-tabs-tab-${id}`} hidden={tab !== id}>
            {panels[id]}
          </div>
        ))}
      </div>

      {editorBase !== undefined ? (
        <PromptVersionEditor base={versions.find((item) => item.version === editorBase) ?? null} nextVersion={(latest?.version ?? 0) + 1}
          labels={labels} saving={busy === 'version'} onClose={() => setEditorBase(undefined)} onSave={(payload, pointLabel) => void createVersion(payload, pointLabel)} />
      ) : null}
      {labelDialog ? (
        <PromptLabelDialog start={labelDialog} labels={labels} versions={versions} bindings={bindings} showUsage={isPlatformAdmin} targetOptions={targetOptions}
          saving={busy === 'label'} onClose={() => setLabelDialog(null)} onConfirm={(move) => void moveLabel(move)}
          onRemove={(label) => { setLabelDialog(null); setLabelToRemove(label); }} />
      ) : null}
      <ConfirmDialog
        open={labelToRemove !== null}
        title={`Remove ${labelToRemove ?? ''}?`}
        description={removeUsers.length
          ? `${removeUsers.length} ${removeUsers.length === 1 ? 'binding uses' : 'bindings use'} this label. Requests skip ${removeUsers.length === 1 ? 'it' : 'them'} until the label exists again. API calls that ask for the label fail.`
          : 'API calls that ask for this label fail until you add it again. The versions stay.'}
        confirmLabel="Remove label"
        destructive
        confirming={busy === 'remove'}
        onConfirm={() => { if (labelToRemove) void removeLabel(labelToRemove); }}
        onClose={() => { if (busy !== 'remove') setLabelToRemove(null); }}
      />
      <ConfirmDialog
        open={confirmDelete}
        title="Delete prompt"
        description={`Delete "${template.template_key}"? This removes all versions, labels, and render logs.${bindings.length ? ` ${bindings.length} ${bindings.length === 1 ? 'binding stops' : 'bindings stop'} resolving it.` : ''}`}
        confirmLabel="Delete prompt"
        destructive
        confirming={busy === 'delete'}
        onConfirm={() => void deleteTemplate()}
        onClose={() => { if (busy !== 'delete') setConfirmDelete(false); }}
      />
    </RecordDetailShell>
  );
}
