import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Trash2 } from 'lucide-react';
import Card from '../components/Card';
import ConfirmDialog from '../components/ConfirmDialog';
import JourneyChecklist from '../components/JourneyChecklist';
import { promptRegistry } from '../lib/api';
import { useApi } from '../lib/hooks';
import { useToast } from '../components/ToastProvider';
import PromptVersionComposerCard from '../components/prompt-registry/PromptVersionComposerCard';
import PromptRolloutCard from '../components/prompt-registry/PromptRolloutCard';
import PromptTestingCard from '../components/prompt-registry/PromptTestingCard';
import PromptHistoryCard from '../components/prompt-registry/PromptHistoryCard';

function parseJsonObject(value: string, fieldName: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(`${fieldName} must be a JSON object`);
    }
    return parsed as Record<string, unknown>;
  } catch (error: any) {
    throw new Error(error?.message || `Invalid JSON in ${fieldName}`);
  }
}

function buildTemplateBody(systemPrompt: string): Record<string, unknown> {
  return {
    messages: [
      {
        role: 'system',
        content: systemPrompt.trim(),
      },
    ],
  };
}

function buildVariablesSchema(rawVariables: string): Record<string, unknown> {
  const variables = rawVariables
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  const properties = Object.fromEntries(variables.map((name) => [name, { type: 'string' }]));
  return {
    type: 'object',
    required: variables,
    properties,
    additionalProperties: true,
  };
}

