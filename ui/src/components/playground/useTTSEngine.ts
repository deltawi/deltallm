import { useCallback, useEffect, useRef, useState } from 'react';
import type { ModelOption } from './types';
import { getTtsConfig } from './types';

export interface TTSStats {
  characters: number;
  latencyMs: number;
  fileSize: number;
}

export interface TTSEngine {
  text: string;
  setText: (v: string) => void;
  voice: string;
  setVoice: (v: string) => void;
  speed: number;
  setSpeed: (v: number) => void;
  format: string;
  setFormat: (v: string) => void;
  voices: string[];
  isGenerating: boolean;
  audioUrl: string | null;
  isPlaying: boolean;
  progress: number;
  duration: number;
  error: string | null;
  stats: TTSStats | null;
  audioRef: React.RefObject<HTMLAudioElement>;
  handleGenerate: () => Promise<void>;
  togglePlay: () => void;
  seekTo: (pct: number) => void;
  handleDownload: () => void;
  resetOutput: () => void;
}

export function useTTSEngine(opts: { apiKey: string; selectedModel: ModelOption | null }): TTSEngine {
  const { apiKey, selectedModel } = opts;
  const initial = selectedModel ? getTtsConfig(selectedModel) : { voices: [], defaultVoice: 'alloy', defaultFormat: 'mp3' };
  const [text, setText] = useState('');
  const [voice, setVoice] = useState(initial.defaultVoice);
  const [speed, setSpeed] = useState(1.0);
  const [format, setFormat] = useState(initial.defaultFormat);
  const [isGenerating, setIsGenerating] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<TTSStats | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  const voices = selectedModel ? getTtsConfig(selectedModel).voices : [];

  // When model changes, sync defaults
  useEffect(() => {
    if (!selectedModel) return;
    const cfg = getTtsConfig(selectedModel);
    setVoice(cfg.defaultVoice);
    setFormat(cfg.defaultFormat);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModel?.id]);

  // Cleanup blob URL on unmount or change
  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  // Audio element listeners
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onTimeUpdate = () => {
      if (audio.duration) setProgress((audio.currentTime / audio.duration) * 100);
    };
    const onEnded = () => {
      setIsPlaying(false);
      setProgress(0);
    };
    const onLoadedMetadata = () => setDuration(audio.duration);
    audio.addEventListener('timeupdate', onTimeUpdate);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('loadedmetadata', onLoadedMetadata);
    return () => {
      audio.removeEventListener('timeupdate', onTimeUpdate);
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('loadedmetadata', onLoadedMetadata);
    };
  }, [audioUrl]);

  const handleGenerate = useCallback(async () => {
    if (!text.trim() || !apiKey || !selectedModel) return;
    setIsGenerating(true);
    setError(null);
    if (audioUrl) {
      URL.revokeObjectURL(audioUrl);
      setAudioUrl(null);
    }
    setStats(null);
    const startTime = performance.now();
    try {
      const res = await fetch('/v1/audio/speech', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: selectedModel.name,
          input: text,
          voice,
          speed,
          response_format: format,
        }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `Request failed (${res.status})`);
      }
      const blob = await res.blob();
      const latencyMs = Math.round(performance.now() - startTime);
      const url = URL.createObjectURL(blob);
      setAudioUrl(url);
      setStats({ characters: text.length, latencyMs, fileSize: blob.size });
    } catch (err: any) {
      setError(err.message || 'TTS request failed');
    } finally {
      setIsGenerating(false);
    }
  }, [text, apiKey, selectedModel, voice, speed, format, audioUrl]);

  const togglePlay = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
    } else {
      void audio.play();
    }
    setIsPlaying(!isPlaying);
  }, [isPlaying]);

  const seekTo = useCallback((pct: number) => {
    const audio = audioRef.current;
    if (!audio || !audio.duration) return;
    audio.currentTime = pct * audio.duration;
  }, []);

  const handleDownload = useCallback(() => {
    if (!audioUrl) return;
    const a = document.createElement('a');
    a.href = audioUrl;
    a.download = `deltallm-tts.${format}`;
    a.click();
  }, [audioUrl, format]);

  const resetOutput = useCallback(() => {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    setIsPlaying(false);
    setProgress(0);
    setDuration(0);
    setStats(null);
    setError(null);
  }, [audioUrl]);

  return {
    text,
    setText,
    voice,
    setVoice,
    speed,
    setSpeed,
    format,
    setFormat,
    voices,
    isGenerating,
    audioUrl,
    isPlaying,
    progress,
    duration,
    error,
    stats,
    audioRef,
    handleGenerate,
    togglePlay,
    seekTo,
    handleDownload,
    resetOutput,
  };
}
