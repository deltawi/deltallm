import { AlertTriangle, RefreshCw, Send } from 'lucide-react';
import Modal from '../Modal';
import type { TierActivationChangeGroup, TierActivationPreview } from '../../lib/api';

type TierActivationDialogProps = {
  open: boolean;
  preview: TierActivationPreview | null;
  loading: boolean;
  activating: boolean;
  error: string | null;
  onClose: () => void;
  onRefresh: () => void;
  onActivate: () => void;
};

export default function TierActivationDialog({
  open,
  preview,
  loading,
  activating,
  error,
  onClose,
  onRefresh,
  onActivate,
}: TierActivationDialogProps) {
  return (
    <Modal open={open} onClose={activating ? () => undefined : onClose} title="Review & activate" wide>
      {loading ? (
        <div className="py-12 text-center">
          <div className="mx-auto h-7 w-7 animate-spin rounded-full border-b-2 border-brand-primary" />
          <p className="mt-3 text-sm text-gray-500">Building a current comparison…</p>
        </div>
      ) : preview ? (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <ImpactStat label="Draft" value={`v${preview.draft.version_number}`} />
            <ImpactStat label="Current live" value={preview.current_active_version ? `v${preview.current_active_version.version_number}` : 'None'} />
            <ImpactStat label="Organizations" value={String(preview.affected_organization_count)} />
            <ImpactStat label="Assignments" value={String(preview.affected_assignment_count)} />
          </div>

          {preview.blockers.length > 0 ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-800" role="alert">
              <p className="font-semibold">Activation is blocked</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-xs">
                {preview.blockers.map((blocker) => <li key={blocker.code}>{blocker.message}</li>)}
              </ul>
            </div>
          ) : null}
          {preview.warnings.map((warning) => (
            <div key={warning.code} className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{warning.message}</span>
            </div>
          ))}

          <section>
            <h3 className="text-sm font-semibold text-gray-900">Configuration changes</h3>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              <ChangeList title="Models added" change={preview.changes.policy_added} tone="green" />
              <ChangeList title="Models removed" change={preview.changes.policy_removed} tone="red" />
              <ChangeList title="Models changed" change={preview.changes.policy_changed} tone="blue" />
              <ChangeList title="Pools added" change={preview.changes.pool_added} tone="green" />
              <ChangeList title="Pools removed" change={preview.changes.pool_removed} tone="red" />
              <ChangeList title="Pools changed" change={preview.changes.pool_changed} tone="blue" />
            </div>
          </section>

          {error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</div>
          ) : null}

          <div className="flex flex-col-reverse gap-2 border-t border-gray-100 pt-4 sm:flex-row sm:justify-between">
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading || activating}
              className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw aria-hidden="true" className="h-4 w-4" />
              Refresh preview
            </button>
            <div className="flex gap-2">
              <button type="button" onClick={onClose} disabled={activating} className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50">Cancel</button>
              <button
                type="button"
                data-autofocus="true"
                onClick={onActivate}
                disabled={!preview.can_activate || activating}
                className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
              >
                <Send aria-hidden="true" className="h-4 w-4" />
                {activating ? 'Activating…' : `Activate v${preview.draft.version_number}`}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="space-y-3 py-6 text-center">
          <p className="text-sm text-gray-600">{error || 'Activation preview is unavailable.'}</p>
          <button type="button" onClick={onRefresh} className="rounded-lg bg-brand-primary px-3 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover">Try again</button>
        </div>
      )}
    </Modal>
  );
}

function ImpactStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-0.5 text-sm font-semibold text-gray-900">{value}</p>
    </div>
  );
}

function ChangeList({
  title,
  change,
  tone,
}: {
  title: string;
  change: TierActivationChangeGroup;
  tone: 'green' | 'red' | 'blue';
}) {
  const toneClass = tone === 'green'
    ? 'border-emerald-100 bg-emerald-50'
    : tone === 'red'
      ? 'border-red-100 bg-red-50'
      : 'border-blue-100 bg-blue-50';
  return (
    <div className={`rounded-lg border px-3 py-2 ${toneClass}`}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-gray-800">{title}</p>
        <span className="text-xs font-bold text-gray-700">{change.count}</span>
      </div>
      {change.items.length > 0 ? (
        <ul className="mt-1 space-y-0.5 text-[11px] text-gray-600">
          {change.items.map((item) => <li key={item} className="truncate" title={item}>{item}</li>)}
          {change.truncated ? <li>More changes are not shown…</li> : null}
        </ul>
      ) : <p className="mt-1 text-[11px] text-gray-400">No changes</p>}
    </div>
  );
}
