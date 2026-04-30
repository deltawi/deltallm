import { useEffect, useMemo, useState } from 'react';
import { models as modelsApi } from '../lib/api';
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
  const [mode, setMode] = useState<PlaygroundMode>('chat');
  const [apiKey, setApiKey] = useState('');
  const [allModels, setAllModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState<ModelOption | null>(null);

  useEffect(() => {
    modelsApi
      .list({ limit: 200 })
      .then((res) => {
        const mapped: ModelOption[] = (res.data || []).map((m: any) => ({
          id: m.deployment_id || m.model_name,
          name: m.model_name,
          provider: m.provider,
          status: m.healthy ? 'online' : 'offline',
          mode: m.mode || 'chat',
          defaultParams: m.model_info?.default_params || {},
        }));
        setAllModels(mapped);
      })
      .catch(() => {
        /* swallow — UI shows empty state */
      });
  }, []);

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

  useEffect(() => {
    if (currentModels.length === 0) {
      if (selectedModel !== null) setSelectedModel(null);
      return;
    }
    if (!selectedModel || !currentModels.some((m) => m.id === selectedModel.id)) {
      setSelectedModel(currentModels[0]);
    }
  }, [currentModels, selectedModel]);

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

  return isMd ? <PlaygroundDesktop {...sharedProps} /> : <PlaygroundMobile {...sharedProps} />;
}
