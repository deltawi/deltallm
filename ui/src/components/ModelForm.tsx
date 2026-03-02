import { useState } from 'react';
import Card from './Card';
import { MessageSquare, FileText, Image, Mic, Volume2, ArrowUpDown, Plus, X, ChevronDown } from 'lucide-react';

let collapsibleIdCounter = 0;

function CollapsibleCard({ title, defaultOpen = false, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  const [id] = useState(() => `collapsible-${++collapsibleIdCounter}`);
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={id}
        className="flex items-center justify-between w-full px-5 py-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset rounded-xl"
      >
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div id={id} role="region" aria-label={title} className="px-5 pb-5">{children}</div>}
    </div>
  );
}

type ModelMode = 'chat' | 'embedding' | 'image_generation' | 'audio_speech' | 'audio_transcription' | 'rerank';

export const MODE_OPTIONS: { value: ModelMode; label: string; icon: React.ReactNode; description: string }[] = [
  { value: 'chat', label: 'Chat', icon: <MessageSquare className="w-4 h-4" />, description: 'Text completions & conversations' },
  { value: 'embedding', label: 'Embedding', icon: <FileText className="w-4 h-4" />, description: 'Text & document embeddings' },
  { value: 'image_generation', label: 'Image Generation', icon: <Image className="w-4 h-4" />, description: 'Text-to-image generation' },
  { value: 'audio_speech', label: 'Text-to-Speech', icon: <Volume2 className="w-4 h-4" />, description: 'Generate spoken audio from text' },
  { value: 'audio_transcription', label: 'Speech-to-Text', icon: <Mic className="w-4 h-4" />, description: 'Transcribe audio to text' },
  { value: 'rerank', label: 'Rerank', icon: <ArrowUpDown className="w-4 h-4" />, description: 'Document re-ranking' },
];

export const MODE_BADGE_COLORS: Record<string, string> = {
  chat: 'bg-blue-100 text-blue-700',
  embedding: 'bg-purple-100 text-purple-700',
  image_generation: 'bg-pink-100 text-pink-700',
  audio_speech: 'bg-green-100 text-green-700',
  audio_transcription: 'bg-yellow-100 text-yellow-700',
  rerank: 'bg-orange-100 text-orange-700',
};

export interface ModelFormValues {
  mode: ModelMode;
  model_name: string;
  model: string;
  api_key: string;
  api_base: string;
  api_version: string;
  rpm: string;
  tpm: string;
  timeout: string;
  stream_timeout: string;
  max_tokens: string;
  weight: string;
  priority: string;
  tags: string;
  input_cost_per_token: string;
  output_cost_per_token: string;
  max_context_window: string;
  max_input_tokens: string;
  max_output_tokens: string;
  output_vector_size: string;
  input_cost_per_image: string;
  input_cost_per_character: string;
  input_cost_per_second: string;
  input_cost_per_audio_token: string;
  output_cost_per_audio_token: string;
  batch_price_multiplier: string;
  batch_input_cost_per_token: string;
  batch_output_cost_per_token: string;
}

export const EMPTY_FORM: ModelFormValues = {
  mode: 'chat',
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
  batch_price_multiplier: '',
  batch_input_cost_per_token: '',
  batch_output_cost_per_token: '',
};

function numOrUndef(val: string): number | undefined {
  return val ? Number(val) : undefined;
}

export function strOrEmpty(val: any): string {
  return val != null ? String(val) : '';
}

