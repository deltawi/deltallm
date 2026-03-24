import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Brain,
  CheckCircle2,
  ChevronRight,
  DollarSign,
  Layers,
  Pencil,
  Radio,
  RefreshCw,
  Route,
  Tag,
  Terminal,
  Trash2,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { ApiError, models, type DeploymentHealth } from '../lib/api';
import { modelEditPath } from '../lib/modelRoutes';
import ModelUsageExamplesCard from '../components/ModelUsageExamplesCard';
import { MODE_OPTIONS } from '../components/ModelForm';
import { HeroTabbedDetailShell, IconTabs, InlineStat, PanelCard } from '../components/admin/shells';

// ─── Constants ────────────────────────────────────────────────────────────────

const PROVIDER_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  openai:     { bg: 'bg-emerald-50',  text: 'text-emerald-700',  dot: 'bg-emerald-500'  },
  anthropic:  { bg: 'bg-violet-50',   text: 'text-violet-700',   dot: 'bg-violet-500'   },
  groq:       { bg: 'bg-orange-50',   text: 'text-orange-700',   dot: 'bg-orange-500'   },
  azure:      { bg: 'bg-blue-50',     text: 'text-blue-700',     dot: 'bg-blue-500'     },
  bedrock:    { bg: 'bg-amber-50',    text: 'text-amber-700',    dot: 'bg-amber-500'    },
  gemini:     { bg: 'bg-sky-50',      text: 'text-sky-700',      dot: 'bg-sky-500'      },
  mistral:    { bg: 'bg-rose-50',     text: 'text-rose-700',     dot: 'bg-rose-500'     },
  cohere:     { bg: 'bg-indigo-50',   text: 'text-indigo-700',   dot: 'bg-indigo-500'   },
  vllm:       { bg: 'bg-lime-50',     text: 'text-lime-700',     dot: 'bg-lime-500'     },
  triton:     { bg: 'bg-green-50',    text: 'text-green-700',    dot: 'bg-green-500'    },
};

const TAB_LIST = [
  { id: 'overview', label: 'Overview',  icon: Layers   },
  { id: 'runtime',  label: 'Runtime',   icon: Zap      },
  { id: 'routing',  label: 'Routing',   icon: Route    },
  { id: 'costs',    label: 'Costs',     icon: DollarSign },
  { id: 'usage',    label: 'API Usage', icon: Terminal },
] as const;

type TabId = (typeof TAB_LIST)[number]['id'];

// ─── Formatters ───────────────────────────────────────────────────────────────

function formatInteger(value: unknown): string | null {
  if (value == null || value === '') return null;
  const num = Number(value);
  if (Number.isNaN(num)) return null;
  return num.toLocaleString();
}

function formatDurationSeconds(value: unknown): string | null {
  const formatted = formatInteger(value);
  return formatted ? `${formatted}s` : null;
}

function formatCost(value: unknown): string | null {
  if (value == null || value === '') return null;
  const num = Number(value);
  if (Number.isNaN(num)) return null;
  if (num === 0) return '$0';
  if (num < 0.0001) return `$${num.toExponential(2)}`;
  return `$${num.toFixed(6)}`;
}

