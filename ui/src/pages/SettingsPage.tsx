import { useState, useEffect } from 'react';
import { useApi } from '../lib/hooks';
import { branding as brandingApi, settings, type UIBrandingAssetKind } from '../lib/api';
import {
  Settings, Route, Database, Shield, HeartPulse,
  Save, Check, ChevronRight, Plus, X, AlertTriangle,
  RefreshCw, Info, Clock, RotateCcw, Paintbrush, Undo2, Upload, Trash2, Image,
} from 'lucide-react';
import BrandLogo from '../components/BrandLogo';
import Button from '../components/Button';
import {
  contrastForeground,
  normalizeBranding,
  readableBrandInk,
  type UIBranding,
  visibleBrandSurface,
  visibleMenuHoverSurface,
} from '../lib/branding';
import { useBranding } from '../lib/brandingContext';

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

const TABS = [
  { id: 'general', label: 'General', icon: Settings },
  { id: 'theme', label: 'Theme', icon: Paintbrush },
  { id: 'routing', label: 'Routing & Reliability', icon: Route },
  { id: 'caching', label: 'Caching', icon: Database },
  { id: 'fallbacks', label: 'Fallbacks', icon: Shield },
  { id: 'health', label: 'Health & Events', icon: HeartPulse },
] as const;

type TabId = (typeof TABS)[number]['id'];

const STRATEGIES = [
  { value: 'simple-shuffle', label: 'Simple Shuffle', desc: 'Random distribution across deployments' },
  { value: 'least-busy', label: 'Least Busy', desc: 'Route to deployment with fewest active requests' },
  { value: 'latency-based-routing', label: 'Latency Based', desc: 'Prefer deployments with lowest latency' },
  { value: 'cost-based-routing', label: 'Cost Based', desc: 'Prefer cheapest available deployment' },
  { value: 'usage-based-routing', label: 'Usage Based', desc: 'Distribute based on usage quotas' },
  { value: 'tag-based-routing', label: 'Tag Based', desc: 'Route by deployment tags' },
  { value: 'priority-based-routing', label: 'Priority Based', desc: 'Route by deployment priority' },
  { value: 'rate-limit-aware', label: 'Rate Limit Aware', desc: 'Avoid rate-limited deployments' },
  { value: 'weighted', label: 'Weighted', desc: 'Custom weight distribution' },
];

const HEX_COLOR = /^#[0-9A-Fa-f]{6}$/;

function validateBranding(value: UIBranding): string | null {
  if (!value.instance_name.trim()) return 'Instance name is required.';
  if (value.instance_name.trim().length > 80) return 'Instance name must be 80 characters or fewer.';
  if ([...value.instance_name].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) {
    return 'Instance name cannot contain control characters.';
  }
  if (!HEX_COLOR.test(value.primary_color)) return 'Primary colour must use the #RRGGBB format.';
  if (!HEX_COLOR.test(value.secondary_color)) return 'Secondary colour must use the #RRGGBB format.';
  if (!HEX_COLOR.test(value.menu_hover_color)) return 'Menu hover colour must use the #RRGGBB format.';
  return null;
}

const BRANDING_IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp,image/svg+xml';
const BRANDING_FAVICON_ACCEPT = `${BRANDING_IMAGE_ACCEPT},image/x-icon,image/vnd.microsoft.icon`;
const BRANDING_ASSET_MAX_BYTES = 2 * 1024 * 1024;

