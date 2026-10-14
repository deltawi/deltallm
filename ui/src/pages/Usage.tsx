import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { AlertCircle } from 'lucide-react';
import { spend, type SpendFeatureStatus, type SpendView } from '../lib/api';
import { useAuth } from '../lib/auth';
import { resolveUiAccess } from '../lib/authorization';
import { useApi } from '../lib/hooks';
import {
  resolveCustomReportingRange,
  resolveReportingRange,
  resolveReportingRangeQuery,
  withReportingRangeQuery,
  type ReportingRangeKey,
} from '../lib/reportingRange';
import { parseRequestWindow, REQUEST_WINDOWS, resolveUsageView, type RequestWindowKey } from '../lib/usageFilters';
import { useUtcReportingDay } from '../lib/useUtcReportingDay';
import ReportingRangeControl from '../components/ReportingRangeControl';
import RefreshControl from '../components/usage/RefreshControl';
import { useAutoRefresh, type AutoRefreshMs } from '../lib/useAutoRefresh';
import { Segmented } from '../components/usage/ReportBits';
import UsageExplorer from './usage/UsageExplorer';
import UsageRequests from './usage/UsageRequests';

const VIEW_LABELS: Record<SpendView, string> = {
  platform: 'Platform',
  organization: 'Organizations',
  team: 'Teams',
  self: 'My usage',
};

function isSpendView(value: string | null): value is SpendView {
  return value === 'platform' || value === 'organization' || value === 'team' || value === 'self';
}

function rangeSubtitle(startDate: string | undefined, endDate: string | undefined, label: string): string {
  if (!startDate || !endDate) return label;
  const format = (value: string, year: boolean) => new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: year ? 'numeric' : undefined,
    timeZone: 'UTC',
  }).format(new Date(`${value}T00:00:00Z`));
  return `${format(startDate, startDate.slice(0, 4) !== endDate.slice(0, 4))} to ${format(endDate, true)}`;
}

export default function Usage() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { session, authMode } = useAuth();
  const uiAccess = resolveUiAccess(authMode, session);
  const utcDay = useUtcReportingDay();
  const requestsPath = location.pathname.replace(/\/+$/, '').endsWith('/requests');

  const { data: featureStatus, error: featureError } = useApi<SpendFeatureStatus>(
    (signal) => spend.featureStatus({ signal }),
    [],
  );
  const capabilities = featureStatus?.capabilities;
  const requestedView = searchParams.get('view');
  const view = resolveUsageView(capabilities, isSpendView(requestedView) ? requestedView : null);
  const availableViews = capabilities?.available_views ?? [];
  const canViewRequests = capabilities ? capabilities.request_logs : view === 'platform' || view === 'organization';
  const scopeReady = featureStatus !== null || featureError !== null;

  const rangeParam = searchParams.get('range');
  const range = useMemo(
    () => resolveReportingRangeQuery(
      rangeParam,
      searchParams.get('start'),
      searchParams.get('end'),
      new Date(`${utcDay}T12:00:00Z`),
    ),
    [rangeParam, searchParams, utcDay],
  );
  const windowKey = parseRequestWindow(searchParams.get('window'));

  const [refreshToken, setRefreshToken] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<number | null>(() => Date.now());
  const [autoRefreshMs, setAutoRefreshMs] = useState<AutoRefreshMs>(0);
  const refresh = () => {
    setRefreshToken((value) => value + 1);
    setUpdatedAt(Date.now());
  };
  useAutoRefresh(autoRefreshMs, () => {
    setReloadToken((value) => value + 1);
    setUpdatedAt(Date.now());
  });

  useEffect(() => {
    if (requestsPath || rangeParam) return;
    setSearchParams(withReportingRangeQuery(searchParams, range), { replace: true });
  }, [range, rangeParam, requestsPath, searchParams, setSearchParams]);

  if (requestsPath && capabilities && !canViewRequests) {
    return <Navigate to={`/usage${location.search}`} replace />;
  }

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setSearchParams(next);
  };

  const tabLink = (path: string) => {
    const next = new URLSearchParams();
    const keep = ['view', 'model', 'provider', 'organization', 'team', 'user', 'api_key', 'unassigned'];
    for (const key of keep) for (const value of searchParams.getAll(key)) next.append(key, value);
    for (const [key, value] of searchParams) if (key.endsWith('_label')) next.set(key, value);
    const suffix = next.toString();
    return suffix ? `${path}?${suffix}` : path;
  };

  const subtitle = requestsPath
    ? 'Every call through the gateway · newest first · UTC'
    : `${VIEW_LABELS[view]} · ${rangeSubtitle(range.startDate, range.endDate, range.label)} · UTC`;

  return (
    <div className="p-4 sm:p-6">
      <div className="mx-auto grid max-w-7xl gap-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <h1 className="text-[22px] font-bold tracking-tight text-gray-900">{requestsPath ? 'Requests' : 'Usage & Spend'}</h1>
            <p className="text-xs text-gray-500">{subtitle}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            {availableViews.length > 1 && (
              <Segmented
                label="Scope"
                value={view}
                options={availableViews.map((key) => ({ key, label: VIEW_LABELS[key] }))}
                onChange={(value) => setParam('view', value)}
              />
            )}
            {requestsPath ? (
              <Segmented<RequestWindowKey>
                label="Time window"
                value={windowKey}
                options={REQUEST_WINDOWS.map((item) => ({ key: item.key, label: item.label }))}
                onChange={(value) => setParam('window', value)}
              />
            ) : (
              <ReportingRangeControl
                value={range.key}
                startDate={range.startDate}
                endDate={range.endDate}
                ariaLabel="Usage period"
                desktopBreakpoint="xl"
                onPresetChange={(key: ReportingRangeKey) => setSearchParams(withReportingRangeQuery(searchParams, resolveReportingRange(key)))}
                onCustomApply={(startDate, endDate) => {
                  const custom = resolveCustomReportingRange(startDate, endDate);
                  if (custom) setSearchParams(withReportingRangeQuery(searchParams, custom));
                }}
              />
            )}
            <RefreshControl
              updatedAt={updatedAt}
              refreshing={false}
              onRefresh={refresh}
              autoRefreshMs={autoRefreshMs}
              onAutoRefreshChange={setAutoRefreshMs}
            />
          </div>
        </div>

        {canViewRequests && (
          <nav aria-label="Usage views" className="flex gap-0.5 border-b border-gray-200">
            {[
              { to: '/usage', label: 'Overview', active: !requestsPath },
              { to: '/usage/requests', label: 'Requests', active: requestsPath },
            ].map((item) => (
              <Link
                key={item.to}
                to={tabLink(item.to)}
                aria-current={item.active ? 'page' : undefined}
                className={`-mb-px border-b-2 px-3 py-2 text-[13px] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${
                  item.active ? 'border-brand-primary font-semibold text-gray-900' : 'border-transparent text-gray-600 hover:text-gray-900'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        )}

        {featureError !== null && featureStatus === null && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            The reporting scope could not be loaded. Platform totals are shown when your account allows it.
          </div>
        )}

        {!scopeReady ? null : requestsPath ? (
          <UsageRequests
            view={view}
            capabilities={capabilities}
            refreshToken={refreshToken}
            reloadToken={reloadToken}
            canOpenAudit={uiAccess.audit}
          />
        ) : (
          <UsageExplorer
            view={view}
            range={range}
            capabilities={capabilities}
            refreshToken={refreshToken}
            reloadToken={reloadToken}
            canViewRequests={canViewRequests}
          />
        )}
      </div>
    </div>
  );
}
