import { useState } from 'react';
import type { PromptLabel, PromptVersion } from '../../lib/api';
import { fmtLocalDay } from '../../lib/format';
import { labelsForVersion, resolveReference, versionVariables, type PromptReference } from '../../lib/promptVersions';
import Button from '../Button';
import PromptBodyView from './PromptBodyView';
import PromptReferenceSelect from './PromptReferenceSelect';
import PromptLabelTags from './PromptLabelTags';

interface PromptOverviewTabProps {
  versions: PromptVersion[];
  labels: PromptLabel[];
  reference: PromptReference;
  onReferenceChange: (reference: PromptReference) => void;
  onOpenVersion: (version: number) => void;
}

function JsonSetting({ label, value, empty }: { label: string; value: Record<string, unknown> | null; empty: string }) {
  const hasValue = value && Object.keys(value).length > 0;
  return (
    <div>
      <dt className="text-xs font-medium text-gray-500">{label}</dt>
      <dd className="mt-1">
        {hasValue ? (
          <pre className="overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-2 font-mono text-xs text-gray-700">{JSON.stringify(value, null, 2)}</pre>
        ) : <span className="text-xs text-gray-500">{empty}</span>}
      </dd>
    </div>
  );
}

export default function PromptOverviewTab({ versions, labels, reference, onReferenceChange, onOpenVersion }: PromptOverviewTabProps) {
  const [showJson, setShowJson] = useState(false);
  const version = resolveReference(reference, versions, labels);
  if (!version) {
    return <p className="rounded-xl border border-dashed border-gray-300 bg-white px-4 py-6 text-sm text-gray-500">This label does not point to a version that still exists.</p>;
  }
  const variables = versionVariables(version);
  const versionLabels = labelsForVersion(labels, version.version);

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <section className="min-w-0 rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-gray-900">Prompt</h2>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="prompt-view-select" className="text-xs text-gray-500">Showing</label>
            <PromptReferenceSelect id="prompt-view-select" label="Label or version to show" value={reference} labels={labels} versions={versions} onChange={onReferenceChange} />
            <Button variant="outline" size="sm" aria-pressed={showJson} onClick={() => setShowJson((current) => !current)}>
              {showJson ? 'Show messages' : 'Show JSON'}
            </Button>
          </div>
        </div>
        <div className="space-y-4 p-4">
          <PromptBodyView body={version.template_body} showJson={showJson} />
          <details className="rounded-lg border border-gray-200 px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium text-gray-700">Model hints and route preferences</summary>
            <dl className="mt-3 grid gap-3 sm:grid-cols-2">
              <JsonSetting label="Model hints" value={version.model_hints} empty="None." />
              <JsonSetting label="Route preferences" value={version.route_preferences} empty="None. Any deployment that the binding allows can serve this prompt." />
            </dl>
          </details>
        </div>
      </section>

      <div className="grid gap-4">
        <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-900">Variables</h2>
            <span className="text-xs text-gray-500">{variables.filter((item) => item.required).length} required</span>
          </div>
          <ul className="space-y-2 p-4">
            {variables.length === 0 ? <li className="text-xs text-gray-500">This version uses no variables.</li> : variables.map((variable) => (
              <li key={variable.name} className="flex items-center justify-between gap-2 text-xs">
                <code className="truncate rounded bg-brand-primary-soft px-1.5 py-0.5 font-medium text-brand-primary-ink">{variable.name}</code>
                <span className="text-gray-500">{variable.type}{variable.required ? '' : ' · optional'}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-gray-900">Version v{version.version}</h2>
            <PromptLabelTags labels={versionLabels} />
          </div>
          <div className="space-y-3 p-4">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
              <dt className="text-gray-500">Created</dt>
              <dd className="text-gray-900">{fmtLocalDay(version.created_at)}</dd>
              <dt className="text-gray-500">Labels</dt>
              <dd className="text-gray-900">{versionLabels.length ? versionLabels.join(', ') : 'None'}</dd>
            </dl>
            <Button variant="outline" size="sm" onClick={() => onOpenVersion(version.version)}>Open in Versions</Button>
          </div>
        </section>
      </div>
    </div>
  );
}
