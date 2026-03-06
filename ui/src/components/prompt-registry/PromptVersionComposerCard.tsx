import Card from '../Card';

interface PromptVersionForm {
  system_prompt: string;
  variables: string;
  model_hints: string;
  route_preferences: string;
  publish: boolean;
}

interface PromptVersionComposerCardProps {
  value: PromptVersionForm;
  creating: boolean;
  onChange: (next: PromptVersionForm) => void;
  onCreate: () => void;
}

export default function PromptVersionComposerCard({ value, creating, onChange, onCreate }: PromptVersionComposerCardProps) {
  const variables = value.variables
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);

  return (
    <Card title="2. Author Version">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Write the system prompt and name the variables it needs</h4>
          <p className="mt-1 text-xs text-slate-500">Start with plain language. Model hints and route preferences are available only in advanced settings.</p>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">System Prompt</label>
          <textarea
            value={value.system_prompt}
            onChange={(event) => onChange({ ...value, system_prompt: event.target.value })}
            className="h-56 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-slate-500">Use placeholders like <code>{'{product_name}'}</code> inside the prompt body.</p>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Required Variables</label>
          <input
            value={value.variables}
            onChange={(event) => onChange({ ...value, variables: event.target.value })}
            placeholder="product_name, customer_name"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-slate-500">Use comma-separated variable names. The registry will build the validation schema for you.</p>
          {variables.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {variables.map((variable) => (
                <span key={variable} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700">
                  {variable}
                </span>
              ))}
            </div>
          )}
        </div>

        <details className="rounded-xl border border-slate-200 px-3 py-3">
          <summary className="cursor-pointer list-none text-sm font-semibold text-slate-900">Advanced version settings</summary>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Model Hints</label>
              <textarea
                value={value.model_hints}
                onChange={(event) => onChange({ ...value, model_hints: event.target.value })}
                className="h-40 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">Route Preferences</label>
              <textarea
                value={value.route_preferences}
                onChange={(event) => onChange({ ...value, route_preferences: event.target.value })}
                className="h-40 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <p className="mt-3 text-xs text-slate-500">Leave route preferences empty unless you are intentionally narrowing which deployments may serve this prompt.</p>

          <label className="mt-4 flex items-start gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={value.publish}
              onChange={(event) => onChange({ ...value, publish: event.target.checked })}
              className="mt-0.5 rounded border-gray-300"
            />
            <span>
              Publish immediately after creation
              <span className="block text-xs text-gray-500">Use this only when the version is already reviewed and ready to become selectable.</span>
            </span>
          </label>
        </details>

        <div className="flex justify-end">
          <button
            type="button"
            onClick={onCreate}
            disabled={creating}
            className="rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {creating ? 'Creating...' : 'Create Version'}
          </button>
        </div>
      </div>
    </Card>
  );
}
