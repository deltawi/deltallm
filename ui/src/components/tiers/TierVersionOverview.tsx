import { Copy, FilePlus2, Send } from 'lucide-react';
import type { TierVersion, TierVersionDetail } from '../../lib/api';
import { formatDateTime, versionLabel } from '../../lib/tiers';
import StatusBadge from '../StatusBadge';

type TierVersionOverviewProps = {
  versions: TierVersion[];
  selectedVersion: TierVersion | null;
  activeVersion: TierVersion | null;
  versionDetail: TierVersionDetail | null | undefined;
  versionPending?: boolean;
  busyAction: string | null;
  onCreateDraft: () => void;
  onCloneActive: () => void;
  onSelectEditor: (versionId: string) => void;
  onArchive: (version: TierVersion) => void;
  onPublish: () => void;
};

export default function TierVersionOverview({
  versions,
  selectedVersion,
  activeVersion,
  versionDetail,
  versionPending = false,
  busyAction,
  onCreateDraft,
  onCloneActive,
  onSelectEditor,
  onArchive,
  onPublish,
}: TierVersionOverviewProps) {
  const actionBusy = busyAction !== null;

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <section className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-gray-900">Versions</h3>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onCloneActive}
              disabled={!activeVersion || actionBusy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-40"
            >
              <Copy className="h-3.5 w-3.5" />
              Clone active
            </button>
            <button
              type="button"
              onClick={onCreateDraft}
              disabled={actionBusy}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-1.5 text-xs font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
            >
              <FilePlus2 className="h-3.5 w-3.5" />
              New draft
            </button>
          </div>
        </div>
        <div className="divide-y divide-gray-100">
          {versions.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-gray-400">No versions yet.</p>
          ) : versions.map((version) => (
            <div
              key={version.tier_version_id}
              className="flex items-center justify-between gap-3 hover:bg-gray-50"
            >
              <button
                type="button"
                onClick={() => onSelectEditor(version.tier_version_id)}
                disabled={actionBusy}
                className="min-w-0 flex-1 px-4 py-3 text-left disabled:cursor-default disabled:opacity-60"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold text-gray-900">Version {version.version_number}</p>
                    <StatusBadge status={version.status} />
                  </div>
                  <p className="mt-1 text-xs text-gray-500">
                    {version.model_policy_count} policies · {version.capacity_pool_count} pools · updated {formatDateTime(version.updated_at)}
                  </p>
                </div>
              </button>
              {version.status !== 'archived' ? (
                <div className="pr-4">
                  <button
                    type="button"
                    onClick={() => onArchive(version)}
                    disabled={actionBusy}
                    className="rounded border border-gray-200 px-2.5 py-1 text-xs font-semibold text-gray-600 hover:bg-white disabled:opacity-40"
                  >
                    Archive
                  </button>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <aside className="rounded-xl border border-gray-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-gray-900">Publish Summary</h3>
        <div className="mt-3 space-y-3 text-sm">
          <SummaryRow label="Selected version" value={versionLabel(selectedVersion)} />
          <SummaryRow label="Model policies" value={String(versionDetail?.model_policies?.length || selectedVersion?.model_policy_count || 0)} />
          <SummaryRow label="Capacity pools" value={String(versionDetail?.capacity_pools?.length || selectedVersion?.capacity_pool_count || 0)} />
          <SummaryRow label="Assignments on version" value={String(selectedVersion?.assignment_count || 0)} />
        </div>
        <button
          type="button"
          onClick={onPublish}
          disabled={!selectedVersion || selectedVersion.status !== 'draft' || versionPending || actionBusy}
          className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
        >
          <Send className="h-4 w-4" />
          Publish selected draft
        </button>
        {selectedVersion?.status !== 'draft' ? (
          <p className="mt-2 text-xs text-gray-400">Only draft versions can be published.</p>
        ) : null}
      </aside>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-gray-100 pb-2 last:border-b-0">
      <span className="text-xs text-gray-500">{label}</span>
      <span className="text-xs font-semibold text-gray-900">{value}</span>
    </div>
  );
}
