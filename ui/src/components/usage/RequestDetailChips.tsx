import { useState } from 'react';
import type { RequestLog, RequestLogDetails } from '../../lib/api/usageReports';
import { fmtDuration, fmtSpendValue } from '../../lib/format';
import {
  CALL_TYPES,
  callTypeLabel,
  parseDetailInput,
  REQUEST_DETAIL_LABELS,
  requestDetailValue,
  type RequestDetailKey,
} from '../../lib/requestLogs';
import ChipMenu, { MenuItem, MenuNote, MenuSearch } from '../audit/ChipMenu';
import { FilterChip } from './UsageFilterChips';

interface RequestDetailChipsProps {
  details: RequestLogDetails;
  /** Rows on screen: they suggest tags, end users and other values without a query. */
  logs: readonly RequestLog[];
  onChange: (details: RequestLogDetails) => void;
}

// Filters in the "More" menu; Endpoint and Tag have their own chips.
const MORE_KEYS: readonly RequestDetailKey[] = ['endUser', 'deployment', 'httpStatus', 'errorType', 'minSpend', 'minDurationMs'];
const COST_PRESETS = [0.01, 0.1, 1, 10];
const DURATION_PRESETS_MS = [1_000, 5_000, 10_000, 30_000, 60_000];

function distinct(values: Iterable<string | null | undefined>, limit = 8): string[] {
  const seen = new Set<string>();
  for (const value of values) {
    if (value && !seen.has(value)) seen.add(value);
    if (seen.size >= limit) break;
  }
  return [...seen];
}

function suggestions(key: RequestDetailKey, logs: readonly RequestLog[]): string[] {
  switch (key) {
    case 'tag':
      return distinct(logs.flatMap((log) => log.request_tags ?? []));
    case 'endUser':
      return distinct(logs.map((log) => log.end_user));
    case 'deployment':
      return distinct(logs.map((log) => log.deployment_model));
    case 'httpStatus':
      return distinct(logs.map((log) => (log.http_status_code ? String(log.http_status_code) : null)));
    case 'errorType':
      return distinct(logs.map((log) => log.error_type));
    default:
      return [];
  }
}

const PLACEHOLDERS: Record<RequestDetailKey, string> = {
  tag: 'Type a tag',
  endUser: 'Type an end user ID',
  callType: 'Type a call type',
  deployment: 'Type a deployment',
  httpStatus: 'Type a status, e.g. 429',
  errorType: 'Type an error type',
  minSpend: 'Type an amount in USD',
  minDurationMs: 'Type seconds, e.g. 2.5',
};

function DetailPicker({
  field,
  logs,
  onPick,
}: {
  field: RequestDetailKey;
  logs: readonly RequestLog[];
  onPick: (value: string | number) => void;
}) {
  const [text, setText] = useState('');
  const typed = parseDetailInput(field, text);
  const needle = text.trim().toLowerCase();
  const onScreen = suggestions(field, logs).filter((value) => value.toLowerCase().includes(needle));
  const label = REQUEST_DETAIL_LABELS[field];
  return (
    <>
      <MenuSearch
        label={label}
        placeholder={PLACEHOLDERS[field]}
        value={text}
        onChange={setText}
        onSubmit={() => typed !== undefined && onPick(typed)}
      />
      {text.trim() && typed === undefined && <MenuNote>Not a valid {label.toLowerCase()}.</MenuNote>}
      {typed !== undefined && !onScreen.includes(String(typed)) && (
        <MenuItem onClick={() => onPick(typed)}>
          {field === 'minSpend'
            ? `At least ${fmtSpendValue(Number(typed))}`
            : field === 'minDurationMs'
              ? `At least ${fmtDuration(Number(typed))}`
              : `Find “${typed}”`}
        </MenuItem>
      )}
      {field === 'minSpend' && !text.trim() && COST_PRESETS.map((amount) => (
        <MenuItem key={amount} onClick={() => onPick(amount)}>At least {fmtSpendValue(amount)}</MenuItem>
      ))}
      {field === 'minDurationMs' && !text.trim() && DURATION_PRESETS_MS.map((ms) => (
        <MenuItem key={ms} onClick={() => onPick(ms)}>At least {fmtDuration(ms)}</MenuItem>
      ))}
      {onScreen.length > 0 && <MenuNote>On screen</MenuNote>}
      {onScreen.map((value) => (
        <MenuItem key={value} onClick={() => onPick(field === 'httpStatus' ? Number(value) : value)}>
          {value}
        </MenuItem>
      ))}
    </>
  );
}

function MoreMenu({
  details,
  logs,
  onPick,
}: {
  details: RequestLogDetails;
  logs: readonly RequestLog[];
  onPick: (key: RequestDetailKey, value: string | number) => void;
}) {
  const [field, setField] = useState<RequestDetailKey | null>(null);
  if (field) return <DetailPicker field={field} logs={logs} onPick={(value) => onPick(field, value)} />;
  const open = MORE_KEYS.filter((key) => details[key] === undefined);
  return (
    <>
      {open.map((key) => (
        <MenuItem key={key} onClick={() => setField(key)} hint={key === 'minSpend' || key === 'minDurationMs' ? 'at least' : undefined}>
          {REQUEST_DETAIL_LABELS[key]}
        </MenuItem>
      ))}
      {open.length === 0 && <MenuNote>All filters are in use.</MenuNote>}
    </>
  );
}

/** Filters that only the Requests view has: endpoint, tag, and a "More" menu for the rest. */
export default function RequestDetailChips({ details, logs, onChange }: RequestDetailChipsProps) {
  const set = (key: RequestDetailKey, value: string | number | undefined) => onChange({ ...details, [key]: value });
  const onScreenTypes = distinct(logs.map((log) => log.call_type)).filter((type) => !CALL_TYPES.some((item) => item.key === type));
  return (
    <>
      <ChipMenu label="Endpoint" rowHint={false} value={requestDetailValue('callType', details)} onClear={() => set('callType', undefined)}>
        {(close) => [...CALL_TYPES, ...onScreenTypes.map((key) => ({ key, label: callTypeLabel(key) }))].map((item) => (
          <MenuItem
            key={item.key}
            hint={<span className="font-mono">{item.key}</span>}
            onClick={() => {
              close();
              set('callType', item.key);
            }}
          >
            {item.label}
          </MenuItem>
        ))}
      </ChipMenu>
      <ChipMenu label="Tag" rowHint={false} value={requestDetailValue('tag', details)} onClear={() => set('tag', undefined)}>
        {(close) => (
          <DetailPicker
            field="tag"
            logs={logs}
            onPick={(value) => {
              close();
              set('tag', value);
            }}
          />
        )}
      </ChipMenu>
      {MORE_KEYS.filter((key) => details[key] !== undefined).map((key) => (
        <FilterChip
          key={key}
          label={REQUEST_DETAIL_LABELS[key]}
          value={requestDetailValue(key, details)}
          active
          onClick={() => set(key, undefined)}
        />
      ))}
      {MORE_KEYS.some((key) => details[key] === undefined) && (
        <ChipMenu label="More filters" rowHint={false} onClear={() => undefined}>
          {(close) => (
            <MoreMenu
              details={details}
              logs={logs}
              onPick={(key, value) => {
                close();
                set(key, value);
              }}
            />
          )}
        </ChipMenu>
      )}
    </>
  );
}
