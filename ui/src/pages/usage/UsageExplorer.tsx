import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { SpendCapabilities, SpendView } from '../../lib/api';
import {
  usageReports,
  type UsageBreakdownRow,
  type UsageDimension,
  type UsageReportScope,
  type UsageSplit,
} from '../../lib/api/usageReports';
import type { ResolvedReportingRange } from '../../lib/reportingRange';
import {
  allowedUsageDimensions,
  chipDimensions,
  groupLabel,
  parseUsageFilters,
  usageFiltersKey,
  withoutUsageFilter,
  withUsageFilter,
  writeUsageFilters,
} from '../../lib/usageFilters';
import { SPLIT_GROUPS, USAGE_METRICS, usageChartData, type UsageMetric } from '../../lib/usageExplorer';
import { useReport } from '../../lib/useReport';
import UsageFilterChips from '../../components/usage/UsageFilterChips';
import UsageKpis from '../../components/usage/UsageKpis';
import UsageTopTable, { TOP_PAGE_SIZE } from '../../components/usage/UsageTopTable';
import UsageTrendCard from '../../components/usage/UsageTrendCard';

const SPLITS: ReadonlyArray<{ key: UsageSplit; label: string }> = [
  { key: 'model', label: 'Model' },
  { key: 'provider', label: 'Provider' },
  { key: 'team', label: 'Team' },
];
const CHIP_DIMENSIONS: readonly UsageDimension[] = ['model', 'provider', 'organization', 'team', 'api_key'];

function parseMetric(value: string | null): UsageMetric {
  return USAGE_METRICS.some((metric) => metric.key === value) ? (value as UsageMetric) : 'spend';
}

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return debounced;
}

/** Requests view window that best matches the explorer range. */
function requestWindowFor(range: ResolvedReportingRange): string {
  return range.key === '7d' ? '7d' : '30d';
}

interface UsageExplorerProps {
  view: SpendView;
  range: ResolvedReportingRange;
  capabilities: SpendCapabilities | undefined;
  refreshToken: number;
  reloadToken: number;
  canViewRequests: boolean;
}

export default function UsageExplorer({ view, range, capabilities, refreshToken, reloadToken, canViewRequests }: UsageExplorerProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const allowed = useMemo(() => allowedUsageDimensions(view, capabilities), [capabilities, view]);
  const filters = useMemo(
    () => parseUsageFilters(searchParams).filter((filter) => allowed.includes(filter.dimension)),
    [allowed, searchParams],
  );
  const metric = parseMetric(searchParams.get('metric'));
  const splitOptions = SPLITS.filter((option) => allowed.includes(option.key)).map((option) => ({
    ...option,
    disabled: filters.some((filter) => filter.dimension === option.key),
  }));
  const requestedSplit = SPLITS.find((option) => option.key === searchParams.get('split'))?.key ?? 'model';
  const split = splitOptions.find((option) => option.key === requestedSplit && !option.disabled)?.key
    ?? splitOptions.find((option) => !option.disabled)?.key
    ?? 'model';
  const requestedTab = searchParams.get('tab') as UsageDimension | null;
  const tab = requestedTab && allowed.includes(requestedTab) ? requestedTab : 'model';
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebounced(search.trim(), 300);

  const scope: UsageReportScope = useMemo(
    () => ({ view, startDate: range.startDate, endDate: range.endDate, filters }),
    [filters, range.endDate, range.startDate, view],
  );
  const scopeKey = `${view}|${range.startDate ?? ''}|${range.endDate ?? ''}|${usageFiltersKey(filters)}`;
  // A new period, view, filter or tab starts the table on its first page.
  const tableKey = `${scopeKey}|${tab}`;
  const [page, setPage] = useState({ key: tableKey, offset: 0 });
  const offset = page.key === tableKey ? page.offset : 0;
  const setOffset = (next: number) => setPage({ key: tableKey, offset: next });

  const summary = useReport(`explorer:summary|${scopeKey}`, (options) => usageReports.summary(scope, options), { refreshToken, reloadToken });
  const series = useReport(
    `explorer:series|${scopeKey}|${range.bucket}|${split}`,
    (options) => usageReports.series(scope, { interval: range.bucket, splitBy: split, maxGroups: SPLIT_GROUPS }, options),
    { refreshToken, reloadToken },
  );
  const breakdown = useReport(
    `explorer:breakdown|${scopeKey}|${tab}|${offset}|${debouncedSearch}`,
    (options) => usageReports.breakdown(
      scope,
      { groupBy: tab, limit: TOP_PAGE_SIZE, offset, search: debouncedSearch },
      options,
    ),
    { refreshToken, reloadToken },
  );

  const updateParams = useCallback((change: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(searchParams);
    change(next);
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const resetTable = () => {
    setOffset(0);
    setSearch('');
  };

  const chartColors = useMemo(() => {
    const response = series.data;
    if (!response || response.split_by !== tab) return null;
    const chart = usageChartData(response, metric);
    const colors = new Map<string | null, string>();
    response.groups.forEach((group, index) => {
      if (!group.is_other) colors.set(group.group_key, chart.series[index].color);
    });
    return colors;
  }, [metric, series.data, tab]);

  const selectRow = (row: UsageBreakdownRow) => {
    const filter = row.group_key === null
      ? { dimension: tab, value: '', unassigned: true }
      : { dimension: tab, value: row.group_key, label: groupLabel(tab, row) };
    resetTable();
    setSearchParams(writeUsageFilters(searchParams, withUsageFilter(filters, filter)));
  };

  const replaceFilters = (nextFilters: typeof filters) => {
    resetTable();
    setSearchParams(writeUsageFilters(searchParams, nextFilters));
  };

  const viewRequests = () => {
    const next = writeUsageFilters(new URLSearchParams(), filters);
    next.set('window', requestWindowFor(range));
    if (searchParams.get('view')) next.set('view', searchParams.get('view') ?? '');
    navigate(`/usage/requests?${next.toString()}`);
  };

  return (
    <div className="grid gap-4">
      <UsageFilterChips
        dimensions={chipDimensions(CHIP_DIMENSIONS, filters, allowed)}
        filters={filters}
        onRemove={(dimension) => replaceFilters(withoutUsageFilter(filters, dimension))}
        onAdd={(dimension) => {
          resetTable();
          updateParams((next) => next.set('tab', dimension));
        }}
        onClear={() => replaceFilters([])}
        hint="Tip: click any row below to filter the whole page"
      />
      <UsageKpis summary={summary} series={series.data} />
      <UsageTrendCard
        series={series}
        metric={metric}
        split={split}
        splitOptions={splitOptions}
        onMetricChange={(value) => updateParams((next) => next.set('metric', value))}
        onSplitChange={(value) => updateParams((next) => next.set('split', value))}
      />
      <UsageTopTable
        dimensions={allowed}
        dimension={tab}
        onDimensionChange={(value) => {
          resetTable();
          updateParams((next) => next.set('tab', value));
        }}
        breakdown={breakdown}
        filters={filters}
        colorFor={(row) => chartColors?.get(row.group_key) ?? null}
        onSelect={selectRow}
        offset={offset}
        onOffsetChange={setOffset}
        search={search}
        onSearchChange={(value) => {
          setSearch(value);
          setOffset(0);
        }}
        onViewRequests={canViewRequests ? viewRequests : undefined}
      />
    </div>
  );
}
