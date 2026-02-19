import { useState } from 'react';
import { useApi } from '../lib/hooks';
import { models } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import Modal from '../components/Modal';
import { Plus, Pencil, Trash2, MessageSquare, FileText, Image, Mic, Volume2, ArrowUpDown } from 'lucide-react';

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

export default function Models() {
  const { data, loading, refetch } = useApi(() => models.list(), []);
  const [showCreate, setShowCreate] = useState(false);
  const [editItem, setEditItem] = useState<any>(null);
  const [form, setForm] = useState<FormState>({ ...emptyForm });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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

    return { model_name: form.model_name, litellm_params, model_info };
  };

  const handleCreate = async () => {
    setError(null);
    setSaving(true);
    try {
      await models.create(buildPayload());
      setShowCreate(false);
      setForm({ ...emptyForm });
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to create model');
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async () => {
    if (!editItem) return;
    setError(null);
    setSaving(true);
    try {
      await models.update(editItem.deployment_id, buildPayload());
      setEditItem(null);
      setForm({ ...emptyForm });
      refetch();
    } catch (err: any) {
      setError(err?.message || 'Failed to update model');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this model?')) return;
    try {
      await models.delete(id);
      refetch();
    } catch (err: any) {
      alert(err?.message || 'Failed to delete model');
    }
  };

  const openEdit = (row: any) => {
    const lp = row.litellm_params || {};
    const mi = row.model_info || {};
    setForm({
      mode: (mi.mode || row.mode || 'chat') as ModelMode,
      model_name: row.model_name || '',
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
    setError(null);
    setEditItem(row);
  };

  const closeModal = () => {
    setShowCreate(false);
    setEditItem(null);
    setError(null);
  };

  const modeLabel = (mode: string) => {
    const opt = MODE_OPTIONS.find(o => o.value === mode);
    return opt ? opt.label : mode;
  };

  const columns = [
    { key: 'model_name', header: 'Model Name', render: (r: any) => <span className="font-medium">{r.model_name}</span> },
    { key: 'mode', header: 'Type', render: (r: any) => {
      const mode = r.mode || r.model_info?.mode || 'chat';
      return <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${MODE_BADGE_COLORS[mode] || 'bg-gray-100 text-gray-700'}`}>{modeLabel(mode)}</span>;
    }},
    { key: 'provider', header: 'Provider', render: (r: any) => <span className="text-gray-500">{r.provider}</span> },
    { key: 'deployment_id', header: 'Deployment ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.deployment_id}</code> },
    { key: 'healthy', header: 'Health', render: (r: any) => <StatusBadge status={r.healthy ? 'healthy' : 'unhealthy'} /> },
    {
      key: 'actions', header: '', render: (r: any) => (
        <div className="flex gap-1">
          <button onClick={() => openEdit(r)} className="p-1.5 hover:bg-gray-100 rounded-lg"><Pencil className="w-4 h-4 text-gray-500" /></button>
          <button onClick={() => handleDelete(r.deployment_id)} className="p-1.5 hover:bg-red-50 rounded-lg"><Trash2 className="w-4 h-4 text-red-500" /></button>
        </div>
      ),
    },
  ];

  const inputClass = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";
  const mode = form.mode;

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Models</h1>
          <p className="text-sm text-gray-500 mt-1">Manage model deployments and providers</p>
        </div>
        <button onClick={() => { setForm({ ...emptyForm }); setError(null); setShowCreate(true); }} className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors">
          <Plus className="w-4 h-4" /> Add Model
        </button>
      </div>
      <Card>
        <DataTable columns={columns} data={data || []} loading={loading} emptyMessage="No models configured" />
      </Card>

      <Modal open={showCreate || !!editItem} onClose={closeModal} title={editItem ? 'Edit Model' : 'Add Model'}>
        <div className="space-y-4">

          <SectionLabel>Model Type</SectionLabel>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {MODE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setForm({ ...form, mode: opt.value })}
                className={`flex items-center gap-2 p-2.5 rounded-lg border text-left text-sm transition-colors ${
                  mode === opt.value
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
            <input value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} placeholder={mode === 'image_generation' ? 'dall-e-3' : mode === 'audio_speech' ? 'tts-1' : mode === 'audio_transcription' ? 'whisper-1' : mode === 'embedding' ? 'text-embedding-3-large' : mode === 'rerank' ? 'rerank-english-v3' : 'gpt-4o'} className={inputClass} />
            <p className="text-xs text-gray-400 mt-1">Public name users will reference in API calls</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Provider / Model</label>
            <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder={mode === 'image_generation' ? 'openai/dall-e-3' : mode === 'audio_speech' ? 'openai/tts-1' : mode === 'audio_transcription' ? 'openai/whisper-1' : mode === 'embedding' ? 'openai/text-embedding-3-large' : mode === 'rerank' ? 'cohere/rerank-english-v3.0' : 'openai/gpt-4o'} className={inputClass} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
              <input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="sk-..." className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
              <input value={form.api_base} onChange={(e) => setForm({ ...form, api_base: e.target.value })} placeholder="https://api.openai.com/v1" className={inputClass} />
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
              <input type="number" value={form.rpm} onChange={(e) => setForm({ ...form, rpm: e.target.value })} placeholder="500" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM</label>
              <input type="number" value={form.tpm} onChange={(e) => setForm({ ...form, tpm: e.target.value })} placeholder="100000" className={inputClass} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Weight</label>
              <input type="number" value={form.weight} onChange={(e) => setForm({ ...form, weight: e.target.value })} placeholder="1" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Load balancing</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Priority</label>
              <input type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} placeholder="0" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Higher = preferred</p>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Tags</label>
            <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="production, fast, us-east" className={inputClass} />
            <p className="text-xs text-gray-400 mt-1">Comma-separated, for tag-based routing</p>
          </div>

          {mode === 'chat' && (
            <>
              <SectionLabel>Chat Settings</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Stream Timeout (s)</label>
                  <input type="number" value={form.stream_timeout} onChange={(e) => setForm({ ...form, stream_timeout: e.target.value })} placeholder="120" className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens / Request</label>
                  <input type="number" value={form.max_tokens} onChange={(e) => setForm({ ...form, max_tokens: e.target.value })} placeholder="4096" className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Context Window</label>
                  <input type="number" value={form.max_context_window} onChange={(e) => setForm({ ...form, max_context_window: e.target.value })} placeholder="128000" className={inputClass} />
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Max Input Tokens</label>
                  <input type="number" value={form.max_input_tokens} onChange={(e) => setForm({ ...form, max_input_tokens: e.target.value })} placeholder="e.g. 128000" className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Max Output Tokens</label>
                  <input type="number" value={form.max_output_tokens} onChange={(e) => setForm({ ...form, max_output_tokens: e.target.value })} placeholder="e.g. 4096" className={inputClass} />
                </div>
              </div>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                  <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.000005" className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Output Cost / Token ($)</label>
                  <input type="number" step="any" value={form.output_cost_per_token} onChange={(e) => setForm({ ...form, output_cost_per_token: e.target.value })} placeholder="0.000015" className={inputClass} />
                </div>
              </div>
            </>
          )}

          {mode === 'embedding' && (
            <>
              <SectionLabel>Embedding Settings</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Context Window</label>
                  <input type="number" value={form.max_context_window} onChange={(e) => setForm({ ...form, max_context_window: e.target.value })} placeholder="8192" className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Output Vector Size</label>
                  <input type="number" value={form.output_vector_size} onChange={(e) => setForm({ ...form, output_vector_size: e.target.value })} placeholder="1536" className={inputClass} />
                </div>
              </div>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.0000001" className={inputClass} />
              </div>
            </>
          )}

          {mode === 'image_generation' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Image ($)</label>
                <input type="number" step="any" value={form.input_cost_per_image} onChange={(e) => setForm({ ...form, input_cost_per_image: e.target.value })} placeholder="0.04" className={inputClass} />
                <p className="text-xs text-gray-400 mt-1">Cost per generated image</p>
              </div>
            </>
          )}

          {mode === 'audio_speech' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Character ($)</label>
                  <input type="number" step="any" value={form.input_cost_per_character} onChange={(e) => setForm({ ...form, input_cost_per_character: e.target.value })} placeholder="0.000015" className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Input Audio Token Cost ($)</label>
                  <input type="number" step="any" value={form.input_cost_per_audio_token} onChange={(e) => setForm({ ...form, input_cost_per_audio_token: e.target.value })} placeholder="0.0001" className={inputClass} />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Output Audio Token Cost ($)</label>
                  <input type="number" step="any" value={form.output_cost_per_audio_token} onChange={(e) => setForm({ ...form, output_cost_per_audio_token: e.target.value })} placeholder="0.0001" className={inputClass} />
                </div>
              </div>
              <p className="text-xs text-gray-400">Use per-character pricing (e.g. OpenAI TTS) or per-audio-token pricing depending on provider</p>
            </>
          )}

          {mode === 'audio_transcription' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Second of Audio ($)</label>
                <input type="number" step="any" value={form.input_cost_per_second} onChange={(e) => setForm({ ...form, input_cost_per_second: e.target.value })} placeholder="0.0001" className={inputClass} />
                <p className="text-xs text-gray-400 mt-1">Cost per second of audio transcribed</p>
              </div>
            </>
          )}

          {mode === 'rerank' && (
            <>
              <SectionLabel>Cost Tracking</SectionLabel>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
                <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.000002" className={inputClass} />
                <p className="text-xs text-gray-400 mt-1">Cost per token in query + documents</p>
              </div>
            </>
          )}

          {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={closeModal} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={editItem ? handleUpdate : handleCreate} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : editItem ? 'Save Changes' : 'Create'}</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