function formatUnixTimestamp(value: number | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

function timeSince(ts: number | null | undefined): string | null {
  if (!ts) return null;
  const secs = Math.round(Date.now() / 1000 - ts);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function modeSpecificItems(mode: string, model: any): Array<{ label: string; value: string | null }> {
  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  switch (mode) {
    case 'chat':
      return [
        { label: 'Context Window',    value: formatInteger(mi.max_tokens) },
        { label: 'Max Input Tokens',  value: formatInteger(mi.max_input_tokens) },
        { label: 'Max Output Tokens', value: formatInteger(mi.max_output_tokens) },
        { label: 'Per Request Cap',   value: formatInteger(lp.max_tokens) },
        { label: 'Stream Timeout',    value: formatDurationSeconds(lp.stream_timeout) },
      ];
    case 'embedding':
      return [
        { label: 'Context Window', value: formatInteger(mi.max_tokens) },
        { label: 'Vector Size',    value: formatInteger(mi.output_vector_size) },
      ];
    case 'image_generation':
      return [{ label: 'Cost / Image', value: formatCost(mi.input_cost_per_image) }];
    case 'audio_speech':
      return [
        { label: 'Cost / Character',      value: formatCost(mi.input_cost_per_character) },
        { label: 'Output Cost / Char',    value: formatCost(mi.output_cost_per_character) },
        { label: 'Input Cost / Second',   value: formatCost(mi.input_cost_per_second) },
        { label: 'Output Cost / Second',  value: formatCost(mi.output_cost_per_second) },
        { label: 'Input Audio Token',     value: formatCost(mi.input_cost_per_audio_token) },
        { label: 'Output Audio Token',    value: formatCost(mi.output_cost_per_audio_token) },
      ];
    case 'audio_transcription':
      return [
        { label: 'Input Cost / Second',  value: formatCost(mi.input_cost_per_second) },
        { label: 'Output Cost / Second', value: formatCost(mi.output_cost_per_second) },
        { label: 'Input Audio Token',    value: formatCost(mi.input_cost_per_audio_token) },
        { label: 'Input Cost / Token',   value: formatCost(mi.input_cost_per_token) },
        { label: 'Output Cost / Token',  value: formatCost(mi.output_cost_per_token) },
      ];
    case 'rerank':
      return [{ label: 'Cost / Token', value: formatCost(mi.input_cost_per_token) }];
    default:
      return [];
  }
}

function formatDefaultParamValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return String(value);
  return String(value);
}

function primaryCost(mode: string, mi: Record<string, any>): string | null {
  if (mode === 'image_generation') return formatCost(mi.input_cost_per_image);
  if (mode === 'audio_speech') {
    return (
      formatCost(mi.input_cost_per_character)
      || formatCost(mi.input_cost_per_audio_token)
      || formatCost(mi.output_cost_per_second)
      || formatCost(mi.input_cost_per_second)
    );
  }
  if (mode === 'audio_transcription') return formatCost(mi.input_cost_per_second) || formatCost(mi.input_cost_per_audio_token);
  return formatCost(mi.input_cost_per_token);
}

// ─── Small shared components ──────────────────────────────────────────────────

function ProviderPill({ provider }: { provider: string }) {
  const colors = PROVIDER_COLORS[provider] || { bg: 'bg-gray-100', text: 'text-gray-700', dot: 'bg-gray-400' };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${colors.bg} ${colors.text}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${colors.dot}`} />
      {provider.charAt(0).toUpperCase() + provider.slice(1)}
    </span>
  );
}

function Field({
  label,
  value,
  mono = false,
  full = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
  full?: boolean;
}) {
  return (
    <div className={`rounded-xl border border-gray-100 bg-white px-4 py-3 ${full ? 'col-span-2' : ''}`}>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-gray-400">{label}</div>
      <div className={`break-all text-sm text-gray-900 ${mono ? 'font-mono text-xs' : 'font-medium'}`}>{value}</div>
    </div>
  );
}

// ─── Tab content panels ───────────────────────────────────────────────────────

const EMPTY_DEPLOYMENT_HEALTH: DeploymentHealth = {
  healthy: false,
  in_cooldown: false,
  consecutive_failures: 0,
  last_error: null,
  last_error_at: null,
  last_success_at: null,
};

function TransportBadge({ transport }: { transport: string }) {
  if (transport === 'grpc') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-violet-100 px-2.5 py-1 text-xs font-semibold text-violet-700">
        <Radio className="h-3.5 w-3.5" />
        gRPC
      </span>
    );
  }
  return <span className="text-sm font-medium text-gray-700">HTTP</span>;
}

function OverviewTab({ model }: { model: any }) {
  const lp = model.deltallm_params || {};
  const health: DeploymentHealth = model.health || EMPTY_DEPLOYMENT_HEALTH;
  const maskedKey = lp.api_key
    ? `${lp.api_key.slice(0, 8)}${'•'.repeat(12)}${lp.api_key.slice(-4)}`
    : null;
  const transport = lp.transport || 'http';
  const isGrpc = transport === 'grpc';

  return (
    <div className="grid grid-cols-2 gap-3">
      <Field label="Public Model Name"    value={model.model_name} />
      <Field label="Deployment ID"        value={model.deployment_id} mono />
      <Field label="Provider"             value={<ProviderPill provider={model.provider} />} />
      <Field label="Provider Model"       value={lp.model || '—'} mono />
      <Field label="Transport"            value={<TransportBadge transport={transport} />} />
      {isGrpc && lp.grpc_address && (
        <Field label="gRPC Address" value={lp.grpc_address} mono />
      )}
      <Field label={isGrpc ? 'HTTP Fallback URL (OpenAI-compatible)' : 'API Base'} value={isGrpc ? (lp.http_fallback_base || lp.api_base || '—') : (lp.api_base || '—')} mono full />
      <Field label="API Key"              value={maskedKey || 'Managed outside this deployment'} mono full />
      {isGrpc && lp.triton_model_name && (
        <Field label="Triton Model Name" value={lp.triton_model_name} mono />
      )}
      {isGrpc && lp.triton_model_version && (
        <Field label="Triton Model Version" value={lp.triton_model_version} mono />
      )}
      <Field label="Timeout"              value={formatDurationSeconds(lp.timeout) || '—'} />
      <Field label="Consecutive Failures" value={String(health.consecutive_failures ?? 0)} />
      <Field label="Last Success"         value={timeSince(health.last_success_at) || '—'} />
      <Field label="Cooldown Active"      value={health.in_cooldown ? 'Yes' : 'No'} />
      {health.last_error && (
        <div className="col-span-2 rounded-xl border border-red-100 bg-red-50 px-4 py-3">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-red-400">Last Error</div>
          <div className="text-sm text-red-800">{health.last_error}</div>
          {health.last_error_at && (
            <div className="mt-1 text-xs text-red-500">{formatUnixTimestamp(health.last_error_at)}</div>
          )}
        </div>
      )}
    </div>
  );
}

function RuntimeTab({ model }: { model: any }) {
  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const modeItems = modeSpecificItems(model.mode || mi.mode || 'chat', model)
    .filter((i) => i.value);
  const defaults = mi.default_params && typeof mi.default_params === 'object'
    ? Object.entries(mi.default_params)
    : [];

  if (modeItems.length === 0 && defaults.length === 0) {
    return <p className="text-sm text-gray-500">No runtime settings configured.</p>;
  }

  return (
    <div className="space-y-5">
      {modeItems.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold text-gray-700">Token Limits &amp; Timeouts</h3>
          <div className="grid grid-cols-2 gap-3">
            {modeItems.map((item) => (
              <Field key={item.label} label={item.label} value={item.value!} />
            ))}
            {lp.timeout && (
              <Field label="Request Timeout" value={formatDurationSeconds(lp.timeout)!} />
            )}
          </div>
        </div>
      )}
      {defaults.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold text-gray-700">Default Parameters</h3>
          <div className="grid grid-cols-2 gap-3">
            {defaults.map(([k, v]) => (
              <Field key={k} label={k} value={formatDefaultParamValue(v)} mono />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RoutingTab({ model }: { model: any }) {
  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const rpmLimit = lp.rpm ?? mi.rpm_limit;
  const tpmLimit = lp.tpm ?? mi.tpm_limit;
  const weight = mi.weight ?? lp.weight;
  const tags: string[] = Array.isArray(mi.tags) ? mi.tags : [];
  const totalWeight = 10;
  const weightNum = Number(weight) || 0;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        {weight != null    && <Field label="Weight"    value={String(weight)} />}
        {mi.priority != null && <Field label="Priority" value={String(mi.priority)} />}
        {rpmLimit != null  && <Field label="RPM Limit" value={Number(rpmLimit).toLocaleString()} />}
        {tpmLimit != null  && <Field label="TPM Limit" value={Number(tpmLimit).toLocaleString()} />}
      </div>

      {weightNum > 0 && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">Weight distribution</span>
            <span className="text-xs text-gray-400">{weightNum} / {totalWeight} total weight</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-blue-500 transition-all"
              style={{ width: `${Math.min((weightNum / totalWeight) * 100, 100)}%` }}
            />
          </div>
          <p className="mt-1.5 text-xs text-gray-400">
            This deployment receives ~{Math.round((weightNum / totalWeight) * 100)}% of routed traffic.
          </p>
        </div>
      )}

      {tags.length > 0 && (
        <div>
          <h3 className="mb-3 text-sm font-semibold text-gray-700">Tags</h3>
          <div className="flex flex-wrap gap-2">
            {tags.map((tag: string) => (
              <span
                key={tag}
                className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-600"
              >
                <Tag className="h-3 w-3 text-gray-400" />
                {tag}
              </span>
            ))}
          </div>
        </div>
      )}

      {weight == null && rpmLimit == null && tpmLimit == null && (
        <p className="text-sm text-gray-500">No routing configuration set.</p>
      )}
    </div>
  );
}

function CostsTab({ model }: { model: any }) {
  const mi = model.model_info || {};
  const rows = [
    { label: 'Input Cost / Token',        value: formatCost(mi.input_cost_per_token),           hint: 'Standard request' },
    { label: 'Output Cost / Token',       value: formatCost(mi.output_cost_per_token),          hint: 'Standard request' },
    { label: 'Cost / Image',              value: formatCost(mi.input_cost_per_image),            hint: 'Image generation' },
    { label: 'Cost / Character',          value: formatCost(mi.input_cost_per_character),        hint: 'Audio speech' },
    { label: 'Output Cost / Character',   value: formatCost(mi.output_cost_per_character),       hint: 'Audio speech' },
    { label: 'Cost / Second',             value: formatCost(mi.input_cost_per_second),           hint: 'Audio' },
    { label: 'Output Cost / Second',      value: formatCost(mi.output_cost_per_second),          hint: 'Audio' },
    { label: 'Input Audio Token',         value: formatCost(mi.input_cost_per_audio_token),      hint: 'Audio' },
    { label: 'Output Audio Token',        value: formatCost(mi.output_cost_per_audio_token),     hint: 'Audio' },
    { label: 'Batch Price Multiplier',    value: mi.batch_price_multiplier != null ? `${mi.batch_price_multiplier}×` : null, hint: 'Batch discount' },
    { label: 'Batch Input Cost / Token',  value: formatCost(mi.batch_input_cost_per_token),      hint: 'Batch pricing' },
    { label: 'Batch Output Cost / Token', value: formatCost(mi.batch_output_cost_per_token),     hint: 'Batch pricing' },
  ].filter((r) => r.value);

  if (rows.length === 0) {
    return <p className="text-sm text-gray-500">No pricing configured for this deployment.</p>;
  }

  const inputCost = Number(mi.input_cost_per_token);
  const outputCost = Number(mi.output_cost_per_token);
  const batchMult = Number(mi.batch_price_multiplier);

  return (
    <div className="space-y-4">
      <div className="overflow-hidden rounded-2xl border border-gray-200">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-400">Field</th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-400">Rate</th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-gray-400">Note</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {rows.map((r) => (
              <tr key={r.label} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-gray-700">{r.label}</td>
                <td className="px-4 py-3 font-mono text-xs text-gray-900">{r.value}</td>
                <td className="px-4 py-3 text-xs text-gray-400">{r.hint}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!Number.isNaN(inputCost) && inputCost > 0 && !Number.isNaN(outputCost) && outputCost > 0 && (
        <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4 text-sm">
          <div className="flex items-start gap-3">
            <TrendingUp className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
            <div>
              <div className="font-semibold text-blue-800">Estimated cost for 1M tokens</div>
              <div className="mt-1 text-blue-700">
                Input: <strong>${(inputCost * 1_000_000).toFixed(2)}</strong>
                &nbsp;·&nbsp;
                Output: <strong>${(outputCost * 1_000_000).toFixed(2)}</strong>
              </div>
              {!Number.isNaN(batchMult) && batchMult > 0 && (
                <div className="mt-0.5 text-xs text-blue-500">
                  Batch pricing available at {batchMult * 100}% of standard rates.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function UsageTab({ modelName, mode }: { modelName: string; mode: string }) {
  return (
    <div className="space-y-4">
      <ModelUsageExamplesCard modelName={modelName} mode={mode} />

      <div className="rounded-2xl border border-gray-200 bg-white p-4">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500">Quick Reference</h4>
        <div className="space-y-2 text-sm text-gray-700">
          <div className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
            <span>
              Clients call{' '}
              <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-900">{modelName}</code>.
              {' '}DeltaLLM routes traffic transparently to the configured provider model.
            </span>
          </div>
          <div className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
            <span>
              Drop-in replacement for any OpenAI-compatible SDK — just swap{' '}
              <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-900">base_url</code>
              {' '}and{' '}
              <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-900">api_key</code>.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function ModelDetail() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const canEdit = userRole === 'platform_admin' || authMode === 'master_key';

  const { data: model, loading, refetch } = useApi(() => models.get(deploymentId!), [deploymentId]);

  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [healthActionMessage, setHealthActionMessage] = useState<string | null>(null);
  const [healthActionError, setHealthActionError] = useState<string | null>(null);

  const handleDelete = async () => {
    if (!confirm('Are you sure you want to delete this model deployment? This cannot be undone.')) return;
    try {
      await models.delete(deploymentId!);
      navigate('/models');
    } catch (err: any) {
      alert(err?.message || 'Failed to delete model');
    }
  };

  const handleCheckHealth = async () => {
    setCheckingHealth(true);
    setHealthActionMessage(null);
    setHealthActionError(null);
    try {
      const result = await models.checkHealth(deploymentId!);
      setHealthActionMessage(result.message);
      await refetch();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setHealthActionError(err.message);
      } else {
        setHealthActionError('Failed to run health check');
      }
    } finally {
      setCheckingHealth(false);
    }
  };

  // ── Loading state ──
  if (loading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center p-6">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
      </div>
    );
  }

  // ── Not found ──
  if (!model) {
    return (
      <div className="p-6">
        <button
          onClick={() => navigate('/models')}
          className="mb-4 flex items-center gap-2 text-sm text-gray-500 transition-colors hover:text-gray-700"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Models
        </button>
        <p className="text-gray-500">Model not found.</p>
      </div>
    );
  }

  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const mode = mi.mode || model.mode || 'chat';
  const modeOpt = MODE_OPTIONS.find((o) => o.value === mode);
  const modeLabel = modeOpt?.label || mode;
  const health: DeploymentHealth = model.health || EMPTY_DEPLOYMENT_HEALTH;

  // Compact stat strip values
  const rpmLimit = lp.rpm ?? mi.rpm_limit;
  const tpmLimit = lp.tpm ?? mi.tpm_limit;
  const weight = mi.weight ?? lp.weight;
  const contextWindow = mi.max_tokens
    ? `${(Number(mi.max_tokens) / 1000).toFixed(0)}K tok`
    : null;
  const inputCostDisplay = primaryCost(mode, mi);

  return (
    <HeroTabbedDetailShell
      backBar={(
        <button
          onClick={() => navigate('/models')}
          className="flex items-center gap-1.5 text-sm text-gray-500 transition hover:text-gray-800"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Models
        </button>
      )}
      hero={(
        <div className="relative overflow-hidden border-b border-gray-200 bg-white">
          <div className="absolute inset-0 bg-gradient-to-br from-blue-50 via-white to-violet-50 opacity-60" />
          <div className="absolute right-0 top-0 h-40 w-40 rounded-full bg-blue-100/40 blur-3xl" />
          <div className="absolute bottom-0 left-1/4 h-32 w-64 rounded-full bg-violet-100/30 blur-3xl" />

          <div className="relative px-6 pb-5 pt-6">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-100 px-2.5 py-1 text-xs font-semibold text-blue-700">
                <Brain className="h-3.5 w-3.5" />
                {modeLabel}
              </span>
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
                  model.healthy ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'
                }`}
              >
                {model.healthy
                  ? <><CheckCircle2 className="h-3.5 w-3.5" /> Healthy</>
                  : <><AlertTriangle className="h-3.5 w-3.5" /> Unhealthy</>}
              </span>
              {model.provider && <ProviderPill provider={model.provider} />}
              {(lp.transport === 'grpc') && (
                <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2.5 py-1 text-xs font-semibold text-violet-700">
                  <Radio className="h-3.5 w-3.5" />
                  gRPC
                </span>
              )}
            </div>

            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h1 className="break-words text-2xl font-bold text-gray-900">{model.model_name}</h1>
                {lp.model && (
                  <p className="mt-0.5 text-sm text-gray-500">
                    Routes to{' '}
                    <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-700">
                      {lp.model}
                    </code>
                    {model.provider && (
                      <> via <span className="font-medium capitalize">{model.provider}</span></>
                    )}
                  </p>
                )}
              </div>

              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={handleCheckHealth}
                  disabled={checkingHealth}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 shadow-sm transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <RefreshCw className={`h-4 w-4 ${checkingHealth ? 'animate-spin' : ''}`} />
                  Check Health
                </button>
                {canEdit && (
                  <>
                    <button
                      onClick={() => navigate(modelEditPath(deploymentId!))}
                      className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-blue-700"
                    >
                      <Pencil className="h-4 w-4" /> Edit
                    </button>
                    <button
                      onClick={handleDelete}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-600 shadow-sm transition hover:bg-red-50"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </>
                )}
              </div>
            </div>

            {healthActionMessage && (
              <div className="mt-4 rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
                {healthActionMessage}
              </div>
            )}
            {healthActionError && (
              <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                {healthActionError}
              </div>
            )}
            {!model.healthy && health.last_error && !healthActionError && (
              <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <div>
                    <div className="font-medium">Why this deployment is unhealthy</div>
                    <div className="mt-1">{health.last_error}</div>
                    {health.last_error_at && (
                      <div className="mt-1 text-xs text-red-700">
                        Last failed check: {formatUnixTimestamp(health.last_error_at)}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {(contextWindow || rpmLimit || tpmLimit || weight != null || inputCostDisplay) && (
              <div className="mt-5 flex flex-wrap items-center gap-6 divide-x divide-gray-100">
                {contextWindow && <InlineStat label="Context Window" value={contextWindow} />}
                {rpmLimit != null && <div className="pl-6"><InlineStat label="RPM" value={Number(rpmLimit).toLocaleString()} /></div>}
                {tpmLimit != null && (
                  <div className="pl-6">
                    <InlineStat
                      label="TPM"
                      value={Number(tpmLimit) >= 1_000_000
                        ? `${(Number(tpmLimit) / 1_000_000).toFixed(1)}M`
                        : Number(tpmLimit).toLocaleString()}
                    />
                  </div>
                )}
                {weight != null && <div className="pl-6"><InlineStat label="Weight" value={`${weight} / 10`} /></div>}
                {inputCostDisplay && <div className="pl-6"><InlineStat label="Input Cost" value={`${inputCostDisplay} / tok`} /></div>}
              </div>
            )}
          </div>
        </div>
      )}
      body={(
        <>
          <IconTabs
            active={activeTab}
            onChange={setActiveTab}
            items={TAB_LIST.map(({ id, label, icon }) => ({ id, label, icon }))}
          />
          <PanelCard>
            {activeTab === 'overview' && <OverviewTab model={model} />}
            {activeTab === 'runtime' && <RuntimeTab model={model} />}
            {activeTab === 'routing' && <RoutingTab model={model} />}
            {activeTab === 'costs' && <CostsTab model={model} />}
            {activeTab === 'usage' && <UsageTab modelName={model.model_name} mode={mode} />}
          </PanelCard>
        </>
      )}
    />
  );
}
