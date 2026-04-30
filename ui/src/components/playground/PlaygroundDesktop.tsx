import { useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Code,
  Copy,
  CornerDownLeft,
  Download,
  Eye,
  EyeOff,
  FileAudio,
  Key,
  MessageSquare,
  Mic,
  PanelRightClose,
  PanelRightOpen,
  Pause,
  Play,
  PlaySquare,
  RotateCcw,
  Sparkles,
  Square,
  StopCircle,
  TerminalSquare,
  Upload,
  User,
  Volume2,
  Zap,
  SlidersHorizontal,
} from 'lucide-react';
import type { ModelOption, PlaygroundMode } from './types';
import { modeEmptyCopy, modeEndpoint } from './types';
import type { ChatEngine } from './useChatEngine';
import type { TTSEngine } from './useTTSEngine';
import type { STTEngine } from './useSTTEngine';

interface SharedProps {
  mode: PlaygroundMode;
  setMode: (m: PlaygroundMode) => void;
  apiKey: string;
  setApiKey: (v: string) => void;
  allModels: ModelOption[];
  currentModels: ModelOption[];
  selectedModel: ModelOption | null;
  setSelectedModel: (m: ModelOption | null) => void;
  noModelsForMode: boolean;
  chat: ChatEngine;
  tts: TTSEngine;
  stt: STTEngine;
}

