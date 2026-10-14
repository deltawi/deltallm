import { ArrowRight, ChevronLeft, ChevronRight, Search } from 'lucide-react';
import type { UsageBreakdownResponse, UsageBreakdownRow, UsageDimension, UsageFilterValue } from '../../lib/api/usageReports';
import { fmtCompact, fmtSpendPrecise, fmtSpendValue } from '../../lib/format';
import { dimensionLabel, groupLabel, keyTail } from '../../lib/usageFilters';
import { errorRate, percentDelta } from '../../lib/usageExplorer';
import type { ReportState } from '../../lib/useReport';
import { DeltaBadge, PanelError, ShareBar, Swatch, TableSkeleton } from './ReportBits';

export const TOP_PAGE_SIZE = 10;
const DEFAULT_COLOR = '#2a78d6';

interface UsageTopTableProps {
  dimensions: readonly UsageDimension[];
  dimension: UsageDimension;
  onDimensionChange: (dimension: UsageDimension) => void;
  breakdown: ReportState<UsageBreakdownResponse>;
  filters: readonly UsageFilterValue[];
  colorFor: (row: UsageBreakdownRow) => string | null;
  onSelect: (row: UsageBreakdownRow) => void;
  offset: number;
  onOffsetChange: (offset: number) => void;
  search: string;
  onSearchChange: (search: string) => void;
  onViewRequests?: () => void;
}

function rowSelected(row: UsageBreakdownRow, dimension: UsageDimension, filters: readonly UsageFilterValue[]): boolean {
  return filters.some((filter) => filter.dimension === dimension
    && (filter.unassigned ? row.group_key === null : filter.value === row.group_key));
}

export default function UsageTopTable({
  dimensions,
  dimension,
  onDimensionChange,
  breakdown,
  filters,
  colorFor,
  onSelect,
  offset,
  onOffsetChange,
  search,
  onSearchChange,
  onViewRequests,
}: UsageTopTableProps) {
  const data = breakdown.data;
  const total = data?.pagination.total ?? 0;
  const scopeSpend = data?.scope_spend ?? 0;
  const showPager = total > TOP_PAGE_SIZE || offset > 0 || search !== '';

  return (
    <section className="min-w-0 rounded-xl border border-gray-200 bg-white px-[18px] py-4" aria-label="Top usage">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Top by" className="flex min-w-0 gap-0.5 overflow-x-auto border-b border-gray-200">
          {dimensions.map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={item === dimension}
              onClick={() => onDimensionChange(item)}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-[13px] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${
                item === dimension ? 'border-brand-primary font-semibold text-gray-900' : 'border-transparent text-gray-600 hover:text-gray-900'
              }`}
            >
              {dimensionLabel(item, true)}
            </button>
          ))}
        </div>
        {onViewRequests && (
          <button
            type="button"
            onClick={onViewRequests}
            className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-900 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
          >
            View matching requests <ArrowRight className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {breakdown.error && <div className="mb-3"><PanelError message={breakdown.error} onRetry={breakdown.retry} stale={data !== null} /></div>}
      <div className={`overflow-x-auto transition-opacity ${breakdown.refreshing ? 'opacity-60' : ''}`} aria-busy={breakdown.loading || breakdown.refreshing}>
        {breakdown.loading ? (
          <TableSkeleton rows={5} columns={7} />
        ) : !data ? null : data.data.length === 0 ? (
          <p className="px-2.5 py-10 text-center text-sm text-gray-500">
            {search ? `No ${dimensionLabel(dimension, true).toLowerCase()} match “${search}”.` : 'No usage in this period.'}
          </p>
        ) : (
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500">
                <th className="whitespace-nowrap border-b border-gray-200 px-2.5 py-2">Name</th>
                <th className="whitespace-nowrap border-b border-gray-200 px-2.5 py-2">Share of spend</th>
                <th className="whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-right">Spend</th>
                <th className="whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-right">Requests</th>
                <th className="whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-right">Tokens</th>
                <th className="whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-right">Error rate</th>
                <th className="whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-right">vs previous</th>
              </tr>
            </thead>
            <tbody>
              {data.data.map((row) => {
                const label = groupLabel(dimension, row);
                const color = colorFor(row) ?? DEFAULT_COLOR;
                const share = scopeSpend > 0 ? row.total_spend / scopeSpend : 0;
                const rate = errorRate(row);
                const selected = rowSelected(row, dimension, filters);
                return (
                  <tr
                    key={`${row.group_key ?? '∅'}:${row.display_name ?? ''}`}
                    tabIndex={0}
                    title={`Filter the page to ${label}`}
                    aria-selected={selected}
                    onClick={() => onSelect(row)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        onSelect(row);
                      }
                    }}
                    className={`cursor-pointer tabular-nums focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${selected ? 'bg-brand-primary-soft' : 'hover:bg-gray-50'}`}
                  >
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px]">
                      <Swatch color={color} />
                      <b className="font-medium text-gray-900">{label}</b>
                      {dimension === 'api_key' && row.group_key && row.display_name && (
                        <span className="ml-1.5 font-mono text-[11px] text-gray-500">{keyTail(row.group_key)}</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px]">
                      <ShareBar share={share} color={color} />
                      <span className="text-xs text-gray-500">{Math.round(share * 100)}%</span>
                    </td>
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] text-right" title={fmtSpendPrecise(row.total_spend)}>
                      {fmtSpendValue(row.total_spend)}
                    </td>
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] text-right">{fmtCompact(row.request_count)}</td>
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] text-right">{fmtCompact(row.total_tokens)}</td>
                    <td className={`whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] text-right ${rate > 0.02 ? 'font-semibold text-rose-700' : ''}`}>
                      {(rate * 100).toFixed(1)}%
                    </td>
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] text-right">
                      {row.previous_spend === null ? (
                        <span className="text-gray-400">—</span>
                      ) : (
                        <DeltaBadge delta={percentDelta(row.total_spend, row.previous_spend, { higherIsBad: true })} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {showPager && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3 px-1 pt-2 text-xs text-gray-500">
          <label className="relative">
            <span className="sr-only">Search {dimensionLabel(dimension, true).toLowerCase()}</span>
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
            <input
              type="search"
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              placeholder={`Search ${dimensionLabel(dimension, true).toLowerCase()}`}
              className="w-56 rounded-lg border border-gray-300 py-1.5 pl-7 pr-2 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary"
            />
          </label>
          <div className="flex items-center gap-2">
            <span className="tabular-nums">
              {total === 0 ? '0' : `${offset + 1}–${Math.min(offset + TOP_PAGE_SIZE, total)}`} of {total.toLocaleString()}
            </span>
            <button
              type="button"
              aria-label="Previous page"
              disabled={offset === 0}
              onClick={() => onOffsetChange(Math.max(offset - TOP_PAGE_SIZE, 0))}
              className="rounded-md border border-gray-300 p-1 text-gray-700 hover:bg-gray-50 disabled:opacity-40"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              aria-label="Next page"
              disabled={!data?.pagination.has_more}
              onClick={() => onOffsetChange(offset + TOP_PAGE_SIZE)}
              className="rounded-md border border-gray-300 p-1 text-gray-700 hover:bg-gray-50 disabled:opacity-40"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
