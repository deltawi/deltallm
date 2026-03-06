import Card from '../Card';

interface RenderForm {
  label: string;
  version: string;
  variables: string;
}

interface PromptTestingCardProps {
  hasVersions: boolean;
  renderForm: RenderForm;
  renderResult: any;
  rendering: boolean;
  onRenderFormChange: (next: RenderForm) => void;
  onDryRun: () => void;
}

export default function PromptTestingCard({
  hasVersions,
  renderForm,
  renderResult,
  rendering,
  onRenderFormChange,
  onDryRun,
}: PromptTestingCardProps) {
  return (
    <Card title="4. Test">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Validate the rendered output before binding this prompt anywhere</h4>
          <p className="mt-1 text-xs text-slate-500">Use a dry-run to confirm variable requirements and inspect the final rendered prompt.</p>
        </div>

        <div className="space-y-3 rounded-xl border border-slate-200 p-4">
          <div>
            <div className="text-sm font-semibold text-slate-900">Dry-run render</div>
            <div className="mt-1 text-xs text-slate-500">Use a label or explicit version to validate variables and inspect the rendered result.</div>
          </div>
          {!hasVersions && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">
              Create at least one version before running a render test.
            </div>
          )}
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <input
              value={renderForm.label}
              onChange={(event) => onRenderFormChange({ ...renderForm, label: event.target.value, version: '' })}
              placeholder="label (production)"
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
            <input
              value={renderForm.version}
              onChange={(event) => onRenderFormChange({ ...renderForm, version: event.target.value })}
              placeholder="version (optional)"
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
          </div>
          <textarea
            value={renderForm.variables}
            onChange={(event) => onRenderFormChange({ ...renderForm, variables: event.target.value })}
            className="h-40 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs font-mono"
          />
          <button
            type="button"
            onClick={onDryRun}
            disabled={rendering || !hasVersions}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {rendering ? 'Rendering...' : 'Run Render Test'}
          </button>
          {renderResult && (
            <pre className="max-h-72 overflow-auto rounded-xl border border-gray-200 bg-gray-50 p-2 text-xs">
              {JSON.stringify(renderResult, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </Card>
  );
}