function ModeUnavailableState({ mode }: { mode: PlaygroundMode }) {
  return (
    <div className="flex flex-1 items-center justify-center bg-gray-50/50 p-6">
      <div className="max-w-md rounded-2xl border border-dashed border-gray-300 bg-white px-8 py-10 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50">
          <Sparkles className="h-7 w-7 text-blue-600" />
        </div>
        <h2 className="mb-2 text-lg font-semibold text-gray-900">
          {mode === 'tts' ? 'Text-to-Speech' : mode === 'stt' ? 'Speech-to-Text' : 'Chat'} unavailable
        </h2>
        <p className="text-sm leading-6 text-gray-500">{modeEmptyCopy(mode)}</p>
      </div>
    </div>
  );
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
            mode === m.key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
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

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function TTSView({ apiKey, tts }: { apiKey: string; tts: TTSEngine }) {
  const {
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
  } = tts;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Voice</label>
            {voices.length > 1 ? (
              <div className="grid grid-cols-3 gap-1.5">
                {voices.map((v) => (
                  <button
                    key={v}
                    onClick={() => setVoice(v)}
                    className={`px-2 py-1.5 text-xs font-medium rounded-md border transition-all ${
                      voice === v
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
                value={voice}
                onChange={(e) => setVoice(e.target.value)}
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
              type="range"
              min="0.25"
              max="4.0"
              step="0.25"
              value={speed}
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
              value={format}
              onChange={(e) => setFormat(e.target.value)}
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
            <span className="text-xs text-gray-400">{text.length} / 4096 characters</span>
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value.slice(0, 4096))}
            className="w-full h-40 p-3 text-sm bg-white border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none leading-relaxed"
            placeholder="Enter the text you want to convert to speech..."
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleGenerate}
            disabled={!text.trim() || isGenerating || !apiKey}
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
            <span className="text-xs text-gray-500">
              Processing with voice "{voice}" at {speed}x speed...
            </span>
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
                    Voice: {voice} · Format: {format.toUpperCase()}
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
                    const rect = e.currentTarget.getBoundingClientRect();
                    seekTo((e.clientX - rect.left) / rect.width);
                  }}
                >
                  <div
                    className="bg-blue-600 h-2 rounded-full transition-all duration-100"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                {duration > 0 && (
                  <div className="flex justify-between mt-1 text-[10px] text-gray-400 font-mono">
                    <span>{((duration * progress) / 100).toFixed(1)}s</span>
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

function STTView({ apiKey, stt }: { apiKey: string; stt: STTEngine }) {
  const {
    selectedFile,
    resetOutput,
    language,
    setLanguage,
    responseFormat,
    setResponseFormat,
    outputPrompt,
    setOutputPrompt,
    isTranscribing,
    transcription,
    isRecording,
    recordingTime,
    error,
    stats,
    copiedTranscription,
    handleFile,
    handleTranscribe,
    startRecording,
    stopRecording,
    copyTranscription,
  } = stt;

  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const formatTime = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
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
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors cursor-pointer ${
              dragOver
                ? 'border-blue-400 bg-blue-50'
                : selectedFile
                  ? 'border-green-300 bg-green-50/50'
                  : 'border-gray-300 bg-gray-50 hover:border-gray-400'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="audio/*,.mp3,.mp4,.mpeg,.m4a,.wav,.webm"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFile(f);
              }}
            />
            {selectedFile ? (
              <div className="space-y-2">
                <div className="w-12 h-12 bg-green-100 rounded-xl flex items-center justify-center mx-auto">
                  <FileAudio className="w-6 h-6 text-green-600" />
                </div>
                <div className="text-sm font-medium text-gray-900">{selectedFile.name}</div>
                <div className="text-xs text-gray-500">
                  {formatSize(selectedFile.size)} · {selectedFile.type || 'audio'}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    resetOutput();
                  }}
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
              onClick={() => (isRecording ? stopRecording() : startRecording())}
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
                {[6, 14, 9, 18, 11, 22, 8, 16, 12, 20, 7, 17, 13, 21, 10, 19, 8, 15, 11, 14].map((h, i) => (
                  <div
                    key={i}
                    className="w-1 bg-red-400 rounded-full animate-pulse"
                    style={{
                      height: `${h}px`,
                      animationDelay: `${i * 50}ms`,
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {selectedFile && !isRecording && (
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
                  onClick={copyTranscription}
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

export default function PlaygroundDesktop({
  mode,
  setMode,
  apiKey,
  setApiKey,
  currentModels,
  selectedModel,
  setSelectedModel,
  noModelsForMode,
  chat,
  tts,
  stt,
}: SharedProps) {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [activeTab, setActiveTab] = useState<'stats' | 'request'>('stats');
  const [showConfig, setShowConfig] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const {
    messages,
    input,
    setInput,
    isStreaming,
    lastStats,
    error,
    copiedId,
    params,
    setParams,
    handleSend,
    handleStop,
    handleReset,
    copyToClipboard,
  } = chat;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      void handleSend();
    }
  };

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
                      onChange={(e) => setSelectedModel(currentModels.find((m) => m.id === e.target.value) || selectedModel)}
                    >
                      {currentModels.length === 0 && <option value="">{modeEmptyCopy(mode)}</option>}
                      {currentModels.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.provider} / {m.name}
                        </option>
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
                      value={params.systemPrompt}
                      onChange={(e) => setParams((p) => ({ ...p, systemPrompt: e.target.value }))}
                      className="w-full h-24 p-2.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                      placeholder="You are a helpful assistant..."
                    />
                  </div>

                  <div className="space-y-4">
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-medium text-gray-700">Temperature</label>
                        <span className="text-xs text-gray-500 font-mono">{params.temperature}</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        value={params.temperature}
                        onChange={(e) => setParams((p) => ({ ...p, temperature: parseFloat(e.target.value) }))}
                        className="w-full accent-blue-600 h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-xs font-medium text-gray-700">Top P</label>
                        <span className="text-xs text-gray-500 font-mono">{params.topP}</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.05"
                        value={params.topP}
                        onChange={(e) => setParams((p) => ({ ...p, topP: parseFloat(e.target.value) }))}
                        className="w-full accent-blue-600 h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                      />
                    </div>
                  </div>

                  <div className="space-y-4">
                    <div>
                      <label className="text-xs font-medium text-gray-700 mb-1 block">Max Tokens</label>
                      <input
                        type="number"
                        value={params.maxTokens}
                        onChange={(e) => setParams((p) => ({ ...p, maxTokens: parseInt(e.target.value) || 0 }))}
                        className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium text-gray-700 mb-1 block">Freq. Penalty</label>
                        <input
                          type="number"
                          value={params.freqPenalty}
                          min={-2}
                          max={2}
                          step={0.1}
                          onChange={(e) => setParams((p) => ({ ...p, freqPenalty: parseFloat(e.target.value) || 0 }))}
                          className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-700 mb-1 block">Pres. Penalty</label>
                        <input
                          type="number"
                          value={params.presPenalty}
                          min={-2}
                          max={2}
                          step={0.1}
                          onChange={(e) => setParams((p) => ({ ...p, presPenalty: parseFloat(e.target.value) || 0 }))}
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
                          { icon: Code, text: 'Write a Python function to parse JSON' },
                          { icon: TerminalSquare, text: 'Explain how DNS resolution works' },
                          { icon: Zap, text: 'Brainstorm 5 ideas for a SaaS product' },
                          { icon: AlertTriangle, text: 'Find the bug in this React component' },
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
                          <div
                            className={`w-8 h-8 rounded-full flex items-center justify-center shadow-sm ${
                              msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-white border border-gray-200 text-gray-700'
                            }`}
                          >
                            {msg.role === 'user' ? <User className="w-5 h-5" /> : <Bot className="w-5 h-5" />}
                          </div>
                        </div>
                        <div className={`flex flex-col max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                          <div
                            className={`flex items-center gap-2 mb-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity ${
                              msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'
                            }`}
                          >
                            <span className="text-xs font-medium text-gray-500">
                              {msg.role === 'user' ? 'You' : selectedModel ? selectedModel.provider + ' / ' + selectedModel.name : 'Assistant'}
                            </span>
                            <span className="text-[10px] text-gray-400">{msg.timestamp}</span>
                          </div>
                          <div
                            className={`relative px-4 py-3 rounded-2xl text-sm leading-relaxed shadow-sm ${
                              msg.role === 'user' ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm'
                            }`}
                          >
                            <div className="space-y-3 whitespace-pre-wrap">
                              {msg.content.split('```').map((part, index) => {
                                if (index % 2 === 1) {
                                  const [lang, ...code] = part.split('\n');
                                  return (
                                    <div key={index} className="my-3 rounded-lg overflow-hidden border border-gray-700 bg-gray-900">
                                      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700">
                                        <span className="text-xs font-mono text-gray-400">{lang || 'text'}</span>
                                        <button className="text-gray-400 hover:text-white" onClick={() => copyToClipboard(code.join('\n'), `${msg.id}-${index}`)}>
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
                      placeholder={apiKey ? 'Message the model... (⌘ Enter to send)' : 'Enter an API key to start chatting...'}
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
          {mode === 'tts' && (selectedModel ? <TTSView apiKey={apiKey} tts={tts} /> : <ModeUnavailableState mode={mode} />)}
          {mode === 'stt' && (selectedModel ? <STTView apiKey={apiKey} stt={stt} /> : <ModeUnavailableState mode={mode} />)}
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
                      <div className="text-center py-8 text-gray-400 text-sm">Send a message to see usage statistics</div>
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
                        <span className="text-gray-900 font-medium capitalize">
                          {mode === 'tts' ? 'Text to Speech' : mode === 'stt' ? 'Speech to Text' : 'Chat'}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-gray-500">Endpoint</span>
                        <code className="text-[10px] font-mono text-blue-600">{modeEndpoint(mode)}</code>
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
                        ? JSON.stringify(
                            {
                              model: selectedModel?.name,
                              messages: [
                                ...(params.systemPrompt ? [{ role: 'system', content: params.systemPrompt }] : []),
                                ...messages.map((m) => ({ role: m.role, content: m.content })),
                              ],
                              temperature: params.temperature,
                              max_tokens: params.maxTokens,
                              top_p: params.topP,
                              stream: true,
                            },
                            null,
                            2,
                          )
                        : mode === 'tts'
                          ? JSON.stringify({ model: selectedModel?.name, input: '...', voice: 'nova', speed: 1.0, response_format: 'mp3' }, null, 2)
                          : JSON.stringify({ model: selectedModel?.name, file: '(binary)', language: 'auto', response_format: 'json' }, null, 2)}
                    </pre>
                  </div>

                  <div>
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">cURL</h3>
                    <pre className="bg-gray-900 rounded-lg p-3 text-[11px] font-mono text-gray-300 overflow-auto max-h-48 whitespace-pre-wrap break-all">
                      {mode === 'chat'
                        ? `curl ${window.location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ model: selectedModel?.name, messages: [{ role: 'user', content: '...' }], stream: true })}'`
                        : mode === 'tts'
                          ? `curl ${window.location.origin}/v1/audio/speech \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ model: selectedModel?.name, input: '...', voice: 'nova' })}' \\\n  --output speech.mp3`
                          : `curl ${window.location.origin}/v1/audio/transcriptions \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -F file=@audio.mp3 \\\n  -F model=${selectedModel?.name}`}
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
