import { useState, type ComponentProps } from 'react';
import { Trash2 } from 'lucide-react';
import type { PromptTemplate } from '../../lib/api';
import Button from '../Button';
import ManagedAssetAccessPanel from '../ManagedAssetAccessPanel';

export interface PromptTemplateDetailsForm {
  name: string;
  description: string;
  owner_scope: string;
}

type AccessPanelProps = Pick<ComponentProps<typeof ManagedAssetAccessPanel>, 'teamOptions' | 'organizationOptions' | 'audiencesLoading' | 'audiencesError' | 'onRetryAudiences'>;

interface PromptSettingsTabProps extends AccessPanelProps {
  template: PromptTemplate;
  canWrite: boolean;
  canDelete: boolean;
  isPlatformAdmin: boolean;
  bindingCount: number;
  saving: boolean;
  onSave: (form: PromptTemplateDetailsForm) => void;
  onAccessSaved: () => void;
  onDelete: () => void;
}

function formFromTemplate(template: PromptTemplate): PromptTemplateDetailsForm {
  return { name: template.name || '', description: template.description || '', owner_scope: template.owner_scope || '' };
}

const inputClass = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-50 disabled:text-gray-500';

export default function PromptSettingsTab({
  template,
  canWrite,
  canDelete,
  isPlatformAdmin,
  bindingCount,
  saving,
  onSave,
  onAccessSaved,
  onDelete,
  ...accessProps
}: PromptSettingsTabProps) {
  // The page remounts this tab (by key) when the saved details change, so the form starts from them.
  const [form, setForm] = useState(() => formFromTemplate(template));
  const dirty = JSON.stringify(form) !== JSON.stringify(formFromTemplate(template));
  const nameError = form.name.trim() ? null : 'Enter a name.';

  return (
    <div className="grid gap-4">
      <section className="rounded-xl border border-gray-200 bg-white shadow-sm" aria-labelledby="prompt-settings-details">
        <div className="flex items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
          <h2 id="prompt-settings-details" className="text-sm font-semibold text-gray-900">Details</h2>
          {canWrite ? (
            <Button size="sm" loading={saving} disabled={!dirty || Boolean(nameError)} onClick={() => onSave(form)}>{saving ? 'Saving…' : 'Save details'}</Button>
          ) : <span className="text-xs text-gray-500">Read only</span>}
        </div>
        <div className="grid gap-4 p-4 lg:grid-cols-3">
          <div>
            <label htmlFor="prompt-settings-name" className="mb-1 block text-sm font-medium text-gray-700">Name</label>
            <input id="prompt-settings-name" value={form.name} disabled={!canWrite} aria-invalid={Boolean(nameError)} aria-describedby={nameError ? 'prompt-settings-name-error' : undefined} onChange={(event) => setForm({ ...form, name: event.target.value })} className={inputClass} />
            {nameError ? <p id="prompt-settings-name-error" className="mt-1 text-xs text-red-600">{nameError}</p> : null}
          </div>
          <div className="lg:col-span-2">
            <label htmlFor="prompt-settings-description" className="mb-1 block text-sm font-medium text-gray-700">Description</label>
            <textarea id="prompt-settings-description" value={form.description} disabled={!canWrite} rows={2} onChange={(event) => setForm({ ...form, description: event.target.value })} className={inputClass} />
          </div>
          <p className="text-xs text-gray-500 lg:col-span-3">
            The template key <code className="rounded bg-gray-100 px-1">{template.template_key}</code> cannot change. Bindings and API calls use it.
          </p>
          {isPlatformAdmin ? (
            <details className="rounded-lg border border-gray-200 px-3 py-2 lg:col-span-3">
              <summary className="cursor-pointer text-sm font-medium text-gray-700">Legacy metadata</summary>
              <div className="mt-3 max-w-md">
                <label htmlFor="prompt-settings-owner-scope" className="mb-1 block text-sm font-medium text-gray-700">Owner scope</label>
                <input id="prompt-settings-owner-scope" value={form.owner_scope} disabled={!canWrite} onChange={(event) => setForm({ ...form, owner_scope: event.target.value })} className={inputClass} />
              </div>
            </details>
          ) : null}
        </div>
      </section>

      {template.access ? (
        <ManagedAssetAccessPanel
          access={template.access}
          assetLabel="Prompt"
          allowPublic={isPlatformAdmin}
          onSaved={onAccessSaved}
          {...accessProps}
        />
      ) : null}

      {canDelete ? (
        <section className="rounded-xl border border-red-200 bg-white shadow-sm" aria-labelledby="prompt-settings-delete">
          <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div>
              <h2 id="prompt-settings-delete" className="text-sm font-semibold text-gray-900">Delete prompt</h2>
              <p className="mt-0.5 text-xs text-gray-500">
                Removes all versions, labels, and render logs.
                {bindingCount > 0 ? ` ${bindingCount} ${bindingCount === 1 ? 'binding stops' : 'bindings stop'} resolving it.` : ''}
              </p>
            </div>
            <Button variant="danger" size="sm" onClick={onDelete}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Delete prompt
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
