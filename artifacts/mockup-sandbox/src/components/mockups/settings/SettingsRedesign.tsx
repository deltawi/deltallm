import { useState } from 'react';
import {
  Settings, Route, Database, Shield, HeartPulse,
  Save, Check, ChevronRight, Plus, X, AlertTriangle,
  RefreshCw, Info, Clock, Zap, Server, RotateCcw,
} from 'lucide-react';

const TABS = [
  { id: 'general', label: 'General', icon: Settings },
  { id: 'routing', label: 'Routing & Reliability', icon: Route },
  { id: 'caching', label: 'Caching', icon: Database },
  { id: 'fallbacks', label: 'Fallbacks', icon: Shield },
  { id: 'health', label: 'Health & Events', icon: HeartPulse },
] as const;

type TabId = (typeof TABS)[number]['id'];

function Toggle({ checked, onChange, id }: { checked: boolean; onChange: (v: boolean) => void; id: string }) {
  return (
    <button
      id={id}
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${checked ? 'bg-violet-600' : 'bg-gray-200'}`}
    >
      <span className={`pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm ring-0 transition-transform ${checked ? 'translate-x-5' : 'translate-x-0'}`} />
    </button>
  );
}

function FieldGroup({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-gray-400">{hint}</p>}
    </div>
  );
}

function SectionCard({ title, description, icon: Icon, children }: { title: string; description?: string; icon?: any; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-3">
        {Icon && (
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-50">
            <Icon className="h-4 w-4 text-violet-600" />
          </div>
        )}
        <div>
          <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
          {description && <p className="text-xs text-gray-500 mt-0.5">{description}</p>}
        </div>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

const STRATEGIES = [
  { value: 'simple-shuffle', label: 'Simple Shuffle', desc: 'Random distribution across deployments' },
  { value: 'least-busy', label: 'Least Busy', desc: 'Route to deployment with fewest active requests' },
  { value: 'latency-based-routing', label: 'Latency Based', desc: 'Prefer deployments with lowest latency' },
  { value: 'cost-based-routing', label: 'Cost Based', desc: 'Prefer cheapest available deployment' },
  { value: 'usage-based-routing', label: 'Usage Based', desc: 'Distribute based on usage quotas' },
  { value: 'rate-limit-aware', label: 'Rate Limit Aware', desc: 'Avoid rate-limited deployments' },
  { value: 'weighted', label: 'Weighted', desc: 'Custom weight distribution' },
];

const FALLBACK_EVENTS = [
  { timestamp: 1711893600, model_group: 'gpt-4', from_deployment: 'openai/gpt-4-0125', to_deployment: 'anthropic/claude-3-sonnet', error_classification: 'rate_limit', success: true },
  { timestamp: 1711893540, model_group: 'gpt-4', from_deployment: 'openai/gpt-4-0125', to_deployment: 'azure/gpt-4-turbo', error_classification: 'timeout', success: true },
  { timestamp: 1711893480, model_group: 'claude-3', from_deployment: 'anthropic/claude-3-opus', to_deployment: 'anthropic/claude-3-sonnet', error_classification: 'context_window_exceeded', success: true },
  { timestamp: 1711893420, model_group: 'gpt-3.5', from_deployment: 'openai/gpt-3.5-turbo', to_deployment: '', error_classification: 'content_policy_violation', success: false },
];

interface FallbackEntry { from: string; to: string; }

function FallbackEditor({ label, description, entries, onChange }: {
  label: string; description?: string; entries: FallbackEntry[]; onChange: (e: FallbackEntry[]) => void;
}) {
  return (
    <div className="mb-5 last:mb-0">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h4 className="text-sm font-medium text-gray-700">{label}</h4>
          {description && <p className="text-xs text-gray-400 mt-0.5">{description}</p>}
        </div>
        <button onClick={() => onChange([...entries, { from: '', to: '' }])} className="flex items-center gap-1.5 text-xs font-medium text-violet-600 hover:text-violet-700 px-2 py-1 rounded-md hover:bg-violet-50 transition-colors">
          <Plus className="w-3.5 h-3.5" /> Add rule
        </button>
      </div>
      {entries.length === 0 ? (
        <div className="py-6 text-center rounded-lg border border-dashed border-gray-200">
          <Shield className="w-5 h-5 text-gray-300 mx-auto mb-1.5" />
          <p className="text-xs text-gray-400">No fallback chains configured</p>
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((entry, i) => (
            <div key={i} className="flex items-center gap-2 group">
              <input value={entry.from} onChange={(e) => { const u = [...entries]; u[i] = { ...u[i], from: e.target.value }; onChange(u); }} placeholder="Source model group" className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent transition-all" />
              <ChevronRight className="w-4 h-4 text-gray-300 shrink-0" />
              <input value={entry.to} onChange={(e) => { const u = [...entries]; u[i] = { ...u[i], to: e.target.value }; onChange(u); }} placeholder="Fallback model group" className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent transition-all" />
              <button onClick={() => onChange(entries.filter((_, idx) => idx !== i))} className="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity">
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function SettingsRedesign() {
  const [activeTab, setActiveTab] = useState<TabId>('general');
  const [saved, setSaved] = useState(false);

  const [form, setForm] = useState({
    instance_name: 'DeltaLLM',
    log_level: 'INFO',
    routing_strategy: 'latency-based-routing',
    num_retries: 3,
    timeout: 600,
    cooldown_time: 60,
    retry_after: 1,
    allowed_fails: 3,
    cache_enabled: true,
    cache_backend: 'redis',
    cache_ttl: 3600,
    background_health_checks: true,
    health_check_interval: 300,
  });

  const [generalFallbacks, setGeneralFallbacks] = useState<FallbackEntry[]>([
    { from: 'gpt-4', to: 'claude-3-sonnet' },
    { from: 'gpt-3.5', to: 'mixtral-8x7b' },
  ]);
  const [ctxFallbacks, setCtxFallbacks] = useState<FallbackEntry[]>([
    { from: 'gpt-4', to: 'claude-3-opus' },
  ]);
  const [contentFallbacks, setContentFallbacks] = useState<FallbackEntry[]>([]);

  const handleSave = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const inputClass = 'w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-violet-500 focus:border-transparent transition-all';
  const selectClass = inputClass + ' appearance-none';

  return (
    <div className="min-h-screen bg-gray-50/80">
      <div className="border-b border-gray-200 bg-white">
        <div className="max-w-6xl mx-auto px-6 py-5 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Settings</h1>
            <p className="text-sm text-gray-500 mt-0.5">Configure proxy behavior, routing, caching, and reliability</p>
          </div>
          <button onClick={handleSave} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-sm ${saved ? 'bg-emerald-600 text-white' : 'bg-violet-600 text-white hover:bg-violet-700'}`}>
            {saved ? <Check className="w-4 h-4" /> : <Save className="w-4 h-4" />}
            {saved ? 'Saved' : 'Save Changes'}
          </button>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-6">
        <div className="flex gap-6">
          <nav className="w-56 shrink-0">
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-1.5 sticky top-6">
              {TABS.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${isActive ? 'bg-violet-50 text-violet-700' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'}`}
                  >
                    <Icon className={`w-4 h-4 ${isActive ? 'text-violet-600' : 'text-gray-400'}`} />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </nav>

          <div className="flex-1 min-w-0 space-y-5">
            {activeTab === 'general' && (
              <>
                <SectionCard title="Instance" description="Basic proxy identity and logging" icon={Settings}>
                  <div className="grid grid-cols-2 gap-5">
                    <FieldGroup label="Instance Name" hint="Display name shown in the admin UI header">
                      <input value={form.instance_name} onChange={(e) => setForm({ ...form, instance_name: e.target.value })} className={inputClass} />
                    </FieldGroup>
                    <FieldGroup label="Log Level" hint="Controls verbosity of system logs">
                      <select value={form.log_level} onChange={(e) => setForm({ ...form, log_level: e.target.value })} className={selectClass}>
                        <option value="DEBUG">DEBUG</option>
                        <option value="INFO">INFO</option>
                        <option value="WARNING">WARNING</option>
                        <option value="ERROR">ERROR</option>
                      </select>
                    </FieldGroup>
                  </div>
                </SectionCard>

                <div className="bg-blue-50/50 border border-blue-100 rounded-xl p-4 flex items-start gap-3">
                  <Info className="w-4 h-4 text-blue-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-medium text-blue-800">Configuration persistence</p>
                    <p className="text-xs text-blue-600 mt-0.5">Settings are persisted to the database and broadcast to all replicas via Redis pub/sub. Changes take effect immediately without restart.</p>
                  </div>
                </div>
              </>
            )}

            {activeTab === 'routing' && (
              <>
                <SectionCard title="Routing Strategy" description="How requests are distributed across model deployments" icon={Route}>
                  <div className="grid grid-cols-1 gap-2">
                    {STRATEGIES.map((s) => (
                      <label
                        key={s.value}
                        className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${form.routing_strategy === s.value ? 'border-violet-300 bg-violet-50/50 ring-1 ring-violet-200' : 'border-gray-100 hover:border-gray-200 hover:bg-gray-50'}`}
                      >
                        <input
                          type="radio"
                          name="strategy"
                          value={s.value}
                          checked={form.routing_strategy === s.value}
                          onChange={(e) => setForm({ ...form, routing_strategy: e.target.value })}
                          className="accent-violet-600"
                        />
                        <div>
                          <p className={`text-sm font-medium ${form.routing_strategy === s.value ? 'text-violet-900' : 'text-gray-800'}`}>{s.label}</p>
                          <p className="text-xs text-gray-500">{s.desc}</p>
                        </div>
                      </label>
                    ))}
                  </div>
                </SectionCard>

                <SectionCard title="Reliability" description="Retry behavior and failure thresholds" icon={RotateCcw}>
                  <div className="grid grid-cols-2 gap-5">
                    <FieldGroup label="Max Retries" hint="Number of retry attempts per failed request">
                      <input type="number" value={form.num_retries} onChange={(e) => setForm({ ...form, num_retries: Number(e.target.value) })} className={inputClass} />
                    </FieldGroup>
                    <FieldGroup label="Request Timeout" hint="Maximum seconds before a request is cancelled">
                      <div className="relative">
                        <input type="number" value={form.timeout} onChange={(e) => setForm({ ...form, timeout: Number(e.target.value) })} className={inputClass + ' pr-8'} />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                      </div>
                    </FieldGroup>
                    <FieldGroup label="Cooldown Time" hint="Seconds a deployment is sidelined after failing">
                      <div className="relative">
                        <input type="number" value={form.cooldown_time} onChange={(e) => setForm({ ...form, cooldown_time: Number(e.target.value) })} className={inputClass + ' pr-8'} />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                      </div>
                    </FieldGroup>
                    <FieldGroup label="Retry Base Delay" hint="Base delay for exponential backoff between retries">
                      <div className="relative">
                        <input type="number" step="0.1" value={form.retry_after} onChange={(e) => setForm({ ...form, retry_after: Number(e.target.value) })} className={inputClass + ' pr-8'} />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                      </div>
                    </FieldGroup>
                    <FieldGroup label="Allowed Fails" hint="Failures before a deployment enters cooldown">
                      <input type="number" value={form.allowed_fails} onChange={(e) => setForm({ ...form, allowed_fails: Number(e.target.value) })} className={inputClass} />
                    </FieldGroup>
                  </div>
                </SectionCard>
              </>
            )}

            {activeTab === 'caching' && (
              <>
                <SectionCard title="Response Caching" description="Cache LLM responses to reduce latency and cost" icon={Database}>
                  <div className="space-y-5">
                    <div className="flex items-center justify-between py-2">
                      <div>
                        <p className="text-sm font-medium text-gray-700">Enable Caching</p>
                        <p className="text-xs text-gray-400 mt-0.5">Cache identical requests to avoid duplicate API calls</p>
                      </div>
                      <Toggle checked={form.cache_enabled} onChange={(v) => setForm({ ...form, cache_enabled: v })} id="cache-toggle" />
                    </div>

                    {form.cache_enabled && (
                      <div className="grid grid-cols-2 gap-5 pt-2 border-t border-gray-100">
                        <FieldGroup label="Backend" hint="Where cached responses are stored">
                          <select value={form.cache_backend} onChange={(e) => setForm({ ...form, cache_backend: e.target.value })} className={selectClass}>
                            <option value="memory">Memory (in-process LRU)</option>
                            <option value="redis">Redis (distributed)</option>
                            <option value="s3">S3 (persistent)</option>
                          </select>
                        </FieldGroup>
                        <FieldGroup label="TTL" hint="How long cached responses remain valid">
                          <div className="relative">
                            <input type="number" value={form.cache_ttl} onChange={(e) => setForm({ ...form, cache_ttl: Number(e.target.value) })} className={inputClass + ' pr-8'} />
                            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                          </div>
                        </FieldGroup>
                      </div>
                    )}
                  </div>
                </SectionCard>

                {form.cache_enabled && (
                  <div className="bg-amber-50/50 border border-amber-100 rounded-xl p-4 flex items-start gap-3">
                    <Info className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
                    <div>
                      <p className="text-sm font-medium text-amber-800">Cache backend: {form.cache_backend}</p>
                      <p className="text-xs text-amber-600 mt-0.5">
                        {form.cache_backend === 'memory' && 'In-memory LRU cache. Fast but not shared across replicas. Best for single-instance deployments.'}
                        {form.cache_backend === 'redis' && 'Distributed cache via Redis. Shared across all replicas. Requires REDIS_URL to be configured.'}
                        {form.cache_backend === 's3' && 'Persistent cache stored in S3. Survives restarts but adds latency. Requires S3 credentials.'}
                      </p>
                    </div>
                  </div>
                )}
              </>
            )}

            {activeTab === 'fallbacks' && (
              <>
                <SectionCard title="Fallback Chains" description="Define automatic failover paths between model groups" icon={Shield}>
                  <FallbackEditor
                    label="General Fallbacks"
                    description="When a model group fails, route to the fallback group"
                    entries={generalFallbacks}
                    onChange={setGeneralFallbacks}
                  />
                  <div className="border-t border-gray-100 my-4" />
                  <FallbackEditor
                    label="Context Window Fallbacks"
                    description="When a request exceeds the model's context limit"
                    entries={ctxFallbacks}
                    onChange={setCtxFallbacks}
                  />
                  <div className="border-t border-gray-100 my-4" />
                  <FallbackEditor
                    label="Content Policy Fallbacks"
                    description="When content is flagged by a model's safety filter"
                    entries={contentFallbacks}
                    onChange={setContentFallbacks}
                  />
                </SectionCard>
              </>
            )}

            {activeTab === 'health' && (
              <>
                <SectionCard title="Background Health Checks" description="Automated monitoring of model deployments" icon={HeartPulse}>
                  <div className="space-y-5">
                    <div className="flex items-center justify-between py-2">
                      <div>
                        <p className="text-sm font-medium text-gray-700">Enable Health Checks</p>
                        <p className="text-xs text-gray-400 mt-0.5">Periodically test deployments and mark unhealthy ones</p>
                      </div>
                      <Toggle checked={form.background_health_checks} onChange={(v) => setForm({ ...form, background_health_checks: v })} id="health-toggle" />
                    </div>
                    {form.background_health_checks && (
                      <div className="pt-2 border-t border-gray-100">
                        <FieldGroup label="Check Interval" hint="How often each deployment is health-checked">
                          <div className="relative w-48">
                            <input type="number" value={form.health_check_interval} onChange={(e) => setForm({ ...form, health_check_interval: Number(e.target.value) })} className={inputClass + ' pr-8'} />
                            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                          </div>
                        </FieldGroup>
                      </div>
                    )}
                  </div>
                </SectionCard>

                <SectionCard title="Recent Fallback Events" description="Live feed of automatic failover activity" icon={AlertTriangle}>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-gray-200">
                          <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Time</th>
                          <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Model Group</th>
                          <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">From → To</th>
                          <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Reason</th>
                          <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {FALLBACK_EVENTS.map((evt, i) => (
                          <tr key={i} className="border-b border-gray-50 hover:bg-gray-50/50 transition-colors">
                            <td className="py-2.5 px-3 text-gray-500 text-xs tabular-nums">
                              <div className="flex items-center gap-1.5">
                                <Clock className="w-3 h-3 text-gray-400" />
                                {new Date(evt.timestamp * 1000).toLocaleTimeString()}
                              </div>
                            </td>
                            <td className="py-2.5 px-3">
                              <span className="font-mono text-xs font-medium text-gray-800 bg-gray-100 px-2 py-0.5 rounded">{evt.model_group}</span>
                            </td>
                            <td className="py-2.5 px-3">
                              <div className="flex items-center gap-1.5 text-xs font-mono text-gray-600">
                                <span className="truncate max-w-[130px]">{evt.from_deployment}</span>
                                {evt.to_deployment && (
                                  <>
                                    <ChevronRight className="w-3 h-3 text-gray-300 shrink-0" />
                                    <span className="truncate max-w-[130px]">{evt.to_deployment}</span>
                                  </>
                                )}
                                {!evt.to_deployment && <span className="text-gray-300 italic">no target</span>}
                              </div>
                            </td>
                            <td className="py-2.5 px-3">
                              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                                evt.error_classification === 'rate_limit' ? 'bg-amber-50 text-amber-700' :
                                evt.error_classification === 'timeout' ? 'bg-purple-50 text-purple-700' :
                                evt.error_classification === 'context_window_exceeded' ? 'bg-orange-50 text-orange-700' :
                                evt.error_classification === 'content_policy_violation' ? 'bg-red-50 text-red-700' :
                                'bg-gray-50 text-gray-700'
                              }`}>
                                {evt.error_classification.replaceAll('_', ' ')}
                              </span>
                            </td>
                            <td className="py-2.5 px-3">
                              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${evt.success ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>
                                {evt.success ? '✓ Resolved' : '✕ Failed'}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </SectionCard>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
