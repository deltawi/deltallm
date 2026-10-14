import type { UsageSeriesResponse, UsageSummaryResponse, UsageWindow } from '../../lib/api/usageReports';
import { fmtCompact, fmtSpendPrecise, fmtSpendValue } from '../../lib/format';
import {
  costPerThousandRequests,
  errorRate,
  percentDelta,
  pointDelta,
  sparklineValues,
} from '../../lib/usageExplorer';
import type { ReportState } from '../../lib/useReport';
import { KpiTile, PanelError, Sparkline } from './ReportBits';

const SPEND_COLOR = '#2a78d6';
const ERROR_COLOR = '#c8333a';

function shortDay(value: string): string {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(
    new Date(`${value}T00:00:00Z`),
  );
}

function previousWindowLabel(window: UsageWindow): string {
  if (!window.previous_start_date || !window.previous_end_date) return 'All time';
  return `vs ${shortDay(window.previous_start_date)} to ${shortDay(window.previous_end_date)}`;
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

interface UsageKpisProps {
  summary: ReportState<UsageSummaryResponse>;
  series: UsageSeriesResponse | null;
}

export default function UsageKpis({ summary, series }: UsageKpisProps) {
  const data = summary.data;
  const current = data?.current;
  const previous = data?.previous ?? null;
  const loading = summary.loading;
  const note = data ? previousWindowLabel(data.window) : '';
  const tokenTotal = current ? current.prompt_tokens + current.completion_tokens : 0;
  const inputShare = current && tokenTotal > 0 ? Math.round((current.prompt_tokens / tokenTotal) * 100) : null;

  return (
    <div className="grid gap-3">
      {summary.error && <PanelError message={summary.error} onRetry={summary.retry} stale={data !== null} />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" aria-busy={loading || summary.refreshing}>
        <KpiTile
          label="Spend"
          loading={loading}
          dimmed={summary.refreshing}
          value={fmtSpendValue(current?.total_spend)}
          title={current ? fmtSpendPrecise(current.total_spend) : undefined}
          delta={current ? percentDelta(current.total_spend, previous?.total_spend, { higherIsBad: true }) : null}
          note={note}
          spark={<Sparkline values={sparklineValues(series, (values) => values.total_spend)} color={SPEND_COLOR} />}
        />
        <KpiTile
          label="Requests"
          loading={loading}
          dimmed={summary.refreshing}
          value={fmtCompact(current?.request_count)}
          title={current?.request_count.toLocaleString()}
          delta={current ? percentDelta(current.request_count, previous?.request_count, { neutral: true }) : null}
          note={previous ? 'vs previous period' : note}
          spark={<Sparkline values={sparklineValues(series, (values) => values.request_count)} color={SPEND_COLOR} />}
        />
        <KpiTile
          label="Tokens"
          loading={loading}
          dimmed={summary.refreshing}
          value={fmtCompact(current?.total_tokens)}
          title={current?.total_tokens.toLocaleString()}
          delta={current ? percentDelta(current.total_tokens, previous?.total_tokens, { neutral: true }) : null}
          note={inputShare === null ? 'No tokens' : `${inputShare}% input · ${100 - inputShare}% output`}
          spark={<Sparkline values={sparklineValues(series, (values) => values.total_tokens)} color={SPEND_COLOR} />}
        />
        <KpiTile
          label="Error rate"
          loading={loading}
          dimmed={summary.refreshing}
          value={current ? pct(errorRate(current)) : '—'}
          delta={current && previous ? pointDelta(errorRate(current), errorRate(previous)) : null}
          note={current ? `${fmtCompact(current.failed_requests)} failed` : ''}
          spark={(
            <Sparkline
              values={sparklineValues(series, (values) => (values.request_count ? values.failed_requests / values.request_count : 0))}
              color={ERROR_COLOR}
            />
          )}
        />
        <KpiTile
          label="Cost per 1K requests"
          loading={loading}
          dimmed={summary.refreshing}
          value={current ? fmtSpendValue(costPerThousandRequests(current)) : '—'}
          delta={current && previous ? percentDelta(costPerThousandRequests(current), costPerThousandRequests(previous), { higherIsBad: true }) : null}
          note={previous ? 'vs previous period' : 'Spend per 1,000 requests'}
          spark={(
            <Sparkline
              values={sparklineValues(series, (values) => (values.request_count ? (values.total_spend / values.request_count) * 1000 : 0))}
              color={SPEND_COLOR}
            />
          )}
        />
      </div>
    </div>
  );
}
