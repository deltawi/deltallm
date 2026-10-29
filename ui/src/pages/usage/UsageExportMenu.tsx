import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { SpendCapabilities, SpendView } from '../../lib/api';
import {
  EXPORT_ROW_LIMIT,
  usageExports,
  type RequestLogExportFormat,
  type UsageReportExport,
} from '../../lib/api/usageExports';
import { saveFile } from '../../lib/download';
import type { ResolvedReportingRange } from '../../lib/reportingRange';
import {
  allowedUsageDimensions,
  dimensionLabel,
  parseRequestWindowSelection,
  parseUsageFilters,
  requestTimeRange,
} from '../../lib/usageFilters';
import { parseRequestDetails } from '../../lib/requestLogs';
import { parseRequestFlags, parseUsageTab } from '../../lib/usageParams';
import ExportMenu from '../../components/ExportMenu';

type ReportExportKey = 'csv' | 'json' | 'daily-csv';

interface UsageExportMenuProps {
  requestsPath: boolean;
  view: SpendView;
  capabilities: SpendCapabilities | undefined;
  range: ResolvedReportingRange;
}

const ROWS = EXPORT_ROW_LIMIT.toLocaleString('en-US');

/** Downloads what the current tab shows: the Overview breakdown or the matching requests. */
export default function UsageExportMenu({ requestsPath, view, capabilities, range }: UsageExportMenuProps) {
  const [searchParams] = useSearchParams();
  const allowed = useMemo(() => allowedUsageDimensions(view, capabilities), [capabilities, view]);
  const filters = parseUsageFilters(searchParams).filter((filter) => allowed.includes(filter.dimension));

  if (requestsPath) {
    const exportRequests = async (format: RequestLogExportFormat) => {
      const flags = parseRequestFlags(searchParams);
      const requestId = searchParams.get('request_id')?.trim();
      const file = await usageExports.requestLogs(
        {
          view,
          ...requestTimeRange(parseRequestWindowSelection(searchParams.get('window')), {
            from: searchParams.get('from'),
            to: searchParams.get('to'),
          }),
          filters,
          status: flags.status,
          cacheHit: flags.cacheHit,
          requestId: requestId || undefined,
          details: parseRequestDetails(searchParams),
        },
        format,
      );
      saveFile(file.blob, file.filename);
    };
    return (
      <ExportMenu<RequestLogExportFormat>
        options={[
          { key: 'csv', label: 'Requests as CSV' },
          { key: 'jsonl', label: 'Requests as JSONL' },
        ]}
        note={`Uses the filters and time window on screen, always newest first, up to ${ROWS} requests. No prompts or responses.`}
        onExport={exportRequests}
      />
    );
  }

  const tab = parseUsageTab(searchParams, allowed);
  const label = tab === 'api_key' ? dimensionLabel(tab) : dimensionLabel(tab).toLowerCase();
  const exportReport = async (key: ReportExportKey) => {
    const params: UsageReportExport = key === 'daily-csv'
      ? { groupBy: tab, format: 'csv', interval: 'day' }
      : { groupBy: tab, format: key };
    const file = await usageExports.report(
      { view, startDate: range.startDate, endDate: range.endDate, filters },
      params,
    );
    saveFile(file.blob, file.filename);
  };
  return (
    <ExportMenu<ReportExportKey>
      options={[
        { key: 'csv', label: `Spend by ${label} as CSV` },
        { key: 'json', label: `Spend by ${label} as JSON` },
        { key: 'daily-csv', label: `Daily spend by ${label} as CSV` },
      ]}
      note={`Uses the period and filters on screen, every ${label}, up to ${ROWS} rows. Days are UTC.`}
      onExport={exportReport}
    />
  );
}
