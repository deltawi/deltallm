import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import {
  ChevronRight, Sparkles, SlidersHorizontal,
  Activity, PlaySquare, StopCircle, CornerDownLeft, Copy, Check,
  Bot, User, Code, RotateCcw, AlertTriangle, Zap, TerminalSquare,
  PanelRightClose, PanelRightOpen, ChevronDown,
  MessageSquare, Volume2, Mic, Upload, Download, Play, Pause,
  Eye, EyeOff, Key, FileAudio, Square
} from 'lucide-react';
import { models as modelsApi } from '../lib/api';

type PlaygroundMode = 'chat' | 'tts' | 'stt';
type MessageRole = 'system' | 'user' | 'assistant';

interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
}

interface ModelOption {
  id: string;
  name: string;
  provider: string;
  status: string;
  mode: string;
  defaultParams?: Record<string, any>;
}

interface RequestStats {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
  model: string;
  cost?: number;
}

function modeLabel(mode: PlaygroundMode): string {
  if (mode === 'tts') return 'Text-to-Speech';
  if (mode === 'stt') return 'Speech-to-Text';
  return 'Chat';
}

function modeEmptyCopy(mode: PlaygroundMode): string {
  if (mode === 'tts') return 'No text-to-speech models are configured yet.';
  if (mode === 'stt') return 'No speech-to-text models are configured yet.';
  return 'No chat models are configured yet.';
}

function ModeUnavailableState({ mode }: { mode: PlaygroundMode }) {
  return (
    <div className="flex flex-1 items-center justify-center bg-gray-50/50 p-6">
      <div className="max-w-md rounded-2xl border border-dashed border-gray-300 bg-white px-8 py-10 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50">
          <Sparkles className="h-7 w-7 text-blue-600" />
        </div>
        <h2 className="mb-2 text-lg font-semibold text-gray-900">{modeLabel(mode)} unavailable</h2>
        <p className="text-sm leading-6 text-gray-500">{modeEmptyCopy(mode)}</p>
      </div>
    </div>
  );
}

function getTtsConfig(model: ModelOption): { voices: string[]; defaultVoice: string; defaultFormat: string } {
  const dp = model.defaultParams || {};
  const voices: string[] = dp.available_voices || (dp.voice ? [dp.voice] : []);
  return {
    voices,
    defaultVoice: dp.voice || (voices.length > 0 ? voices[0] : 'alloy'),
    defaultFormat: dp.response_format || 'mp3',
  };
}

function ModeSelector({ mode, onChange }: { mode: PlaygroundMode; onChange: (m: PlaygroundMode) => void }) {
  const modes: { key: PlaygroundMode; label: string; icon: React.ReactNode }[] = [
    { key: 'chat', label: 'Chat', icon: <MessageSquare className="w-3.5 h-3.5" /> },
    { key: 'tts', label: 'Text to Speech', icon: <Volume2 className="w-3.5 h-3.5" /> },
    { key: 'stt', label: 'Speech to Text', icon: <Mic className="w-3.5 h-3.5" /> },
  ];

  return (
    <div className="flex items-center bg-gray-100 rounded-lg p-0.5">
      {modes.map((m) => (
        <button
          key={m.key}
          onClick={() => onChange(m.key)}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
            mode === m.key
              ? 'bg-white text-gray-900 shadow-sm'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          {m.icon}
          {m.label}
        </button>
      ))}
    </div>
  );
}

function ApiKeyInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="flex-1 min-w-[200px] max-w-[280px]">
      <label className="block text-xs font-medium text-gray-500 mb-1">API Key</label>
      <div className="relative">
        <Key className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
        <input
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="sk-..."
          className="w-full bg-white border border-gray-200 rounded-md py-1.5 pl-8 pr-8 text-sm text-gray-700 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
        <button
          onClick={() => setVisible(!visible)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
          title={visible ? 'Hide key' : 'Show key'}
        >
          {visible ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
        </button>
      </div>
    </div>
  );
}