function BrandingAssetField({
  assetKey,
  label,
  hint,
  url,
  busy,
  disabled,
  onUpload,
  onRemove,
}: {
  assetKey: UIBrandingAssetKind;
  label: string;
  hint: string;
  url: string | null;
  busy: boolean;
  disabled: boolean;
  onUpload: (file: File) => Promise<void>;
  onRemove: () => Promise<void>;
}) {
  const previewClass = assetKey === 'logo_full'
    ? 'h-12 max-w-full object-contain'
    : 'h-12 w-12 object-contain';

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
      <div className="mb-3 flex h-16 items-center justify-center rounded-lg border border-gray-200 bg-white px-3">
        {url ? (
          <img src={url} alt={`${label} preview`} className={previewClass} />
        ) : (
          <Image className="h-7 w-7 text-gray-300" aria-hidden="true" />
        )}
      </div>
      <p className="text-sm font-medium text-gray-800">{label}</p>
      <p className="mt-0.5 min-h-8 text-xs text-gray-500">{hint}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <label
          className={`inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-xs font-medium text-brand-on-primary transition-colors hover:bg-brand-primary-hover ${disabled ? 'pointer-events-none opacity-50' : 'cursor-pointer'}`}
        >
          <Upload className="h-3.5 w-3.5" />
          {busy ? 'Working…' : url ? 'Replace' : 'Upload'}
          <input
            type="file"
            accept={assetKey === 'favicon' ? BRANDING_FAVICON_ACCEPT : BRANDING_IMAGE_ACCEPT}
            className="sr-only"
            disabled={disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) void onUpload(file);
            }}
          />
        </label>
        {url && (
          <button
            type="button"
            onClick={() => { void onRemove(); }}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-600 transition-colors hover:border-red-200 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" /> Remove
          </button>
        )}
      </div>
      <p className="mt-2 text-[11px] text-gray-400">PNG, JPEG, WebP, SVG{assetKey === 'favicon' ? ', or ICO' : ''}; maximum 2 MB.</p>
    </div>
  );
}

function ColorField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint: string;
}) {
  const pickerValue = HEX_COLOR.test(value) ? value : '#000000';
  return (
    <FieldGroup label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={pickerValue}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          className="h-10 w-12 cursor-pointer rounded-lg border border-gray-200 bg-white p-1"
          aria-label={`${label} picker`}
        />
        <input
          value={value}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-sm uppercase transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary"
          maxLength={7}
          spellCheck={false}
        />
      </div>
    </FieldGroup>
  );
}

