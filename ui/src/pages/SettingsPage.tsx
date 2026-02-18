import { useState, useEffect } from 'react';
import { useApi } from '../lib/hooks';
import { settings } from '../lib/api';
import Card from '../components/Card';
import { Save, Check } from 'lucide-react';

export default function SettingsPage() {
  const { data, loading, refetch } = useApi(() => settings.get(), []);
  const [form, setForm] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) {
      setForm({
        routing_strategy: data.router_settings?.routing_strategy || 'simple-shuffle',
        num_retries: data.router_settings?.num_retries ?? 0,
        timeout: data.router_settings?.timeout ?? 600,
        cooldown_time: data.router_settings?.cooldown_time ?? 60,
        cache_enabled: data.general_settings?.cache_enabled ?? false,
        cache_backend: data.general_settings?.cache_backend || 'memory',
        cache_ttl: data.general_settings?.cache_ttl ?? 3600,
        background_health_checks: data.general_settings?.background_health_checks ?? false,
        health_check_interval: data.general_settings?.health_check_interval ?? 300,
        log_level: data.general_settings?.log_level || 'INFO',
        instance_name: data.general_settings?.instance_name || 'DeltaLLM',
      });
    }
  }, [data]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await settings.update({
        router_settings: {
          routing_strategy: form.routing_strategy,
          num_retries: Number(form.num_retries),
          timeout: Number(form.timeout),
          cooldown_time: Number(form.cooldown_time),
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
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
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

        <Card title="Routing">
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
          </div>
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
