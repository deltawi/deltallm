import {
  ArrowUpDown,
  FileText,
  Image,
  MessageSquare,
  Mic,
  Volume2,
  type LucideIcon,
} from 'lucide-react';
import type { ModelDeploymentDetail } from '../lib/api';
import { normalizeProvider, supportsCustomUpstreamAuthProvider } from '../lib/providers';

export type ModelMode =
  | 'chat'
  | 'embedding'
  | 'image_generation'
  | 'audio_speech'
  | 'audio_transcription'
  | 'rerank';

export interface ModelModeOption {
  value: ModelMode;
  label: string;
  icon: LucideIcon;
  description: string;
}

export const MODE_OPTIONS: ModelModeOption[] = [
  { value: 'chat', label: 'Chat', icon: MessageSquare, description: 'Text completions & conversations' },
  { value: 'embedding', label: 'Embedding', icon: FileText, description: 'Text & document embeddings' },
  { value: 'image_generation', label: 'Image Generation', icon: Image, description: 'Text-to-image generation' },
  { value: 'audio_speech', label: 'Text-to-Speech', icon: Volume2, description: 'Generate spoken audio from text' },
  { value: 'audio_transcription', label: 'Speech-to-Text', icon: Mic, description: 'Transcribe audio to text' },
  { value: 'rerank', label: 'Rerank', icon: ArrowUpDown, description: 'Document re-ranking' },
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
  provider: string;
  model: string;
  credential_source: 'inline' | 'named';
  named_credential_id: string;
  named_credential_name: string;
  inline_credentials_present: boolean;
  clear_inline_api_key: boolean;
  api_key: string;
  api_base: string;
  api_version: string;
  auth_header_name: string;
  auth_header_format: string;
  existing_auth_header_name: boolean;
  existing_auth_header_format: boolean;
  rpm: string;
  tpm: string;
  timeout: string;
  stream_timeout: string;
  max_tokens: string;
  weight: string;
  priority: string;
  tags: string;
  access_groups: string;
  input_cost_per_token: string;
  output_cost_per_token: string;
  input_cost_per_token_cache_hit: string;
  output_cost_per_token_cache_hit: string;
  max_context_window: string;
  max_input_tokens: string;
  max_output_tokens: string;
  output_vector_size: string;
  upstream_max_batch_inputs: string;
  input_cost_per_image: string;
  input_cost_per_character: string;
  output_cost_per_character: string;
  input_cost_per_second: string;
  output_cost_per_second: string;
  input_cost_per_audio_token: string;
  output_cost_per_audio_token: string;
  batch_price_multiplier: string;
  batch_input_cost_per_token: string;
  batch_output_cost_per_token: string;
}

export interface ModelPayload {
  model_name: string;
  named_credential_id?: string | null;
  deltallm_params: Record<string, unknown>;
  model_info: Record<string, unknown>;
}

export const EMPTY_FORM: ModelFormValues = {
  mode: 'chat',
  model_name: '',
  provider: '',
  model: '',
  credential_source: 'inline',
  named_credential_id: '',
  named_credential_name: '',
  inline_credentials_present: false,
  clear_inline_api_key: false,
  api_key: '',
  api_base: '',
  api_version: '',
  auth_header_name: '',
  auth_header_format: '',
  existing_auth_header_name: false,
  existing_auth_header_format: false,
  rpm: '',
  tpm: '',
  timeout: '',
  stream_timeout: '',
  max_tokens: '',
  weight: '',
  priority: '',
  tags: '',
  access_groups: '',
  input_cost_per_token: '',
  output_cost_per_token: '',
  input_cost_per_token_cache_hit: '',
  output_cost_per_token_cache_hit: '',
  max_context_window: '',
  max_input_tokens: '',
  max_output_tokens: '',
  output_vector_size: '',
  upstream_max_batch_inputs: '',
  input_cost_per_image: '',
  input_cost_per_character: '',
  output_cost_per_character: '',
  input_cost_per_second: '',
  output_cost_per_second: '',
  input_cost_per_audio_token: '',
  output_cost_per_audio_token: '',
  batch_price_multiplier: '',
  batch_input_cost_per_token: '',
  batch_output_cost_per_token: '',
};

function numOrUndef(val: string): number | undefined {
  return val ? Number(val) : undefined;
}

function positiveIntOrUndef(val: string): number | undefined {
  if (!val) return undefined;
  const parsed = Number(val);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}

function commaSeparatedListOrUndef(val: string): string[] | undefined {
  const items = val.split(',').map((item) => item.trim()).filter(Boolean);
  return items.length > 0 ? items : undefined;
}

export function strOrEmpty(val: unknown): string {
  return val != null ? String(val) : '';
}