function TTSView({ apiKey, selectedModel }: { apiKey: string; selectedModel: ModelOption }) {
  const ttsConfig = getTtsConfig(selectedModel);
  const [ttsText, setTtsText] = useState('');
  const [selectedVoice, setSelectedVoice] = useState(ttsConfig.defaultVoice);
  const [speed, setSpeed] = useState(1.0);
  const [responseFormat, setResponseFormat] = useState(ttsConfig.defaultFormat);

  useEffect(() => {
    const cfg = getTtsConfig(selectedModel);
    setSelectedVoice(cfg.defaultVoice);
    setResponseFormat(cfg.defaultFormat);
  }, [selectedModel.id]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<{ characters: number; latencyMs: number; fileSize: number } | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    return () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
  }, [audioUrl]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onTimeUpdate = () => {
      if (audio.duration) setProgress((audio.currentTime / audio.duration) * 100);
    };
    const onEnded = () => { setIsPlaying(false); setProgress(0); };
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

  const handleGenerate = async () => {
    if (!ttsText.trim() || !apiKey) return;
    setIsGenerating(true);
    setError(null);
    if (audioUrl) { URL.revokeObjectURL(audioUrl); setAudioUrl(null); }
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
          input: ttsText,
          voice: selectedVoice,
          speed,
          response_format: responseFormat,
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
      setStats({ characters: ttsText.length, latencyMs, fileSize: blob.size });
    } catch (err: any) {
      setError(err.message || 'TTS request failed');
    } finally {
      setIsGenerating(false);
    }
  };

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) { audio.pause(); } else { audio.play(); }
    setIsPlaying(!isPlaying);
  };

  const handleDownload = () => {
    if (!audioUrl) return;
    const a = document.createElement('a');
    a.href = audioUrl;
    a.download = `deltallm-tts.${responseFormat}`;
    a.click();
  };

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Voice</label>
            {ttsConfig.voices.length > 1 ? (
              <div className="grid grid-cols-3 gap-1.5">
                {ttsConfig.voices.map((v) => (
                  <button
                    key={v}
                    onClick={() => setSelectedVoice(v)}
                    className={`px-2 py-1.5 text-xs font-medium rounded-md border transition-all ${
                      selectedVoice === v
                        ? 'bg-blue-50 border-blue-300 text-blue-700'
                        : 'bg-white border-gray-200 text-gray-600 hover:border-gray-300'
                    }`}
                  >
                    {v}
                  </button>
                ))}
              </div>
            ) : (
              <input
                type="text"
                value={selectedVoice}
                onChange={(e) => setSelectedVoice(e.target.value)}
                placeholder="e.g. alloy, nova, autumn"
                className="w-full px-3 py-1.5 text-xs border border-gray-200 rounded-md focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            )}
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-gray-700">Speed</label>
              <span className="text-xs text-gray-500 font-mono">{speed.toFixed(1)}x</span>
            </div>
            <input
              type="range" min="0.25" max="4.0" step="0.25" value={speed}
              onChange={(e) => setSpeed(parseFloat(e.target.value))}
              className="w-full accent-blue-600 h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer"
            />
            <div className="flex justify-between text-[10px] text-gray-400 mt-1">
              <span>0.25x</span>
              <span>4.0x</span>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Format</label>
            <select
              value={responseFormat}
              onChange={(e) => setResponseFormat(e.target.value)}
              className="w-full appearance-none bg-white border border-gray-200 rounded-md py-1.5 pl-3 pr-8 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="mp3">MP3</option>
              <option value="opus">Opus</option>
              <option value="aac">AAC</option>
              <option value="flac">FLAC</option>
              <option value="wav">WAV</option>
              <option value="pcm">PCM</option>
            </select>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs font-medium text-gray-700">Input Text</label>
            <span className="text-xs text-gray-400">{ttsText.length} / 4096 characters</span>
          </div>
          <textarea
            value={ttsText}
            onChange={(e) => setTtsText(e.target.value.slice(0, 4096))}
            className="w-full h-40 p-3 text-sm bg-white border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none leading-relaxed"
            placeholder="Enter the text you want to convert to speech..."
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleGenerate}
            disabled={!ttsText.trim() || isGenerating || !apiKey}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400 transition-colors shadow-sm"
          >
            {isGenerating ? (
              <>
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Generating...
              </>
            ) : (
              <>
                <Volume2 className="w-4 h-4" />
                Generate Speech
              </>
            )}
          </button>
          {!apiKey && <span className="text-xs text-amber-600">Enter an API key first</span>}
          {isGenerating && (
            <span className="text-xs text-gray-500">Processing with voice "{selectedVoice}" at {speed}x speed...</span>
          )}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-none" />
              <div>
                <div className="font-medium">Error</div>
                <div className="mt-1 text-xs whitespace-pre-wrap">{error}</div>
              </div>
            </div>
          </div>
        )}

        {audioUrl && (
          <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
            <audio ref={audioRef} src={audioUrl} preload="metadata" />
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                  <FileAudio className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-900">Generated Audio</div>
                  <div className="text-xs text-gray-500">
                    Voice: {selectedVoice} · Format: {responseFormat.toUpperCase()}
                    {duration > 0 && ` · Duration: ${duration.toFixed(1)}s`}
                  </div>
                </div>
              </div>
              <button
                onClick={handleDownload}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-md transition-colors"
              >
                <Download className="w-3.5 h-3.5" />
                Download
              </button>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={togglePlay}
                className="w-10 h-10 bg-blue-600 text-white rounded-full flex items-center justify-center hover:bg-blue-700 shadow-sm transition-colors flex-none"
              >
                {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </button>
              <div className="flex-1">
                <div
                  className="w-full bg-gray-100 rounded-full h-2 cursor-pointer"
                  onClick={(e) => {
                    const audio = audioRef.current;
                    if (!audio || !audio.duration) return;
                    const rect = e.currentTarget.getBoundingClientRect();
                    const pct = (e.clientX - rect.left) / rect.width;
                    audio.currentTime = pct * audio.duration;
                  }}
                >
                  <div
                    className="bg-blue-600 h-2 rounded-full transition-all duration-100"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                {duration > 0 && (
                  <div className="flex justify-between mt-1 text-[10px] text-gray-400 font-mono">
                    <span>{(duration * progress / 100).toFixed(1)}s</span>
                    <span>{duration.toFixed(1)}s</span>
                  </div>
                )}
              </div>
            </div>

            {stats && (
              <div className="mt-4 flex items-center gap-4">
                <div className="flex-1 flex items-center gap-6">
                  {[
                    { label: 'Characters', value: stats.characters.toString() },
                    { label: 'Duration', value: duration > 0 ? `${duration.toFixed(1)}s` : '—' },
                    { label: 'File Size', value: formatBytes(stats.fileSize) },
                    { label: 'Latency', value: `${(stats.latencyMs / 1000).toFixed(1)}s` },
                  ].map((stat) => (
                    <div key={stat.label} className="text-center">
                      <div className="text-xs text-gray-400">{stat.label}</div>
                      <div className="text-sm font-semibold text-gray-900">{stat.value}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function STTView({ apiKey, selectedModel }: { apiKey: string; selectedModel: ModelOption }) {
  const [dragOver, setDragOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcription, setTranscription] = useState<string | null>(null);
  const [language, setLanguage] = useState('');
  const [responseFormat, setResponseFormat] = useState('json');
  const [outputPrompt, setOutputPrompt] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<{ words: number; latencyMs: number; detectedLang?: string } | null>(null);
  const [copiedTranscription, setCopiedTranscription] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleFile = (file: File) => {
    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
      setError('File size exceeds 25 MB limit');
      return;
    }
    setSelectedFile(file);
    setTranscription(null);
    setError(null);
    setStats(null);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : '';
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      recorder.onstop = () => {
        const actualMime = recorder.mimeType || 'audio/webm';
        const ext = actualMime.includes('webm') ? 'webm' : actualMime.includes('mp4') ? 'mp4' : 'webm';
        const blob = new Blob(chunksRef.current, { type: actualMime });
        if (blob.size < 1000) {
          setError('Recording too short — please record at least 1 second of audio');
          stream.getTracks().forEach(t => t.stop());
          return;
        }
        const file = new File([blob], `recording.${ext}`, { type: actualMime });
        handleFile(file);
        stream.getTracks().forEach(t => t.stop());
      };
      mediaRecorderRef.current = recorder;
      recorder.start(1000);
      setIsRecording(true);
      setRecordingTime(0);
      timerRef.current = setInterval(() => setRecordingTime(t => t + 1), 1000);
    } catch (err) {
      setError('Microphone access denied');
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  };

  const handleTranscribe = async () => {
    if (!selectedFile || !apiKey) return;
    setIsTranscribing(true);
    setError(null);
    setTranscription(null);
    setStats(null);
    const startTime = performance.now();
    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('model', selectedModel.name);
      if (language) formData.append('language', language);
      formData.append('response_format', responseFormat);
      if (outputPrompt) formData.append('prompt', outputPrompt);

      const res = await fetch('/v1/audio/transcriptions', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${apiKey}` },
        body: formData,
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `Request failed (${res.status})`);
      }
      const latencyMs = Math.round(performance.now() - startTime);
      const ct = res.headers.get('content-type') || '';
      let text: string;
      if (ct.includes('application/json')) {
        const json = await res.json();
        text = json.text ?? JSON.stringify(json, null, 2);
        setStats({
          words: (json.text || '').split(/\s+/).filter(Boolean).length,
          latencyMs,
          detectedLang: json.language,
        });
      } else {
        text = await res.text();
        setStats({ words: text.split(/\s+/).filter(Boolean).length, latencyMs });
      }
      setTranscription(text);
    } catch (err: any) {
      setError(err.message || 'Transcription request failed');
    } finally {
      setIsTranscribing(false);
    }
  };

  const formatTime = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Language</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="w-full appearance-none bg-white border border-gray-200 rounded-md py-1.5 pl-3 pr-8 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Auto-detect</option>
              <option value="en">English</option>
              <option value="es">Spanish</option>
              <option value="fr">French</option>
              <option value="de">German</option>
              <option value="ja">Japanese</option>
              <option value="zh">Chinese</option>
              <option value="ar">Arabic</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Response Format</label>
            <select
              value={responseFormat}
              onChange={(e) => setResponseFormat(e.target.value)}
              className="w-full appearance-none bg-white border border-gray-200 rounded-md py-1.5 pl-3 pr-8 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="json">JSON</option>
              <option value="text">Plain Text</option>
              <option value="srt">SRT (Subtitles)</option>
              <option value="verbose_json">Verbose JSON</option>
              <option value="vtt">VTT</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Prompt (optional)</label>
            <input
              type="text"
              value={outputPrompt}
              onChange={(e) => setOutputPrompt(e.target.value)}
              placeholder="Guide the model style..."
              className="w-full bg-white border border-gray-200 rounded-md py-1.5 px-3 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors cursor-pointer ${
              dragOver ? 'border-blue-400 bg-blue-50' : selectedFile ? 'border-green-300 bg-green-50/50' : 'border-gray-300 bg-gray-50 hover:border-gray-400'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="audio/*,.mp3,.mp4,.mpeg,.m4a,.wav,.webm"
              className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
            />
            {selectedFile ? (
              <div className="space-y-2">
                <div className="w-12 h-12 bg-green-100 rounded-xl flex items-center justify-center mx-auto">
                  <FileAudio className="w-6 h-6 text-green-600" />
                </div>
                <div className="text-sm font-medium text-gray-900">{selectedFile.name}</div>
                <div className="text-xs text-gray-500">{formatSize(selectedFile.size)} · {selectedFile.type || 'audio'}</div>
                <button
                  onClick={(e) => { e.stopPropagation(); setSelectedFile(null); setTranscription(null); setStats(null); }}
                  className="text-xs text-red-500 hover:text-red-600"
                >
                  Remove
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                <Upload className="w-8 h-8 text-gray-400 mx-auto" />
                <div className="text-sm font-medium text-gray-700">Drop audio file here or click to browse</div>
                <div className="text-xs text-gray-500">Supports MP3, MP4, MPEG, M4A, WAV, WEBM · Max 25 MB</div>
              </div>
            )}
          </div>

          <div className="border border-gray-200 rounded-xl p-6 flex flex-col items-center justify-center text-center bg-white">
            <button
              onClick={() => isRecording ? stopRecording() : startRecording()}
              className={`w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-sm mb-3 ${
                isRecording
                  ? 'bg-red-500 text-white animate-pulse shadow-red-200 shadow-lg'
                  : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
              }`}
            >
              {isRecording ? <Square className="w-6 h-6" /> : <Mic className="w-7 h-7" />}
            </button>
            <div className="text-sm font-medium text-gray-900">
              {isRecording ? `Recording... ${formatTime(recordingTime)}` : 'Record Audio'}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {isRecording ? 'Click to stop' : 'Click to start recording from microphone'}
            </div>
            {isRecording && (
              <div className="flex items-center gap-1 mt-3">
                {Array.from({ length: 20 }).map((_, i) => (
                  <div
                    key={i}
                    className="w-1 bg-red-400 rounded-full animate-pulse"
                    style={{
                      height: `${Math.random() * 20 + 4}px`,
                      animationDelay: `${i * 50}ms`,
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {(selectedFile || isRecording) && !isRecording && (
          <div className="flex items-center gap-3">
            <button
              onClick={handleTranscribe}
              disabled={isTranscribing || !apiKey}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400 transition-colors shadow-sm"
            >
              {isTranscribing ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Transcribing...
                </>
              ) : (
                <>
                  <Mic className="w-4 h-4" />
                  Transcribe
                </>
              )}
            </button>
            {!apiKey && <span className="text-xs text-amber-600">Enter an API key first</span>}
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-none" />
              <div>
                <div className="font-medium">Error</div>
                <div className="mt-1 text-xs whitespace-pre-wrap">{error}</div>
              </div>
            </div>
          </div>
        )}

        {transcription !== null && (
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-gray-50">
              <div className="flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-gray-500" />
                <span className="text-sm font-medium text-gray-900">Transcription Result</span>
                {stats?.detectedLang && (
                  <span className="px-1.5 py-0.5 bg-green-100 text-green-700 text-[10px] font-medium rounded">
                    Detected: {stats.detectedLang}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { navigator.clipboard.writeText(transcription); setCopiedTranscription(true); setTimeout(() => setCopiedTranscription(false), 2000); }}
                  className="flex items-center gap-1 px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded"
                >
                  {copiedTranscription ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
                  {copiedTranscription ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>

            <div className="p-4 text-sm text-gray-800 leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
              {transcription}
            </div>

            {stats && (
              <div className="flex items-center gap-6 px-4 py-3 border-t border-gray-100 bg-gray-50">
                {[
                  { label: 'Words', value: stats.words.toString() },
                  { label: 'Latency', value: `${(stats.latencyMs / 1000).toFixed(1)}s` },
                  ...(stats.detectedLang ? [{ label: 'Language', value: stats.detectedLang }] : []),
                ].map((stat) => (
                  <div key={stat.label}>
                    <span className="text-xs text-gray-400">{stat.label}: </span>
                    <span className="text-xs font-semibold text-gray-700">{stat.value}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Playground() {
  const [mode, setMode] = useState<PlaygroundMode>('chat');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [allModels, setAllModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState<ModelOption | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeTab, setActiveTab] = useState<'stats' | 'request'>('stats');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [lastStats, setLastStats] = useState<RequestStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [systemPrompt, setSystemPrompt] = useState('You are a helpful AI assistant.');
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(2048);
  const [topP, setTopP] = useState(1);
  const [freqPenalty, setFreqPenalty] = useState(0);
  const [presPenalty, setPresPenalty] = useState(0);
  const [showConfig, setShowConfig] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    modelsApi.list({ limit: 200 }).then((res) => {
      const mapped: ModelOption[] = (res.data || []).map((m: any) => ({
        id: m.deployment_id || m.model_name,
        name: m.model_name,
        provider: m.provider,
        status: m.healthy ? 'online' : 'offline',
        mode: m.mode || 'chat',
        defaultParams: m.model_info?.default_params || {},
      }));
      setAllModels(mapped);
    }).catch(() => {});
  }, []);

  const MODE_MAP: Record<PlaygroundMode, string[]> = {
    chat: ['chat', 'completion'],
    tts: ['audio_speech'],
    stt: ['audio_transcription'],
  };
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
      if (selectedModel !== null) {
        setSelectedModel(null);
      }
      return;
    }
    if (!selectedModel || !currentModels.some((model) => model.id === selectedModel.id)) {
      setSelectedModel(currentModels[0]);
    }
  }, [currentModels, selectedModel]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, isStreaming]);

  const handleSend = async () => {
    if (!input.trim() || !apiKey || !selectedModel) return;

    const newUserMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    };

    setMessages(prev => [...prev, newUserMsg]);
    setInput('');
    setIsStreaming(true);
    setError(null);
    setLastStats(null);

    const startTime = performance.now();
    const controller = new AbortController();
    abortRef.current = controller;

    const chatMessages: { role: string; content: string }[] = [];
    if (systemPrompt.trim()) {
      chatMessages.push({ role: 'system', content: systemPrompt });
    }
    for (const m of [...messages, newUserMsg]) {
      chatMessages.push({ role: m.role, content: m.content });
    }

    try {
      const res = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          model: selectedModel.name,
          messages: chatMessages,
          temperature,
          max_tokens: maxTokens,
          top_p: topP,
          frequency_penalty: freqPenalty,
          presence_penalty: presPenalty,
          stream: true,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `Request failed (${res.status})`);
      }

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let assistantContent = '';
      let usage: any = null;

      if (reader) {
        let buffer = '';
        const processLine = (line: string) => {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data: ')) return;
          const data = trimmed.slice(6);
          if (data === '[DONE]') return;
          try {
            const parsed = JSON.parse(data);
            const delta = parsed.choices?.[0]?.delta?.content;
            if (delta) {
              assistantContent += delta;
              setMessages(prev => {
                const last = prev[prev.length - 1];
                if (last?.role === 'assistant' && last.id === 'streaming') {
                  return [...prev.slice(0, -1), { ...last, content: assistantContent }];
                }
                return [...prev, {
                  id: 'streaming',
                  role: 'assistant',
                  content: assistantContent,
                  timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
                }];
              });
            }
            if (parsed.usage) usage = parsed.usage;
          } catch {}
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) processLine(line);
        }
        buffer += decoder.decode();
        if (buffer.trim()) processLine(buffer);
      }

      const latencyMs = Math.round(performance.now() - startTime);

      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last?.id === 'streaming') {
          return [...prev.slice(0, -1), { ...last, id: Date.now().toString() }];
        }
        if (!assistantContent) {
          return [...prev, {
            id: Date.now().toString(),
            role: 'assistant',
            content: assistantContent || '(empty response)',
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          }];
        }
        return prev;
      });

      setLastStats({
        promptTokens: usage?.prompt_tokens ?? 0,
        completionTokens: usage?.completion_tokens ?? 0,
        totalTokens: usage?.total_tokens ?? 0,
        latencyMs,
        model: selectedModel.name,
      });
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setError(err.message || 'Request failed');
        setMessages(prev => {
          const last = prev[prev.length - 1];
          if (last?.id === 'streaming') return prev.slice(0, -1);
          return prev;
        });
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  };

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setMessages(prev => {
      const last = prev[prev.length - 1];
      if (last?.id === 'streaming') {
        if (!last.content.trim()) return prev.slice(0, -1);
        return [...prev.slice(0, -1), { ...last, id: Date.now().toString() }];
      }
      return prev;
    });
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleSend();
    }
  };

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleReset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setMessages([]);
    setInput('');
    setError(null);
    setLastStats(null);
  }, []);

  return (
    <div className="flex flex-col h-full bg-gray-50 font-sans text-slate-900 overflow-hidden">
      <div className="flex-none border-b border-gray-200 bg-white px-6 py-4 z-10 relative">
        <div className="flex items-center justify-between">
          <div>
            <div className="mb-1 flex items-center gap-1.5 text-xs text-gray-500">
              <span>Home</span>
              <ChevronRight className="h-3 w-3" />
              <span className="font-medium text-gray-700">Playground</span>
            </div>
            <div className="flex items-center gap-4">
              <h1 className="flex items-center gap-2 text-xl font-bold text-gray-900">
                <Sparkles className="h-5 w-5 text-blue-600" />
                Playground
              </h1>
              <ModeSelector mode={mode} onChange={setMode} />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleReset}
              className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium text-gray-600 bg-white border border-gray-300 rounded-md hover:bg-gray-50"
            >
              <RotateCcw className="h-4 w-4" />
              Reset
            </button>
            <button
              onClick={() => setIsSidebarOpen(!isSidebarOpen)}
              className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-md transition-colors"
              title="Toggle sidebar"
            >
              {isSidebarOpen ? <PanelRightClose className="h-5 w-5" /> : <PanelRightOpen className="h-5 w-5" />}
            </button>
          </div>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex flex-col flex-1 min-w-0 bg-white">
          <div className="flex-none border-b border-gray-200 bg-white shadow-sm z-10">
            <div className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-4">
                <div className="flex-1 min-w-[240px] max-w-xs relative">
                  <label className="block text-xs font-medium text-gray-500 mb-1">Model</label>
                  <div className="relative group">
                    <select
                      className="w-full appearance-none bg-gray-50 border border-gray-200 rounded-md py-1.5 pl-3 pr-8 text-sm font-medium text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent cursor-pointer"
                      value={selectedModel?.id || ''}
                      onChange={(e) => setSelectedModel(currentModels.find(m => m.id === e.target.value) || selectedModel)}
                    >
                      {currentModels.length === 0 && <option value="">{modeEmptyCopy(mode)}</option>}
                      {currentModels.map(m => (
                        <option key={m.id} value={m.id}>{m.provider} / {m.name}</option>
                      ))}
                    </select>
                    <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-gray-500">
                      <ChevronDown className="h-4 w-4" />
                    </div>
                    {selectedModel && (
                      <div
                        className={`absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border border-white ${
                          selectedModel.status === 'online' ? 'bg-green-500' : 'bg-yellow-500'
                        }`}
                        title={`Status: ${selectedModel.status}`}
                      />
                    )}
                  </div>
                </div>

                    <ApiKeyInput value={apiKey} onChange={setApiKey} />

                {mode === 'chat' && (
                  <div className="ml-auto flex items-end h-full pt-5">
                    <button
                      onClick={() => setShowConfig(!showConfig)}
                      className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                        showConfig ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-100'
                      }`}
                    >
                      <SlidersHorizontal className="h-4 w-4" />
                      Parameters
                    </button>
                  </div>
                )}
              </div>

              {noModelsForMode && (
                <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  {modeEmptyCopy(mode)}
                </div>
              )}

              {showConfig && mode === 'chat' && (
                <div className="mt-4 pt-4 border-t border-gray-100 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                  <div className="col-span-full md:col-span-2 lg:col-span-1">
                    <label className="flex items-center justify-between text-xs font-medium text-gray-700 mb-2">
                      <span>System Prompt</span>
                    </label>
                    <textarea
                      value={systemPrompt}
                      onChange={(e) => setSystemPrompt(e.target.value)}
                      className="w-full h-24 p-2.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                      placeholder="You are a helpful assistant..."
                    />
                  </div>

                  <div className="space-y-4">
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-medium text-gray-700">Temperature</label>
                        <span className="text-xs text-gray-500 font-mono">{temperature}</span>
                      </div>
                      <input
                        type="range" min="0" max="2" step="0.1" value={temperature}
                        onChange={(e) => setTemperature(parseFloat(e.target.value))}
                        className="w-full accent-blue-600 h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-medium text-gray-700">Top P</label>
                        <span className="text-xs text-gray-500 font-mono">{topP}</span>
                      </div>
                      <input
                        type="range" min="0" max="1" step="0.05" value={topP}
                        onChange={(e) => setTopP(parseFloat(e.target.value))}
                        className="w-full accent-blue-600 h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <label className="text-xs font-medium text-gray-700 mb-1 block">Max Tokens</label>
                      <input
                        type="number" value={maxTokens}
                        onChange={(e) => setMaxTokens(parseInt(e.target.value) || 0)}
                        className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium text-gray-700 mb-1 block">Freq. Penalty</label>
                        <input
                          type="number" value={freqPenalty} min={-2} max={2} step={0.1}
                          onChange={(e) => setFreqPenalty(parseFloat(e.target.value) || 0)}
                          className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-700 mb-1 block">Pres. Penalty</label>
                        <input
                          type="number" value={presPenalty} min={-2} max={2} step={0.1}
                          onChange={(e) => setPresPenalty(parseFloat(e.target.value) || 0)}
                          className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {mode === 'chat' && !noModelsForMode && (
            <>
              <div className="flex-1 overflow-y-auto bg-gray-50/50 p-4 sm:p-6 scroll-smooth">
                <div className="max-w-4xl mx-auto space-y-6 pb-4">
                  {error && (
                    <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
                      <div className="flex items-start gap-2">
                        <AlertTriangle className="w-4 h-4 mt-0.5 flex-none" />
                        <div>
                          <div className="font-medium">Error</div>
                          <div className="mt-1 text-xs whitespace-pre-wrap">{error}</div>
                        </div>
                      </div>
                    </div>
                  )}

                  {messages.length === 0 && !error ? (
                    <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center px-4">
                      <div className="w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mb-6 shadow-sm ring-1 ring-blue-100">
                        <Sparkles className="h-8 w-8 text-blue-600" />
                      </div>
                      <h2 className="text-xl font-semibold text-gray-900 mb-2">Start a Conversation</h2>
                      <p className="text-gray-500 max-w-md mb-8">
                        Test your configured models and prompts in real-time. Requests are routed through the DeltaLLM gateway.
                      </p>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-lg">
                        {[
                          { icon: Code, text: "Write a Python function to parse JSON" },
                          { icon: TerminalSquare, text: "Explain how DNS resolution works" },
                          { icon: Zap, text: "Brainstorm 5 ideas for a SaaS product" },
                          { icon: AlertTriangle, text: "Find the bug in this React component" }
                        ].map((chip, idx) => (
                          <button
                            key={idx}
                            onClick={() => setInput(chip.text)}
                            className="flex items-center gap-3 p-3 text-left bg-white border border-gray-200 rounded-xl hover:border-blue-300 hover:bg-blue-50/50 hover:shadow-sm transition-all group"
                          >
                            <chip.icon className="h-5 w-5 text-gray-400 group-hover:text-blue-500" />
                            <span className="text-sm font-medium text-gray-600 group-hover:text-gray-900">{chip.text}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    messages.map((msg) => (
                      <div key={msg.id} className={`flex gap-4 group ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}>
                        <div className="flex-none">
                          <div className={`w-8 h-8 rounded-full flex items-center justify-center shadow-sm ${
                            msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-white border border-gray-200 text-gray-700'
                          }`}>
                            {msg.role === 'user' ? <User className="w-5 h-5" /> : <Bot className="w-5 h-5" />}
                          </div>
                        </div>
                        <div className={`flex flex-col max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                          <div className={`flex items-center gap-2 mb-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity ${
                            msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'
                          }`}>
                            <span className="text-xs font-medium text-gray-500">
                              {msg.role === 'user' ? 'You' : selectedModel ? selectedModel.provider + ' / ' + selectedModel.name : 'Assistant'}
                            </span>
                            <span className="text-[10px] text-gray-400">{msg.timestamp}</span>
                          </div>
                          <div className={`relative px-4 py-3 rounded-2xl text-sm leading-relaxed shadow-sm ${
                            msg.role === 'user'
                              ? 'bg-blue-600 text-white rounded-tr-sm'
                              : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm'
                          }`}>
                            <div className="space-y-3 whitespace-pre-wrap">
                              {msg.content.split('```').map((part, index) => {
                                if (index % 2 === 1) {
                                  const [lang, ...code] = part.split('\n');
                                  return (
                                    <div key={index} className="my-3 rounded-lg overflow-hidden border border-gray-700 bg-gray-900">
                                      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700">
                                        <span className="text-xs font-mono text-gray-400">{lang || 'text'}</span>
                                        <button
                                          className="text-gray-400 hover:text-white"
                                          onClick={() => copyToClipboard(code.join('\n'), `${msg.id}-${index}`)}
                                        >
                                          {copiedId === `${msg.id}-${index}` ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                                        </button>
                                      </div>
                                      <pre className="p-3 text-[13px] font-mono text-gray-300 overflow-x-auto">
                                        <code>{code.join('\n')}</code>
                                      </pre>
                                    </div>
                                  );
                                }
                                return <p key={index}>{part}</p>;
                              })}
                            </div>
                          </div>
                          {msg.role === 'assistant' && (
                            <div className="flex items-center gap-2 mt-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
                              <button
                                className="p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
                                onClick={() => copyToClipboard(msg.content, msg.id)}
                                title="Copy response"
                              >
                                {copiedId === msg.id ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ))
                  )}

                  {isStreaming && messages[messages.length - 1]?.role !== 'assistant' && (
                    <div className="flex gap-4 group flex-row">
                      <div className="flex-none">
                        <div className="w-8 h-8 rounded-full flex items-center justify-center shadow-sm bg-white border border-gray-200 text-gray-700">
                          <Bot className="w-5 h-5" />
                        </div>
                      </div>
                      <div className="flex flex-col max-w-[85%] items-start">
                        <div className="relative px-5 py-4 rounded-2xl rounded-tl-sm bg-white border border-gray-200 shadow-sm">
                          <div className="flex space-x-1.5 items-center h-2">
                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
                            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>
              </div>

              <div className="flex-none bg-white border-t border-gray-200 p-4">
                <div className="max-w-4xl mx-auto relative">
                  <div className="flex items-end gap-2 bg-white border border-gray-200 rounded-xl shadow-sm focus-within:border-blue-300 p-1.5 transition-all">
                    <textarea
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={handleKeyDown}
                      placeholder={apiKey ? "Message the model... (⌘ Enter to send)" : "Enter an API key to start chatting..."}
                      className="flex-1 max-h-48 min-h-[44px] bg-transparent border-0 resize-none py-2.5 px-3 text-sm focus:ring-0 focus:outline-none text-gray-900 placeholder:text-gray-400"
                      rows={Math.min(5, Math.max(1, input.split('\n').length))}
                      disabled={!apiKey || !selectedModel}
                    />
                    <div className="flex flex-col justify-end pb-1 pr-1">
                      {isStreaming ? (
                        <button
                          onClick={handleStop}
                          className="p-2 text-white bg-red-500 hover:bg-red-600 rounded-lg shadow-sm transition-colors flex items-center justify-center group"
                          title="Stop generating"
                        >
                          <StopCircle className="w-5 h-5 group-hover:scale-110 transition-transform" />
                        </button>
                      ) : (
                        <button
                          onClick={handleSend}
                          disabled={!input.trim() || !apiKey || !selectedModel}
                          className="p-2 text-white bg-blue-600 hover:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400 rounded-lg shadow-sm transition-colors flex items-center justify-center group"
                        >
                          <PlaySquare className="w-5 h-5 group-hover:-translate-y-0.5 group-hover:translate-x-0.5 transition-transform" />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center justify-between mt-2 px-1">
                    <div className="flex items-center gap-2 text-[11px] text-gray-500">
                      <CornerDownLeft className="w-3 h-3" />
                      <span>Use ⌘ + Enter to send</span>
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}

          {mode === 'chat' && noModelsForMode && <ModeUnavailableState mode={mode} />}
          {mode === 'tts' && (selectedModel ? <TTSView apiKey={apiKey} selectedModel={selectedModel} /> : <ModeUnavailableState mode={mode} />)}
          {mode === 'stt' && (selectedModel ? <STTView apiKey={apiKey} selectedModel={selectedModel} /> : <ModeUnavailableState mode={mode} />)}
        </div>

        {isSidebarOpen && (
          <div className="w-[320px] flex-none border-l border-gray-200 bg-white flex flex-col shadow-[-4px_0_15px_-3px_rgba(0,0,0,0.02)] z-20 transition-all duration-300">
            <div className="flex items-center border-b border-gray-200 px-2 py-2">
              <button
                onClick={() => setActiveTab('stats')}
                className={`flex-1 py-1.5 px-2 text-xs font-medium rounded-md flex items-center justify-center gap-1.5 ${
                  activeTab === 'stats' ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                }`}
              >
                <Activity className="w-3.5 h-3.5" />
                Stats
              </button>
              <button
                onClick={() => setActiveTab('request')}
                className={`flex-1 py-1.5 px-2 text-xs font-medium rounded-md flex items-center justify-center gap-1.5 ${
                  activeTab === 'request' ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                }`}
              >
                <Code className="w-3.5 h-3.5" />
                Inspect
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              {activeTab === 'stats' && (
                <div className="space-y-6">
                  <div>
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">Last Request Usage</h3>
                    {lastStats ? (
                      <>
                        <div className="grid grid-cols-2 gap-3 mb-3">
                          <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                            <div className="text-[10px] text-gray-500 mb-1">Prompt Tokens</div>
                            <div className="text-xl font-semibold text-gray-900">{lastStats.promptTokens}</div>
                          </div>
                          <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                            <div className="text-[10px] text-gray-500 mb-1">Completion Tokens</div>
                            <div className="text-xl font-semibold text-gray-900">{lastStats.completionTokens}</div>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                            <div className="text-[10px] text-gray-500 mb-1">Total Tokens</div>
                            <div className="text-lg font-semibold text-gray-900">{lastStats.totalTokens}</div>
                          </div>
                          <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                            <div className="text-[10px] text-gray-500 mb-1">Latency</div>
                            <div className="text-lg font-semibold text-gray-900">{(lastStats.latencyMs / 1000).toFixed(2)}s</div>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="text-center py-8 text-gray-400 text-sm">
                        Send a message to see usage statistics
                      </div>
                    )}
                  </div>

                  <div>
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">Session Info</h3>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-gray-500">Model</span>
                        <span className="text-gray-900 font-medium">{selectedModel?.name || '—'}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-gray-500">Provider</span>
                        <span className="text-gray-900 font-medium">{selectedModel?.provider || '—'}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-gray-500">Messages</span>
                        <span className="text-gray-900 font-medium">{messages.length}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-gray-500">Mode</span>
                        <span className="text-gray-900 font-medium capitalize">{mode === 'tts' ? 'Text to Speech' : mode === 'stt' ? 'Speech to Text' : 'Chat'}</span>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-gray-500">Endpoint</span>
                        <code className="text-[10px] font-mono text-blue-600">
                          {mode === 'chat' ? '/v1/chat/completions' : mode === 'tts' ? '/v1/audio/speech' : '/v1/audio/transcriptions'}
                        </code>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'request' && (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">Request Body</h3>
                    <pre className="bg-gray-900 rounded-lg p-3 text-[11px] font-mono text-gray-300 overflow-auto max-h-96 whitespace-pre-wrap">
                      {mode === 'chat'
                        ? JSON.stringify({
                            model: selectedModel?.name,
                            messages: [
                              ...(systemPrompt ? [{ role: 'system', content: systemPrompt }] : []),
                              ...messages.map(m => ({ role: m.role, content: m.content })),
                            ],
                            temperature,
                            max_tokens: maxTokens,
                            top_p: topP,
                            stream: true,
                          }, null, 2)
                        : mode === 'tts'
                        ? JSON.stringify({ model: selectedModel?.name, input: '...', voice: 'nova', speed: 1.0, response_format: 'mp3' }, null, 2)
                        : JSON.stringify({ model: selectedModel?.name, file: '(binary)', language: 'auto', response_format: 'json' }, null, 2)
                      }
                    </pre>
                  </div>

                  <div>
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">cURL</h3>
                    <pre className="bg-gray-900 rounded-lg p-3 text-[11px] font-mono text-gray-300 overflow-auto max-h-48 whitespace-pre-wrap break-all">
                      {mode === 'chat'
                        ? `curl ${window.location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ model: selectedModel?.name, messages: [{ role: 'user', content: '...' }], stream: true })}'`
                        : mode === 'tts'
                        ? `curl ${window.location.origin}/v1/audio/speech \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ model: selectedModel?.name, input: '...', voice: 'nova' })}' \\\n  --output speech.mp3`
                        : `curl ${window.location.origin}/v1/audio/transcriptions \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -F file=@audio.mp3 \\\n  -F model=${selectedModel?.name}`
                      }
                    </pre>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
