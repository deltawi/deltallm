import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { X } from 'lucide-react';
import type { RequestLog } from '../../lib/api/usageReports';
import { fmtSpendPrecise } from '../../lib/format';
import {
  logDurationMs,
  logKeyName,
  logProvider,
  logStatusCode,
  relativeTime,
  utcTimestamp,
} from '../../lib/requestLogs';

const INPUT_COLOR = '#2a78d6';
const OUTPUT_COLOR = '#eb6834';

function errorTitle(log: RequestLog): string {
  const code = log.http_status_code ?? 0;
  if (code === 429) return 'Rate limited';
  if (code === 408 || code === 504) return 'Timeout';
  if (code === 401 || code === 403) return 'Not authorized';
  if (code >= 500) return 'Provider error';
  return log.error_type || 'Request failed';
}

function errorText(log: RequestLog): string | null {
  const error = log.metadata?.['error'];
  if (typeof error === 'string' && error.trim()) return error;
  if (error && typeof error === 'object') {
    const message = (error as Record<string, unknown>)['message'];
    if (typeof message === 'string' && message.trim()) return message;
  }
  return log.error_type ? `Error type: ${log.error_type}` : null;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <span className="block text-[11px] uppercase tracking-wider text-gray-500">{label}</span>
      <b className="block truncate font-medium tabular-nums text-gray-900">{children}</b>
    </div>
  );
}

interface RequestLogDrawerProps {
  log: RequestLog;
  canOpenAudit: boolean;
  onClose: () => void;
  onFilterKey: (log: RequestLog) => void;
}

export default function RequestLogDrawer({ log, canOpenAudit, onClose, onFilterKey }: RequestLogDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [copied, setCopied] = useState(false);
  const failed = log.status === 'error';
  const duration = logDurationMs(log);
  const input = log.prompt_tokens || 0;
  const output = log.completion_tokens || 0;
  const message = failed ? errorText(log) : null;
  const metadata = {
    ...(log.metadata ?? {}),
    ...(log.request_tags?.length ? { tags: log.request_tags } : {}),
  };

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [log.id, onClose]);

  const copy = () => {
    void navigator.clipboard?.writeText(log.request_id).then(() => setCopied(true), () => setCopied(false));
  };

  return (
    <aside
      role="dialog"
      aria-label={`Request ${log.request_id}`}
      className="fixed inset-y-0 right-0 z-40 grid w-full max-w-[420px] content-start gap-3.5 overflow-y-auto border-l border-gray-300 bg-white px-5 py-[18px] shadow-[-12px_0_30px_rgba(0,0,0,0.1)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs text-gray-500">Request</div>
          <div className="break-all font-mono text-[13px] text-gray-900">{log.request_id}</div>
        </div>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-lg border border-gray-300 p-1.5 text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className={`inline-flex items-center gap-1.5 font-medium ${failed ? 'text-rose-700' : 'text-gray-900'}`}>
          <span aria-hidden="true" className={`h-[7px] w-[7px] rounded-full ${failed ? 'bg-rose-600' : 'bg-emerald-600'}`} />
          {logStatusCode(log)} {failed ? 'Failed' : 'Succeeded'}
        </span>
        <span className="text-xs text-gray-500" title={utcTimestamp(log.start_time)}>
          · {relativeTime(log.start_time)}{duration !== null ? ` · ${(duration / 1000).toFixed(1)} s` : ''}
        </span>
      </div>
      {failed && (
        <div className="rounded-lg bg-rose-50 px-3 py-2.5 text-xs text-rose-700">
          <b>{errorTitle(log)}</b>
          {message && <><br />{message}</>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-sm">
        <Field label="Model">{log.model || '—'}</Field>
        <Field label="Provider">{logProvider(log) || '—'}</Field>
        <Field label="Team">{log.team_alias || log.team_id || 'No team'}</Field>
        <Field label="API key">{logKeyName(log)}</Field>
        <Field label="Cost">{log.spend ? fmtSpendPrecise(log.spend) : '$0 (not charged)'}</Field>
        <Field label="Cache">{log.cache_hit ? 'Hit' : 'Miss'}</Field>
        {(log.organization_name || log.organization_id) && <Field label="Organization">{log.organization_name || log.organization_id}</Field>}
        {(log.user_email || log.user) && <Field label="User">{log.user_email || log.user}</Field>}
      </div>
      <div>
        <div className="mb-1.5 text-xs text-gray-500">Tokens · {(input + output).toLocaleString()} total</div>
        <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-[5px] bg-gray-100">
          {input > 0 && <span style={{ flex: input, background: INPUT_COLOR }} />}
          {output > 0 && <span style={{ flex: output, background: OUTPUT_COLOR }} />}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
          <span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-[3px]" style={{ background: INPUT_COLOR }} />Input <em className="font-semibold not-italic text-gray-900">{input.toLocaleString()}</em></span>
          <span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-[3px]" style={{ background: OUTPUT_COLOR }} />Output <em className="font-semibold not-italic text-gray-900">{output.toLocaleString()}</em></span>
          <span>Cached input <em className="font-semibold not-italic text-gray-900">{(log.prompt_tokens_cached ?? 0).toLocaleString()}</em></span>
        </div>
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-gray-700">Metadata and tags</summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-2.5 font-mono text-[11px] text-gray-800">
          {Object.keys(metadata).length ? JSON.stringify(metadata, null, 2) : 'No metadata'}
        </pre>
      </details>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={copy} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 hover:bg-gray-50">
          {copied ? 'Copied' : 'Copy request ID'}
        </button>
        {canOpenAudit && (
          <Link
            to={`/audit?request_id=${encodeURIComponent(log.request_id)}`}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 hover:bg-gray-50"
          >
            Open audit timeline
          </Link>
        )}
        {log.api_key && (
          <button type="button" onClick={() => onFilterKey(log)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-900 hover:bg-gray-50">
            Show requests from this key
          </button>
        )}
      </div>
    </aside>
  );
}
