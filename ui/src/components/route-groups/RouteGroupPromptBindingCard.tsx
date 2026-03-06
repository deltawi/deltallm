import { Trash2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import Card from '../Card';
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
    <Card title="Prompt Binding">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Attach a prompt to this group only when you need one</h4>
          <p className="mt-1 text-xs text-slate-500">
            Prompt content is created in Prompt Registry. This page controls whether this group should resolve one automatically.
          </p>
        </div>

        {!hasTemplates && !loadingTemplates ? (
          <div className="rounded-xl border border-dashed border-slate-300 px-4 py-4 text-sm text-slate-600">
            <div>No prompts are registered yet.</div>
            <div className="mt-1 text-xs text-slate-500">Create a prompt first, then return here to bind it to this group.</div>
            <Link
              to="/prompts"
              className="mt-3 inline-flex rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
            >
              + New Prompt
            </Link>
          </div>
        ) : (
          <>
            <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Prompt</label>
                <select
                  value={bindingForm.template_key}
                  onChange={(event) => onBindingFormChange({ ...bindingForm, template_key: event.target.value })}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                >
                  <option value="">{loadingTemplates ? 'Loading prompts...' : 'Select a prompt'}</option>
                  {templates.map((template) => (
                    <option key={template.prompt_template_id} value={template.template_key}>
                      {template.name} ({template.template_key})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Label</label>
                <input
                  value={bindingForm.label}
                  onChange={(event) => onBindingFormChange({ ...bindingForm, label: event.target.value })}
                  placeholder="production"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Priority</label>
                <input
                  value={bindingForm.priority}
                  onChange={(event) => onBindingFormChange({ ...bindingForm, priority: event.target.value })}
                  placeholder="100"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                />
              </div>
            </div>

            <label className="flex items-start gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={bindingForm.enabled}
                onChange={(event) => onBindingFormChange({ ...bindingForm, enabled: event.target.checked })}
                className="mt-0.5 rounded border-gray-300"
              />
              <span>
                Binding is active
                <span className="block text-xs text-gray-500">Turn this off to keep the binding record without resolving it for live requests.</span>
              </span>
            </label>

            <div className="flex justify-end">
              <button
                type="button"
                onClick={onSaveBinding}
                disabled={savingBinding || !bindingForm.template_key.trim()}
                className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {savingBinding ? 'Saving...' : 'Bind Prompt'}
              </button>
            </div>
          </>
        )}

        <div className="overflow-auto rounded-xl border border-gray-100">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Prompt</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Label</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Priority</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Status</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600" />
              </tr>
            </thead>
            <tbody>
              {bindings.map((binding) => (
                <tr key={binding.prompt_binding_id} className="border-t border-gray-100">
                  <td className="px-3 py-2">
                    <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs">{binding.template_key}</code>
                  </td>
                  <td className="px-3 py-2">{binding.label}</td>
                  <td className="px-3 py-2">{binding.priority}</td>
                  <td className="px-3 py-2">{binding.enabled ? 'Active' : 'Off'}</td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => onDeleteBinding(binding)}
                      disabled={deletingBinding === binding.prompt_binding_id}
                      className="rounded-lg p-1.5 hover:bg-red-50 disabled:opacity-50"
                    >
                      <Trash2 className="h-4 w-4 text-red-500" />
                    </button>
                  </td>
                </tr>
              ))}
              {bindings.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-center text-sm text-gray-400">
                    No prompt bound to this group yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
