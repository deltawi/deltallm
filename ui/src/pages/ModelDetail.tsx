import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { useAuth } from '../lib/auth';
import { models } from '../lib/api';
import Card from '../components/Card';
import Modal from '../components/Modal';
import StatusBadge from '../components/StatusBadge';
import {
  ArrowLeft, Pencil, Trash2, MessageSquare, FileText, Image,
  Mic, Volume2, ArrowUpDown, X, Plus, Server, Gauge, DollarSign, Settings2, Layers
} from 'lucide-react';

type ModelMode = 'chat' | 'embedding' | 'image_generation' | 'audio_speech' | 'audio_transcription' | 'rerank';

const MODE_OPTIONS: { value: ModelMode; label: string; icon: React.ReactNode; description: string }[] = [
  { value: 'chat', label: 'Chat', icon: <MessageSquare className="w-4 h-4" />, description: 'Text completions & conversations' },
  { value: 'embedding', label: 'Embedding', icon: <FileText className="w-4 h-4" />, description: 'Text & document embeddings' },
  { value: 'image_generation', label: 'Image Generation', icon: <Image className="w-4 h-4" />, description: 'Text-to-image generation' },
  { value: 'audio_speech', label: 'Text-to-Speech', icon: <Volume2 className="w-4 h-4" />, description: 'Generate spoken audio from text' },
  { value: 'audio_transcription', label: 'Speech-to-Text', icon: <Mic className="w-4 h-4" />, description: 'Transcribe audio to text' },
  { value: 'rerank', label: 'Rerank', icon: <ArrowUpDown className="w-4 h-4" />, description: 'Document re-ranking' },
];

const MODE_BADGE_COLORS: Record<string, string> = {
  chat: 'bg-blue-100 text-blue-700',
  embedding: 'bg-purple-100 text-purple-700',
  image_generation: 'bg-pink-100 text-pink-700',
  audio_speech: 'bg-green-100 text-green-700',
  audio_transcription: 'bg-yellow-100 text-yellow-700',
  rerank: 'bg-orange-100 text-orange-700',
};

const MODE_ICON_COLORS: Record<string, string> = {
  chat: 'bg-blue-50 text-blue-600',
  embedding: 'bg-purple-50 text-purple-600',
  image_generation: 'bg-pink-50 text-pink-600',
  audio_speech: 'bg-green-50 text-green-600',
  audio_transcription: 'bg-yellow-50 text-yellow-600',
  rerank: 'bg-orange-50 text-orange-600',
};

const emptyForm = {
  mode: 'chat' as ModelMode,
  model_name: '',
  model: '',
  api_key: '',
  api_base: '',
  api_version: '',
  rpm: '',
  tpm: '',
  timeout: '',
  stream_timeout: '',
  max_tokens: '',
  weight: '',
  priority: '',
  tags: '',
  input_cost_per_token: '',
  output_cost_per_token: '',
  max_context_window: '',
  max_input_tokens: '',
  max_output_tokens: '',
  output_vector_size: '',
  input_cost_per_image: '',
  input_cost_per_character: '',
  input_cost_per_second: '',
  input_cost_per_audio_token: '',
  output_cost_per_audio_token: '',
};

type FormState = typeof emptyForm;

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 pt-2">
      <span className="text-xs font-semibold uppercase tracking-wider text-gray-400">{children}</span>
      <div className="flex-1 border-t border-gray-200" />
    </div>
  );
}

function numOrUndef(val: string): number | undefined {
  return val ? Number(val) : undefined;
}

