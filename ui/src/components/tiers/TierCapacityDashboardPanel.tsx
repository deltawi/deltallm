import { Activity, AlertTriangle, Gauge, RefreshCw, Zap } from 'lucide-react';
import type { TierCapacityDashboard, TierCapacityDashboardPool } from '../../lib/api';
import { errorMessage, formatLimit, formatDateTime } from '../../lib/tiers';

type TierCapacityDashboardPanelProps = {
  dashboard: TierCapacityDashboard | null;
  loading: boolean;
  error: unknown;
  onRefresh: () => void;
};

export default function TierCapacityDashboardPanel({
  dashboard,
  loading,
  error,
  onRefresh,
}: TierCapacityDashboardPanelProps) {
  const pools = dashboard?.pools || [];
  const totalPoolCount = dashboard?.total_pool_count ?? pools.length;
  const visiblePools = pools.slice(0, 8);
  const hiddenPoolCount = Math.max(0, pools.length - visiblePools.length);
  const unloadedPoolCount = Math.max(0, totalPoolCount - pools.length);
  const hotSpots = (dashboard?.limit_hit_heatmap || []).slice(0, 5);
  const advancedPools = dashboard?.advanced_pool_count ?? pools.filter((pool) => pool.advanced_fair_share).length;
  const saturatedPools = dashboard?.saturated_pool_count ?? pools.filter((pool) => maxSaturation(pool) >= (pool.saturation_threshold ?? 0.85)).length;
  const limitHits = dashboard?.limit_hit_count ?? (dashboard?.limit_hit_heatmap || []).reduce((total, row) => total + Number(row.count || 0), 0);
  const scanTruncated = Boolean(dashboard?.pool_scan_truncated);

  return (
    <section className="overflow-hidden rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">Capacity Fairness</h2>
          <p className="mt-0.5 text-xs text-gray-500">
            {dashboard ? `Snapshot ${dashboard.snapshot.etag} · ${formatDateTime(dashboard.generated_at)} · showing ${visiblePools.length} of ${totalPoolCount}` : 'No capacity data loaded'}
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {error ? (
        <div className="mx-4 mt-4 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {errorMessage(error, 'Capacity dashboard unavailable.')}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-px border-b border-gray-100 bg-gray-100 md:grid-cols-4">
        <Metric icon={Gauge} label="Pools" value={String(totalPoolCount)} />
        <Metric icon={Zap} label="Fair-share" value={String(advancedPools)} />
        <Metric icon={Activity} label="Saturated" value={String(saturatedPools)} />
        <Metric icon={AlertTriangle} label="Limit hits" value={String(limitHits)} />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs uppercase text-gray-400">
            <tr>
              <th className="px-4 py-2 text-left">Pool</th>
              <th className="px-4 py-2 text-left">Model</th>
              <th className="px-4 py-2 text-left">Strategy</th>
              <th className="px-4 py-2 text-left">RPM</th>
              <th className="px-4 py-2 text-left">TPM</th>
              <th className="px-4 py-2 text-left">Orgs</th>
              <th className="px-4 py-2 text-left">Boosts</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading && !dashboard ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center">
                  <div className="mx-auto h-5 w-5 animate-spin rounded-full border-b-2 border-blue-600" />
                </td>
              </tr>
            ) : pools.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-sm text-gray-400">No capacity pools in the active snapshot.</td>
              </tr>
            ) : visiblePools.map((pool) => (
              <tr key={`${pool.pool_key}:${pool.callable_key}`} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-mono text-xs text-gray-700">{pool.pool_key}</td>
                <td className="px-4 py-3 font-mono text-xs text-gray-700">{pool.callable_key}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    <span className={`rounded px-2 py-1 text-xs font-semibold ${pool.advanced_fair_share ? 'bg-blue-50 text-blue-700' : 'bg-gray-100 text-gray-600'}`}>
                      {pool.strategy}
                    </span>
                    {pool.cleanup_lagged ? (
                      <span className="rounded bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-700">
                        Cleanup lag
                      </span>
                    ) : null}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <UsageBar used={pool.rpm_used} limit={pool.rpm_capacity} />
                </td>
                <td className="px-4 py-3">
                  <UsageBar used={pool.tpm_used} limit={pool.tpm_capacity} />
                </td>
                <td className="px-4 py-3 text-xs text-gray-600">
                  {pool.active_org_count} active · {pool.member_count} members
                </td>
                <td className="px-4 py-3 text-xs text-gray-600">{pool.active_boost_count ?? pool.active_boosts.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hiddenPoolCount > 0 || unloadedPoolCount > 0 ? (
        <div className="border-t border-gray-100 px-4 py-2 text-xs text-gray-500">
          {hiddenPoolCount > 0 ? `${hiddenPoolCount} more ranked pools loaded but hidden from this compact view.` : null}
          {hiddenPoolCount > 0 && unloadedPoolCount > 0 ? ' ' : null}
          {unloadedPoolCount > 0 ? `${unloadedPoolCount} additional ranked pools are outside this request limit.` : null}
          {scanTruncated ? ` Capacity scan capped at ${dashboard?.scanned_pool_count ?? 0} pools.` : null}
        </div>
      ) : null}
      {hotSpots.length > 0 ? (
        <div className="border-t border-gray-100">
          <div className="px-4 py-2 text-xs font-semibold uppercase text-gray-400">Limit hot spots</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <tbody className="divide-y divide-gray-100">
                {hotSpots.map((row) => (
                  <tr key={`${row.pool_key}:${row.callable_key}:${row.organization_id || 'none'}:${row.scope}:${row.tier_key || 'none'}`} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-gray-700">{row.pool_key}</td>
                    <td className="px-4 py-2 font-mono text-gray-700">{row.callable_key}</td>
                    <td className="px-4 py-2 font-mono text-gray-600">{row.organization_id || 'unknown org'}</td>
                    <td className="px-4 py-2 text-gray-500">{row.scope}</td>
                    <td className="px-4 py-2 text-right font-semibold tabular-nums text-gray-900">{row.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Gauge;
  label: string;
  value: string;
}) {
  return (
    <div className="bg-white px-4 py-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-gray-500">
        <Icon className="h-4 w-4 text-gray-400" />
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-gray-900">{value}</div>
    </div>
  );
}

function UsageBar({ used, limit }: { used: number; limit?: number | null }) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : null;
  const tone = pct == null ? 'bg-gray-300' : pct >= 95 ? 'bg-red-500' : pct >= 85 ? 'bg-amber-500' : 'bg-blue-500';
  return (
    <div className="min-w-[140px]">
      <div className="mb-1 flex items-center justify-between gap-2 text-xs text-gray-500">
        <span className="tabular-nums">{formatLimit(used)}</span>
        <span className="tabular-nums">{limit ? `${pct}%` : 'No cap'}</span>
      </div>
      <div className="h-1.5 rounded-full bg-gray-100">
        <div className={`h-1.5 rounded-full ${tone}`} style={{ width: `${pct ?? 0}%` }} />
      </div>
    </div>
  );
}

function maxSaturation(pool: TierCapacityDashboardPool): number {
  return Math.max(Number(pool.rpm_saturation || 0), Number(pool.tpm_saturation || 0));
}
