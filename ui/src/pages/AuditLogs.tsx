import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, ChevronDown, ChevronRight, Clock, Download, ExternalLink, Search, X } from 'lucide-react';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { audit, type AuditEvent, type Pagination } from '../lib/api';
import { useApi } from '../lib/hooks';

type Category = 'all' | 'admin' | 'auth' | 'data_plane' | 'spend';

const CATEGORY_OPTIONS: Array<{ value: Category; label: string }> = [
  { value: 'all', label: 'All Categories' },
  { value: 'admin', label: 'Admin Actions' },
  { value: 'auth', label: 'Authentication' },
  { value: 'data_plane', label: 'Data Plane' },
  { value: 'spend', label: 'Spend & Observability' },
];

function getActionCategory(action: string): Category {
  if (action.startsWith('ADMIN_')) return 'admin';
  if (action.startsWith('AUTH_')) return 'auth';
  if (action.startsWith('GLOBAL_SPEND_') || action.startsWith('SPEND_')) return 'spend';
  return 'data_plane';
}

function formatActionLabel(action: string): string {
  const category = getActionCategory(action);
  let normalized = action;
  if (category === 'admin') normalized = action.replace(/^ADMIN_/, '');
  else if (category === 'auth') normalized = action.replace(/^AUTH_/, '');
  else if (category === 'spend') normalized = action.replace(/^GLOBAL_SPEND_/, '').replace(/^SPEND_/, '');
  else normalized = action.replace(/_REQUEST$/, '');
  return normalized
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(' ');
}

function timeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffSec = Math.max(0, Math.floor((now - then) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.floor(diffHr / 24)}d ago`;
}

function truncateText(value: string | null | undefined, size = 12): string {
  if (!value) return '—';
  if (value.length <= size) return value;
  return `${value.slice(0, size)}...`;
}

function CollapsibleSection({ title, defaultOpen = false, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-gray-200 rounded-lg">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
      >
        {title}
        {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
      </button>
      {open ? <div className="px-4 pb-3 border-t border-gray-100">{children}</div> : null}
    </div>
  );
}

function DetailField({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="py-1.5">
      <dt className="text-xs font-medium text-gray-500 uppercase tracking-wider">{label}</dt>
      <dd className={`mt-0.5 text-sm text-gray-900 ${mono ? 'font-mono text-xs break-all' : ''}`}>{value || '—'}</dd>
    </div>
  );
}

function EventDetailPanel({ eventId, onClose, onViewTimeline }: { eventId: string; onClose: () => void; onViewTimeline: (requestId: string) => void }) {
  const { data: event, loading, error } = useApi(() => audit.get(eventId), [eventId]);
  if (loading) {
    return (
      <div className="fixed inset-y-0 right-0 w-full max-w-lg bg-white shadow-xl z-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }
  if (error || !event) {
    return (
      <div className="fixed inset-y-0 right-0 w-full max-w-lg bg-white shadow-xl z-50 flex flex-col">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-semibold">Event Detail</h2>
          <button type="button" onClick={onClose} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
        </div>
        <div className="flex-1 flex items-center justify-center text-gray-500">Failed to load event</div>
      </div>
    );
  }

  const hasTimelineId = event.request_id || event.correlation_id;
  return (
    <>
      <div className="fixed inset-0 bg-black/30 z-40" onClick={onClose} />
      <div className="fixed inset-y-0 right-0 w-full max-w-lg bg-white shadow-xl z-50 flex flex-col overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 shrink-0">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">{formatActionLabel(event.action)}</h2>
            <div className="mt-1">
              <StatusBadge status={event.status === 'success' ? 'active' : 'unhealthy'} label={event.status || 'unknown'} />
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 hover:bg-gray-100 rounded-lg"><X className="w-5 h-5 text-gray-500" /></button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          <div className="grid grid-cols-2 gap-x-4">
            <DetailField label="Event ID" value={event.event_id} mono />
            <DetailField label="Occurred At" value={new Date(event.occurred_at).toLocaleString()} />
            <DetailField label="Action" value={event.action} mono />
            <DetailField label="Status" value={event.status || 'unknown'} />
          </div>

          <CollapsibleSection title="Actor Information" defaultOpen>
            <div className="grid grid-cols-2 gap-x-4">
              <DetailField label="Actor Type" value={event.actor_type} />
              <DetailField label="Actor ID" value={event.actor_id} mono />
              <DetailField label="API Key" value={truncateText(event.api_key, 20)} mono />
              <DetailField label="IP Address" value={event.ip} mono />
              <DetailField label="User Agent" value={event.user_agent} />
            </div>
          </CollapsibleSection>

          <CollapsibleSection title="Resource Information" defaultOpen>
            <div className="grid grid-cols-2 gap-x-4">
              <DetailField label="Resource Type" value={event.resource_type} />
              <DetailField label="Resource ID" value={event.resource_id} mono />
              <DetailField label="Organization ID" value={event.organization_id} mono />
            </div>
          </CollapsibleSection>

          <CollapsibleSection title="Request Tracking" defaultOpen>
            <div className="grid grid-cols-2 gap-x-4">
              <DetailField label="Request ID" value={event.request_id} mono />
              <DetailField label="Correlation ID" value={event.correlation_id} mono />
            </div>
            {hasTimelineId ? (
              <button
                type="button"
                onClick={() => {
                  onClose();
                  onViewTimeline(event.request_id || event.correlation_id || '');
                }}
                className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 mt-1"
              >
                <ExternalLink className="w-3 h-3" /> View Timeline
              </button>
            ) : null}
          </CollapsibleSection>

          <CollapsibleSection title="Metadata" defaultOpen>
            <pre className="mt-2 text-xs font-mono bg-gray-50 rounded p-3 overflow-x-auto max-h-56 text-gray-800">
              {JSON.stringify(event.metadata || {}, null, 2)}
            </pre>
          </CollapsibleSection>

          {event.payloads && event.payloads.length > 0 ? (
            <CollapsibleSection title={`Payloads (${event.payloads.length})`} defaultOpen>
              <div className="space-y-3 mt-2">
                {event.payloads.map((payload) => (
                  <div key={payload.payload_id || `${payload.kind}-${payload.created_at || ''}`}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-medium text-gray-600 uppercase">{payload.kind}</span>
                      {payload.redacted ? <span className="text-xs text-red-500 font-medium">Redacted</span> : null}
                      {payload.size_bytes != null ? <span className="text-xs text-gray-400">{payload.size_bytes} bytes</span> : null}
                    </div>
                    {payload.content_json ? (
                      <pre className="text-xs font-mono bg-gray-50 rounded p-3 overflow-x-auto max-h-64 text-gray-800">
                        {typeof payload.content_json === 'string' ? payload.content_json : JSON.stringify(payload.content_json, null, 2)}
                      </pre>
                    ) : payload.storage_uri ? (
                      <p className="text-xs text-gray-500 italic">Stored at: {payload.storage_uri}</p>
                    ) : (
                      <p className="text-xs text-gray-400 italic">No content available</p>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          ) : null}
        </div>
      </div>
    </>
  );
}

export default function AuditLogs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [category, setCategory] = useState<Category>('all');
  const [action, setAction] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [actorIdFilter, setActorIdFilter] = useState('');
  const [debouncedActorId, setDebouncedActorId] = useState('');
  const [requestIdFilter, setRequestIdFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const exportRef = useRef<HTMLDivElement>(null);
  const limit = 50;

  useEffect(() => {
    const reqId = searchParams.get('request_id');
    if (reqId) setRequestIdFilter(reqId);
  }, [searchParams]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedActorId(actorIdFilter.trim()), 300);
    return () => clearTimeout(timer);
  }, [actorIdFilter]);

  useEffect(() => {
    setOffset(0);
  }, [category, action, statusFilter, startDate, endDate, debouncedActorId, requestIdFilter]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const filterParams = useMemo(() => {
    const params: Record<string, unknown> = { limit, offset };
    if (action) params.action = action;
    if (statusFilter) params.status = statusFilter;
    if (startDate) params.start_date = startDate;
    if (endDate) params.end_date = endDate;
    if (debouncedActorId) params.actor_id = debouncedActorId;
    if (requestIdFilter) params.request_id = requestIdFilter;
    return params;
  }, [action, statusFilter, startDate, endDate, debouncedActorId, requestIdFilter, limit, offset]);

  const { data, loading, error } = useApi(() => audit.list(filterParams), [filterParams]);
  const events = data?.events || [];
  const pagination: Pagination | undefined = data?.pagination;

  const availableActions = useMemo(() => {
    const allActions = Array.from(new Set(events.map((event) => event.action))).sort();
    if (category === 'all') return allActions;
    return allActions.filter((eventAction) => getActionCategory(eventAction) === category);
  }, [events, category]);

  const exportAudit = useCallback(async (format: 'csv' | 'jsonl') => {
    setExporting(true);
    try {
      const url = audit.exportUrl({ ...filterParams, format, limit: 10000 });
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      const masterKey = sessionStorage.getItem('deltallm_master_key');
      if (masterKey) headers['X-Master-Key'] = masterKey;
      const res = await fetch(url, { credentials: 'include', headers });
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const downloadUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = downloadUrl;
      anchor.download = `audit-events.${format === 'csv' ? 'csv' : 'jsonl'}`;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(downloadUrl);
    } finally {
      setExporting(false);
      setExportOpen(false);
    }
  }, [filterParams]);

  const handleViewTimeline = useCallback((requestId: string) => {
    setRequestIdFilter(requestId);
    setAction('');
    setCategory('all');
    setStatusFilter('');
    setStartDate('');
    setEndDate('');
    setActorIdFilter('');
    setDebouncedActorId('');
    setOffset(0);
    setSearchParams({ request_id: requestId });
  }, [setSearchParams]);

  const clearRequestIdFilter = useCallback(() => {
    setRequestIdFilter('');
    setSearchParams({});
  }, [setSearchParams]);

  const resetFilters = useCallback(() => {
    setCategory('all');
    setAction('');
    setStatusFilter('');
    setStartDate('');
    setEndDate('');
    setActorIdFilter('');
    setDebouncedActorId('');
    setRequestIdFilter('');
    setOffset(0);
    setSearchParams({});
  }, [setSearchParams]);

  const columns = [
    {
      key: 'occurred_at',
      header: 'Time',
      render: (row: AuditEvent) => (
        <span title={new Date(row.occurred_at).toLocaleString()} className="text-xs text-gray-500 whitespace-nowrap">
          <Clock className="w-3 h-3 inline mr-1 -mt-0.5" />
          {timeAgo(row.occurred_at)}
        </span>
      ),
    },
    {
      key: 'action',
      header: 'Action',
      render: (row: AuditEvent) => <span className="text-sm text-gray-900">{formatActionLabel(row.action)}</span>,
    },
    {
      key: 'actor_id',
      header: 'Actor',
      render: (row: AuditEvent) => <span className="text-xs text-gray-700 font-mono">{truncateText(row.actor_id)}</span>,
    },
    {
      key: 'resource_type',
      header: 'Resource',
      render: (row: AuditEvent) => (
        <div className="text-xs">
          {row.resource_type ? <span className="text-gray-500">{row.resource_type}</span> : null}
          {row.resource_id ? <span className="text-gray-700 font-mono ml-1">{truncateText(row.resource_id, 10)}</span> : null}
          {!row.resource_type && !row.resource_id ? <span className="text-gray-400">—</span> : null}
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row: AuditEvent) => <StatusBadge status={row.status === 'success' ? 'active' : 'unhealthy'} label={row.status || 'unknown'} />,
    },
    {
      key: 'latency_ms',
      header: 'Latency',
      render: (row: AuditEvent) => <span className="text-xs text-gray-500">{row.latency_ms != null ? `${row.latency_ms} ms` : '—'}</span>,
    },
  ];

  return (
    <div className="p-6 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Audit Logs</h1>
          <p className="text-sm text-gray-500 mt-1">Track security and platform activity</p>
        </div>
        <div className="relative" ref={exportRef}>
          <button
            type="button"
            onClick={() => setExportOpen((v) => !v)}
            disabled={exporting}
            className="inline-flex items-center gap-2 px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 shadow-sm disabled:opacity-50"
          >
            <Download className="w-4 h-4" />
            {exporting ? 'Exporting...' : 'Export'}
            <ChevronDown className="w-3 h-3" />
          </button>
          {exportOpen && !exporting ? (
            <div className="absolute right-0 mt-1 w-40 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-1">
              <button type="button" onClick={() => exportAudit('csv')} className="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                Export as CSV
              </button>
              <button type="button" onClick={() => exportAudit('jsonl')} className="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                Export as JSONL
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {requestIdFilter ? (
        <div className="mb-4 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 flex items-center justify-between">
          <div className="text-sm text-blue-800">
            Showing timeline for request: <code className="font-mono text-xs bg-blue-100 px-1.5 py-0.5 rounded">{requestIdFilter}</code>
          </div>
          <button type="button" onClick={clearRequestIdFilter} className="text-blue-600 hover:text-blue-800 text-sm font-medium">
            Clear filter
          </button>
        </div>
      ) : null}

      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4 space-y-3">
        <div className="flex flex-wrap gap-3">
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as Category)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          >
            {CATEGORY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>

          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500 max-w-[260px]"
          >
            <option value="">All Actions</option>
            {availableActions.map((item) => <option key={item} value={item}>{formatActionLabel(item)}</option>)}
          </select>

          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          >
            <option value="">All Statuses</option>
            <option value="success">Success</option>
            <option value="error">Error</option>
          </select>

          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />

          <div className="relative flex-1 min-w-[220px]">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={actorIdFilter}
              onChange={(e) => setActorIdFilter(e.target.value)}
              placeholder="Search by actor ID"
              className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            />
          </div>
          <button
            type="button"
            onClick={resetFilters}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Reset Filters
          </button>
        </div>
      </div>

      {error ? (
        <div className="bg-white border border-red-200 rounded-lg p-8 text-center">
          <AlertTriangle className="w-8 h-8 text-red-400 mx-auto mb-2" />
          <p className="text-red-600 font-medium">Failed to load audit events</p>
          <p className="text-sm text-gray-500 mt-1">Check your permissions or try refreshing</p>
        </div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <DataTable
            columns={columns}
            data={events}
            loading={loading}
            emptyMessage="No audit events found"
            onRowClick={(row) => setSelectedEventId((row as AuditEvent).event_id)}
            pagination={pagination}
            onPageChange={setOffset}
          />
        </div>
      )}

      {selectedEventId ? (
        <EventDetailPanel
          eventId={selectedEventId}
          onClose={() => setSelectedEventId(null)}
          onViewTimeline={handleViewTimeline}
        />
      ) : null}
    </div>
  );
}