export function buildModelPayload(form: ModelFormValues, defaultParams: { key: string; value: string }[]) {
  const deltallm_params: Record<string, any> = {
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
    deltallm_params.stream_timeout = numOrUndef(form.stream_timeout);
    deltallm_params.max_tokens = numOrUndef(form.max_tokens);
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

  if (form.mode === 'chat' || form.mode === 'embedding') {
    model_info.batch_price_multiplier = numOrUndef(form.batch_price_multiplier);
    model_info.batch_input_cost_per_token = numOrUndef(form.batch_input_cost_per_token);
    model_info.batch_output_cost_per_token = numOrUndef(form.batch_output_cost_per_token);
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

  return { model_name: form.model_name, deltallm_params, model_info };
}

export function formFromModel(model: any): { form: ModelFormValues; defaultParams: { key: string; value: string }[] } {
  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const form: ModelFormValues = {
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
    batch_price_multiplier: strOrEmpty(mi.batch_price_multiplier),
    batch_input_cost_per_token: strOrEmpty(mi.batch_input_cost_per_token),
    batch_output_cost_per_token: strOrEmpty(mi.batch_output_cost_per_token),
  };
  const existingDefaults = mi.default_params;
  let defaultParams: { key: string; value: string }[] = [];
  if (existingDefaults && typeof existingDefaults === 'object') {
    defaultParams = Object.entries(existingDefaults).map(([key, value]) => ({ key, value: String(value) }));
  }
  return { form, defaultParams };
}

interface ModelFormProps {
  initialValues?: ModelFormValues;
  initialDefaultParams?: { key: string; value: string }[];
  onSubmit: (payload: ReturnType<typeof buildModelPayload>) => Promise<void>;
  onCancel: () => void;
  submitLabel?: string;
  saving?: boolean;
  error?: string | null;
}

const inputClass = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500";

export default function ModelForm({
  initialValues,
  initialDefaultParams,
  onSubmit,
  onCancel,
  submitLabel = 'Create',
  saving = false,
  error = null,
}: ModelFormProps) {
  const [form, setForm] = useState<ModelFormValues>(initialValues || { ...EMPTY_FORM });
  const [defaultParams, setDefaultParams] = useState<{ key: string; value: string }[]>(initialDefaultParams || []);

  const mode = form.mode;

  const handleSubmit = async () => {
    const payload = buildModelPayload(form, defaultParams);
    await onSubmit(payload);
  };

  return (
    <div className="space-y-6">
      <Card title="Model Type">
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
      </Card>

      <Card title="Provider Connection">
        <div className="space-y-4">
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
        </div>
      </Card>

      <CollapsibleCard title="Rate Limits & Routing">
        <div className="space-y-4">
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
        </div>
      </CollapsibleCard>

      {mode === 'chat' && (
        <CollapsibleCard title="Chat Settings">
          <div className="space-y-4">
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
          </div>
        </CollapsibleCard>
      )}

      {mode === 'embedding' && (
        <CollapsibleCard title="Embedding Settings">
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
        </CollapsibleCard>
      )}

      <CollapsibleCard title="Cost Tracking">
        <div className="space-y-4">
          {mode === 'chat' && (
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
          )}

          {mode === 'embedding' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
              <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.0000001" className={inputClass} />
            </div>
          )}

          {mode === 'image_generation' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Image ($)</label>
              <input type="number" step="any" value={form.input_cost_per_image} onChange={(e) => setForm({ ...form, input_cost_per_image: e.target.value })} placeholder="0.04" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Cost per generated image</p>
            </div>
          )}

          {mode === 'audio_speech' && (
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
          )}

          {mode === 'audio_transcription' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cost / Second of Audio ($)</label>
              <input type="number" step="any" value={form.input_cost_per_second} onChange={(e) => setForm({ ...form, input_cost_per_second: e.target.value })} placeholder="0.0001" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Cost per second of audio transcribed</p>
            </div>
          )}

          {mode === 'rerank' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Input Cost / Token ($)</label>
              <input type="number" step="any" value={form.input_cost_per_token} onChange={(e) => setForm({ ...form, input_cost_per_token: e.target.value })} placeholder="0.000002" className={inputClass} />
              <p className="text-xs text-gray-400 mt-1">Cost per token in query + documents</p>
            </div>
          )}

          {(mode === 'chat' || mode === 'embedding') && (
            <>
              <div className="border-t border-gray-100 pt-4 mt-4">
                <h4 className="text-sm font-medium text-gray-700 mb-1">Batch Pricing</h4>
                <p className="text-xs text-gray-400 mb-3">Optional overrides for batch API processing. Set a multiplier (e.g. 0.5 = 50% discount) or explicit per-token costs. Explicit costs take priority over the multiplier.</p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Price Multiplier</label>
                    <input type="number" step="any" value={form.batch_price_multiplier} onChange={(e) => setForm({ ...form, batch_price_multiplier: e.target.value })} placeholder="0.5" className={inputClass} />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Batch Input Cost / Token ($)</label>
                    <input type="number" step="any" value={form.batch_input_cost_per_token} onChange={(e) => setForm({ ...form, batch_input_cost_per_token: e.target.value })} placeholder="e.g. 0.0000025" className={inputClass} />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Batch Output Cost / Token ($)</label>
                    <input type="number" step="any" value={form.batch_output_cost_per_token} onChange={(e) => setForm({ ...form, batch_output_cost_per_token: e.target.value })} placeholder="e.g. 0.0000075" className={inputClass} />
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </CollapsibleCard>

      <CollapsibleCard title="Default Parameters">
        <div className="space-y-3">
          <p className="text-xs text-gray-400">Default values injected into provider requests when not specified by the caller (e.g. voice, response_format)</p>
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
        </div>
      </CollapsibleCard>

      {error && <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>}

      <div className="flex justify-end gap-3">
        <button onClick={onCancel} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
        <button onClick={handleSubmit} disabled={saving} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50">{saving ? 'Saving...' : submitLabel}</button>
      </div>
    </div>
  );
}
