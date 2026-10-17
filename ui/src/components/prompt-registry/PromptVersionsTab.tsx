import type { PromptLabel, PromptVersion } from '../../lib/api';
import { fmtLocalDay } from '../../lib/format';
import { labelsForVersion, versionVariables } from '../../lib/promptVersions';
import Button from '../Button';
import PromptBodyView from './PromptBodyView';
import PromptLabelTags from './PromptLabelTags';

interface PromptVersionsTabProps {
  versions: PromptVersion[];
  labels: PromptLabel[];
  selected: number;
  compareWith: number | null;
  canWrite: boolean;
  onSelect: (version: number) => void;
  onCompareChange: (version: number | null) => void;
  onPointLabel: (version: number) => void;
  onNewFrom: (version: number) => void;
  onTest: (version: number) => void;
}

export default function PromptVersionsTab({
  versions,
  labels,
  selected,
  compareWith,
  canWrite,
  onSelect,
  onCompareChange,
  onPointLabel,
  onNewFrom,
  onTest,
}: PromptVersionsTabProps) {
  const current = versions.find((item) => item.version === selected) ?? versions[0];
  if (!current) return null;
  const other = compareWith === null ? null : versions.find((item) => item.version === compareWith && item.version !== current.version) ?? null;
  const currentVariables = versionVariables(current).map((item) => item.name);
  const otherVariables = other ? versionVariables(other).map((item) => item.name) : [];
  const added = other ? currentVariables.filter((name) => !otherVariables.includes(name)) : [];
  const dropped = other ? otherVariables.filter((name) => !currentVariables.includes(name)) : [];

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <section className="min-w-0 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm" aria-label="All versions">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs text-gray-500">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">Version</th>
                <th scope="col" className="px-4 py-2 font-medium">Labels</th>
                <th scope="col" className="px-4 py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((version) => {
                const isSelected = version.version === current.version;
                return (
                  <tr
                    key={version.prompt_version_id}
                    aria-selected={isSelected}
                    onClick={() => onSelect(version.version)}
                    className={`cursor-pointer border-t border-gray-100 ${isSelected ? 'bg-brand-primary-soft' : 'hover:bg-gray-50'}`}
                  >
                    <td className="px-4 py-2.5">
                      <button
                        type="button"
                        onClick={(event) => { event.stopPropagation(); onSelect(version.version); }}
                        aria-current={isSelected ? 'true' : undefined}
                        className="rounded font-mono font-semibold text-gray-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary"
                      >
                        v{version.version}
                      </button>
                    </td>
                    <td className="px-4 py-2.5"><PromptLabelTags labels={labelsForVersion(labels, version.version)} /></td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-xs tabular-nums text-gray-600">{fmtLocalDay(version.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="min-w-0 rounded-xl border border-gray-200 bg-white shadow-sm" aria-label={`Version ${current.version}`}>
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
          <h2 className="font-mono text-sm font-semibold text-gray-900">v{current.version}</h2>
          <div className="flex items-center gap-2">
            <label htmlFor="prompt-compare-select" className="text-xs text-gray-500">Compare with</label>
            <select
              id="prompt-compare-select"
              value={other ? String(other.version) : ''}
              onChange={(event) => onCompareChange(event.target.value ? Number(event.target.value) : null)}
              className="control-field-md rounded-lg border border-gray-300 bg-white px-3 focus:outline-none focus:ring-2 focus:ring-brand-primary"
            >
              <option value="">Nothing</option>
              {versions.filter((item) => item.version !== current.version).map((item) => {
                const itemLabels = labelsForVersion(labels, item.version);
                return <option key={item.prompt_version_id} value={item.version}>v{item.version}{itemLabels.length ? ` (${itemLabels.join(', ')})` : ''}</option>;
              })}
            </select>
          </div>
        </div>
        <div className="space-y-3 p-4">
          <PromptBodyView body={current.template_body} compareBody={other ? other.template_body : undefined} />
          {added.length || dropped.length ? (
            <p className="text-xs text-gray-600">
              {added.length ? <>New variables: <code className="text-brand-primary-ink">{added.join(', ')}</code>. </> : null}
              {dropped.length ? <>No longer used: <code>{dropped.join(', ')}</code>.</> : null}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2 pt-1">
            {canWrite ? <Button variant="outline" size="sm" onClick={() => onPointLabel(current.version)}>Point a label here</Button> : null}
            {canWrite ? <Button variant="outline" size="sm" onClick={() => onNewFrom(current.version)}>New version from v{current.version}</Button> : null}
            <Button variant="outline" size="sm" onClick={() => onTest(current.version)}>Test v{current.version}</Button>
          </div>
        </div>
      </section>
    </div>
  );
}
