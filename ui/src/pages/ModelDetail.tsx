import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Clock3,
  DollarSign,
  Gauge,
  Layers,
  Pencil,
  RefreshCw,
  Server,
  ShieldCheck,
  Trash2,
  type LucideIcon,
} from 'lucide-react';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { ApiError, models } from '../lib/api';
import { modelEditPath } from '../lib/modelRoutes';
import Card from '../components/Card';
import ModelUsageExamplesCard from '../components/ModelUsageExamplesCard';
import ProviderBadge from '../components/ProviderBadge';
import StatusBadge from '../components/StatusBadge';
import { MODE_BADGE_COLORS, MODE_OPTIONS } from '../components/ModelForm';

function MetricTile({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: LucideIcon;
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
      <div className="mb-2 flex items-center gap-2">
        <div className="rounded-lg bg-white p-1.5 text-blue-600 shadow-sm">
          <Icon className="h-4 w-4" />
        </div>
        <span className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</span>
      </div>
      <div className="text-sm font-semibold text-gray-900">{value}</div>
      {hint ? <div className="mt-1 text-xs text-gray-500">{hint}</div> : null}
    </div>
  );
}

function SectionTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-base font-semibold text-gray-900">{title}</h2>
      {subtitle ? <p className="mt-1 text-sm text-gray-500">{subtitle}</p> : null}
    </div>
  );
}

function FieldGrid({
  items,
  columns = 2,
}: {
  items: Array<{ label: string; value: React.ReactNode; mono?: boolean }>;
  columns?: 1 | 2;
}) {
  const visible = items.filter((item) => item.value !== null && item.value !== undefined && item.value !== '');
  if (visible.length === 0) {
    return <p className="text-sm text-gray-500">Nothing configured.</p>;
  }

  return (
    <div className={`grid gap-3 ${columns === 2 ? 'md:grid-cols-2' : 'grid-cols-1'}`}>
      {visible.map((item) => (
        <div key={item.label} className="rounded-xl border border-gray-200 bg-white px-4 py-3">
          <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{item.label}</div>
          <div className={`mt-1 break-words text-sm text-gray-900 ${item.mono ? 'font-mono text-xs' : ''}`}>{item.value}</div>
        </div>
      ))}
    </div>
  );
}

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

function summarizeLimits(rpm: unknown, tpm: unknown): string {
  const rpmValue = formatInteger(rpm);
  const tpmValue = formatInteger(tpm);
  if (!rpmValue && !tpmValue) return 'No limits';
  if (rpmValue && tpmValue) return `${rpmValue} RPM · ${tpmValue} TPM`;
  if (rpmValue) return `${rpmValue} RPM`;
  return `${tpmValue} TPM`;
}

function primaryCost(mode: string, modelInfo: Record<string, any>): string | null {
  if (mode === 'image_generation') return formatCost(modelInfo.input_cost_per_image);
  if (mode === 'audio_speech') return formatCost(modelInfo.input_cost_per_character) || formatCost(modelInfo.input_cost_per_audio_token);
  if (mode === 'audio_transcription') return formatCost(modelInfo.input_cost_per_second);
  return formatCost(modelInfo.input_cost_per_token);
}

