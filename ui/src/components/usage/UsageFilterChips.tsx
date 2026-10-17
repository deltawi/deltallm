import { X } from 'lucide-react';
import type { UsageDimension, UsageFilterValue } from '../../lib/api/usageReports';
import { dimensionLabel, filterLabel } from '../../lib/usageFilters';

interface UsageFilterChipsProps {
  dimensions: readonly UsageDimension[];
  filters: readonly UsageFilterValue[];
  onRemove: (dimension: UsageDimension) => void;
  onAdd: (dimension: UsageDimension) => void;
  onClear: () => void;
  hint?: string;
}

export function FilterChip({
  label,
  value,
  onClick,
  active,
}: {
  label: string;
  value?: string;
  onClick: () => void;
  active: boolean;
}) {
  if (active) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-label={`Remove filter: ${label} is ${value}`}
        className="control-sm inline-flex max-w-xs items-center gap-1.5 rounded-full border border-brand-primary bg-brand-primary-soft text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
      >
        <span className="truncate">
          {label} is <b className="font-semibold">{value}</b>
        </span>
        <X className="h-3 w-3 shrink-0 text-gray-500" aria-hidden="true" />
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      className="control-sm inline-flex items-center gap-1.5 rounded-full border border-dashed border-gray-300 bg-white text-gray-600 hover:border-gray-400 hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
    >
      + {label}
    </button>
  );
}

/** Active filters as removable chips, and "+ Dimension" chips that open that dimension's list. */
export default function UsageFilterChips({ dimensions, filters, onRemove, onAdd, onClear, hint }: UsageFilterChipsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      {dimensions.map((dimension) => {
        const filter = filters.find((current) => current.dimension === dimension);
        return (
          <FilterChip
            key={dimension}
            label={dimensionLabel(dimension)}
            value={filter ? filterLabel(filter) : undefined}
            active={Boolean(filter)}
            onClick={() => (filter ? onRemove(dimension) : onAdd(dimension))}
          />
        );
      })}
      {filters.length > 0 ? (
        <button
          type="button"
          onClick={onClear}
          className="rounded px-1 text-xs font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
        >
          Clear filters
        </button>
      ) : hint ? (
        <span className="text-xs text-gray-500">{hint}</span>
      ) : null}
    </div>
  );
}
