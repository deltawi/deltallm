export type PlaygroundMode = 'chat' | 'tts' | 'stt';
export type MessageRole = 'system' | 'user' | 'assistant';

export interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
}

export interface ModelOption {
  id: string;
  name: string;
  provider: string;
  status: string;
  mode: string;
  defaultParams?: Record<string, any>;
}

export interface RequestStats {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
  model: string;
  cost?: number;
}

export function modeLabel(mode: PlaygroundMode): string {
  if (mode === 'tts') return 'Text-to-Speech';
  if (mode === 'stt') return 'Speech-to-Text';
  return 'Chat';
}

export function modeShortLabel(mode: PlaygroundMode): string {
  if (mode === 'tts') return 'TTS';
  if (mode === 'stt') return 'STT';
  return 'Chat';
}

export function modeEmptyCopy(mode: PlaygroundMode): string {
  if (mode === 'tts') return 'No text-to-speech models are configured yet.';
  if (mode === 'stt') return 'No speech-to-text models are configured yet.';
  return 'No chat models are configured yet.';
}

export function modeEndpoint(mode: PlaygroundMode): string {
  if (mode === 'tts') return '/v1/audio/speech';
  if (mode === 'stt') return '/v1/audio/transcriptions';
  return '/v1/chat/completions';
}

export function getTtsConfig(model: ModelOption): { voices: string[]; defaultVoice: string; defaultFormat: string } {
  const dp = model.defaultParams || {};
  const voices: string[] = dp.available_voices || (dp.voice ? [dp.voice] : []);
  return {
    voices,
    defaultVoice: dp.voice || (voices.length > 0 ? voices[0] : 'alloy'),
    defaultFormat: dp.response_format || 'mp3',
  };
}