function modeSpecificItems(mode: string, model: any): Array<{ label: string; value: string | null }> {
  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};

  switch (mode) {
    case 'chat':
      return [
        { label: 'Context Window', value: formatInteger(mi.max_tokens) },
        { label: 'Max Input Tokens', value: formatInteger(mi.max_input_tokens) },
        { label: 'Max Output Tokens', value: formatInteger(mi.max_output_tokens) },
        { label: 'Per Request Cap', value: formatInteger(lp.max_tokens) },
        { label: 'Stream Timeout', value: formatDurationSeconds(lp.stream_timeout) },
      ];
    case 'embedding':
      return [
        { label: 'Context Window', value: formatInteger(mi.max_tokens) },
        { label: 'Vector Size', value: formatInteger(mi.output_vector_size) },
      ];
    case 'image_generation':
      return [{ label: 'Cost / Image', value: formatCost(mi.input_cost_per_image) }];
    case 'audio_speech':
      return [
        { label: 'Cost / Character', value: formatCost(mi.input_cost_per_character) },
        { label: 'Input Audio Token', value: formatCost(mi.input_cost_per_audio_token) },
        { label: 'Output Audio Token', value: formatCost(mi.output_cost_per_audio_token) },
      ];
    case 'audio_transcription':
      return [{ label: 'Cost / Second', value: formatCost(mi.input_cost_per_second) }];
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

function formatUnixTimestamp(value: number | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString();
}

export default function ModelDetail() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const canEdit = userRole === 'platform_admin' || authMode === 'master_key';
  const detail = useApi(() => models.get(deploymentId!), [deploymentId]);
  const { data: model, loading, refetch } = detail;
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [healthActionMessage, setHealthActionMessage] = useState<string | null>(null);
  const [healthActionError, setHealthActionError] = useState<string | null>(null);

  if (loading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center p-6">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!model) {
    return (
      <div className="p-6">
        <button onClick={() => navigate('/models')} className="mb-4 flex items-center gap-2 text-sm text-gray-500 transition-colors hover:text-gray-700">
          <ArrowLeft className="h-4 w-4" /> Back to Models
        </button>
        <p className="text-gray-500">Model not found.</p>
      </div>
    );
  }

  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const mode = mi.mode || model.mode || 'chat';
  const modeOpt = MODE_OPTIONS.find((option) => option.value === mode);
  const modeLabel = modeOpt?.label || mode;
  const modeDescription = modeOpt?.description || 'Model deployment';
  const modeIcon = modeOpt?.icon || <Layers className="h-4 w-4" />;
  const maskedKey = lp.api_key ? `${lp.api_key.slice(0, 8)}${'•'.repeat(12)}${lp.api_key.slice(-4)}` : null;
  const rpmLimit = lp.rpm ?? mi.rpm_limit;
  const tpmLimit = lp.tpm ?? mi.tpm_limit;
  const health = model.health;
  const tags = Array.isArray(mi.tags) ? mi.tags : [];
  const defaults = mi.default_params && typeof mi.default_params === 'object' ? Object.entries(mi.default_params) : [];
  const modeItems = modeSpecificItems(mode, model)
    .filter((item) => item.value)
    .map((item) => ({ label: item.label, value: item.value as string }));
  const costItems = [
    { label: 'Input Cost / Token', value: formatCost(mi.input_cost_per_token) },
    { label: 'Output Cost / Token', value: formatCost(mi.output_cost_per_token) },
    { label: 'Cost / Image', value: formatCost(mi.input_cost_per_image) },
    { label: 'Cost / Character', value: formatCost(mi.input_cost_per_character) },
    { label: 'Cost / Second', value: formatCost(mi.input_cost_per_second) },
    { label: 'Input Audio Token', value: formatCost(mi.input_cost_per_audio_token) },
    { label: 'Output Audio Token', value: formatCost(mi.output_cost_per_audio_token) },
    { label: 'Batch Multiplier', value: mi.batch_price_multiplier != null ? String(mi.batch_price_multiplier) : null },
    { label: 'Batch Input Cost / Token', value: formatCost(mi.batch_input_cost_per_token) },
    { label: 'Batch Output Cost / Token', value: formatCost(mi.batch_output_cost_per_token) },
  ].filter((item) => item.value);

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

  return (
    <div className="mx-auto max-w-6xl p-4 sm:p-6">
      <button onClick={() => navigate('/models')} className="mb-4 flex items-center gap-2 text-sm text-gray-500 transition-colors hover:text-gray-700">
        <ArrowLeft className="h-4 w-4" /> Back to Models
      </button>

      <div className="mb-6 flex flex-col gap-4 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${MODE_BADGE_COLORS[mode] || 'bg-gray-100 text-gray-700'}`}>
                {modeIcon}
                {modeLabel}
              </span>
              <StatusBadge status={model.healthy ? 'healthy' : 'unhealthy'} />
              <ProviderBadge provider={model.provider} model={lp.model} />
            </div>
            <h1 className="break-words text-2xl font-bold text-gray-900">{model.model_name}</h1>
            <p className="mt-1 text-sm text-gray-500">{modeDescription}</p>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              onClick={handleCheckHealth}
              disabled={checkingHealth}
              className="inline-flex items-center justify-center gap-2 rounded-lg border border-blue-200 px-4 py-2 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${checkingHealth ? 'animate-spin' : ''}`} /> Check Health
            </button>
          {canEdit ? (
            <>
              <button onClick={() => navigate(modelEditPath(deploymentId!))} className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700">
                <Pencil className="h-4 w-4" /> Edit
              </button>
              <button onClick={handleDelete} className="inline-flex items-center justify-center gap-2 rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-50">
                <Trash2 className="h-4 w-4" /> Delete
              </button>
            </>
          ) : null}
          </div>
        </div>

        {!model.healthy && (health?.last_error || healthActionError) ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-medium">Why this deployment is unhealthy</div>
                <div className="mt-1">{health?.last_error || healthActionError}</div>
                <div className="mt-1 text-xs text-red-700">
                  {health?.last_error_at ? `Last failed check: ${formatUnixTimestamp(health.last_error_at)}` : 'Run Check Health after changing provider settings.'}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {healthActionMessage ? (
          <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">{healthActionMessage}</div>
        ) : null}
        {healthActionError && model.healthy ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{healthActionError}</div>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricTile
            icon={ShieldCheck}
            label="Status"
            value={model.healthy ? 'Healthy' : 'Unhealthy'}
            hint={health?.in_cooldown ? 'Unavailable while cooldown is active' : 'Current router health'}
          />
          <MetricTile
            icon={Server}
            label="Provider Model"
            value={<code className="break-all text-xs font-mono text-gray-900">{lp.model || 'Not set'}</code>}
            hint={model.provider || 'Provider inferred from model string'}
          />
          <MetricTile
            icon={Gauge}
            label="Limits"
            value={summarizeLimits(rpmLimit, tpmLimit)}
            hint="Per-deployment capacity"
          />
          <MetricTile
            icon={DollarSign}
            label="Primary Cost"
            value={primaryCost(mode, mi) || 'Not configured'}
            hint="Main reporting field"
          />
        </div>
      </div>

      <div className="mb-6">
        <ModelUsageExamplesCard modelName={model.model_name} mode={mode} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <div className="space-y-6">
          <Card>
            <SectionTitle title="Overview" subtitle="The identifiers and provider settings operators need most often." />
            <FieldGrid
              items={[
                { label: 'Public Model Name', value: model.model_name },
                { label: 'Deployment ID', value: model.deployment_id, mono: true },
                { label: 'Provider', value: <ProviderBadge provider={model.provider} model={lp.model} /> },
                { label: 'Provider Model', value: lp.model, mono: true },
                { label: 'API Base', value: lp.api_base, mono: true },
                { label: 'API Version', value: lp.api_version },
                { label: 'API Key', value: maskedKey || 'Managed outside this deployment', mono: true },
                { label: 'Timeout', value: formatDurationSeconds(lp.timeout) },
                { label: 'Consecutive Failures', value: health?.consecutive_failures ? String(health.consecutive_failures) : null },
                { label: 'Last Success', value: formatUnixTimestamp(health?.last_success_at) },
                { label: 'Last Failure', value: formatUnixTimestamp(health?.last_error_at) },
              ]}
            />
          </Card>

          <Card>
            <SectionTitle title="Runtime Settings" subtitle="Mode-specific limits and defaults used when forwarding requests." />
            <FieldGrid items={modeItems} />
            {defaults.length > 0 ? (
              <div className="mt-4 border-t border-gray-100 pt-4">
                <div className="mb-3 text-sm font-medium text-gray-900">Default Parameters</div>
                <FieldGrid
                  items={defaults.map(([key, value]) => ({
                    label: key,
                    value: formatDefaultParamValue(value),
                    mono: true,
                  }))}
                />
              </div>
            ) : null}
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <SectionTitle title="Routing" subtitle="How this deployment participates in traffic distribution." />
            <FieldGrid
              columns={1}
              items={[
                { label: 'Weight', value: formatInteger(mi.weight ?? lp.weight) },
                { label: 'Priority', value: formatInteger(mi.priority) },
                { label: 'RPM Limit', value: formatInteger(rpmLimit) },
                { label: 'TPM Limit', value: formatInteger(tpmLimit) },
                {
                  label: 'Tags',
                  value: tags.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {tags.map((tag: string) => (
                        <span key={tag} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
                          {tag}
                        </span>
                      ))}
                    </div>
                  ) : null,
                },
              ]}
            />
          </Card>

          <Card>
            <SectionTitle title="Cost Tracking" subtitle="Pricing fields used by spend reporting and billing." />
            <FieldGrid columns={1} items={costItems.map((item) => ({ label: item.label, value: item.value }))} />
          </Card>

          <Card>
            <SectionTitle title="Quick Reference" subtitle="What this deployment means to callers and operators." />
            <div className="space-y-3 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
              <div className="flex items-start gap-3">
                <div className="rounded-lg bg-white p-1.5 text-blue-600 shadow-sm">
                  <Clock3 className="h-4 w-4" />
                </div>
                <div className="leading-6">
                  Clients call <code className="rounded bg-white px-1.5 py-0.5 text-xs text-blue-900">{model.model_name}</code>. DeltaLLM forwards that traffic to{' '}
                  <code className="rounded bg-white px-1.5 py-0.5 text-xs text-blue-900">{lp.model || 'the configured provider model'}</code>.
                </div>
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
