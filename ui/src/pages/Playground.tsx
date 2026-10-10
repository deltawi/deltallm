import { uiMount } from '../lib/uiMount';
import ConsoleInferenceKey from '../components/playground/ConsoleInferenceKey';
import { useMemo, useState } from 'react';
import { ApiError, models as modelsApi } from '../lib/api';
import { useApi } from '../lib/hooks';
import Button from '../components/Button';
import type { ModelOption, PlaygroundMode } from '../components/playground/types';
import { useChatEngine } from '../components/playground/useChatEngine';
import { useTTSEngine } from '../components/playground/useTTSEngine';
import { useSTTEngine } from '../components/playground/useSTTEngine';
import { useIsMd } from '../components/playground/useViewport';
import PlaygroundDesktop from '../components/playground/PlaygroundDesktop';
import PlaygroundMobile from '../components/playground/PlaygroundMobile';

const MODE_MAP: Record<PlaygroundMode, string[]> = {
  chat: ['chat', 'completion'],
  tts: ['audio_speech'],
  stt: ['audio_transcription'],
};

export default function Playground() {
  const isMd = useIsMd();
  const [mode, setModeState] = useState<PlaygroundMode>('chat');
  const [apiKey, setApiKey] = useState('');
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const modelRead = useApi((signal) => modelsApi.list({ limit: 200 }, signal), []);
  const denied = modelRead.error instanceof ApiError && modelRead.error.status === 403;
  const allModels = useMemo<ModelOption[]>(() => denied ? [] : (modelRead.data?.data || []).map((model) => ({
    id: model.deployment_id || model.model_name,
    name: model.model_name,
    provider: model.provider,
    status: model.healthy == null ? 'unknown' : model.healthy ? 'online' : 'offline',
    mode: model.mode || 'chat',
    defaultParams: model.model_info?.default_params || {},
  })), [modelRead.data, denied]);

  const chatModels = useMemo(
    () => allModels.filter((model) => MODE_MAP.chat.includes(model.mode)),
    [allModels],
  );
  const ttsModels = useMemo(
    () => allModels.filter((model) => MODE_MAP.tts.includes(model.mode)),
    [allModels],
  );
  const sttModels = useMemo(
    () => allModels.filter((model) => MODE_MAP.stt.includes(model.mode)),
    [allModels],
  );
  const currentModels = useMemo(() => {
    if (mode === 'tts') return ttsModels;
    if (mode === 'stt') return sttModels;
    return chatModels;
  }, [chatModels, mode, sttModels, ttsModels]);
  const noModelsForMode = currentModels.length === 0;

  const selectedModel = currentModels.find((model) => model.id === selectedModelId) || currentModels[0] || null;
  const setSelectedModel = (model: ModelOption | null) => setSelectedModelId(model?.id ?? null);
  const setMode = (next: PlaygroundMode) => {
    if (next !== mode) setSelectedModelId(null);
    setModeState(next);
  };

  const chat = useChatEngine({ apiKey, selectedModel });
  const tts = useTTSEngine({ apiKey, selectedModel });
  const stt = useSTTEngine({ apiKey, selectedModel });

  const sharedProps = {
    mode,
    setMode,
    apiKey,
    setApiKey,
    allModels,
    currentModels,
    selectedModel,
    setSelectedModel,
    noModelsForMode,
    chat,
    tts,
    stt,
  };

  const view = isMd ? <PlaygroundDesktop {...sharedProps} /> : <PlaygroundMobile {...sharedProps} />;
  return <div className="flex h-full flex-col">
    {uiMount().external_console && <ConsoleInferenceKey onSelection={(ready) => setApiKey(ready ? 'console-selected-key' : '')} />}
    {modelRead.loading && <p role="status" className="px-4 py-2 text-sm text-gray-500">Loading models…</p>}
    {Boolean(modelRead.error) && <div role="alert" className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
      <span>{denied ? 'You do not have access to these models.' : modelRead.data ? 'Could not update models. The previous result is shown.' : 'Could not load models.'}</span>
      {!denied && <Button variant="link" size="sm" onClick={modelRead.refetch}>Retry</Button>}
    </div>}
    <div className="min-h-0 flex-1">{view}</div>
  </div>;
}
