import { Archive, Copy, FilePlus2, RotateCcw } from 'lucide-react';
import type { Pagination, TierVersion } from '../../lib/api';
import { formatDateTime } from '../../lib/tiers';
import TierVersionBadge from './TierVersionBadge';

type TierVersionRailProps = {
  currentVersions: TierVersion[];
  archivedVersions: TierVersion[];
  archivedPagination?: Pagination | null;
  selectedVersionId: string | null;
  busy: boolean;
  onSelect: (versionId: string) => void;
  onCreateDraft: () => void;
  onClone: (version: TierVersion) => void;
  onArchive: (version: TierVersion) => void;
  onLoadMoreArchived: () => void;
};

export default function TierVersionRail({
  currentVersions,
  archivedVersions,
  archivedPagination,
  selectedVersionId,
  busy,
  onSelect,
  onCreateDraft,
  onClone,
  onArchive,
  onLoadMoreArchived,
}: TierVersionRailProps) {
  const drafts = currentVersions.filter((version) => version.status === 'draft');
  const live = currentVersions.find((version) => version.status === 'active') || null;
  const allVersions = [...currentVersions, ...archivedVersions];
  const selected = allVersions
    .find((version) => version.tier_version_id === selectedVersionId) || null;

  const primaryAction = selected?.status === 'archived'
    ? { label: 'Restore as draft', icon: RotateCcw, action: () => onClone(selected) }
    : drafts.length === 1
      ? { label: `Continue Draft v${drafts[0].version_number}`, icon: FilePlus2, action: () => onSelect(drafts[0].tier_version_id) }
      : drafts.length > 1
        ? {
            label: 'Choose a draft',
            icon: FilePlus2,
            action: () => document.querySelector<HTMLElement>('[data-tier-draft-version="true"]')?.focus(),
          }
        : live
          ? { label: 'Edit live configuration', icon: Copy, action: () => onClone(live) }
          : { label: 'Create draft', icon: FilePlus2, action: onCreateDraft };
  const PrimaryActionIcon = primaryAction?.icon;

  return (
    <aside id="tier-version-rail" className="overflow-hidden rounded-xl border border-gray-200 bg-white lg:sticky lg:top-5 lg:self-start">
      <div className="border-b border-gray-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-gray-900">Versions</h2>
        <p className="mt-0.5 text-xs text-gray-500">Drafts are editable. Live and archived versions are immutable.</p>
        {drafts.length > 1 ? (
          <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs text-amber-900">
            Choose a draft below. There are {drafts.length} attributed drafts, so none was opened automatically.
          </p>
        ) : null}
        {primaryAction && PrimaryActionIcon ? (
          <button
            type="button"
            onClick={primaryAction.action}
            disabled={busy}
            className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-xs font-semibold text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
          >
            <PrimaryActionIcon aria-hidden="true" className="h-3.5 w-3.5" />
            {primaryAction.label}
          </button>
        ) : null}
      </div>

      <VersionSection
        title="Drafts"
        versions={drafts}
        selectedVersionId={selectedVersionId}
        busy={busy}
        onSelect={onSelect}
        allVersions={allVersions}
      />
      <VersionSection
        title="Live"
        versions={live ? [live] : []}
        selectedVersionId={selectedVersionId}
        busy={busy}
        onSelect={onSelect}
        allVersions={allVersions}
      />
      <VersionSection
        title="Archived"
        versions={archivedVersions}
        selectedVersionId={selectedVersionId}
        busy={busy}
        onSelect={onSelect}
        allVersions={allVersions}
        emptyLabel="No archived history"
      />

      {archivedPagination?.has_more ? (
        <div className="border-t border-gray-100 p-3">
          <button
            type="button"
            onClick={onLoadMoreArchived}
            disabled={busy}
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            Load 10 more archived versions
          </button>
        </div>
      ) : null}

      {selected ? (
        <div className="space-y-2 border-t border-gray-100 bg-gray-50 p-3">
          {selected.status === 'archived' ? (
            <button
              type="button"
              onClick={() => onClone(selected)}
              disabled={busy}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-semibold text-brand-primary-ink hover:bg-blue-50 disabled:opacity-50"
            >
              <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
              Restore as draft
            </button>
          ) : (
            <button
              type="button"
              onClick={() => onClone(selected)}
              disabled={busy}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50"
            >
              <Copy aria-hidden="true" className="h-3.5 w-3.5" />
              Clone as new draft
            </button>
          )}
          {selected.status !== 'archived' ? (
            <button
              type="button"
              onClick={() => onArchive(selected)}
              disabled={busy}
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-100 disabled:opacity-50"
            >
              <Archive aria-hidden="true" className="h-3.5 w-3.5" />
              Archive version
            </button>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}

function VersionSection({
  title,
  versions,
  selectedVersionId,
  busy,
  onSelect,
  allVersions,
  emptyLabel = 'None',
}: {
  title: string;
  versions: TierVersion[];
  selectedVersionId: string | null;
  busy: boolean;
  onSelect: (versionId: string) => void;
  allVersions: TierVersion[];
  emptyLabel?: string;
}) {
  return (
    <section className="border-b border-gray-100 last:border-b-0">
      <h3 className="px-4 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-wider text-gray-400">{title}</h3>
      {versions.length === 0 ? (
        <p className="px-4 pb-3 text-xs text-gray-400">{emptyLabel}</p>
      ) : versions.map((version) => {
        const selected = version.tier_version_id === selectedVersionId;
        return (
          <button
            key={version.tier_version_id}
            type="button"
            onClick={() => onSelect(version.tier_version_id)}
            disabled={busy}
            aria-current={selected ? 'true' : undefined}
            data-tier-draft-version={version.status === 'draft' ? 'true' : undefined}
            className={`block w-full border-l-2 px-4 py-3 text-left focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand-primary disabled:opacity-60 ${
              selected ? 'border-brand-primary bg-brand-primary-soft' : 'border-transparent hover:bg-gray-50'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-gray-900">Version {version.version_number}</span>
              <TierVersionBadge status={version.status} versionNumber={version.version_number} />
            </div>
            <p className="mt-1 text-[11px] text-gray-500">
              {version.model_policy_count} models · {version.capacity_pool_count} pools
            </p>
            <p className="mt-0.5 truncate text-[11px] text-gray-400">
              {creatorLabel(version)} · {formatDateTime(version.updated_at)}
            </p>
            {version.source_tier_version_id ? (
              <p className="mt-0.5 truncate text-[11px] text-gray-400" title={version.source_tier_version_id}>
                Cloned from {sourceVersionLabel(version.source_tier_version_id, allVersions)}
              </p>
            ) : null}
          </button>
        );
      })}
    </section>
  );
}

function sourceVersionLabel(sourceVersionId: string, versions: TierVersion[]): string {
  const source = versions.find((version) => version.tier_version_id === sourceVersionId);
  return source ? `v${source.version_number}` : sourceVersionId;
}

function creatorLabel(version: TierVersion): string {
  if (version.created_by_kind === 'account') {
    return version.created_by_email
      || (version.created_by_account_id ? `Admin ${version.created_by_account_id}` : 'Platform admin');
  }
  if (version.created_by_kind === 'master_key') return 'Master-key admin';
  if (version.created_by_kind === 'system') return 'System';
  return 'Creator unknown';
}
