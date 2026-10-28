import { useEffect, useRef, useState } from 'react';
import type { SpendView } from '../../lib/api';
import { usageReports, type UsageDimension, type UsageFilterValue } from '../../lib/api/usageReports';
import { dimensionLabel, filterLabel, groupLabel } from '../../lib/usageFilters';
import type { RequestFlags } from '../../lib/usageParams';
import { useReport } from '../../lib/useReport';
import { Skeleton } from './ReportBits';
import { FilterChip } from './UsageFilterChips';

export type { RequestFlags };

interface RequestFilterChipsProps {
  view: SpendView;
  sinceDate: string;
  untilDate?: string;
  dimensions: readonly UsageDimension[];
  filters: readonly UsageFilterValue[];
  flags: RequestFlags;
  onFilter: (filter: UsageFilterValue | null, dimension: UsageDimension) => void;
  onFlags: (flags: RequestFlags) => void;
}

/** A small menu with the busiest values of one dimension. It loads only while open. */
function ValueMenu({
  view,
  sinceDate,
  untilDate,
  dimension,
  filters,
  onPick,
  onClose,
}: {
  view: SpendView;
  sinceDate: string;
  untilDate?: string;
  dimension: UsageDimension;
  filters: readonly UsageFilterValue[];
  onPick: (filter: UsageFilterValue) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const others = filters.filter((filter) => filter.dimension !== dimension);
  const options = useReport(
    `requests:values|${view}|${sinceDate}|${untilDate ?? ''}|${dimension}|${others.map((filter) => `${filter.dimension}=${filter.value}`).join('&')}`,
    (load) => usageReports.breakdown(
      { view, startDate: sinceDate, endDate: untilDate, filters: others },
      { groupBy: dimension, sortBy: 'requests', limit: 8, offset: 0 },
      load,
    ),
  );

  useEffect(() => {
    const onPointer = (event: MouseEvent) => {
      // The chip and its menu share a wrapper; the chip's own click closes the menu.
      const anchor = ref.current?.parentElement ?? ref.current;
      if (anchor && !anchor.contains(event.target as Node)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Choose a ${dimensionLabel(dimension).toLowerCase()}`}
      className="absolute left-0 top-full z-30 mt-1 w-64 rounded-lg border border-gray-200 bg-white p-1 shadow-lg"
    >
      {options.loading ? (
        <div className="grid gap-2 p-2">
          {Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-3.5 w-full" />)}
        </div>
      ) : options.error ? (
        <p className="p-2 text-xs text-rose-700">{options.error}</p>
      ) : options.data?.data.length ? (
        options.data.data.map((row) => (
          <button
            key={row.group_key ?? '∅'}
            type="button"
            role="menuitem"
            onClick={() => onPick(
              row.group_key === null
                ? { dimension, value: '', unassigned: true }
                : { dimension, value: row.group_key, label: groupLabel(dimension, row) },
            )}
            className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-xs text-gray-800 hover:bg-gray-50 focus:bg-gray-50 focus:outline-none"
          >
            <span className="truncate">{groupLabel(dimension, row)}</span>
            <span className="shrink-0 tabular-nums text-gray-500">{row.request_count.toLocaleString()}</span>
          </button>
        ))
      ) : (
        <p className="p-2 text-xs text-gray-500">No {dimensionLabel(dimension, true).toLowerCase()} in this period.</p>
      )}
    </div>
  );
}

export default function RequestFilterChips({ view, sinceDate, untilDate, dimensions, filters, flags, onFilter, onFlags }: RequestFilterChipsProps) {
  const [open, setOpen] = useState<UsageDimension | null>(null);
  return (
    <div className="flex flex-wrap items-center gap-2.5">
      <FilterChip
        label="Status"
        value={flags.status === 'error' ? 'Errors' : 'Success'}
        active={flags.status !== undefined}
        onClick={() => onFlags({ ...flags, status: flags.status ? undefined : 'error' })}
      />
      {dimensions.map((dimension) => {
        const filter = filters.find((current) => current.dimension === dimension);
        return (
          <span key={dimension} className="relative">
            <FilterChip
              label={dimensionLabel(dimension)}
              value={filter ? filterLabel(filter) : undefined}
              active={Boolean(filter)}
              onClick={() => (filter ? onFilter(null, dimension) : setOpen(open === dimension ? null : dimension))}
            />
            {open === dimension && (
              <ValueMenu
                view={view}
                sinceDate={sinceDate}
                untilDate={untilDate}
                dimension={dimension}
                filters={filters}
                onClose={() => setOpen(null)}
                onPick={(picked) => {
                  setOpen(null);
                  onFilter(picked, dimension);
                }}
              />
            )}
          </span>
        );
      })}
      <FilterChip
        label="Cache"
        value={flags.cacheHit ? 'Hit' : 'Miss'}
        active={flags.cacheHit !== undefined}
        onClick={() => onFlags({ ...flags, cacheHit: flags.cacheHit === undefined ? true : undefined })}
      />
    </div>
  );
}
