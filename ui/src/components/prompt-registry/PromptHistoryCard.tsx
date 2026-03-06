import Card from '../Card';
import type { PromptVersion } from '../../lib/api';

function versionBody(version: PromptVersion | undefined): string {
  if (!version) return '';
  return JSON.stringify(version.template_body || {}, null, 2);
}

interface PromptHistoryCardProps {
  versions: PromptVersion[];
  diffLeftVersion: string;
  diffRightVersion: string;
  publishingVersion: number | null;
  onDiffLeftChange: (value: string) => void;
  onDiffRightChange: (value: string) => void;
  onPublishVersion: (version: number) => void;
}

export default function PromptHistoryCard({
  versions,
  diffLeftVersion,
  diffRightVersion,
  publishingVersion,
  onDiffLeftChange,
  onDiffRightChange,
  onPublishVersion,
}: PromptHistoryCardProps) {
  const leftVersion = versions.find((version) => String(version.version) === diffLeftVersion);
  const rightVersion = versions.find((version) => String(version.version) === diffRightVersion);

  const diffSummary = (() => {
    const left = versionBody(leftVersion).split('\n');
    const right = versionBody(rightVersion).split('\n');
    if (!leftVersion || !rightVersion) return null;
    const max = Math.max(left.length, right.length);
    let changed = 0;
    for (let index = 0; index < max; index += 1) {
      if (left[index] !== right[index]) changed += 1;
    }
    return { changedLines: changed, totalLeft: left.length, totalRight: right.length };
  })();

  return (
    <Card title="5. History">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Inspect and compare immutable versions</h4>
          <p className="mt-1 text-xs text-slate-500">History is intentionally separate from authoring so routine creation and rollout stay focused.</p>
        </div>

        {versions.length === 0 && (
          <div className="rounded-xl border border-dashed border-slate-300 px-3 py-4 text-sm text-slate-500">
            No versions exist yet. Create the first version above, then return here to compare or republish historical versions.
          </div>
        )}

        <div className="overflow-auto rounded-xl border border-gray-100">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Version</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Status</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Published By</th>
                <th className="px-3 py-2 text-left font-medium text-gray-600">Actions</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((version) => (
                <tr key={version.prompt_version_id} className="border-t border-gray-100">
                  <td className="px-3 py-2">v{version.version}</td>
                  <td className="px-3 py-2">
                    <span className={`rounded px-2 py-0.5 text-xs ${version.status === 'published' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'}`}>
                      {version.status}
                    </span>
                  </td>
                  <td className="px-3 py-2">{version.published_by || '—'}</td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => onPublishVersion(version.version)}
                      disabled={publishingVersion === version.version}
                      className="rounded border border-gray-200 px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-50"
                    >
                      {publishingVersion === version.version ? 'Publishing...' : 'Publish'}
                    </button>
                  </td>
                </tr>
              ))}
              {versions.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-sm text-gray-400">
                    No versions yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <select value={diffLeftVersion} onChange={(event) => onDiffLeftChange(event.target.value)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm">
            <option value="">Select left version</option>
            {versions.map((version) => (
              <option key={version.prompt_version_id} value={version.version}>
                v{version.version}
              </option>
            ))}
          </select>
          <select value={diffRightVersion} onChange={(event) => onDiffRightChange(event.target.value)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm">
            <option value="">Select right version</option>
            {versions.map((version) => (
              <option key={version.prompt_version_id} value={version.version}>
                v{version.version}
              </option>
            ))}
          </select>
        </div>

        {diffSummary && (
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-2 text-xs text-gray-600">
            Changed lines: {diffSummary.changedLines} (left {diffSummary.totalLeft}, right {diffSummary.totalRight})
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <pre className="max-h-64 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-2 text-xs">{versionBody(leftVersion)}</pre>
          <pre className="max-h-64 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-2 text-xs">{versionBody(rightVersion)}</pre>
        </div>
      </div>
    </Card>
  );
}