function toModelMode(value: unknown): ModelMode {
  return MODE_OPTIONS.some((option) => option.value === value)
    ? (value as ModelMode)
    : 'chat';
}

export function buildModelPayload(
  form: ModelFormValues,
  defaultParams: { key: string; value: string }[],
): ModelPayload {
  const useNamedCredential = form.credential_source === 'named';
  const supportsCustomAuth = supportsCustomUpstreamAuthProvider(form.provider, form.model);
  const deltallm_params: Record<string, unknown> = {
    provider: form.provider.trim() || undefined,
    model: form.model.trim(),
    api_key: useNamedCredential
      ? undefined
      : form.clear_inline_api_key
        ? null
        : form.api_key || undefined,
    api_base: useNamedCredential ? undefined : form.api_base.trim() || undefined,
    api_version: useNamedCredential ? undefined : form.api_version.trim() || undefined,
    auth_header_name: useNamedCredential || !supportsCustomAuth
      ? undefined
      : form.auth_header_name.trim() || (form.existing_auth_header_name ? null : undefined),
    auth_header_format: useNamedCredential || !supportsCustomAuth
      ? undefined
      : form.auth_header_format.trim() || (form.existing_auth_header_format ? null : undefined),
    rpm: numOrUndef(form.rpm),
    tpm: numOrUndef(form.tpm),
    timeout: numOrUndef(form.timeout),
    weight: numOrUndef(form.weight),
  };

  if (form.mode === 'chat') {
    deltallm_params.stream_timeout = numOrUndef(form.stream_timeout);
    deltallm_params.max_tokens = numOrUndef(form.max_tokens);
  }

  const model_info: Record<string, unknown> = {
    mode: form.mode,
    priority: numOrUndef(form.priority),
    tags: form.tags ? commaSeparatedListOrUndef(form.tags) : undefined,
    access_groups: form.access_groups ? commaSeparatedListOrUndef(form.access_groups) : undefined,
    weight: numOrUndef(form.weight),
  };

  if (form.mode === 'chat') {
    model_info.input_cost_per_token = numOrUndef(form.input_cost_per_token);
    model_info.output_cost_per_token = numOrUndef(form.output_cost_per_token);
    model_info.input_cost_per_token_cache_hit = numOrUndef(form.input_cost_per_token_cache_hit);
    model_info.output_cost_per_token_cache_hit = numOrUndef(form.output_cost_per_token_cache_hit);
    model_info.max_tokens = numOrUndef(form.max_context_window);
    model_info.max_input_tokens = numOrUndef(form.max_input_tokens);
    model_info.max_output_tokens = numOrUndef(form.max_output_tokens);
  } else if (form.mode === 'embedding') {
    model_info.input_cost_per_token = numOrUndef(form.input_cost_per_token);
    model_info.input_cost_per_token_cache_hit = numOrUndef(form.input_cost_per_token_cache_hit);
    model_info.output_vector_size = numOrUndef(form.output_vector_size);
    model_info.max_tokens = numOrUndef(form.max_context_window);
    model_info.upstream_max_batch_inputs = positiveIntOrUndef(form.upstream_max_batch_inputs);
  } else if (form.mode === 'image_generation') {
    model_info.input_cost_per_image = numOrUndef(form.input_cost_per_image);
  } else if (form.mode === 'audio_speech') {
    model_info.input_cost_per_character = numOrUndef(form.input_cost_per_character);
    model_info.output_cost_per_character = numOrUndef(form.output_cost_per_character);
    model_info.input_cost_per_second = numOrUndef(form.input_cost_per_second);
    model_info.output_cost_per_second = numOrUndef(form.output_cost_per_second);
    model_info.input_cost_per_audio_token = numOrUndef(form.input_cost_per_audio_token);
    model_info.output_cost_per_audio_token = numOrUndef(form.output_cost_per_audio_token);
  } else if (form.mode === 'audio_transcription') {
    model_info.input_cost_per_second = numOrUndef(form.input_cost_per_second);
    model_info.output_cost_per_second = numOrUndef(form.output_cost_per_second);
  } else if (form.mode === 'rerank') {
    model_info.input_cost_per_token = numOrUndef(form.input_cost_per_token);
  }

  if (form.mode === 'chat' || form.mode === 'embedding') {
    model_info.batch_price_multiplier = numOrUndef(form.batch_price_multiplier);
    model_info.batch_input_cost_per_token = numOrUndef(form.batch_input_cost_per_token);
    model_info.batch_output_cost_per_token = numOrUndef(form.batch_output_cost_per_token);
  }

  const defaultParamsPayload: Record<string, string | number | boolean> = {};
  for (const param of defaultParams) {
    if (!param.key.trim()) {
      continue;
    }
    const value = param.value.trim();
    if (value === 'true') defaultParamsPayload[param.key.trim()] = true;
    else if (value === 'false') defaultParamsPayload[param.key.trim()] = false;
    else if (value !== '' && !Number.isNaN(Number(value))) defaultParamsPayload[param.key.trim()] = Number(value);
    else defaultParamsPayload[param.key.trim()] = value;
  }
  model_info.default_params = Object.keys(defaultParamsPayload).length > 0 ? defaultParamsPayload : {};

  return {
    model_name: form.model_name.trim(),
    named_credential_id: useNamedCredential ? form.named_credential_id.trim() || null : null,
    deltallm_params,
    model_info,
  };
}

