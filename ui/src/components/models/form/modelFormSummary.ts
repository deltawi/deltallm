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

/** A short example request that calls the model by its API model ID. */
export function modelCallSnippet(mode: ModelMode, apiModelId: string): string {
  const endpoint = ENDPOINTS[mode] || ENDPOINTS.chat;
  return `${endpoint.path}\n${endpoint.body(apiModelId.trim() || '<api-model-id>')}`;
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
