import { Info, Plus, ShieldCheck, Sparkles, X } from 'lucide-react';
import Button from '../Button';
import { useApi } from '../../lib/hooks';
import { models } from '../../lib/api';
import {
  OPENCODE_SESSION_HEADER,
  type HeaderAction,
  type HeaderRule,
  type ParamAction,
  type ParamRule,
  type RequestShapingForm,
} from './requestShaping';

const fieldClass = 'control-field w-full min-w-0 rounded-lg border border-gray-300 bg-white px-3 focus:outline-none focus:ring-2 focus:ring-brand-primary';
const monoFieldClass = `${fieldClass} font-mono text-xs`;
const placeholderCellClass = 'control-field flex min-w-0 items-center rounded-lg border border-dashed border-gray-200 px-3 text-xs text-gray-400';
// Phones: action and remove on one line, then name and value. Wider: one row.
const rowClass = 'grid grid-cols-[minmax(0,1fr)_auto] gap-2 sm:grid-cols-[9rem_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-center';
const nameCellClass = 'order-3 col-span-2 sm:order-none sm:col-span-1';
const valueCellClass = 'order-4 col-span-2 sm:order-none sm:col-span-1';

const PARAM_ACTIONS: { value: ParamAction; label: string }[] = [
  { value: 'default', label: 'Default' },
  { value: 'override', label: 'Override' },
  { value: 'drop', label: 'Drop' },
];

const HEADER_ACTIONS: { value: HeaderAction; label: string }[] = [
  { value: 'session', label: 'Session ID' },
  { value: 'forward', label: 'Forward' },
  { value: 'set', label: 'Set' },
];

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} className="order-2 rounded-lg p-2 hover:bg-red-50 sm:order-none">
      <X className="h-4 w-4 text-red-400" />
    </button>
  );
}

function AddRowButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1.5 text-sm font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover"
    >
      <Plus className="h-3.5 w-3.5" /> {label}
    </button>
  );
}

interface ParametersCardBodyProps {
  shaping: RequestShapingForm;
  onChange: (shaping: RequestShapingForm) => void;
  chat: boolean;
  description: string;
  details?: string[];
  keyPlaceholder: string;
  valuePlaceholder: string;
}

export function ParametersCardBody({
  shaping,
  onChange,
  chat,
  description,
  details,
  keyPlaceholder,
  valuePlaceholder,
}: ParametersCardBodyProps) {
  const update = (index: number, patch: Partial<ParamRule>) => {
    onChange({ ...shaping, params: shaping.params.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)) });
  };
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        {chat ? (
          <p className="text-xs text-gray-500">
            <b className="font-medium text-gray-700">Default</b> fills a value the client left out.{' '}
            <b className="font-medium text-gray-700">Override</b> always wins.{' '}
            <b className="font-medium text-gray-700">Drop</b> removes the field. Use the provider's field names.
          </p>
        ) : (
          <p className="text-xs text-gray-400">{description}</p>
        )}
        {details?.map((detail) => (
          <p key={detail} className="text-xs text-gray-500">{detail}</p>
        ))}
      </div>
      {shaping.params.map((rule, index) => (
        <div key={index} className={chat ? rowClass : 'flex items-center gap-2'}>
          {chat && (
            <select
              aria-label="Parameter action"
              value={rule.action}
              onChange={(event) => update(index, { action: event.target.value as ParamAction })}
              className={fieldClass}
            >
              {PARAM_ACTIONS.map((action) => <option key={action.value} value={action.value}>{action.label}</option>)}
            </select>
          )}
          <input
            aria-label="Parameter name"
            value={rule.key}
            onChange={(event) => update(index, { key: event.target.value })}
            placeholder={keyPlaceholder}
            className={chat ? `${nameCellClass} ${monoFieldClass}` : `flex-1 ${fieldClass}`}
          />
          {rule.action === 'drop' ? (
            <div className={`${valueCellClass} ${placeholderCellClass}`}>Removed before the call</div>
          ) : (
            <input
              aria-label="Parameter value"
              value={rule.value}
              onChange={(event) => update(index, { value: event.target.value })}
              placeholder={valuePlaceholder}
              className={chat ? `${valueCellClass} ${monoFieldClass}` : `flex-1 ${fieldClass}`}
            />
          )}
          <RemoveButton
            label={`Remove parameter ${rule.key || index + 1}`}
            onClick={() => onChange({ ...shaping, params: shaping.params.filter((_, i) => i !== index) })}
          />
        </div>
      ))}
      <AddRowButton
        label={chat ? 'Add parameter' : 'Add Default Parameter'}
        onClick={() => onChange({ ...shaping, params: [...shaping.params, { action: 'default', key: '', value: '' }] })}
      />
    </div>
  );
}

function headerValueHint(action: HeaderAction): string {
  if (action === 'forward') return "Client's value";
  return "Client's value, else the gateway's session ID";
}

