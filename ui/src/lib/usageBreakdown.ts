import type {
  SpendCapabilities,
  SpendGroupRow,
  SpendUsageDimension,
  SpendUsageMetric,
  SpendView,
} from './api';

interface ReportingContextResponse {
  reporting_context?: {
    api_version: number;
    active_view: SpendView;
  };
}

export function resolveUsageView(
  capabilities: SpendCapabilities | undefined,
  requested: SpendView | null,
): SpendView {
  if (!capabilities) return 'platform';
  return requested && capabilities.available_views.includes(requested)
    ? requested
    : capabilities.default_view;
}

export function supportsCursorSpendLogs(apiVersion: number | undefined): boolean {
  return Number.isFinite(apiVersion) && Number(apiVersion) >= 2;
}

export function verifiedReportingResponse<T extends ReportingContextResponse>(
  response: T,
  expectedView: SpendView,
): T {
  const context = response.reporting_context;
  if (
    !context
    || context.api_version < 2
    || context.active_view !== expectedView
  ) {
    throw new Error('Usage data is temporarily unavailable while the updated reporting service rolls out. Please retry.');
  }
  return response;
}

const DIMENSION_LABELS: Record<SpendUsageDimension, { singular: string; plural: string }> = {
  organization: { singular: 'Organization', plural: 'Organizations' },
  team: { singular: 'Team', plural: 'Teams' },
  user: { singular: 'User', plural: 'Users' },
};

export function usageDimensionLabel(dimension: SpendUsageDimension, plural = false): string {
  return plural ? DIMENSION_LABELS[dimension].plural : DIMENSION_LABELS[dimension].singular;
}

export function resolveUsageDimension(
  current: SpendUsageDimension,
  available: readonly SpendUsageDimension[],
): SpendUsageDimension {
  return available.includes(current) ? current : available[0] ?? current;
}

export function lastUsagePageOffset(total: number, pageSize: number): number {
  if (!Number.isFinite(total) || !Number.isFinite(pageSize) || total <= 0 || pageSize <= 0) return 0;
  return Math.floor((total - 1) / pageSize) * pageSize;
}

export function usageGroupLabel(dimension: SpendUsageDimension, row: SpendGroupRow): string {
  if (row.is_unassigned) {
    return `Unassigned ${DIMENSION_LABELS[dimension].singular.toLowerCase()}`;
  }
  return row.display_name?.trim() || row.group_key || 'Unknown';
}

export function usageGroupSecondaryLabel(row: SpendGroupRow): string | null {
  if (
    row.is_unassigned
    || !row.group_key
    || !row.display_name?.trim()
    || row.display_name.trim() === row.group_key
  ) {
    return null;
  }
  return row.group_key;
}

export function usageGroupIdentity(row: SpendGroupRow): string {
  return row.is_unassigned ? 'unassigned' : `assigned:${row.group_key ?? ''}`;
}

export function usageModelLabel(row: SpendGroupRow): string {
  return row.is_unassigned ? 'Unspecified model' : row.group_key || 'Unknown model';
}

export function usageMetricValue(row: SpendGroupRow, metric: SpendUsageMetric): number {
  return metric === 'spend' ? Number(row.total_spend) : Number(row.total_tokens);
}

export function relativeUsageBarWidth(value: number, maximum: number, minimumVisibleWidth = 0): number {
  if (!Number.isFinite(value) || !Number.isFinite(maximum) || value <= 0 || maximum <= 0) return 0;
  return Math.min(100, Math.max(minimumVisibleWidth, (value / maximum) * 100));
}

export function usageMetricLabel(metric: SpendUsageMetric): string {
  return metric === 'spend' ? 'USD' : 'Tokens';
}
