import { useState, useEffect } from 'react';
import { useApi } from '../lib/hooks';
import { settings } from '../lib/api';
import Card from '../components/Card';
import { Save, Check, Plus, X, AlertTriangle, RefreshCw } from 'lucide-react';

interface FallbackEntry {
  from: string;
  to: string;
}

function parseFallbacks(raw: any[]): FallbackEntry[] {
  if (!Array.isArray(raw)) return [];
  const entries: FallbackEntry[] = [];
  for (const item of raw) {
    if (typeof item === 'object' && item !== null) {
      for (const [key, targets] of Object.entries(item)) {
        if (Array.isArray(targets)) {
          for (const t of targets) {
            entries.push({ from: key, to: String(t) });
          }
        }
      }
    }
  }
  return entries;
}

function serializeFallbacks(entries: FallbackEntry[]): any[] {
  const map: Record<string, string[]> = {};
  for (const e of entries) {
    if (e.from && e.to) {
      if (!map[e.from]) map[e.from] = [];
      map[e.from].push(e.to);
    }
  }
  return Object.entries(map).map(([k, v]) => ({ [k]: v }));
}

export default function SettingsPage() {
  const { data, loading, refetch } = useApi(() => settings.get(), []);
  const [form, setForm] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [fallbacks, setFallbacks] = useState<FallbackEntry[]>([]);
  const [ctxFallbacks, setCtxFallbacks] = useState<FallbackEntry[]>([]);
  const [contentFallbacks, setContentFallbacks] = useState<FallbackEntry[]>([]);
  const [fallbackEvents, setFallbackEvents] = useState<any[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(false);

  useEffect(() => {
    if (data) {
      setForm({
        routing_strategy: data.router_settings?.routing_strategy || 'simple-shuffle',
        num_retries: data.router_settings?.num_retries ?? 0,
        timeout: data.router_settings?.timeout ?? 600,
        cooldown_time: data.router_settings?.cooldown_time ?? 60,
        retry_after: data.router_settings?.retry_after ?? 0,
        allowed_fails: data.router_settings?.allowed_fails ?? 3,
        cache_enabled: data.general_settings?.cache_enabled ?? false,
        cache_backend: data.general_settings?.cache_backend || 'memory',
        cache_ttl: data.general_settings?.cache_ttl ?? 3600,
        background_health_checks: data.general_settings?.background_health_checks ?? false,
        health_check_interval: data.general_settings?.health_check_interval ?? 300,
        log_level: data.general_settings?.log_level || 'INFO',
        instance_name: data.general_settings?.instance_name || 'DeltaLLM',
      });
      setFallbacks(parseFallbacks(data.litellm_settings?.fallbacks || []));
      setCtxFallbacks(parseFallbacks(data.litellm_settings?.context_window_fallbacks || []));
      setContentFallbacks(parseFallbacks(data.litellm_settings?.content_policy_fallbacks || []));
    }
  }, [data]);

  const loadFallbackEvents = async () => {
    setLoadingEvents(true);
    try {
      const resp = await fetch('/health/fallback-events?limit=50');
      const json = await resp.json();
      setFallbackEvents(json.events || []);
    } catch {
      setFallbackEvents([]);
    } finally {
      setLoadingEvents(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await settings.update({
        router_settings: {
          routing_strategy: form.routing_strategy,
          num_retries: Number(form.num_retries),
          timeout: Number(form.timeout),
          cooldown_time: Number(form.cooldown_time),
          retry_after: Number(form.retry_after),
          allowed_fails: Number(form.allowed_fails),
        },
        general_settings: {
          cache_enabled: form.cache_enabled,
          cache_backend: form.cache_backend,
          cache_ttl: Number(form.cache_ttl),
          background_health_checks: form.background_health_checks,
          health_check_interval: Number(form.health_check_interval),
          log_level: form.log_level,
          instance_name: form.instance_name,
        },
        litellm_settings: {
          fallbacks: serializeFallbacks(fallbacks),
          context_window_fallbacks: serializeFallbacks(ctxFallbacks),
          content_policy_fallbacks: serializeFallbacks(contentFallbacks),
        },
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      refetch();
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
          <p className="text-sm text-gray-500 mt-1">Configure proxy behavior and system settings</p>
        </div>
        <button onClick={handleSave} disabled={saving} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50">
          {saved ? <Check className="w-4 h-4" /> : <Save className="w-4 h-4" />}
          {saved ? 'Saved' : saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>

      <div className="space-y-6">
        <Card title="General">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Instance Name</label>
              <input value={form.instance_name || ''} onChange={(e) => setForm({ ...form, instance_name: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Log Level</label>
              <select value={form.log_level || 'INFO'} onChange={(e) => setForm({ ...form, log_level: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>
          </div>
        </Card>

        <Card title="Routing & Reliability">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Routing Strategy</label>
              <select value={form.routing_strategy || ''} onChange={(e) => setForm({ ...form, routing_strategy: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="simple-shuffle">Simple Shuffle</option>
                <option value="least-busy">Least Busy</option>
                <option value="latency-based-routing">Latency Based</option>
                <option value="cost-based-routing">Cost Based</option>
                <option value="usage-based-routing">Usage Based</option>
                <option value="weighted">Weighted</option>
                <option value="rate-limit-aware">Rate Limit Aware</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Retries</label>
              <input type="number" value={form.num_retries ?? ''} onChange={(e) => setForm({ ...form, num_retries: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Timeout (seconds)</label>
              <input type="number" value={form.timeout ?? ''} onChange={(e) => setForm({ ...form, timeout: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cooldown Time (seconds)</label>
              <input type="number" value={form.cooldown_time ?? ''} onChange={(e) => setForm({ ...form, cooldown_time: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Retry Base Delay (seconds)</label>
              <input type="number" step="0.1" value={form.retry_after ?? ''} onChange={(e) => setForm({ ...form, retry_after: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
              <p className="text-xs text-gray-400 mt-1">Base delay for exponential backoff between retries</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Allowed Fails Before Cooldown</label>
              <input type="number" value={form.allowed_fails ?? ''} onChange={(e) => setForm({ ...form, allowed_fails: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
        </Card>

        <Card title="Fallback Chains">
          <p className="text-sm text-gray-500 mb-4">Define model group fallbacks. When a model group fails, requests are routed to the fallback group.</p>
          <FallbackEditor label="General Fallbacks" entries={fallbacks} onChange={setFallbacks} />
          <FallbackEditor label="Context Window Fallbacks" entries={ctxFallbacks} onChange={setCtxFallbacks} description="When a request exceeds the model's context window, try these models instead." />
          <FallbackEditor label="Content Policy Fallbacks" entries={contentFallbacks} onChange={setContentFallbacks} description="When content is flagged by a model's safety filter, try these models instead." />
        </Card>

        <Card title="Recent Fallback Events">
          <div className="flex items-center gap-3 mb-4">
            <button onClick={loadFallbackEvents} disabled={loadingEvents} className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800">
              <RefreshCw className={`w-4 h-4 ${loadingEvents ? 'animate-spin' : ''}`} />
              {loadingEvents ? 'Loading...' : 'Load Events'}
            </button>
            <span className="text-xs text-gray-400">{fallbackEvents.length} events</span>
          </div>
          {fallbackEvents.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left py-2 px-2 text-gray-500 font-medium">Time</th>
                    <th className="text-left py-2 px-2 text-gray-500 font-medium">Model Group</th>
                    <th className="text-left py-2 px-2 text-gray-500 font-medium">From</th>
                    <th className="text-left py-2 px-2 text-gray-500 font-medium">To</th>
                    <th className="text-left py-2 px-2 text-gray-500 font-medium">Classification</th>
                    <th className="text-left py-2 px-2 text-gray-500 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {fallbackEvents.map((evt: any, i: number) => (
                    <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                      <td className="py-1.5 px-2 text-gray-600">{new Date(evt.timestamp * 1000).toLocaleTimeString()}</td>
                      <td className="py-1.5 px-2 font-mono text-gray-800">{evt.model_group}</td>
                      <td className="py-1.5 px-2 font-mono text-gray-600 truncate max-w-[120px]">{evt.from_deployment}</td>
                      <td className="py-1.5 px-2 font-mono text-gray-600 truncate max-w-[120px]">{evt.to_deployment || '-'}</td>
                      <td className="py-1.5 px-2">
                        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium ${
                          evt.error_classification === 'context_window_exceeded' ? 'bg-orange-100 text-orange-700' :
                          evt.error_classification === 'content_policy_violation' ? 'bg-red-100 text-red-700' :
                          evt.error_classification === 'rate_limit' ? 'bg-yellow-100 text-yellow-700' :
                          evt.error_classification === 'timeout' ? 'bg-purple-100 text-purple-700' :
                          evt.error_classification === 'success' ? 'bg-green-100 text-green-700' :
                          'bg-gray-100 text-gray-700'
                        }`}>
                          {evt.error_classification === 'success' ? null : <AlertTriangle className="w-3 h-3" />}
                          {evt.error_classification}
                        </span>
                      </td>
                      <td className="py-1.5 px-2">
                        <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${evt.success ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                          {evt.success ? 'OK' : 'FAIL'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-gray-400">No fallback events loaded. Click "Load Events" to see recent activity.</p>
          )}
        </Card>

        <Card title="Caching">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="flex items-center gap-3">
              <input type="checkbox" checked={form.cache_enabled || false} onChange={(e) => setForm({ ...form, cache_enabled: e.target.checked })} id="cache_enabled" className="rounded" />
              <label htmlFor="cache_enabled" className="text-sm text-gray-700">Enable Caching</label>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cache Backend</label>
              <select value={form.cache_backend || 'memory'} onChange={(e) => setForm({ ...form, cache_backend: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="memory">Memory</option>
                <option value="redis">Redis</option>
                <option value="s3">S3</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cache TTL (seconds)</label>
              <input type="number" value={form.cache_ttl ?? ''} onChange={(e) => setForm({ ...form, cache_ttl: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
        </Card>

        <Card title="Health Checks">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="flex items-center gap-3">
              <input type="checkbox" checked={form.background_health_checks || false} onChange={(e) => setForm({ ...form, background_health_checks: e.target.checked })} id="health_checks" className="rounded" />
              <label htmlFor="health_checks" className="text-sm text-gray-700">Enable Background Health Checks</label>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Check Interval (seconds)</label>
              <input type="number" value={form.health_check_interval ?? ''} onChange={(e) => setForm({ ...form, health_check_interval: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function FallbackEditor({ label, entries, onChange, description }: {
  label: string;
  entries: FallbackEntry[];
  onChange: (entries: FallbackEntry[]) => void;
  description?: string;
}) {
  const addEntry = () => onChange([...entries, { from: '', to: '' }]);
  const removeEntry = (i: number) => onChange(entries.filter((_, idx) => idx !== i));
  const updateEntry = (i: number, field: 'from' | 'to', value: string) => {
    const updated = [...entries];
    updated[i] = { ...updated[i], [field]: value };
    onChange(updated);
  };

  return (
    <div className="mb-4 last:mb-0">
      <div className="flex items-center justify-between mb-2">
        <div>
          <h4 className="text-sm font-medium text-gray-700">{label}</h4>
          {description && <p className="text-xs text-gray-400 mt-0.5">{description}</p>}
        </div>
        <button onClick={addEntry} className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800">
          <Plus className="w-3 h-3" /> Add
        </button>
      </div>
      {entries.length === 0 ? (
        <p className="text-xs text-gray-400 italic">No fallback chains configured.</p>
      ) : (
        <div className="space-y-2">
          {entries.map((entry, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                value={entry.from}
                onChange={(e) => updateEntry(i, 'from', e.target.value)}
                placeholder="Source model group"
                className="flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
              <span className="text-xs text-gray-400">&#8594;</span>
              <input
                value={entry.to}
                onChange={(e) => updateEntry(i, 'to', e.target.value)}
                placeholder="Fallback model group"
                className="flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
              <button onClick={() => removeEntry(i)} className="text-gray-400 hover:text-red-500">
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