export function HeadersNetworkCardBody({
  shaping,
  onChange,
  chat,
}: {
  shaping: RequestShapingForm;
  onChange: (shaping: RequestShapingForm) => void;
  chat: boolean;
}) {
  const { data: proxyData, loading: proxiesLoading } = useApi((signal) => models.outboundProxies(signal), []);
  const proxies = proxyData?.data ?? [];
  const selectedMissing = Boolean(shaping.outboundProxy) && !proxiesLoading && !proxies.includes(shaping.outboundProxy);
  const update = (index: number, patch: Partial<HeaderRule>) => {
    onChange({ ...shaping, headers: shaping.headers.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)) });
  };
  const hasOpenCodeSession = shaping.headers.some(
    (rule) => rule.action === 'session' && rule.name.trim().toLowerCase() === OPENCODE_SESSION_HEADER,
  );
  const addOpenCodeSession = () => {
    const others = shaping.headers.filter(
      (rule) => rule.action !== 'session' && rule.name.trim().toLowerCase() !== OPENCODE_SESSION_HEADER,
    );
    onChange({ ...shaping, headers: [{ action: 'session', name: OPENCODE_SESSION_HEADER, value: '' }, ...others] });
  };

  return (
    <div className="space-y-5">
      {chat && (
        <div className="space-y-3">
          <div className="flex flex-col items-start gap-2 sm:flex-row sm:justify-between sm:gap-3">
            <p className="text-xs text-gray-500">
              <b className="font-medium text-gray-700">Forward</b> passes the client's header.{' '}
              <b className="font-medium text-gray-700">Set</b> sends a fixed value.{' '}
              <b className="font-medium text-gray-700">Session ID</b> always sends one: the client's, or a stable ID from the gateway.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="shrink-0"
              onClick={addOpenCodeSession}
              disabled={hasOpenCodeSession}
              title="OpenCode Go needs x-opencode-session on every call"
            >
              <Sparkles className="h-3.5 w-3.5" />
              OpenCode Go
            </Button>
          </div>
          {shaping.headers.map((rule, index) => (
            <div key={index} className={rowClass}>
              <select
                aria-label="Header action"
                value={rule.action}
                onChange={(event) => update(index, { action: event.target.value as HeaderAction })}
                className={fieldClass}
              >
                {HEADER_ACTIONS.map((action) => <option key={action.value} value={action.value}>{action.label}</option>)}
              </select>
              <input
                aria-label="Header name"
                value={rule.name}
                onChange={(event) => update(index, { name: event.target.value })}
                placeholder="x-header-name"
                className={`${nameCellClass} ${monoFieldClass}`}
              />
              {rule.action === 'set' ? (
                <input
                  aria-label="Header value"
                  value={rule.value}
                  onChange={(event) => update(index, { value: event.target.value })}
                  placeholder="Value"
                  className={`${valueCellClass} ${monoFieldClass}`}
                />
              ) : (
                <div className={`${valueCellClass} ${placeholderCellClass}`}>{headerValueHint(rule.action)}</div>
              )}
              <RemoveButton
                label={`Remove header ${rule.name || index + 1}`}
                onClick={() => onChange({ ...shaping, headers: shaping.headers.filter((_, i) => i !== index) })}
              />
            </div>
          ))}
          <AddRowButton
            label="Add header"
            onClick={() => onChange({ ...shaping, headers: [...shaping.headers, { action: 'forward', name: '', value: '' }] })}
          />
          <p className="flex items-start gap-1.5 text-xs text-gray-500">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
            Auth and credential headers (Authorization, Cookie, API keys) are never forwarded or set.
          </p>
        </div>
      )}
      <div className={chat ? 'border-t border-gray-100 pt-4' : ''}>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,20rem)_1fr] sm:items-end">
          <div>
            <label htmlFor="model-outbound-proxy" className="mb-1 block text-sm font-medium text-gray-700">Outbound proxy</label>
            <select
              id="model-outbound-proxy"
              value={shaping.outboundProxy}
              onChange={(event) => onChange({ ...shaping, outboundProxy: event.target.value })}
              className={fieldClass}
            >
              <option value="">Direct (default)</option>
              {proxies.map((name) => <option key={name} value={name}>{name}</option>)}
              {selectedMissing && <option value={shaping.outboundProxy}>{shaping.outboundProxy} (not configured)</option>}
            </select>
          </div>
          <p className="flex items-start gap-1.5 text-xs text-gray-500 sm:pb-2">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {proxies.length === 0 && !proxiesLoading
              ? 'No proxies are configured. An operator adds them in general_settings.upstream_proxies.'
              : 'Calls and health checks for this model go through it. The operator defines proxies in the gateway config.'}
          </p>
        </div>
      </div>
    </div>
  );
}
