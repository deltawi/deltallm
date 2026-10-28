import { useEffect, useMemo, useState } from 'react';
import { Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { AlertCircle, BarChart3, List } from 'lucide-react';
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
import {
  parseRequestWindowSelection,
  REQUEST_WINDOWS,
  resolveUsageView,
  utcInputValue,
  validateRequestRange,
  type RequestWindowSelection,
} from '../lib/usageFilters';
import { navSectionLabel } from '../lib/navigation';
import { useUtcReportingDay } from '../lib/useUtcReportingDay';
import { HeaderTabBar, IndexShell } from '../components/admin/shells';
import ReportingRangeControl from '../components/ReportingRangeControl';
import RefreshControl from '../components/usage/RefreshControl';
import { useAutoRefresh, type AutoRefreshMs } from '../lib/useAutoRefresh';
import { Segmented } from '../components/usage/ReportBits';
import UsageExplorer from './usage/UsageExplorer';
import UsageRequests from './usage/UsageRequests';

const REQUEST_WINDOW_OPTIONS: ReadonlyArray<{ key: RequestWindowSelection; label: string }> = [
  ...REQUEST_WINDOWS.map((item) => ({ key: item.key, label: item.label })),
  { key: 'custom', label: 'Custom' },
];

const DATE_INPUT =
  'control-field-md rounded-lg border border-gray-200 bg-white px-2 text-xs text-gray-900 focus:border-brand-primary focus:outline-none focus:ring-1 focus:ring-brand-primary';

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
  if (startDate === endDate) return format(endDate, true);
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
  const windowKey = parseRequestWindowSelection(searchParams.get('window'));
  const customFrom = searchParams.get('from');
  const customTo = searchParams.get('to');
  const customError = windowKey === 'custom' ? validateRequestRange(customFrom, customTo) : null;

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

  const setWindow = (value: RequestWindowSelection) => {
    const next = new URLSearchParams(searchParams);
    next.set('window', value);
    if (value === 'custom') {
      const now = new Date();
      if (!next.get('from')) next.set('from', utcInputValue(new Date(now.getTime() - 24 * 3_600_000)));
    } else {
      next.delete('from');
      next.delete('to');
    }
    setSearchParams(next);
  };

  // Typing a date changes the bound on each keystroke; replace the entry so Back leaves the custom window.
  const setCustomBound = (key: 'from' | 'to', value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const tabLink = (path: string) => {
    const next = new URLSearchParams();
    const keep = ['view', 'range', 'start', 'end', 'window', 'from', 'to', 'model', 'provider', 'organization', 'team', 'user', 'api_key', 'unassigned'];
    for (const key of keep) for (const value of searchParams.getAll(key)) next.append(key, value);
    for (const [key, value] of searchParams) if (key.endsWith('_label')) next.set(key, value);
    const suffix = next.toString();
    return suffix ? `${path}?${suffix}` : path;
  };

  const subtitle = requestsPath
    ? 'Requests · every call through the gateway · UTC'
    : `${VIEW_LABELS[view]} · ${rangeSubtitle(range.startDate, range.endDate, range.label)} · UTC`;

  return (
    <IndexShell
      eyebrowPrefix={navSectionLabel('/usage')}
      title="Usage & Spend"
      titleIcon={BarChart3}
      description={subtitle}
      action={(
        <RefreshControl
          updatedAt={updatedAt}
          refreshing={false}
          onRefresh={refresh}
          autoRefreshMs={autoRefreshMs}
          onAutoRefreshChange={setAutoRefreshMs}
        />
      )}
      tabs={(
        <HeaderTabBar
          label="Usage views"
          items={canViewRequests ? [
            { to: tabLink('/usage'), label: 'Overview', icon: BarChart3, active: !requestsPath },
            { to: tabLink('/usage/requests'), label: 'Requests', icon: List, active: requestsPath },
          ] : []}
          controls={(
            <>
              {availableViews.length > 1 && (
                <Segmented
                  label="Scope"
                  value={view}
                  options={availableViews.map((key) => ({ key, label: VIEW_LABELS[key] }))}
                  onChange={(value) => setParam('view', value)}
                />
              )}
              {requestsPath ? (
                <>
                  {windowKey === 'custom' && (
                    <span className="flex flex-wrap items-center gap-1.5 text-xs text-gray-600">
                      <label className="sr-only" htmlFor="requests-from">From (UTC)</label>
                      <input
                        id="requests-from"
                        type="datetime-local"
                        value={customFrom ?? ''}
                        aria-invalid={Boolean(customError) || undefined}
                        aria-describedby={customError ? 'requests-range-error' : undefined}
                        onChange={(event) => setCustomBound('from', event.target.value)}
                        className={DATE_INPUT}
                      />
                      to
                      <label className="sr-only" htmlFor="requests-to">To (UTC), empty means now</label>
                      <input
                        id="requests-to"
                        type="datetime-local"
                        value={customTo ?? ''}
                        aria-invalid={Boolean(customError) || undefined}
                        aria-describedby={customError ? 'requests-range-error' : undefined}
                        onChange={(event) => setCustomBound('to', event.target.value)}
                        className={DATE_INPUT}
                      />
                      UTC
                      {customError && (
                        <span id="requests-range-error" role="alert" className="text-rose-600">
                          {customError} Showing the last 24 hours.
                        </span>
                      )}
                    </span>
                  )}
                  <Segmented<RequestWindowSelection>
                    label="Time window"
                    value={windowKey}
                    options={REQUEST_WINDOW_OPTIONS}
                    onChange={setWindow}
                  />
                </>
              ) : (
                <ReportingRangeControl
                  value={range.key}
                  startDate={range.startDate}
                  endDate={range.endDate}
                  ariaLabel="Usage period"
                  desktopBreakpoint="xl"
                  allowCustom
                  customLabel={range.label}
                  onPresetChange={(key: ReportingRangeKey) => setSearchParams(withReportingRangeQuery(searchParams, resolveReportingRange(key)))}
                  onCustomApply={(startDate, endDate) => {
                    const custom = resolveCustomReportingRange(startDate, endDate);
                    if (custom) setSearchParams(withReportingRangeQuery(searchParams, custom));
                  }}
                />
              )}
            </>
          )}
        />
      )}
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
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
    </IndexShell>
  );
}