export default function PromptTemplateDetail() {
  const { templateKey } = useParams<{ templateKey: string }>();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const detail = useApi(() => promptRegistry.getTemplate(templateKey!), [templateKey]);
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [creatingVersion, setCreatingVersion] = useState(false);
  const [assigningLabel, setAssigningLabel] = useState(false);
  const [publishingVersion, setPublishingVersion] = useState<number | null>(null);
  const [deletingTemplate, setDeletingTemplate] = useState(false);
  const [confirmDeleteTemplate, setConfirmDeleteTemplate] = useState(false);
  const [templateForm, setTemplateForm] = useState({ name: '', description: '', owner_scope: '' });
  const [versionForm, setVersionForm] = useState({
    system_prompt: 'You are a helpful assistant for {product_name}.',
    variables: 'product_name',
    model_hints: '{\n  "preferred_mode": "chat"\n}',
    route_preferences: '{}',
    publish: false,
  });
  const [labelForm, setLabelForm] = useState({ label: 'production', version: '', require_approval: false, approved_by: '' });
  const [renderForm, setRenderForm] = useState({
    label: 'production',
    version: '',
    variables: '{\n  "product_name": "DeltaLLM"\n}',
  });
  const [renderResult, setRenderResult] = useState<any>(null);
  const [rendering, setRendering] = useState(false);
  const [diffLeftVersion, setDiffLeftVersion] = useState('');
  const [diffRightVersion, setDiffRightVersion] = useState('');

  const template = detail.data?.template;
  const versions = detail.data?.versions || [];
  const labels = detail.data?.labels || [];

  useEffect(() => {
    if (!template) return;
    setTemplateForm({
      name: template.name || '',
      description: template.description || '',
      owner_scope: template.owner_scope || '',
    });
  }, [template]);

  if (detail.loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[300px]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!templateKey || !template) {
    return (
      <div className="p-6">
        <button onClick={() => navigate('/prompts')} className="mb-4 flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeft className="w-4 h-4" /> Back to Prompt Registry
        </button>
        <p className="text-sm text-gray-500">Prompt template not found.</p>
      </div>
    );
  }

  const checklistSteps = [
    { label: 'Create version', done: versions.length > 0, hint: 'A template shell does nothing until it has at least one version.' },
    {
      label: 'Register label',
      done: labels.length > 0 || versions.some((version) => version.status === 'published'),
      hint: 'Move a stable label such as production or staging onto a version before consumer pages bind it.',
    },
    { label: 'Run test', done: !!renderResult, hint: 'Use a render test before this prompt is used anywhere.' },
  ];

  const handleUpdateTemplate = async () => {
    setSavingTemplate(true);
    try {
      await promptRegistry.updateTemplate(template.template_key, {
        name: templateForm.name.trim(),
        description: templateForm.description.trim() || null,
        owner_scope: templateForm.owner_scope.trim() || null,
      });
      pushToast({ tone: 'success', title: 'Template updated', message: 'Template metadata was saved.' });
      detail.refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Update failed', message: error?.message || 'Failed to update template.' });
    } finally {
      setSavingTemplate(false);
    }
  };

  const handleCreateVersion = async () => {
    setCreatingVersion(true);
    try {
      if (!versionForm.system_prompt.trim()) {
        throw new Error('system prompt is required');
      }
      const templateBody = buildTemplateBody(versionForm.system_prompt);
      const variablesSchema = buildVariablesSchema(versionForm.variables);
      const modelHints = parseJsonObject(versionForm.model_hints, 'model_hints');
      const routePreferences = parseJsonObject(versionForm.route_preferences, 'route_preferences');
      const created = await promptRegistry.createVersion(template.template_key, {
        template_body: templateBody,
        variables_schema: variablesSchema,
        model_hints: modelHints,
        route_preferences: routePreferences,
        publish: versionForm.publish,
      });
      pushToast({ tone: 'success', title: 'Version created', message: `Created v${created.version}.` });
      detail.refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Create version failed', message: error?.message || 'Failed to create version.' });
    } finally {
      setCreatingVersion(false);
    }
  };

  const handlePublishVersion = async (version: number) => {
    setPublishingVersion(version);
    try {
      await promptRegistry.publishVersion(template.template_key, version);
      pushToast({ tone: 'success', title: 'Version published', message: `Published v${version}.` });
      detail.refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Publish failed', message: error?.message || 'Failed to publish version.' });
    } finally {
      setPublishingVersion(null);
    }
  };

  const handleAssignLabel = async () => {
    const targetVersion = Number(labelForm.version || 0);
    if (!targetVersion) {
      pushToast({ tone: 'error', title: 'Missing version', message: 'Select a version before assigning label.' });
      return;
    }
    setAssigningLabel(true);
    try {
      await promptRegistry.assignLabel(template.template_key, {
        label: labelForm.label.trim(),
        version: targetVersion,
        require_approval: labelForm.require_approval,
        approved_by: labelForm.approved_by.trim() || null,
      });
      pushToast({ tone: 'success', title: 'Label assigned', message: `${labelForm.label} now points to v${targetVersion}.` });
      detail.refetch();
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Assign label failed', message: error?.message || 'Failed to assign label.' });
    } finally {
      setAssigningLabel(false);
    }
  };

  const handleDryRun = async () => {
    setRendering(true);
    try {
      const variables = parseJsonObject(renderForm.variables, 'variables');
      const payload: any = { template_key: template.template_key, variables };
      if (renderForm.version.trim()) payload.version = Number(renderForm.version);
      else payload.label = renderForm.label.trim() || 'production';
      const result = await promptRegistry.dryRunRender(payload);
      setRenderResult(result);
      pushToast({ tone: 'success', title: 'Dry-run successful', message: 'Prompt rendered successfully.' });
    } catch (error: any) {
      setRenderResult(null);
      pushToast({ tone: 'error', title: 'Dry-run failed', message: error?.message || 'Prompt render failed.' });
    } finally {
      setRendering(false);
    }
  };

  const handleDeleteTemplate = async () => {
    setDeletingTemplate(true);
    try {
      await promptRegistry.deleteTemplate(template.template_key);
      pushToast({ tone: 'success', title: 'Template deleted', message: `${template.template_key} was deleted.` });
      navigate('/prompts');
    } catch (error: any) {
      pushToast({ tone: 'error', title: 'Delete failed', message: error?.message || 'Failed to delete template.' });
    } finally {
      setDeletingTemplate(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto p-4 sm:p-6">
      <button onClick={() => navigate('/prompts')} className="mb-5 flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 transition-colors">
        <ArrowLeft className="w-4 h-4" /> Back to Prompt Registry
      </button>

      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{template.name}</h1>
          <p className="mt-1 text-sm text-gray-500">
            Template key: <code className="rounded bg-gray-100 px-1.5 py-0.5">{template.template_key}</code>
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Versions</div>
            <div className="mt-1 text-lg font-semibold text-slate-900">{versions.length}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Labels</div>
            <div className="mt-1 text-lg font-semibold text-slate-900">{labels.length}</div>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Published</div>
            <div className="mt-1 text-sm font-semibold text-slate-900">
              {versions.find((version) => version.status === 'published') ? 'Version ready' : 'Not yet'}
            </div>
          </div>
        </div>
      </div>

      <JourneyChecklist
        title="Rollout Progress"
        description="Prompt setup is simplest when you author first, register a stable version second, and test before any consuming page binds it."
        steps={checklistSteps}
      />

      <div className="mt-5 space-y-5">
        <Card
          title="1. Template"
          action={
            <button
              onClick={() => setConfirmDeleteTemplate(true)}
              className="inline-flex items-center gap-2 rounded-lg border border-red-200 px-3 py-2 text-sm text-red-700 hover:bg-red-50"
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </button>
          }
        >
          <div className="space-y-4">
            <div>
              <h4 className="text-sm font-semibold text-slate-900">Define the prompt shell</h4>
              <p className="mt-1 text-xs text-slate-500">Keep metadata concise. Most work happens in versions and rollout, not here.</p>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Name</label>
                <input
                  value={templateForm.name}
                  onChange={(event) => setTemplateForm({ ...templateForm, name: event.target.value })}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div className="lg:col-span-2">
                <label className="mb-1 block text-sm font-medium text-gray-700">Description</label>
                <textarea
                  value={templateForm.description}
                  onChange={(event) => setTemplateForm({ ...templateForm, description: event.target.value })}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>

            <details className="rounded-xl border border-slate-200 px-3 py-3">
              <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900">Advanced metadata</summary>
              <div className="mt-4">
                <label className="mb-1 block text-sm font-medium text-gray-700">Owner Scope</label>
                <input
                  value={templateForm.owner_scope}
                  onChange={(event) => setTemplateForm({ ...templateForm, owner_scope: event.target.value })}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </details>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleUpdateTemplate}
                disabled={savingTemplate}
                className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {savingTemplate ? 'Saving...' : 'Save Template'}
              </button>
            </div>
          </div>
        </Card>

        <PromptVersionComposerCard value={versionForm} creating={creatingVersion} onChange={setVersionForm} onCreate={handleCreateVersion} />

        <PromptRolloutCard
          versions={versions}
          labels={labels}
          labelForm={labelForm}
          assigningLabel={assigningLabel}
          onLabelFormChange={setLabelForm}
          onAssignLabel={handleAssignLabel}
        />

        <PromptTestingCard
          hasVersions={versions.length > 0}
          renderForm={renderForm}
          renderResult={renderResult}
          rendering={rendering}
          onRenderFormChange={setRenderForm}
          onDryRun={handleDryRun}
        />

        <PromptHistoryCard
          versions={versions}
          diffLeftVersion={diffLeftVersion}
          diffRightVersion={diffRightVersion}
          publishingVersion={publishingVersion}
          onDiffLeftChange={setDiffLeftVersion}
          onDiffRightChange={setDiffRightVersion}
          onPublishVersion={handlePublishVersion}
        />
      </div>

      <ConfirmDialog
        open={confirmDeleteTemplate}
        title="Delete prompt template"
        description={`Delete "${template.template_key}"? This removes all versions, labels, and render logs. Consumer pages using this prompt will stop resolving it.`}
        confirmLabel="Delete Template"
        destructive
        confirming={deletingTemplate}
        onConfirm={handleDeleteTemplate}
        onClose={() => {
          if (!deletingTemplate) setConfirmDeleteTemplate(false);
        }}
      />
    </div>
  );
}
