import { AlertTriangle, CheckCircle2, Database, Gauge, Layers, Tag } from 'lucide-react';
import type { ElementType } from 'react';
import type { OrganizationTierPolicyPreview } from '../../lib/api';
import { describeRateLimit, formatDateTime, formatLimit } from '../../lib/tiers';

type TierPolicyPreviewPanelProps = {
  preview: OrganizationTierPolicyPreview | null;
  loading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
};

export default function TierPolicyPreviewPanel({
  preview,
  loading = false,
  error = null,
  onRefresh,
}: TierPolicyPreviewPanelProps) {
  if (loading) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-6 text-center">
        <div className="mx-auto h-6 w-6 animate-spin rounded-full border-b-2 border-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
        {error}
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-6 text-center text-sm text-gray-400">
        No tier policy preview loaded.
      </div>
    );
  }

  const snapshotTone = preview.snapshot.snapshot_stale || preview.snapshot.last_reload_failed
    ? 'border-amber-200 bg-amber-50 text-amber-800'
    : 'border-emerald-100 bg-emerald-50 text-emerald-700';

  return (
    <div className="space-y-4">
      <div className={`rounded-xl border px-4 py-3 ${snapshotTone}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {preview.snapshot.snapshot_stale || preview.snapshot.last_reload_failed
              ? <AlertTriangle className="h-4 w-4" />
              : <CheckCircle2 className="h-4 w-4" />}
            <div>
              <p className="text-sm font-semibold">Snapshot {preview.snapshot.mode}</p>
              <p className="text-xs opacity-80">
                Generated {formatDateTime(preview.snapshot.generated_at)} · etag {preview.snapshot.etag}
              </p>
            </div>
          </div>
          {onRefresh ? (
            <button
              type="button"
              onClick={onRefresh}
              className="rounded-lg border border-white/60 bg-white/70 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-white"
            >
              Refresh
            </button>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <PreviewMetric icon={Tag} label="Tier keys" value={preview.tier_keys.length ? preview.tier_keys.join(', ') : 'None'} />
        <PreviewMetric icon={Layers} label="Allowed models" value={String(preview.allowed_callable_keys.length)} />
        <PreviewMetric icon={Gauge} label="Rate checks" value={String(preview.rate_limits.length)} />
        <PreviewMetric icon={Database} label="Capacity pools" value={String(preview.capacity_pools.length)} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section className="rounded-xl border border-gray-200 bg-white">
          <div className="border-b border-gray-100 px-4 py-3">
            <h3 className="text-sm font-semibold text-gray-900">Effective Model Policy</h3>
          </div>
          <div className="max-h-80 overflow-auto">
            {preview.model_policies.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-gray-400">No explicit model policy.</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-gray-50 text-xs uppercase text-gray-400">
                  <tr>
                    <th className="px-4 py-2 text-left">Model</th>
                    <th className="px-4 py-2 text-left">Access</th>
                    <th className="px-4 py-2 text-left">RPM</th>
                    <th className="px-4 py-2 text-left">TPM</th>
                    <th className="px-4 py-2 text-left">Pool</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {preview.model_policies.map((policy) => (
                    <tr key={policy.callable_key}>
                      <td className="px-4 py-2 font-mono text-xs text-gray-700">{policy.callable_key}</td>
                      <td className="px-4 py-2 text-xs font-semibold text-gray-700">{policy.access_mode}</td>
                      <td className="px-4 py-2 text-xs text-gray-600">{formatLimit(policy.limits.rpm_limit)}</td>
                      <td className="px-4 py-2 text-xs text-gray-600">{formatLimit(policy.limits.tpm_limit)}</td>
                      <td className="px-4 py-2 text-xs text-gray-500">{policy.capacity_pool_key || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>

        <section className="rounded-xl border border-gray-200 bg-white">
          <div className="border-b border-gray-100 px-4 py-3">
            <h3 className="text-sm font-semibold text-gray-900">Rate Limits</h3>
          </div>
          <div className="max-h-80 overflow-auto">
            {preview.rate_limits.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-gray-400">No tier rate checks.</p>
            ) : (
              <div className="divide-y divide-gray-100">
                {preview.rate_limits.map((limit) => (
                  <div key={`${limit.scope}:${limit.entity_id}:${limit.window_seconds}`} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="font-mono text-xs font-semibold text-gray-700">{limit.scope}</p>
                      <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                        {describeRateLimit(limit)}
                      </span>
                    </div>
                    <p className="mt-1 font-mono text-[11px] text-gray-400">{limit.entity_id}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="rounded-xl border border-gray-200 bg-white">
        <div className="border-b border-gray-100 px-4 py-3">
          <h3 className="text-sm font-semibold text-gray-900">Pricing and Pools</h3>
        </div>
        <div className="grid grid-cols-1 divide-y divide-gray-100 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
          <div className="p-4">
            {preview.pricing_policies.length === 0 ? (
              <p className="text-sm text-gray-400">No tier pricing overrides.</p>
            ) : (
              <div className="space-y-3">
                {preview.pricing_policies.map((policy) => (
                  <div key={`${policy.callable_key}:${policy.mode}`} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                    <div className="flex items-center justify-between">
                      <p className="font-mono text-xs font-semibold text-gray-700">{policy.callable_key}</p>
                      <span className="text-xs font-medium text-gray-500">{policy.mode}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {Object.entries(policy.pricing).map(([key, value]) => (
                        <span key={key} className="rounded bg-white px-2 py-0.5 text-[11px] text-gray-600">
                          {key}: {value}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="p-4">
            {preview.capacity_pools.length === 0 ? (
              <p className="text-sm text-gray-400">No referenced capacity pools.</p>
            ) : (
              <div className="space-y-3">
                {preview.capacity_pools.map((pool) => (
                  <div key={`${pool.pool_key}:${pool.callable_key}`} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                    <div className="flex items-center justify-between">
                      <p className="font-mono text-xs font-semibold text-gray-700">{pool.pool_key}</p>
                      <span className="text-xs font-medium text-gray-500">{pool.strategy}</span>
                    </div>
                    <p className="mt-1 font-mono text-[11px] text-gray-400">{pool.callable_key}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <span className="rounded bg-white px-2 py-0.5 text-[11px] text-gray-600">RPM {formatLimit(pool.rpm_capacity)}</span>
                      <span className="rounded bg-white px-2 py-0.5 text-[11px] text-gray-600">TPM {formatLimit(pool.tpm_capacity)}</span>
                      <span className="rounded bg-white px-2 py-0.5 text-[11px] text-gray-600">Parallel {formatLimit(pool.max_parallel_requests)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function PreviewMetric({ icon: Icon, label, value }: { icon: ElementType; label: string; value: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-3">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-blue-600" />
        <span className="text-xs font-medium uppercase text-gray-400">{label}</span>
      </div>
      <p className="mt-2 truncate text-sm font-semibold text-gray-900" title={value}>{value}</p>
    </div>
  );
}
