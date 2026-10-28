import { apiDownload, type DownloadedFile } from './transport';
import {
  requestLogQuery,
  usageScopeQuery,
  type RequestLogQuery,
  type UsageDimension,
  type UsageInterval,
  type UsageReportScope,
} from './usageReports';

export type UsageExportFormat = 'csv' | 'json';
export type RequestLogExportFormat = 'csv' | 'jsonl';

/** Rows the UI asks for in one export. The API accepts up to 50,000. */
export const EXPORT_ROW_LIMIT = 10_000;

export interface UsageReportExport {
  groupBy: UsageDimension;
  format: UsageExportFormat;
  /** Omit for one row per group; ``day`` gives one row per UTC day and group. */
  interval?: UsageInterval;
}

/** Exports are always newest first, so they take no sort. */
export type RequestLogExportQuery = Omit<RequestLogQuery, 'cursor' | 'limit' | 'sort'>;

export function usageReportExportQuery(scope: UsageReportScope, params: UsageReportExport): URLSearchParams {
  const query = usageScopeQuery(scope);
  query.set('group_by', params.groupBy);
  query.set('format', params.format);
  if (params.interval) query.set('interval', params.interval);
  query.set('limit', String(EXPORT_ROW_LIMIT));
  return query;
}

export function requestLogExportQuery(params: RequestLogExportQuery, format: RequestLogExportFormat): URLSearchParams {
  const query = requestLogQuery({ ...params, limit: EXPORT_ROW_LIMIT });
  query.delete('pagination_mode');
  query.set('format', format);
  return query;
}

function path(base: string, query: URLSearchParams): string {
  return `${base}?${query.toString()}`;
}

export const usageExports = {
  report: (scope: UsageReportScope, params: UsageReportExport): Promise<DownloadedFile> => apiDownload(
    path('/ui/api/spend/explorer/export', usageReportExportQuery(scope, params)),
    `usage-${params.groupBy}.${params.format}`,
  ),
  requestLogs: (params: RequestLogExportQuery, format: RequestLogExportFormat): Promise<DownloadedFile> => apiDownload(
    path('/ui/api/logs/export', requestLogExportQuery(params, format)),
    `requests.${format}`,
  ),
};
