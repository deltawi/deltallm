import { useMemo } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { UsageInterval, UsageSeriesResponse, UsageSplit } from '../../lib/api/usageReports';
import { fmtCompact, fmtSpendAxis, fmtSpendPrecise, fmtSpendValue } from '../../lib/format';
import { formatBucketLabel, formatBucketTick } from '../../lib/reportingRange';
import { USAGE_METRICS, usageChartData, type ChartRow, type ChartSeries, type UsageMetric } from '../../lib/usageExplorer';
import type { ReportState } from '../../lib/useReport';
import { PanelError, Segmented, Skeleton } from './ReportBits';

interface UsageTrendCardProps {
  series: ReportState<UsageSeriesResponse>;
  metric: UsageMetric;
  split: UsageSplit;
  splitOptions: ReadonlyArray<{ key: UsageSplit; label: string; disabled?: boolean }>;
  onMetricChange: (metric: UsageMetric) => void;
  onSplitChange: (split: UsageSplit) => void;
}

function formatMetric(metric: UsageMetric, value: number, axis = false): string {
  if (metric === 'spend') return axis ? fmtSpendAxis(value) : fmtSpendValue(value);
  return fmtCompact(value);
}

interface TooltipEntry {
  payload?: ChartRow;
}

function TrendTooltip({
  active,
  payload,
  series,
  metric,
  interval,
}: {
  active?: boolean;
  payload?: readonly TooltipEntry[];
  series: readonly ChartSeries[];
  metric: UsageMetric;
  interval: UsageInterval;
}) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const precise = (value: number) => (metric === 'spend' ? fmtSpendPrecise(value) : value.toLocaleString());
  return (
    <div className="min-w-[170px] rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-xs shadow-lg">
      <p className="mb-1 font-semibold text-gray-900">{formatBucketLabel(row.bucket, interval)}</p>
      {[...series].reverse().map((item) => (
        <div key={item.dataKey} className="flex justify-between gap-3.5 text-gray-600">
          <span className="flex items-center">
            <i className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: item.color }} />
            {item.label}
          </span>
          <b className="font-semibold tabular-nums text-gray-900">{precise(Number(row[item.dataKey] ?? 0))}</b>
        </div>
      ))}
      <div className="mt-1 flex justify-between gap-3.5 border-t border-gray-200 pt-1 text-gray-600">
        <span>Total</span>
        <b className="font-semibold tabular-nums text-gray-900">{precise(row.total)}</b>
      </div>
    </div>
  );
}

export default function UsageTrendCard({ series, metric, split, splitOptions, onMetricChange, onSplitChange }: UsageTrendCardProps) {
  const response = series.data;
  const chart = useMemo(() => (response ? usageChartData(response, metric) : null), [metric, response]);
  const interval = response?.interval ?? 'day';
  const empty = chart !== null && chart.rows.every((row) => row.total === 0);

  return (
    <section className="min-w-0 rounded-xl border border-gray-200 bg-white px-[18px] py-4" aria-label="Usage over time">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <Segmented label="Metric" value={metric} options={USAGE_METRICS} onChange={onMetricChange} />
        <div className="flex items-center gap-2.5">
          <span className="text-xs text-gray-500">Split by</span>
          <Segmented label="Split by" value={split} options={splitOptions} onChange={onSplitChange} />
        </div>
      </div>
      {series.error && <div className="mb-3"><PanelError message={series.error} onRetry={series.retry} stale={response !== null} /></div>}
      <div className={`h-[230px] transition-opacity ${series.refreshing ? 'opacity-60' : ''}`} aria-busy={series.loading || series.refreshing}>
        {series.loading ? (
          <div className="flex h-full items-end gap-2 pb-6 pl-12" role="status" aria-label="Loading chart">
            {Array.from({ length: 24 }, (_, index) => (
              <div key={index} className="flex-1" style={{ height: `${30 + ((index * 37) % 60)}%` }}>
                <Skeleton className="h-full w-full" />
              </div>
            ))}
          </div>
        ) : !chart ? (
          <div className="flex h-full items-center justify-center text-sm text-gray-400">The chart is not available.</div>
        ) : empty ? (
          <div className="flex h-full items-center justify-center text-sm text-gray-400">No usage matches these filters in this period.</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart.rows} margin={{ top: 8, right: 0, left: 0, bottom: 0 }} barCategoryGap="28%" accessibilityLayer>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e4e5ec" />
              <XAxis
                dataKey="bucket"
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 10, fill: '#7c7f8f' }}
                tickFormatter={(value: string) => formatBucketTick(value, interval)}
                minTickGap={24}
                interval="preserveStartEnd"
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 10, fill: '#7c7f8f' }}
                tickFormatter={(value: number) => formatMetric(metric, value, true)}
                width={52}
                allowDecimals={metric === 'spend'}
              />
              <Tooltip
                cursor={{ fill: '#f3f4f8' }}
                content={<TrendTooltip series={chart.series} metric={metric} interval={interval} />}
              />
              {chart.series.map((item, index) => (
                <Bar
                  key={item.dataKey}
                  dataKey={item.dataKey}
                  name={item.label}
                  stackId="usage"
                  fill={item.color}
                  maxBarSize={26}
                  radius={index === chart.series.length - 1 ? [3, 3, 0, 0] : undefined}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      {chart && !empty && (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-gray-600">
          {chart.series.map((item) => (
            <span key={item.dataKey} className="inline-flex items-center gap-1.5">
              <i className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: item.color }} />
              {item.label}
              <em className="font-semibold not-italic tabular-nums text-gray-900">{formatMetric(metric, item.total)}</em>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
