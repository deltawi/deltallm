import clsx from 'clsx';

type TierVersionBadgeProps = {
  status: string;
  versionNumber?: number | null;
  label?: string;
};

const statusClasses: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-700',
  draft: 'bg-amber-100 text-amber-800',
  archived: 'bg-gray-100 text-gray-600',
  disabled: 'bg-gray-100 text-gray-600',
};

export default function TierVersionBadge({ status, versionNumber, label }: TierVersionBadgeProps) {
  const normalized = String(status || '').trim().toLowerCase();
  const stateLabel = normalized === 'active'
    ? 'Live'
    : normalized === 'draft'
      ? 'Draft'
      : normalized === 'archived'
        ? 'Archived'
        : normalized || 'Unknown';
  const content = label || `${stateLabel}${versionNumber == null ? '' : ` v${versionNumber}`}`;

  return (
    <span
      className={clsx(
        'inline-flex w-auto shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold leading-4',
        statusClasses[normalized] || 'bg-gray-100 text-gray-600',
      )}
    >
      {content}
    </span>
  );
}
