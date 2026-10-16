import Button from '../Button';
import SideDrawer from '../SideDrawer';
import { jsonLineDiff, type PolicyChange } from '../../lib/routeGroupPolicyDiff';
import type { DraftIssue } from '../../lib/routeGroupSummary';
import PolicyPublishControl from './PolicyPublishControl';

export function JsonDiff({ before, after, label }: { before: unknown; after: unknown; label: string }) {
  const lines = jsonLineDiff(before ?? {}, after);
  return (
    <pre aria-label={label} className="max-h-80 overflow-auto rounded-lg bg-gray-950 p-3 font-mono text-xs leading-5">
      {lines.map((line, index) => (
        <span key={index} className={`block ${line.kind === 'added' ? 'bg-emerald-950 text-emerald-300' : line.kind === 'removed' ? 'bg-red-950 text-red-300' : 'text-gray-400'}`}>
          {line.kind === 'added' ? '+ ' : line.kind === 'removed' ? '- ' : '  '}{line.text}
        </span>
      ))}
    </pre>
  );
}

interface PolicyReviewDrawerProps {
  title: string;
  changes: PolicyChange[];
  issues: DraftIssue[];
  policy: Record<string, unknown>;
  livePolicy: Record<string, unknown> | null;
  dryRun: string;
  busy: boolean;
  onPublish: () => void;
  onClose: () => void;
}

// The last step before publishing: what changes in plain words, what to watch, and the JSON diff.
export default function PolicyReviewDrawer({ title, changes, issues, policy, livePolicy, dryRun, busy, onPublish, onClose }: PolicyReviewDrawerProps) {
  const blocking = issues.filter((issue) => issue.level === 'error');
  const warnings = issues.filter((issue) => issue.level === 'warning');
  return (
    <SideDrawer
      title={title}
      description="New requests use it within a few seconds. The live version stays in history."
      saving={busy}
      onClose={onClose}
      size="md"
      overlayLabel="Close review"
      footer={(
        <>
          <Button size="sm" variant="ghost" disabled={busy} onClick={onClose}>Keep editing</Button>
          <PolicyPublishControl policy={policy} activePolicy={livePolicy} busy={busy} disabled={blocking.length > 0} onPublish={onPublish} />
        </>
      )}
    >
      <div className="space-y-4 text-sm">
        {blocking.length > 0 ? (
          <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            <strong>Fix this first.</strong> {blocking.map((issue) => issue.text).join(' ')}
          </p>
        ) : null}
        <div>
          <h3 className="text-xs font-semibold text-gray-900">What changes</h3>
          {changes.length === 0 ? <p className="mt-2 text-xs text-gray-500">The draft has the same settings as the live version.</p> : (
            <dl className="mt-2 divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white">
              {changes.map((change, index) => (
                <div key={`${change.label}-${index}`} className="grid gap-1 px-3 py-2 text-xs sm:grid-cols-[9rem_1fr]">
                  <dt className="font-medium text-gray-700">{change.label}</dt>
                  <dd className="text-gray-600"><span className="text-gray-500 line-through decoration-gray-300">{change.before}</span> → <span className="font-medium text-gray-900">{change.after}</span></dd>
                </div>
              ))}
            </dl>
          )}
        </div>
        {warnings.map((issue, index) => (
          <p key={index} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{issue.text}</p>
        ))}
        <p className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600">{dryRun}</p>
        <details className="text-xs text-gray-600">
          <summary className="cursor-pointer select-none">Show JSON diff</summary>
          <div className="mt-2"><JsonDiff before={livePolicy} after={policy} label="JSON changes from live to draft" /></div>
        </details>
      </div>
    </SideDrawer>
  );
}
