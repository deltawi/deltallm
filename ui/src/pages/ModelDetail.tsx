import { useParams, useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { models } from '../lib/api';
import Card from '../components/Card';
import StatusBadge from '../components/StatusBadge';
import { MODE_OPTIONS, MODE_BADGE_COLORS } from '../components/ModelForm';
import {
  ArrowLeft, Pencil, Trash2, MessageSquare, FileText,
  Server, Gauge, DollarSign, Settings2, Layers
} from 'lucide-react';

const MODE_ICON_COLORS: Record<string, string> = {
  chat: 'bg-blue-50 text-blue-600',
  embedding: 'bg-purple-50 text-purple-600',
  image_generation: 'bg-pink-50 text-pink-600',
  audio_speech: 'bg-green-50 text-green-600',
  audio_transcription: 'bg-yellow-50 text-yellow-600',
  rerank: 'bg-orange-50 text-orange-600',
};

function InfoRow({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  if (value === null || value === undefined || value === '' || value === 'N/A') return null;
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1 sm:gap-4 py-2.5 border-b border-gray-50 last:border-0">
      <span className="text-sm text-gray-500 sm:w-44 shrink-0">{label}</span>
      <span className={`text-sm text-gray-900 ${mono ? 'font-mono bg-gray-50 px-2 py-0.5 rounded' : ''}`}>{value}</span>
    </div>
  );
}

function DetailSection({ icon: Icon, title, color, children }: { icon: any; title: string; color: string; children: React.ReactNode }) {
  return (
    <Card>
      <div className="p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className={`p-2 rounded-lg ${color}`}>
            <Icon className="w-4 h-4" />
          </div>
          <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        </div>
        <div>{children}</div>
      </div>
    </Card>
  );
}

function formatCost(val: any): string | null {
  if (val == null) return null;
  const n = Number(val);
  if (n === 0) return '$0';
  if (n < 0.0001) return `$${n.toExponential(2)}`;
  return `$${n.toFixed(6)}`;
}

export default function ModelDetail() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const navigate = useNavigate();
  const { session, authMode } = useAuth();
  const userRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const canEdit = userRole === 'platform_admin' || authMode === 'master_key';
  const { data: model, loading } = useApi(() => models.get(deploymentId!), [deploymentId]);

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[400px]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!model) {
    return (
      <div className="p-6">
        <button onClick={() => navigate('/models')} className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 mb-4">
          <ArrowLeft className="w-4 h-4" /> Back to Models
        </button>
        <p className="text-gray-500">Model not found.</p>
      </div>
    );
  }

  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const mode: string = mi.mode || model.mode || 'chat';
  const modeOpt = MODE_OPTIONS.find(o => o.value === mode);
  const modeLabel = modeOpt ? modeOpt.label : mode;
  const modeIcon = modeOpt ? modeOpt.icon : <Layers className="w-4 h-4" />;
  const maskedKey = lp.api_key ? `${lp.api_key.slice(0, 8)}${'•'.repeat(20)}${lp.api_key.slice(-4)}` : null;

  const handleDelete = async () => {
    if (!confirm('Are you sure you want to delete this model deployment? This cannot be undone.')) return;
    try {
      await models.delete(deploymentId!);
      navigate('/models');
    } catch (err: any) {
      alert(err?.message || 'Failed to delete model');
    }
  };

  const costItems: { label: string; value: string | null }[] = [];
  if (mi.input_cost_per_token != null) costItems.push({ label: 'Input Cost / Token', value: formatCost(mi.input_cost_per_token) });
  if (mi.output_cost_per_token != null) costItems.push({ label: 'Output Cost / Token', value: formatCost(mi.output_cost_per_token) });
  if (mi.input_cost_per_image != null) costItems.push({ label: 'Cost / Image', value: formatCost(mi.input_cost_per_image) });
  if (mi.input_cost_per_character != null) costItems.push({ label: 'Cost / Character', value: formatCost(mi.input_cost_per_character) });
  if (mi.input_cost_per_second != null) costItems.push({ label: 'Cost / Second', value: formatCost(mi.input_cost_per_second) });
  if (mi.input_cost_per_audio_token != null) costItems.push({ label: 'Input Audio Token Cost', value: formatCost(mi.input_cost_per_audio_token) });
  if (mi.output_cost_per_audio_token != null) costItems.push({ label: 'Output Audio Token Cost', value: formatCost(mi.output_cost_per_audio_token) });
  if (mi.batch_price_multiplier != null) costItems.push({ label: 'Batch Price Multiplier', value: String(mi.batch_price_multiplier) });
  if (mi.batch_input_cost_per_token != null) costItems.push({ label: 'Batch Input Cost / Token', value: formatCost(mi.batch_input_cost_per_token) });
  if (mi.batch_output_cost_per_token != null) costItems.push({ label: 'Batch Output Cost / Token', value: formatCost(mi.batch_output_cost_per_token) });

  const dpEntries = mi.default_params ? Object.entries(mi.default_params) : [];

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto">
      <button onClick={() => navigate('/models')} className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 mb-5 transition-colors">
        <ArrowLeft className="w-4 h-4" /> Back to Models
      </button>

      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-4">
          <div className={`p-3 rounded-xl ${MODE_ICON_COLORS[mode] || 'bg-gray-50 text-gray-600'}`}>
            {modeIcon}
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{model.model_name}</h1>
            <div className="flex items-center gap-3 mt-1.5">
              <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium ${MODE_BADGE_COLORS[mode] || 'bg-gray-100 text-gray-700'}`}>
                {modeLabel}
              </span>
              <StatusBadge status={model.healthy ? 'healthy' : 'unhealthy'} />
              <code className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded">{model.deployment_id}</code>
            </div>
          </div>
        </div>
        {canEdit && (
          <div className="flex items-center gap-2">
            <button onClick={() => navigate(`/models/${deploymentId}/edit`)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
              <Pencil className="w-4 h-4" /> Edit Settings
            </button>
            <button onClick={handleDelete} className="flex items-center gap-2 px-4 py-2 border border-red-200 text-red-600 rounded-lg text-sm font-medium hover:bg-red-50 transition-colors">
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <DetailSection icon={Server} title="Provider Connection" color="bg-slate-100 text-slate-600">
          <InfoRow label="Provider" value={model.provider} />
          <InfoRow label="Provider Model" value={lp.model} mono />
          <InfoRow label="API Base" value={lp.api_base} mono />
          <InfoRow label="API Key" value={maskedKey} mono />
          {lp.api_version && <InfoRow label="API Version" value={lp.api_version} />}
          <InfoRow label="Timeout" value={lp.timeout ? `${lp.timeout}s` : null} />
        </DetailSection>

        <DetailSection icon={Gauge} title="Rate Limits & Routing" color="bg-indigo-50 text-indigo-600">
          <InfoRow label="Weight" value={mi.weight ?? lp.weight} />
          <InfoRow label="Priority" value={mi.priority} />
          <InfoRow label="RPM Limit" value={lp.rpm || mi.rpm_limit ? String(lp.rpm || mi.rpm_limit) : null} />
          <InfoRow label="TPM Limit" value={lp.tpm || mi.tpm_limit ? String(lp.tpm || mi.tpm_limit) : null} />
          {Array.isArray(mi.tags) && mi.tags.length > 0 && (
            <InfoRow label="Tags" value={
              <div className="flex flex-wrap gap-1.5">
                {mi.tags.map((t: string) => (
                  <span key={t} className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs">{t}</span>
                ))}
              </div>
            } />
          )}
        </DetailSection>

        {mode === 'chat' && (
          <DetailSection icon={MessageSquare} title="Chat Settings" color="bg-blue-50 text-blue-600">
            <InfoRow label="Context Window" value={mi.max_tokens ? Number(mi.max_tokens).toLocaleString() : null} />
            <InfoRow label="Max Input Tokens" value={mi.max_input_tokens ? Number(mi.max_input_tokens).toLocaleString() : null} />
            <InfoRow label="Max Output Tokens" value={mi.max_output_tokens ? Number(mi.max_output_tokens).toLocaleString() : null} />
            <InfoRow label="Max Tokens / Request" value={lp.max_tokens ? Number(lp.max_tokens).toLocaleString() : null} />
            <InfoRow label="Stream Timeout" value={lp.stream_timeout ? `${lp.stream_timeout}s` : null} />
          </DetailSection>
        )}

        {mode === 'embedding' && (
          <DetailSection icon={FileText} title="Embedding Settings" color="bg-purple-50 text-purple-600">
            <InfoRow label="Context Window" value={mi.max_tokens ? Number(mi.max_tokens).toLocaleString() : null} />
            <InfoRow label="Output Vector Size" value={mi.output_vector_size ? Number(mi.output_vector_size).toLocaleString() : null} />
          </DetailSection>
        )}

        {costItems.length > 0 && (
          <DetailSection icon={DollarSign} title="Cost Tracking" color="bg-emerald-50 text-emerald-600">
            {costItems.map(c => c.value && <InfoRow key={c.label} label={c.label} value={c.value} />)}
          </DetailSection>
        )}

        {dpEntries.length > 0 && (
          <DetailSection icon={Settings2} title="Default Parameters" color="bg-amber-50 text-amber-600">
            <p className="text-xs text-gray-400 mb-3">Injected into provider requests when not specified by the caller</p>
            {dpEntries.map(([key, value]) => (
              <InfoRow key={key} label={key} value={
                <code className="text-xs bg-gray-50 px-2 py-0.5 rounded">{String(value)}</code>
              } />
            ))}
          </DetailSection>
        )}
      </div>
    </div>
  );
}
