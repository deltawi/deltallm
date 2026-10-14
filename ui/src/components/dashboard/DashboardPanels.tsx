import { Link } from 'react-router-dom';
import type { ProviderHealthSummary } from '../../lib/api';
import type { LiveProvider, LiveTopResponse } from '../../lib/api/usageReports';
import { fmtSpendPrecise, fmtSpendValue } from '../../lib/format';
import type { LiveTiles, MonthToDate } from '../../lib/liveDashboard';
import { providerDisplayName } from '../../lib/providers';
import { tileCount, tileSpend } from '../../lib/usageExplorer';
import { keyTail } from '../../lib/usageFilters';
import type { ReportState } from '../../lib/useReport';
import { KpiTile, PanelError, ShareBar, Skeleton, Sparkline, Swatch, TableSkeleton } from '../usage/ReportBits';

const SERIES = '#2a78d6';
const BAD = '#c8333a';

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function LiveKpis({
  tiles,
  month,
  hourlyState,
  dailyLoading,
}: {
  tiles: LiveTiles | null;
  month: MonthToDate | null;
  hourlyState: ReportState<unknown>;
  dailyLoading: boolean;
}) {
  const loading = hourlyState.loading;
  // Without a platform budget, scale the bar to the larger of the forecast and last month.
  const monthScale = month ? Math.max(month.forecast, month.lastMonth ?? 0) : 0;
  const spentShare = month && monthScale > 0 ? Math.min(month.spend / monthScale, 1) : 0;
  const forecastShare = month && monthScale > 0 ? Math.min(month.forecast / monthScale, 1) : 0;
  return (
    <div className="grid gap-3">
      {hourlyState.error && <PanelError message={hourlyState.error} onRetry={hourlyState.retry} stale={tiles !== null} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <KpiTile
          label="Requests, last 24 h"
          loading={loading}
          value={tileCount(tiles?.requests24h)}
          title={tiles?.requests24h.toLocaleString()}
          delta={tiles?.requestsDelta}
          note="vs same hours yesterday"
          spark={<Sparkline values={tiles?.requestsSpark ?? []} color={SERIES} />}
          to="/usage/requests?window=24h"
        />
        <KpiTile
          label="Error rate, last 24 h"
          loading={loading}
          value={tiles ? pct(tiles.errorRate24h) : '—'}
          delta={tiles?.errorRateDelta}
          note={tiles?.peakErrorHour ? `Peak at ${tiles.peakErrorHour} UTC` : 'vs the 24 hours before'}
          spark={<Sparkline values={tiles?.errorSpark ?? []} color={BAD} />}
          to="/usage/requests?window=24h&status=error"
        />
        <KpiTile
          label="Spend, last 24 h"
          loading={loading || (dailyLoading && !tiles)}
          value={tileSpend(tiles?.spend24h)}
          title={tiles ? fmtSpendPrecise(tiles.spend24h) : undefined}
          delta={tiles?.spendDelta}
          note={tiles?.sevenDayAverage !== null && tiles?.sevenDayAverage !== undefined ? 'vs 7-day average' : `Today ${fmtSpendValue(tiles?.spendToday)}`}
          spark={<Sparkline values={tiles?.spendSpark ?? []} color={SERIES} />}
          to="/usage?range=7d&metric=spend"
        />
        <KpiTile
          label="Active keys, last 24 h"
          loading={loading}
          value={tileCount(tiles?.activeKeys24h)}
          note="Keys that sent requests"
          spark={<svg className="mt-1 h-7 w-full" aria-hidden="true" />}
          to="/usage?range=7d&tab=api_key"
        />
        <KpiTile
          label="Month to date"
          loading={dailyLoading || loading}
          value={tileSpend(month?.spend)}
          title={month ? fmtSpendPrecise(month.spend) : undefined}
          note={month ? `forecast ${fmtSpendValue(month.forecast)}${month.lastMonth !== null ? ` · last month ${fmtSpendValue(month.lastMonth)}` : ''}` : ''}
          spark={(
            <div className="relative mt-3 h-2.5 overflow-hidden rounded-[5px] bg-gray-100" aria-hidden="true">
              <span
                className="absolute inset-y-0 left-0 rounded-[5px] opacity-40"
                style={{
                  width: `${forecastShare * 100}%`,
                  backgroundImage: 'repeating-linear-gradient(-45deg, rgb(var(--brand-primary)) 0 4px, transparent 4px 8px)',
                }}
              />
              <span className="absolute inset-y-0 left-0 rounded-[5px] bg-brand-primary" style={{ width: `${spentShare * 100}%` }} />
            </div>
          )}
          to={month ? `/usage?range=custom&start=${new Date().toISOString().slice(0, 8)}01&end=${new Date().toISOString().slice(0, 10)}` : undefined}
        />
      </div>
    </div>
  );
}

interface ProviderRow {
  provider: string;
  label: string;
  models: number;
  healthy: number;
  status: 'healthy' | 'degraded' | 'down';
  traffic: LiveProvider | undefined;
}

const STATUS_STYLES = {
  healthy: { dot: '#0f8a5f', pill: 'bg-emerald-50 text-emerald-700', label: 'Healthy' },
  degraded: { dot: '#a86a00', pill: 'bg-amber-50 text-amber-700', label: 'Degraded' },
  down: { dot: '#c8333a', pill: 'bg-rose-50 text-rose-700', label: 'Down' },
} as const;

export function ProvidersCard({
  health,
  healthLoading,
  live,
}: {
  health: ProviderHealthSummary | null;
  healthLoading: boolean;
  live: ReportState<{ providers: LiveProvider[] }>;
}) {
  const traffic = new Map((live.data?.providers ?? []).map((row) => [row.provider.toLowerCase(), row]));
  const rows: ProviderRow[] = (health?.providers ?? []).map((row) => ({
    provider: row.provider,
    label: providerDisplayName(row.provider),
    models: row.models,
    healthy: row.healthy_models,
    status: row.status,
    traffic: traffic.get(row.provider.toLowerCase()),
  }));
  const healthy = rows.filter((row) => row.status === 'healthy').length;
  return (
    <section className="min-w-0 rounded-xl border border-gray-200 bg-white px-[18px] py-4" aria-label="Providers">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-gray-900">Providers</h2>
        {health && <span className="text-xs text-gray-500">{healthy} of {rows.length} healthy · {health.total_models} models</span>}
      </div>
      {live.error && <div className="mb-3"><PanelError message={live.error} onRetry={live.retry} stale={live.data !== null} /></div>}
      {healthLoading && !health ? (
        <TableSkeleton rows={4} columns={4} />
      ) : rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-gray-500">No providers are configured yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[12.5px] tabular-nums">
            <thead>
              <tr className="text-left text-[11px] font-semibold uppercase tracking-wider text-gray-500">
                <th className="border-b border-gray-200 px-2.5 py-2">Provider</th>
                <th className="border-b border-gray-200 px-2.5 py-2">Models up</th>
                <th className="border-b border-gray-200 px-2.5 py-2 text-right">Error rate, 24 h</th>
                <th className="border-b border-gray-200 px-2.5 py-2 text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const style = STATUS_STYLES[row.status];
                const rate = row.traffic && row.traffic.request_count > 0 ? row.traffic.failed_requests / row.traffic.request_count : null;
                return (
                  <tr key={row.provider} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px]">
                      <Link to={`/usage/requests?window=24h&provider=${encodeURIComponent(row.provider)}`} className="text-gray-900 hover:underline">
                        <Swatch color={style.dot} round />
                        {row.label}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px]">{row.healthy}/{row.models} model{row.models === 1 ? '' : 's'}</td>
                    <td className={`whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] text-right ${rate !== null && rate > 0.05 ? 'font-semibold text-rose-700' : ''}`}>
                      {live.loading ? <Skeleton className="ml-auto h-3 w-10" /> : rate === null ? <span className="text-gray-400">No traffic</span> : pct(rate)}
                    </td>
                    <td className="whitespace-nowrap border-b border-gray-100 px-2.5 py-[9px] text-right">
                      <span className={`rounded-full px-[7px] py-px text-[11px] ${style.pill}`}>{style.label}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const TOP_LABELS = { api_key: 'key', model: 'model', team: 'team' } as const;

export function GatewayCard({
  counts,
  top,
}: {
  counts: ReadonlyArray<{ label: string; value: number | null; to: string }>;
  top: ReportState<LiveTopResponse>;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3.5 rounded-xl border border-gray-200 bg-white px-[18px] py-4" aria-label="Gateway at a glance">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-gray-900">Gateway at a glance</h2>
        <Link to="/models" className="text-xs text-gray-500 hover:text-gray-900">Manage →</Link>
      </div>
      <div className="grid gap-2.5">
        {counts.map((count) => (
          <Link key={count.label} to={count.to} className="flex items-center justify-between text-gray-900 hover:text-brand-primary-ink">
            <span className="text-[13px] text-gray-500">{count.label}</span>
            {count.value === null ? <Skeleton className="h-4 w-8" /> : <b className="font-semibold tabular-nums">{count.value.toLocaleString()}</b>}
          </Link>
        ))}
      </div>
      <div className="grid gap-1.5 border-t border-gray-200 pt-3">
        <span className="text-xs text-gray-500">Busiest in the last hour, share of requests</span>
        {top.error && <PanelError message={top.error} onRetry={top.retry} stale={top.data !== null} />}
        {top.loading ? (
          Array.from({ length: 3 }, (_, index) => <Skeleton key={index} className="h-3.5 w-full" />)
        ) : !top.data?.items.length ? (
          <span className="text-xs text-gray-400">No requests in the last hour.</span>
        ) : (
          top.data.items.map((item) => {
            const name = item.display_name || (item.dimension === 'api_key' ? `Key ${keyTail(item.group_key)}` : item.group_key);
            const params = new URLSearchParams({ window: '1h', [item.dimension]: item.group_key });
            if (item.display_name) params.set(`${item.dimension}_label`, item.display_name);
            return (
              <Link key={item.dimension} to={`/usage/requests?${params.toString()}`} className="flex items-center gap-2.5 text-gray-900">
                <span className="w-[130px] truncate text-xs">
                  {name} <span className="text-gray-500">{TOP_LABELS[item.dimension]}</span>
                </span>
                <span className="flex flex-1 items-center"><ShareBar share={item.share} color={SERIES} wide /></span>
                <span className="w-11 whitespace-nowrap text-right text-xs tabular-nums text-gray-500">{Math.round(item.share * 100)}%</span>
              </Link>
            );
          })
        )}
      </div>
    </section>
  );
}
