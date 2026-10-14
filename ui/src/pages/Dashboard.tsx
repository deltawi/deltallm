import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, LayoutDashboard } from 'lucide-react';
import { health, keys as keysApi, models as modelsApi, routeGroups as routeGroupsApi } from '../lib/api';
import { usageReports } from '../lib/api/usageReports';
import { useAuth } from '../lib/auth';
import { useSessionPrincipal } from '../lib/authContext';
import { resolveUiAccess } from '../lib/authorization';
import { fmtSpendValue } from '../lib/format';
import { useApi } from '../lib/hooks';
import {
  attentionItems,
  liveTiles,
  monthToDate,
  readDismissals,
  writeDismissals,
  type DismissalStorage,
  type Dismissals,
} from '../lib/liveDashboard';
import { providerDisplayName } from '../lib/providers';
import { useReport } from '../lib/useReport';
import { IndexShell } from '../components/admin/shells';
import AttentionCard from '../components/dashboard/AttentionCard';
import { GatewayCard, LiveKpis, ProvidersCard } from '../components/dashboard/DashboardPanels';
import RefreshControl from '../components/usage/RefreshControl';
import { useAutoRefresh } from '../lib/useAutoRefresh';

const LIVE_REFRESH_MS = 60_000;

function browserStorage(): DismissalStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

export default function Dashboard() {
  const { session, authMode } = useAuth();
  const uiAccess = resolveUiAccess(authMode, session);
  const principal = useSessionPrincipal();
  const [refreshToken, setRefreshToken] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [updatedAt, setUpdatedAt] = useState(() => Date.now());
  useAutoRefresh(LIVE_REFRESH_MS, () => {
    setReloadToken((value) => value + 1);
    setUpdatedAt(Date.now());
  });
  const tokens = { refreshToken, reloadToken };

  const { data: providerHealth, loading: providerHealthLoading } = useApi(
    (signal) => modelsApi.providerHealthSummary(signal),
    [refreshToken],
  );
  const { data: gatewayHealth } = useApi(() => health.check(), [refreshToken]);
  const { data: keyPage } = useApi(
    () => (uiAccess.keys ? keysApi.list({ limit: 1 }) : Promise.resolve(null)),
    [uiAccess.keys],
  );
  const { data: routeGroupPage } = useApi(
    (signal) => (uiAccess.route_groups ? routeGroupsApi.list({ limit: 1 }, signal) : Promise.resolve(null)),
    [uiAccess.route_groups],
  );

  const hourly = useReport('live:hourly', (options) => usageReports.liveHourly('platform', options), tokens);
  const providers = useReport('live:providers', (options) => usageReports.liveProviders('platform', options), tokens);
  const budgets = useReport('live:budgets', (options) => usageReports.budgetAlerts('platform', options), tokens);
  const daily = useReport('live:daily', (options) => usageReports.liveDaily('platform', options), {
    ...tokens,
    maxAgeMs: 15 * 60_000,
  });
  const top = useReport('live:top', (options) => usageReports.liveTop('platform', options), tokens);

  const tiles = useMemo(() => (hourly.data ? liveTiles(hourly.data, daily.data) : null), [daily.data, hourly.data]);
  const month = useMemo(
    () => (daily.data && tiles ? monthToDate(daily.data, tiles.spendToday) : null),
    [daily.data, tiles],
  );

  const providerInputs = useMemo(
    () => (providerHealth?.providers ?? []).map((row) => ({
      provider: row.provider,
      label: providerDisplayName(row.provider),
      status: row.status,
      models: row.models,
      healthy_models: row.healthy_models,
    })),
    [providerHealth],
  );
  const items = useMemo(
    () => attentionItems({
      providers: providerInputs,
      liveProviders: providers.data?.providers ?? null,
      tiles,
      budgets: budgets.data?.items ?? null,
      formatMoney: fmtSpendValue,
    }),
    [budgets.data, providerInputs, providers.data, tiles],
  );
  const attentionLoading = providerHealthLoading || hourly.loading || providers.loading || budgets.loading;

  const storage = browserStorage();
  const [dismissed, setDismissed] = useState(() => readDismissals(storage, principal));
  const updateDismissed = (next: Dismissals) => {
    setDismissed(next);
    writeDismissals(storage, principal, next);
  };

  const status = gatewayHealth?.readiness?.status || gatewayHealth?.liveliness || 'unknown';
  const gatewayHealthy = status === 'ok' || status === 'healthy';
  const time = new Date(updatedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

  const counts = [
    ...(uiAccess.keys ? [{ label: 'API keys', value: keyPage ? keyPage.pagination?.total ?? keyPage.data.length : null, to: '/keys' }] : []),
    { label: 'Models', value: providerHealth ? providerHealth.total_models : null, to: '/models' },
    { label: 'Providers', value: providerHealth ? providerHealth.summary.total_providers : null, to: '/models' },
    ...(uiAccess.route_groups
      ? [{ label: 'Route groups', value: routeGroupPage ? routeGroupPage.pagination?.total ?? routeGroupPage.data.length : null, to: '/route-groups' }]
      : []),
  ];

  return (
    <IndexShell
      title="Dashboard"
      titleIcon={LayoutDashboard}
      description="Live view · last 24 hours · UTC"
      action={(
        <div className="flex flex-wrap items-center gap-2.5">
          <RefreshControl
            updatedAt={updatedAt}
            refreshing={hourly.refreshing}
            failed={gatewayHealth !== null && !gatewayHealthy}
            label={`${gatewayHealth === null ? 'Gateway' : gatewayHealthy ? 'Gateway healthy' : `Gateway ${status}`} · updated ${time}`}
            onRefresh={() => {
              setRefreshToken((value) => value + 1);
              setUpdatedAt(Date.now());
            }}
          />
          {uiAccess.usage && (
            <Link
              to="/usage?range=30d"
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary focus-visible:ring-offset-2"
            >
              Open Usage <ArrowRight className="h-4 w-4" />
            </Link>
          )}
        </div>
      )}
    >
      <div className="grid gap-4">
        <AttentionCard
          items={items}
          dismissed={dismissed}
          loading={attentionLoading}
          onDismiss={(item) => updateDismissed({ ...dismissed, [item.id]: { at: Date.now(), rank: item.rank } })}
          onRestore={(id) => {
            const next = { ...dismissed };
            delete next[id];
            updateDismissed(next);
          }}
        />

        <LiveKpis tiles={tiles} month={month} hourlyState={hourly} dailyLoading={daily.loading} />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <ProvidersCard health={providerHealth} healthLoading={providerHealthLoading} live={providers} />
          <GatewayCard counts={counts} top={top} />
        </div>
      </div>
    </IndexShell>
  );
}
