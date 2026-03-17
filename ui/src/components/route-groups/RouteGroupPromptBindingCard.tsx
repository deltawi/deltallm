import { Tag, Trash2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { PromptBinding, PromptTemplate } from '../../lib/api';

interface BindingFormValues {
  template_key: string;
  label: string;
  priority: string;
  enabled: boolean;
}

interface RouteGroupPromptBindingCardProps {
  bindings: PromptBinding[];
  templates: PromptTemplate[];
  bindingForm: BindingFormValues;
  loadingTemplates: boolean;
  savingBinding: boolean;
  deletingBinding: string | null;
  onBindingFormChange: (next: BindingFormValues) => void;
  onSaveBinding: () => void;
  onDeleteBinding: (binding: PromptBinding) => void;
}

export default function RouteGroupPromptBindingCard({
  bindings,
  templates,
  bindingForm,
  loadingTemplates,
  savingBinding,
  deletingBinding,
  onBindingFormChange,
  onSaveBinding,
  onDeleteBinding,
}: RouteGroupPromptBindingCardProps) {
  const hasTemplates = templates.length > 0;

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-900">Prompt Binding</h3>
        <p className="mt-0.5 text-xs text-gray-500">
          Attach a prompt to this group so the gateway resolves it automatically for every request.
          Prompts are created in{' '}
          <Link to="/prompts" className="text-violet-600 underline hover:text-violet-800">
            Prompt Registry
          </Link>.
        </p>
      </div>

      {/* Active bindings */}
      {bindings.length > 0 && (
        <div className="mb-4 space-y-2">
          {bindings.map((b) => (
            <div
              key={b.prompt_binding_id}
              className="flex items-center justify-between rounded-xl border border-violet-100 bg-violet-50 px-4 py-3"
            >
              <div className="flex items-start gap-2.5">
                <Tag className="mt-0.5 h-4 w-4 shrink-0 text-violet-400" />
                <div>
                  <code className="text-sm font-semibold text-violet-900">{b.template_key}</code>
                  <div className="mt-0.5 text-xs text-violet-600">
                    label: <strong>{b.label}</strong> · priority {b.priority} · {b.enabled ? 'active' : <span className="text-gray-500">off</span>}
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => onDeleteBinding(b)}
                disabled={deletingBinding === b.prompt_binding_id}
                className="rounded-lg p-1.5 text-violet-300 hover:bg-red-50 hover:text-red-400 disabled:opacity-50"
                aria-label="Remove binding"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Empty state / no templates */}
      {!hasTemplates && !loadingTemplates ? (
        <div className="rounded-xl border border-dashed border-gray-200 px-4 py-5 text-sm text-gray-500">
          <div className="font-medium">No prompts registered yet.</div>
          <div className="mt-1 text-xs text-gray-400">Create a prompt first, then return here to bind it.</div>
          <Link
            to="/prompts"
            className="mt-3 inline-flex rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            + New Prompt
          </Link>
        </div>
      ) : (
        /* Add binding form */
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1">
              <label className="mb-1 block text-xs font-medium text-gray-700">Prompt</label>
              <select
                value={bindingForm.template_key}
                onChange={(e) => onBindingFormChange({ ...bindingForm, template_key: e.target.value })}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
              >
                <option value="">{loadingTemplates ? 'Loading…' : 'Select a prompt'}</option>
                {templates.map((t) => (
                  <option key={t.prompt_template_id} value={t.template_key}>
                    {t.name} ({t.template_key})
                  </option>
                ))}
              </select>
            </div>
            <div className="w-28">
              <label className="mb-1 block text-xs font-medium text-gray-700">Label</label>
              <input
                value={bindingForm.label}
                onChange={(e) => onBindingFormChange({ ...bindingForm, label: e.target.value })}
                placeholder="production"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
              />
            </div>
            <div className="w-24">
              <label className="mb-1 block text-xs font-medium text-gray-700">Priority</label>
              <input
                value={bindingForm.priority}
                onChange={(e) => onBindingFormChange({ ...bindingForm, priority: e.target.value })}
                placeholder="100"
                type="number"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
              />
            </div>
            <button
              type="button"
              onClick={onSaveBinding}
              disabled={savingBinding || !bindingForm.template_key.trim()}
              className="rounded-xl bg-violet-600 px-3 py-2 text-sm font-semibold text-white hover:bg-violet-700 disabled:opacity-50"
            >
              {savingBinding ? 'Saving…' : 'Bind'}
            </button>
          </div>

          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={bindingForm.enabled}
              onChange={(e) => onBindingFormChange({ ...bindingForm, enabled: e.target.checked })}
              className="mt-0.5 rounded border-gray-300"
            />
            <span>
              Active
              <span className="block text-xs text-gray-400">Uncheck to keep the binding record without resolving it for live requests.</span>
            </span>
          </label>
        </div>
      )}
    </div>
  );
}