function strOrEmpty(val: any): string {
  return val != null ? String(val) : '';
}

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
  const canEdit = userRole === 'platform_admin' || userRole === 'platform_co_admin' || authMode === 'master_key';
  const { data: model, loading, refetch } = useApi(() => models.get(deploymentId!), [deploymentId]);

  const [showEdit, setShowEdit] = useState(false);
  const [form, setForm] = useState<FormState>({ ...emptyForm });
  const [defaultParams, setDefaultParams] = useState<{ key: string; value: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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

  const lp = model.litellm_params || {};
  const mi = model.model_info || {};
  const mode: string = mi.mode || model.mode || 'chat';
  const modeOpt = MODE_OPTIONS.find(o => o.value === mode);
  const modeLabel = modeOpt ? modeOpt.label : mode;
  const modeIcon = modeOpt ? modeOpt.icon : <Layers className="w-4 h-4" />;
  const maskedKey = lp.api_key ? `${lp.api_key.slice(0, 8)}${'•'.repeat(20)}${lp.api_key.slice(-4)}` : null;

  const openEdit = () => {
    setForm({
      mode: (mi.mode || model.mode || 'chat') as ModelMode,
      model_name: model.model_name || '',
      model: lp.model || '',
      api_key: lp.api_key || '',
      api_base: lp.api_base || '',
      api_version: lp.api_version || '',
      rpm: strOrEmpty(lp.rpm),
      tpm: strOrEmpty(lp.tpm),
      timeout: strOrEmpty(lp.timeout),
      stream_timeout: strOrEmpty(lp.stream_timeout),
      max_tokens: strOrEmpty(lp.max_tokens),
      weight: strOrEmpty(mi.weight || lp.weight),
      priority: strOrEmpty(mi.priority),
      tags: Array.isArray(mi.tags) ? mi.tags.join(', ') : '',
      input_cost_per_token: strOrEmpty(mi.input_cost_per_token),
      output_cost_per_token: strOrEmpty(mi.output_cost_per_token),
      max_context_window: strOrEmpty(mi.max_tokens),
      max_input_tokens: strOrEmpty(mi.max_input_tokens),
      max_output_tokens: strOrEmpty(mi.max_output_tokens),
      output_vector_size: strOrEmpty(mi.output_vector_size),
      input_cost_per_image: strOrEmpty(mi.input_cost_per_image),
      input_cost_per_character: strOrEmpty(mi.input_cost_per_character),
      input_cost_per_second: strOrEmpty(mi.input_cost_per_second),
      input_cost_per_audio_token: strOrEmpty(mi.input_cost_per_audio_token),
      output_cost_per_audio_token: strOrEmpty(mi.output_cost_per_audio_token),
    });
    const existingDefaults = mi.default_params;
    if (existingDefaults && typeof existingDefaults === 'object') {
      setDefaultParams(Object.entries(existingDefaults).map(([key, value]) => ({ key, value: String(value) })));
    } else {
      setDefaultParams([]);
    }
    setError(null);
    setShowEdit(true);
  };

  const buildPayload = () => {
    const litellm_params: Record<string, any> = {
      model: form.model,
      api_key: form.api_key || undefined,
      api_base: form.api_base || undefined,
      api_version: form.api_version || undefined,
      rpm: numOrUndef(form.rpm),
      tpm: numOrUndef(form.tpm),
      timeout: numOrUndef(form.timeout),
      weight: numOrUndef(form.weight),
    };
    if (form.mode === 'chat') {
      litellm_params.stream_timeout = numOrUndef(form.stream_timeout);
      litellm_params.max_tokens = numOrUndef(form.max_tokens);
    }
    const model_info: Record<string, any> = {
      mode: form.mode,
      priority: numOrUndef(form.priority),
      tags: form.tags ? form.tags.split(',').map(t => t.trim()).filter(Boolean) : undefined,
      weight: numOrUndef(form.weight),
    };
    if (form.mode === 'chat') {
      model_info.input_cost_per_token = numOrUndef(form.input_cost_per_token);
      model_info.output_cost_per_token = numOrUndef(form.output_cost_per_token);
      model_info.max_tokens = numOrUndef(form.max_context_window);
      model_info.max_input_tokens = numOrUndef(form.max_input_tokens);
      model_info.max_output_tokens = numOrUndef(form.max_output_tokens);
    } else if (form.mode === 'embedding') {
      model_info.input_cost_per_token = numOrUndef(form.input_cost_per_token);
      model_info.output_vector_size = numOrUndef(form.output_vector_size);
      model_info.max_tokens = numOrUndef(form.max_context_window);
    } else if (form.mode === 'image_generation') {
      model_info.input_cost_per_image = numOrUndef(form.input_cost_per_image);
    } else if (form.mode === 'audio_speech') {
      model_info.input_cost_per_character = numOrUndef(form.input_cost_per_character);
      model_info.input_cost_per_audio_token = numOrUndef(form.input_cost_per_audio_token);
      model_info.output_cost_per_audio_token = numOrUndef(form.output_cost_per_audio_token);
    } else if (form.mode === 'audio_transcription') {
      model_info.input_cost_per_second = numOrUndef(form.input_cost_per_second);
    } else if (form.mode === 'rerank') {
      model_info.input_cost_per_token = numOrUndef(form.input_cost_per_token);
    }
    const dp: Record<string, any> = {};
    for (const p of defaultParams) {
      if (p.key.trim()) {
        const v = p.value.trim();
        if (v === 'true') dp[p.key.trim()] = true;
        else if (v === 'false') dp[p.key.trim()] = false;
        else if (v !== '' && !isNaN(Number(v)) && v !== '') dp[p.key.trim()] = Number(v);
        else dp[p.key.trim()] = v;
      }
    }
    model_info.default_params = Object.keys(dp).length > 0 ? dp : {};
    return { model_name: form.model_name, litellm_params, model_info };
  };

  const handleUpdate = async () => {
    setError(null);
    setSaving(true);
    try {
      await models.update(deploymentId!, buildPayload());
      setShowEdit(false);
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to update model');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm('Are you sure you want to delete this model deployment? This cannot be undone.')) return;
    try {
      await models.delete(deploymentId!);
      navigate('/models');
    } catch (err: any) {
      alert(err?.message || 'Failed to delete model');
    }
  };

  const inputClass = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";
  const fMode = form.mode;

  const costItems: { label: string; value: string | null }[] = [];
  if (mi.input_cost_per_token != null) costItems.push({ label: 'Input Cost / Token', value: formatCost(mi.input_cost_per_token) });
  if (mi.output_cost_per_token != null) costItems.push({ label: 'Output Cost / Token', value: formatCost(mi.output_cost_per_token) });
  if (mi.input_cost_per_image != null) costItems.push({ label: 'Cost / Image', value: formatCost(mi.input_cost_per_image) });
  if (mi.input_cost_per_character != null) costItems.push({ label: 'Cost / Character', value: formatCost(mi.input_cost_per_character) });
  if (mi.input_cost_per_second != null) costItems.push({ label: 'Cost / Second', value: formatCost(mi.input_cost_per_second) });
  if (mi.input_cost_per_audio_token != null) costItems.push({ label: 'Input Audio Token Cost', value: formatCost(mi.input_cost_per_audio_token) });
  if (mi.output_cost_per_audio_token != null) costItems.push({ label: 'Output Audio Token Cost', value: formatCost(mi.output_cost_per_audio_token) });

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
            <button onClick={openEdit} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
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

      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Model Settings">
        <div className="space-y-4">
          <SectionLabel>Model Type</SectionLabel>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {MODE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setForm({ ...form, mode: opt.value })}
                className={`flex items-center gap-2 p-2.5 rounded-lg border text-left text-sm transition-colors ${
                  fMode === opt.value
                    ? 'border-blue-500 bg-blue-50 text-blue-700'
                    : 'border-gray-200 hover:border-gray-300 text-gray-600'
                }`}
              >
                {opt.icon}
                <div>
                  <div className="font-medium text-xs">{opt.label}</div>
                  <div className="text-[10px] text-gray-400 leading-tight">{opt.description}</div>
                </div>
              </button>
            ))}
          </div>

          <SectionLabel>Provider Connection</SectionLabel>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Model Name</label>
            <input value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} className={inputClass} />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Provider / Model</label>
            <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} className={inputClass} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
              <input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
              <input value={form.api_base} onChange={(e) => setForm({ ...form, api_base: e.target.value })} className={inputClass} />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Version</label>
              <input value={form.api_version} onChange={(e) => setForm({ ...form, api_version: e.target.value })} placeholder="e.g. 2024-02-01 (Azure)" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Timeout (s)</label>
              <input type="number" value={form.timeout} onChange={(e) => setForm({ ...form, timeout: e.target.value })} placeholder="300" className={inputClass} />
            </div>
          </div>

          <SectionLabel>Rate Limits & Routing</SectionLabel>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM</label>
              <input type="number" value={form.rpm} onChange={(e) => setForm({ ...form, rpm: e.target.value })} className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM</label>
              <input type="number" value={form.tpm} onChange={(e) => setForm({ ...form, tpm: e.target.value })} className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Weight</label>
              <input type="number" value={form.weight} onChange={(e) => setForm({ ...form, weight: e.target.value })} className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Priority</label>
              <input type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} className={inputClass} />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Tags</label>
            <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="production, fast, us-east" className={inputClass} />
          </div>

          {fMode === 'chat' && (
            <>
              <SectionLabel>Chat Settings</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Stream Timeout (s)</label>
                  <input type="number" value={form.stream_timeout} onChange={(e) => setForm({ ...form, stream_timeout: e.target.value })} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens / Request</label>
                  <input type="number" value={form.max_tokens} onChange={(e) => setForm({ ...form, max_tokens: e.target.value })} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Context Window</label>
                  <input type="number" value={form.max_context_window} onChange={(e) => setForm({ ...form, max_context_window: e.target.value })} className={inputClass} />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Max Input Tokens</label>
                  <input type="number" value={form.max_input_tokens} onChange={(e) => setForm({ ...form, max_input_tokens: e.target.value })} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Max Output Tokens</label>
                  <input type="number" value={form.max_output_tokens} onChange={(e) => setForm({ ...form, max_output_tokens: e.target.value })} className={inputClass} />
                </div>
              </div>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                  <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Output Cost / Token ($)</label>
                  <input type="number" step="any" value={form.output_cost_per_token} onChange={(e) => setForm({ ...form, output_cost_per_token: e.target.value })} className={inputClass} />
                </div>
              </div>
            </>
          )}
          {fMode === 'embedding' && (
            <>
              <SectionLabel>Embedding Settings</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Context Window</label>
                  <input type="number" value={form.max_context_window} onChange={(e) => setForm({ ...form, max_context_window: e.target.value })} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Output Vector Size</label>
                  <input type="number" value={form.output_vector_size} onChange={(e) => setForm({ ...form, output_vector_size: e.target.value })} className={inputClass} />
                </div>
              </div>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} className={inputClass} />
              </div>
            </>
          )}
          {fMode === 'image_generation' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Image ($)</label>
                <input type="number" step="any" value={form.input_cost_per_image} onChange={(e) => setForm({ ...form, input_cost_per_image: e.target.value })} className={inputClass} />
              </div>
            </>
          )}
          {fMode === 'audio_speech' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Character ($)</label>
                  <input type="number" step="any" value={form.input_cost_per_character} onChange={(e) => setForm({ ...form, input_cost_per_character: e.target.value })} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Input Audio Token ($)</label>
                  <input type="number" step="any" value={form.input_cost_per_audio_token} onChange={(e) => setForm({ ...form, input_cost_per_audio_token: e.target.value })} className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Output Audio Token ($)</label>
                  <input type="number" step="any" value={form.output_cost_per_audio_token} onChange={(e) => setForm({ ...form, output_cost_per_audio_token: e.target.value })} className={inputClass} />
                </div>
              </div>
            </>
          )}
          {fMode === 'audio_transcription' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Second of Audio ($)</label>
                <input type="number" step="any" value={form.input_cost_per_second} onChange={(e) => setForm({ ...form, input_cost_per_second: e.target.value })} className={inputClass} />
              </div>
            </>
          )}
          {fMode === 'rerank' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} className={inputClass} />
              </div>
            </>
          )}

          <SectionLabel>Default Parameters</SectionLabel>
          <p className="text-xs text-gray-400">Default values injected into provider requests when not specified by the caller</p>
          {defaultParams.map((param, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                value={param.key}
                onChange={(e) => {
                  const updated = [...defaultParams];
                  updated[idx] = { ...updated[idx], key: e.target.value };
                  setDefaultParams(updated);
                }}
                placeholder="Key (e.g. voice)"
                className={`flex-1 ${inputClass}`}
              />
              <input
                value={param.value}
                onChange={(e) => {
                  const updated = [...defaultParams];
                  updated[idx] = { ...updated[idx], value: e.target.value };
                  setDefaultParams(updated);
                }}
                placeholder="Value (e.g. alloy)"
                className={`flex-1 ${inputClass}`}
              />
              <button
                type="button"
                onClick={() => setDefaultParams(defaultParams.filter((_, i) => i !== idx))}
                className="p-2 hover:bg-red-50 rounded-lg"
              >
                <X className="w-4 h-4 text-red-400" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => setDefaultParams([...defaultParams, { key: '', value: '' }])}
            className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-700 font-medium"
          >
            <Plus className="w-3.5 h-3.5" /> Add Default Parameter
          </button>

          {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowEdit(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleUpdate} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : 'Save Changes'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
