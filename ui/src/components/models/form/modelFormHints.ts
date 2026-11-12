import type { ProviderModelDiscoveryPayload, ProviderModelOption } from '../../../lib/api';
import type { ModelMode } from '../../modelFormShared';

export type DefaultParameterHint = {
  description: string;
  details?: string[];
  keyPlaceholder: string;
  valuePlaceholder: string;
};

const PROVIDER_MODEL_PLACEHOLDERS: Partial<Record<string, Partial<Record<ModelMode, string>>>> = {
  elevenlabs: {
    audio_speech: 'eleven_multilingual_v2',
    audio_transcription: 'scribe_v2',
  },
  sglang: {
    chat: 'meta-llama/Llama-3.1-8B-Instruct',
    embedding: 'Qwen/Qwen3-Embedding-0.6B',
  },
};

// Providers with no built-in API base: show a typical address instead.
export const PROVIDER_API_BASE_PLACEHOLDERS: Partial<Record<string, string>> = {
  sglang: 'http://sglang:30000/v1',
  openai_compatible: 'https://your-server.example/v1',
};

export const PROVIDER_API_KEY_HINTS: Partial<Record<string, string>> = {
  sglang: 'Use the --api-key value of the SGLang server. If it runs without one, enter any value.',
  openai_compatible: 'The key your server expects. If it needs none, enter any value.',
};

export function normalizedProviderKey(provider: string | null | undefined): string {
  return (provider || '').trim().toLowerCase();
}

export function upstreamModelPlaceholder(mode: ModelMode, provider?: string): string {
  const providerPlaceholder = PROVIDER_MODEL_PLACEHOLDERS[normalizedProviderKey(provider)]?.[mode];
  if (providerPlaceholder) return providerPlaceholder;
  if (mode === 'image_generation') return 'gpt-image-1.5';
  if (mode === 'audio_speech') return 'gpt-4o-mini-tts';
  if (mode === 'realtime') return 'gpt-realtime';
  if (mode === 'audio_transcription') return 'gpt-4o-transcribe';
  if (mode === 'embedding') return 'text-embedding-3-large';
  if (mode === 'rerank') return 'rerank-english-v3.0';
  return 'gpt-5.4';
}

export function defaultParameterHint(provider: string, mode: ModelMode): DefaultParameterHint {
  const providerKey = normalizedProviderKey(provider);
  if (providerKey === 'elevenlabs' && mode === 'audio_speech') {
    return {
      description: 'Default values injected into ElevenLabs TTS requests when callers omit them.',
      details: [
        'Use voice_id as the fallback voice when the public request does not include voice.',
        'Use output_format for provider-native formats such as mp3_44100_128; public response_format still takes priority.',
      ],
      keyPlaceholder: 'Key (voice_id or output_format)',
      valuePlaceholder: 'Value (voice ID or mp3_44100_128)',
    };
  }

  if (providerKey === 'elevenlabs' && mode === 'audio_transcription') {
    return {
      description: 'Default values injected into ElevenLabs STT requests when callers omit them.',
      details: [
        'Common safe defaults include language_code, timestamps_granularity, diarize, num_speakers, and tag_audio_events.',
      ],
      keyPlaceholder: 'Key (e.g. timestamps_granularity)',
      valuePlaceholder: 'Value (e.g. word)',
    };
  }

  return {
    description: 'Default values injected into provider requests when not specified by the caller (e.g. voice, response_format)',
    keyPlaceholder: 'Key (e.g. voice)',
    valuePlaceholder: 'Value (e.g. alloy)',
  };
}

export function findProviderModelOption(options: ProviderModelOption[], modelId: string): ProviderModelOption | null {
  const normalizedId = modelId.trim().toLowerCase();
  if (!normalizedId) return null;
  return options.find((option) => option.id.trim().toLowerCase() === normalizedId) || null;
}

export function emptyDiscoveryResult(): { data: ProviderModelOption[]; warnings: string[] } {
  return { data: [], warnings: [] };
}

export function buildLiveDiscoveryRequestKey(payload: ProviderModelDiscoveryPayload): string {
  return [
    payload.provider || '',
    payload.mode || '',
    payload.named_credential_id || '',
    payload.api_key || '',
    payload.api_base || '',
    payload.api_version || '',
    payload.auth_header_name || '',
    payload.auth_header_format || '',
  ].join('::');
}