export function formFromModel(
  model: ModelDeploymentDetail,
): { form: ModelFormValues; defaultParams: { key: string; value: string }[] } {
  const lp = (model.deltallm_params || {}) as Record<string, unknown>;
  const mi = (model.model_info || {}) as Record<string, unknown>;
  const explicitProvider = strOrEmpty(lp.provider).trim();
  const inferredProvider = normalizeProvider(undefined, strOrEmpty(lp.model));
  const provider = explicitProvider || (inferredProvider !== 'unknown' ? inferredProvider : '');
  const upstreamModel = strOrEmpty(lp.model);
  const normalizedModel = explicitProvider
    ? upstreamModel
    : provider && upstreamModel.startsWith(`${provider}/`)
      ? upstreamModel.slice(provider.length + 1)
      : upstreamModel;

  const form: ModelFormValues = {
    mode: toModelMode(mi.mode || model.mode),
    model_name: model.model_name || '',
    provider,
    model: normalizedModel,
    credential_source: model.credential_source || (model.named_credential_id ? 'named' : 'inline'),
    named_credential_id: strOrEmpty(model.named_credential_id),
    named_credential_name: strOrEmpty(model.named_credential_name),
    inline_credentials_present: Boolean(model.inline_credentials_present),
    clear_inline_api_key: false,
    api_key: '',
    api_base: strOrEmpty(lp.api_base),
    api_version: strOrEmpty(lp.api_version),
    auth_header_name: strOrEmpty(lp.auth_header_name),
    auth_header_format: strOrEmpty(lp.auth_header_format),
    existing_auth_header_name: Boolean(strOrEmpty(lp.auth_header_name).trim()),
    existing_auth_header_format: Boolean(strOrEmpty(lp.auth_header_format).trim()),
    rpm: strOrEmpty(lp.rpm),
    tpm: strOrEmpty(lp.tpm),
    timeout: strOrEmpty(lp.timeout),
    stream_timeout: strOrEmpty(lp.stream_timeout),
    max_tokens: strOrEmpty(lp.max_tokens),
    weight: strOrEmpty(mi.weight ?? lp.weight),
    priority: strOrEmpty(mi.priority),
    tags: Array.isArray(mi.tags) ? mi.tags.join(', ') : '',
    access_groups: Array.isArray(mi.access_groups) ? mi.access_groups.join(', ') : '',
    input_cost_per_token: strOrEmpty(mi.input_cost_per_token),
    output_cost_per_token: strOrEmpty(mi.output_cost_per_token),
    input_cost_per_token_cache_hit: strOrEmpty(mi.input_cost_per_token_cache_hit),
    output_cost_per_token_cache_hit: strOrEmpty(mi.output_cost_per_token_cache_hit),
    max_context_window: strOrEmpty(mi.max_tokens),
    max_input_tokens: strOrEmpty(mi.max_input_tokens),
    max_output_tokens: strOrEmpty(mi.max_output_tokens),
    output_vector_size: strOrEmpty(mi.output_vector_size),
    upstream_max_batch_inputs: strOrEmpty(mi.upstream_max_batch_inputs),
    input_cost_per_image: strOrEmpty(mi.input_cost_per_image),
    input_cost_per_character: strOrEmpty(mi.input_cost_per_character),
    output_cost_per_character: strOrEmpty(mi.output_cost_per_character),
    input_cost_per_second: strOrEmpty(mi.input_cost_per_second),
    output_cost_per_second: strOrEmpty(mi.output_cost_per_second),
    input_cost_per_audio_token: strOrEmpty(mi.input_cost_per_audio_token),
    output_cost_per_audio_token: strOrEmpty(mi.output_cost_per_audio_token),
    batch_price_multiplier: strOrEmpty(mi.batch_price_multiplier),
    batch_input_cost_per_token: strOrEmpty(mi.batch_input_cost_per_token),
    batch_output_cost_per_token: strOrEmpty(mi.batch_output_cost_per_token),
  };

  const defaultParamsValue = mi.default_params;
  const defaultParams =
    defaultParamsValue && typeof defaultParamsValue === 'object'
      ? Object.entries(defaultParamsValue).map(([key, value]) => ({ key, value: String(value) }))
      : [];

  return { form, defaultParams };
}
