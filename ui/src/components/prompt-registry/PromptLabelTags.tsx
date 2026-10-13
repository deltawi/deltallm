import { PRODUCTION_LABEL } from '../../lib/promptVersions';

/** Label chips for a version. The text names the label, so the color is only a hint. */
export default function PromptLabelTags({ labels, emptyText = 'No label' }: { labels: string[]; emptyText?: string }) {
  if (labels.length === 0) return <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">{emptyText}</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {labels.map((label) => (
        <span
          key={label}
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${label === PRODUCTION_LABEL ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}
        >
          {label}
        </span>
      ))}
    </span>
  );
}
