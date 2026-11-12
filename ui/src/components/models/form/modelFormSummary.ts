import type { ModelMode } from '../../modelFormShared';

const ENDPOINTS: Record<ModelMode, { path: string; body: (model: string) => string }> = {
  chat: { path: 'POST /v1/chat/completions', body: (model) => `{\n  "model": "${model}",\n  "messages": [...]\n}` },
  embedding: { path: 'POST /v1/embeddings', body: (model) => `{\n  "model": "${model}",\n  "input": "..."\n}` },
  image_generation: { path: 'POST /v1/images/generations', body: (model) => `{\n  "model": "${model}",\n  "prompt": "..."\n}` },
  audio_speech: { path: 'POST /v1/audio/speech', body: (model) => `{\n  "model": "${model}",\n  "input": "...",\n  "voice": "..."\n}` },
  audio_transcription: { path: 'POST /v1/audio/transcriptions', body: (model) => `model=${model}\nfile=@audio.mp3` },
  realtime: { path: 'WebSocket /v1/realtime', body: (model) => `?model=${model}` },
  rerank: { path: 'POST /v1/rerank', body: (model) => `{\n  "model": "${model}",\n  "query": "...",\n  "documents": [...]\n}` },
};

/**
 * A short example request that calls the model by its API model ID. A live
 * transcription Realtime model needs intent=transcription, or the gateway asks
 * for a conversation session and routing rejects the model.
 */
export function modelCallSnippet(mode: ModelMode, apiModelId: string, realtimeProfile?: 'realtime' | 'transcription'): string {
  const endpoint = ENDPOINTS[mode] || ENDPOINTS.chat;
  const body = endpoint.body(apiModelId.trim() || '<api-model-id>');
  const intent = mode === 'realtime' && realtimeProfile === 'transcription' ? '&intent=transcription' : '';
  return `${endpoint.path}\n${body}${intent}`;
}

export interface ChecklistItem {
  label: string;
  done: boolean;
  optional?: boolean;
}

export interface ChecklistInput {
  modelName: string;
  apiModelId: string;
  provider: string;
  model: string;
  credentialReady: boolean;
  hasPrice: boolean;
}

export function modelFormChecklist(input: ChecklistInput): ChecklistItem[] {
  return [
    { label: 'Name', done: Boolean(input.modelName.trim()) },
    { label: 'API model ID', done: Boolean(input.apiModelId.trim()) },
    { label: 'Provider', done: Boolean(input.provider.trim()) },
    { label: 'Provider model', done: Boolean(input.model.trim()) },
    { label: 'Credential', done: input.credentialReady },
    { label: 'Pricing', done: input.hasPrice, optional: true },
  ];
}