function Toggle({ checked, onChange, id }: { checked: boolean; onChange: (v: boolean) => void; id: string }) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${checked ? 'bg-brand-primary' : 'bg-gray-200'}`}
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
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand-secondary-soft">
            <Icon className="h-4 w-4 text-brand-secondary-ink" />
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
        <button onClick={() => onChange([...entries, { from: '', to: '' }])} className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-brand-primary-ink transition-colors hover:bg-brand-primary-soft hover:text-brand-primary-ink-hover">
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
              <input value={entry.from} onChange={(e) => { const u = [...entries]; u[i] = { ...u[i], from: e.target.value }; onChange(u); }} placeholder="Source model group" className="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary" />
              <ChevronRight className="w-4 h-4 text-gray-300 shrink-0" />
              <input value={entry.to} onChange={(e) => { const u = [...entries]; u[i] = { ...u[i], to: e.target.value }; onChange(u); }} placeholder="Fallback model group" className="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary" />
              <button
                onClick={() => onChange(entries.filter((_, idx) => idx !== i))}
                aria-label="Remove fallback rule"
                className="rounded-md p-1 text-gray-400 transition-colors hover:text-red-500 focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-1 focus-visible:text-red-500"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const { data, loading, refetch } = useApi(() => settings.get(), []);
  const { branding, setBranding } = useBranding();
  const [activeTab, setActiveTab] = useState<TabId>('general');
  const [form, setForm] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [fallbacks, setFallbacks] = useState<FallbackEntry[]>([]);
  const [ctxFallbacks, setCtxFallbacks] = useState<FallbackEntry[]>([]);
  const [contentFallbacks, setContentFallbacks] = useState<FallbackEntry[]>([]);
  const [fallbackEvents, setFallbackEvents] = useState<any[]>([]);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [brandingForm, setBrandingForm] = useState<UIBranding>(branding);
  const [persistedBranding, setPersistedBranding] = useState<UIBranding>(branding);
  const [brandingError, setBrandingError] = useState<string | null>(null);
  const [busyBrandingAsset, setBusyBrandingAsset] = useState<UIBrandingAssetKind | null>(null);

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
      });
      const loadedBranding = normalizeBranding({
        instance_name: data.general_settings?.instance_name,
        ...data.general_settings?.ui_branding,
      });
      setBrandingForm(loadedBranding);
      setPersistedBranding(loadedBranding);
      setFallbacks(parseFallbacks(data.deltallm_settings?.fallbacks || []));
      setCtxFallbacks(parseFallbacks(data.deltallm_settings?.context_window_fallbacks || []));
      setContentFallbacks(parseFallbacks(data.deltallm_settings?.content_policy_fallbacks || []));
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
        },
        deltallm_settings: {
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

  const handleBrandingSave = async () => {
    const validationError = validateBranding(brandingForm);
    if (validationError) {
      setBrandingError(validationError);
      return;
    }

    setSaving(true);
    setBrandingError(null);
    try {
      const savedBranding = normalizeBranding(await brandingApi.update({
        instance_name: brandingForm.instance_name.trim(),
        primary_color: brandingForm.primary_color,
        secondary_color: brandingForm.secondary_color,
        menu_hover_color: brandingForm.menu_hover_color,
      }));
      setBrandingForm(savedBranding);
      setPersistedBranding(savedBranding);
      setBranding(savedBranding);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (error) {
      setBrandingError(error instanceof Error ? error.message : 'Could not save theme.');
    } finally {
      setSaving(false);
    }
  };

  const applySavedAsset = (assetKey: UIBrandingAssetKind, saved: UIBranding) => {
    const field = assetKey === 'logo_mark'
      ? 'logo_mark_url'
      : assetKey === 'logo_full'
        ? 'logo_full_url'
        : 'favicon_url';
    setBrandingForm((current) => ({ ...current, [field]: saved[field] }));
    setPersistedBranding((current) => ({ ...current, [field]: saved[field] }));
    setBranding(saved);
  };

  const handleBrandingAssetUpload = async (assetKey: UIBrandingAssetKind, file: File) => {
    if (file.size > BRANDING_ASSET_MAX_BYTES) {
      setBrandingError('Branding assets must be 2 MB or smaller.');
      return;
    }
    setBusyBrandingAsset(assetKey);
    setBrandingError(null);
    try {
      const saved = normalizeBranding(await brandingApi.uploadAsset(assetKey, file));
      applySavedAsset(assetKey, saved);
    } catch (error) {
      setBrandingError(error instanceof Error ? error.message : 'Could not upload branding asset.');
    } finally {
      setBusyBrandingAsset(null);
    }
  };

  const handleBrandingAssetRemove = async (assetKey: UIBrandingAssetKind) => {
    setBusyBrandingAsset(assetKey);
    setBrandingError(null);
    try {
      const saved = normalizeBranding(await brandingApi.deleteAsset(assetKey));
      applySavedAsset(assetKey, saved);
    } catch (error) {
      setBrandingError(error instanceof Error ? error.message : 'Could not remove branding asset.');
    } finally {
      setBusyBrandingAsset(null);
    }
  };

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center h-64">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-brand-primary" />
      </div>
    );
  }

  const inputClass = 'w-full px-3 py-2 border border-gray-200 rounded-lg text-sm bg-gray-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary focus:border-transparent transition-all';
  const selectClass = inputClass;
  const themePreview = normalizeBranding(brandingForm);
  const primaryPreview = visibleBrandSurface(themePreview.primary_color);
  const secondaryPreview = visibleBrandSurface(themePreview.secondary_color);
  const menuHoverPreview = visibleMenuHoverSurface(themePreview.menu_hover_color);
  const themeDirty = JSON.stringify(brandingForm) !== JSON.stringify(persistedBranding);

  return (
    <div className="p-4 sm:p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Settings</h1>
            <p className="text-sm text-gray-500 mt-0.5">Configure appearance, proxy behavior, routing, caching, and reliability</p>
          </div>
          <Button
            variant={saved ? 'success' : 'primary'}
            onClick={activeTab === 'theme' ? handleBrandingSave : handleSave}
            loading={saving}
            disabled={activeTab === 'theme' && (!themeDirty || busyBrandingAsset !== null)}
          >
            {saved ? <Check className="w-4 h-4" /> : <Save className="w-4 h-4" />}
            {saved ? 'Saved' : saving ? 'Saving...' : 'Save Changes'}
          </Button>
        </div>

        <div className="flex flex-col gap-6 md:flex-row">
          <nav className="w-56 shrink-0 hidden md:block">
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-1.5 sticky top-6">
              {TABS.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${isActive ? 'bg-brand-secondary-soft text-brand-secondary-ink' : 'text-gray-600 hover:bg-brand-menu-hover hover:text-brand-menu-hover-foreground'}`}
                  >
                    <Icon className={`w-4 h-4 ${isActive ? 'text-brand-secondary-ink' : 'text-gray-400'}`} />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </nav>

          <div className="md:hidden mb-4 w-full">
            <div className="flex gap-1 overflow-x-auto bg-white rounded-xl border border-gray-200 shadow-sm p-1.5">
              {TABS.map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium whitespace-nowrap transition-colors ${isActive ? 'bg-brand-secondary-soft text-brand-secondary-ink' : 'text-gray-600'}`}
                  >
                    <Icon className={`w-3.5 h-3.5 ${isActive ? 'text-brand-secondary-ink' : 'text-gray-400'}`} />
                    {tab.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="flex-1 min-w-0 space-y-5">
            {activeTab === 'general' && (
              <>
                <SectionCard title="Runtime" description="Basic proxy logging configuration" icon={Settings}>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                    <FieldGroup label="Log Level" hint="Controls verbosity of system logs">
                      <select value={form.log_level || 'INFO'} onChange={(e) => setForm({ ...form, log_level: e.target.value })} className={selectClass}>
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

            {activeTab === 'theme' && (
              <>
                <SectionCard title="Theme identity" description="Customize the product shell for this installation" icon={Paintbrush}>
                  <div className="space-y-5">
                    {brandingError && (
                      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                        {brandingError}
                      </div>
                    )}
                    <FieldGroup label="Instance Name" hint="Shown in the sidebar, authentication screens, browser title, and emails">
                      <input
                        value={brandingForm.instance_name}
                        onChange={(event) => setBrandingForm({ ...brandingForm, instance_name: event.target.value })}
                        className={inputClass}
                        maxLength={80}
                      />
                    </FieldGroup>
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                      <BrandingAssetField
                        assetKey="logo_mark"
                        label="Simple logo"
                        hint="Square mark used in collapsed and compact placements."
                        url={brandingForm.logo_mark_url}
                        busy={busyBrandingAsset === 'logo_mark'}
                        disabled={busyBrandingAsset !== null}
                        onUpload={(file) => handleBrandingAssetUpload('logo_mark', file)}
                        onRemove={() => handleBrandingAssetRemove('logo_mark')}
                      />
                      <BrandingAssetField
                        assetKey="logo_full"
                        label="Expanded logo"
                        hint="Horizontal wordmark used when enough space is available."
                        url={brandingForm.logo_full_url}
                        busy={busyBrandingAsset === 'logo_full'}
                        disabled={busyBrandingAsset !== null}
                        onUpload={(file) => handleBrandingAssetUpload('logo_full', file)}
                        onRemove={() => handleBrandingAssetRemove('logo_full')}
                      />
                      <BrandingAssetField
                        assetKey="favicon"
                        label="Favicon"
                        hint="Browser icon; the built-in mark is used when empty."
                        url={brandingForm.favicon_url}
                        busy={busyBrandingAsset === 'favicon'}
                        disabled={busyBrandingAsset !== null}
                        onUpload={(file) => handleBrandingAssetUpload('favicon', file)}
                        onRemove={() => handleBrandingAssetRemove('favicon')}
                      />
                    </div>
                  </div>
                </SectionCard>

                <SectionCard title="Theme colours" description="Semantic colours are applied without changing warning, success, or danger states" icon={Paintbrush}>
                  <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
                    <ColorField
                      label="Primary"
                      value={brandingForm.primary_color}
                      onChange={(value) => setBrandingForm({ ...brandingForm, primary_color: value })}
                      hint="Primary actions and focus rings"
                    />
                    <ColorField
                      label="Secondary"
                      value={brandingForm.secondary_color}
                      onChange={(value) => setBrandingForm({ ...brandingForm, secondary_color: value })}
                      hint="Secondary actions and navigation accents"
                    />
                    <ColorField
                      label="Menu Hover"
                      value={brandingForm.menu_hover_color}
                      onChange={(value) => setBrandingForm({ ...brandingForm, menu_hover_color: value })}
                      hint="Navigation hover background"
                    />
                  </div>
                </SectionCard>

                <SectionCard title="Preview" description="Review logo and control treatments before saving">
                  <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                    <div className="space-y-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
                      <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">Logo treatments</p>
                      <div className="rounded-lg border border-gray-200 bg-white p-3">
                        <BrandLogo variant="expanded" brandingOverride={themePreview} />
                      </div>
                      <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-3">
                        <BrandLogo variant="mark" brandingOverride={themePreview} />
                        <span className="text-xs text-gray-500">Collapsed mark</span>
                      </div>
                    </div>
                    <div className="space-y-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
                      <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">Interactive treatments</p>
                      <div className="flex flex-wrap gap-2">
                        <span
                          className="rounded-lg px-4 py-2 text-sm font-medium"
                          style={{
                            backgroundColor: primaryPreview,
                            color: contrastForeground(primaryPreview),
                          }}
                        >
                          Primary action
                        </span>
                        <span
                          className="rounded-lg border bg-white px-4 py-2 text-sm font-medium"
                          style={{
                            borderColor: secondaryPreview,
                            color: readableBrandInk(themePreview.secondary_color),
                          }}
                        >
                          Secondary action
                        </span>
                      </div>
                      <div
                        className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium"
                        style={{
                          backgroundColor: menuHoverPreview,
                          color: contrastForeground(menuHoverPreview),
                        }}
                      >
                        <Settings className="h-4 w-4" /> Menu hover
                      </div>
                    </div>
                  </div>
                  <div className="mt-5 flex justify-end">
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setBrandingForm(persistedBranding);
                        setBrandingError(null);
                      }}
                      disabled={!themeDirty}
                    >
                      <Undo2 className="h-4 w-4" /> Discard changes
                    </Button>
                  </div>
                </SectionCard>
              </>
            )}

            {activeTab === 'routing' && (
              <>
                <SectionCard title="Routing Strategy" description="How requests are distributed across model deployments" icon={Route}>
                  <div className="grid grid-cols-1 gap-2">
                    {STRATEGIES.map((s) => (
                      <label
                        key={s.value}
                        className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${form.routing_strategy === s.value ? 'border-violet-300 bg-violet-50/50 ring-1 ring-brand-primary/20' : 'border-gray-100 hover:border-gray-200 hover:bg-gray-50'}`}
                      >
                        <input
                          type="radio"
                          name="strategy"
                          value={s.value}
                          checked={form.routing_strategy === s.value}
                          onChange={(e) => setForm({ ...form, routing_strategy: e.target.value })}
                          className="accent-brand-primary"
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
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                    <FieldGroup label="Max Retries" hint="Number of retry attempts per failed request">
                      <input type="number" value={form.num_retries ?? ''} onChange={(e) => setForm({ ...form, num_retries: e.target.value })} className={inputClass} />
                    </FieldGroup>
                    <FieldGroup label="Request Timeout" hint="Maximum seconds before a request is cancelled">
                      <div className="relative">
                        <input type="number" value={form.timeout ?? ''} onChange={(e) => setForm({ ...form, timeout: e.target.value })} className={inputClass + ' pr-10'} />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                      </div>
                    </FieldGroup>
                    <FieldGroup label="Cooldown Time" hint="Seconds a deployment is sidelined after failing">
                      <div className="relative">
                        <input type="number" value={form.cooldown_time ?? ''} onChange={(e) => setForm({ ...form, cooldown_time: e.target.value })} className={inputClass + ' pr-10'} />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                      </div>
                    </FieldGroup>
                    <FieldGroup label="Retry Base Delay" hint="Base delay for exponential backoff between retries">
                      <div className="relative">
                        <input type="number" step="0.1" value={form.retry_after ?? ''} onChange={(e) => setForm({ ...form, retry_after: e.target.value })} className={inputClass + ' pr-10'} />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                      </div>
                    </FieldGroup>
                    <FieldGroup label="Allowed Fails" hint="Failures before a deployment enters cooldown">
                      <input type="number" value={form.allowed_fails ?? ''} onChange={(e) => setForm({ ...form, allowed_fails: e.target.value })} className={inputClass} />
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
                      <Toggle checked={form.cache_enabled || false} onChange={(v) => setForm({ ...form, cache_enabled: v })} id="cache-toggle" />
                    </div>

                    {form.cache_enabled && (
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 pt-2 border-t border-gray-100">
                        <FieldGroup label="Backend" hint="Where cached responses are stored">
                          <select value={form.cache_backend || 'memory'} onChange={(e) => setForm({ ...form, cache_backend: e.target.value })} className={selectClass}>
                            <option value="memory">Memory (in-process LRU)</option>
                            <option value="redis">Redis (distributed)</option>
                            <option value="s3">S3 (persistent)</option>
                          </select>
                        </FieldGroup>
                        <FieldGroup label="TTL" hint="How long cached responses remain valid">
                          <div className="relative">
                            <input type="number" value={form.cache_ttl ?? ''} onChange={(e) => setForm({ ...form, cache_ttl: e.target.value })} className={inputClass + ' pr-10'} />
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
              <SectionCard title="Fallback Chains" description="Define automatic failover paths between model groups" icon={Shield}>
                <FallbackEditor
                  label="General Fallbacks"
                  description="When a model group fails, route to the fallback group"
                  entries={fallbacks}
                  onChange={setFallbacks}
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
                      <Toggle checked={form.background_health_checks || false} onChange={(v) => setForm({ ...form, background_health_checks: v })} id="health-toggle" />
                    </div>
                    {form.background_health_checks && (
                      <div className="pt-2 border-t border-gray-100">
                        <FieldGroup label="Check Interval" hint="How often each deployment is health-checked">
                          <div className="relative w-48">
                            <input type="number" value={form.health_check_interval ?? ''} onChange={(e) => setForm({ ...form, health_check_interval: e.target.value })} className={inputClass + ' pr-10'} />
                            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">sec</span>
                          </div>
                        </FieldGroup>
                      </div>
                    )}
                  </div>
                </SectionCard>

                <SectionCard title="Recent Fallback Events" description="Live feed of automatic failover activity" icon={AlertTriangle}>
                  <div className="flex items-center gap-3 mb-4">
                    <button onClick={loadFallbackEvents} disabled={loadingEvents} className="flex items-center gap-2 text-sm font-medium text-brand-secondary-ink hover:text-brand-secondary-ink-hover px-3 py-1.5 rounded-lg hover:bg-violet-50 transition-colors disabled:opacity-50">
                      <RefreshCw className={`w-4 h-4 ${loadingEvents ? 'animate-spin' : ''}`} />
                      {loadingEvents ? 'Loading...' : 'Load Events'}
                    </button>
                    {fallbackEvents.length > 0 && (
                      <span className="text-xs text-gray-400">{fallbackEvents.length} events</span>
                    )}
                  </div>
                  {fallbackEvents.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-gray-200">
                            <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Time</th>
                            <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Model Group</th>
                            <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">From / To</th>
                            <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Reason</th>
                            <th className="text-left py-2.5 px-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {fallbackEvents.map((evt: any, i: number) => (
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
                                  {evt.to_deployment ? (
                                    <>
                                      <ChevronRight className="w-3 h-3 text-gray-300 shrink-0" />
                                      <span className="truncate max-w-[130px]">{evt.to_deployment}</span>
                                    </>
                                  ) : (
                                    <span className="text-gray-300 italic">no target</span>
                                  )}
                                </div>
                              </td>
                              <td className="py-2.5 px-3">
                                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                                  evt.error_classification === 'rate_limit' ? 'bg-amber-50 text-amber-700' :
                                  evt.error_classification === 'timeout' ? 'bg-purple-50 text-purple-700' :
                                  evt.error_classification === 'context_window_exceeded' ? 'bg-orange-50 text-orange-700' :
                                  evt.error_classification === 'content_policy_violation' ? 'bg-red-50 text-red-700' :
                                  evt.error_classification === 'success' ? 'bg-emerald-50 text-emerald-700' :
                                  'bg-gray-50 text-gray-700'
                                }`}>
                                  {(evt.error_classification || '').replaceAll('_', ' ')}
                                </span>
                              </td>
                              <td className="py-2.5 px-3">
                                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${evt.success ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>
                                  {evt.success ? 'Resolved' : 'Failed'}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="py-8 text-center rounded-lg border border-dashed border-gray-200">
                      <AlertTriangle className="w-5 h-5 text-gray-300 mx-auto mb-1.5" />
                      <p className="text-xs text-gray-400">No fallback events loaded. Click "Load Events" to see recent activity.</p>
                    </div>
                  )}
                </SectionCard>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
