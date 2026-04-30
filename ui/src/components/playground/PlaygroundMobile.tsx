import { useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
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
  Pause,
  Play,
  RotateCcw,
  Send,
  SlidersHorizontal,
  Sparkles,
  Square,
  StopCircle,
  Upload,
  User,
  Volume2,
  X,
} from 'lucide-react';
import type { ModelOption, PlaygroundMode } from './types';
import { modeEmptyCopy, modeEndpoint, modeShortLabel } from './types';
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

const STARTER_CHIPS = [
  'Write a Python function to parse JSON',
  'Explain how DNS resolution works',
  'Brainstorm 5 ideas for a SaaS product',
  'Find the bug in this React component',
];

function ModePills({ mode, onChange }: { mode: PlaygroundMode; onChange: (m: PlaygroundMode) => void }) {
  const items: { key: PlaygroundMode; label: string; icon: React.ReactNode }[] = [
    { key: 'chat', label: 'Chat', icon: <MessageSquare className="w-3.5 h-3.5" /> },
    { key: 'tts', label: 'TTS', icon: <Volume2 className="w-3.5 h-3.5" /> },
    { key: 'stt', label: 'STT', icon: <Mic className="w-3.5 h-3.5" /> },
  ];
  return (
    <div className="flex items-center bg-gray-100 rounded-lg p-0.5 w-full" role="tablist" aria-label="Playground mode">
      {items.map((m) => (
        <button
          key={m.key}
          onClick={() => onChange(m.key)}
          role="tab"
          aria-selected={mode === m.key}
          className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs font-medium rounded-md transition-all ${
            mode === m.key ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'
          }`}
        >
          {m.icon}
          {m.label}
        </button>
      ))}
    </div>
  );
}

interface SheetProps {
  open: boolean;
  onClose: () => void;
  titleId: string;
  children: React.ReactNode;
  /** Optional ref to the first focusable element inside the sheet body. */
  firstFocusRef?: React.RefObject<HTMLElement>;
  /** Optional element to return focus to when the sheet closes. */
  returnFocusRef?: React.MutableRefObject<HTMLElement | null>;
  maxHeightClass?: string;
}

function BottomSheet({
  open,
  onClose,
  titleId,
  children,
  firstFocusRef,
  returnFocusRef,
  maxHeightClass = 'max-h-[85vh]',
}: SheetProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    const focusTimer = window.setTimeout(() => {
      firstFocusRef?.current?.focus();
    }, 0);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.clearTimeout(focusTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Restore focus on unmount
  useEffect(() => {
    if (open) return;
    const restoreTo = returnFocusRef?.current;
    if (restoreTo && typeof restoreTo.focus === 'function') {
      window.setTimeout(() => restoreTo.focus(), 0);
      if (returnFocusRef) returnFocusRef.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-black/40" />
      <div
        className={`relative bg-white rounded-t-2xl w-full max-w-md ${maxHeightClass} flex flex-col shadow-xl`}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

function ChatMobileBubble({
  role,
  content,
  ts,
  modelLabel,
  copyToClipboard,
  copiedId,
  msgId,
}: {
  role: 'user' | 'assistant' | 'system';
  content: string;
  ts: string;
  modelLabel: string;
  copyToClipboard: (text: string, id: string) => void;
  copiedId: string | null;
  msgId: string;
}) {
  const isUser = role === 'user';
  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div className="flex-none">
        <div
          className={`w-7 h-7 rounded-full flex items-center justify-center shadow-sm ${
            isUser ? 'bg-blue-600 text-white' : 'bg-white border border-gray-200 text-gray-700'
          }`}
        >
          {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
        </div>
      </div>
      <div className={`flex flex-col max-w-[85%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div className={`flex items-center gap-2 mb-0.5 px-1 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
          <span className="text-[10px] font-medium text-gray-500 truncate max-w-[180px]">{isUser ? 'You' : modelLabel}</span>
          <span className="text-[10px] text-gray-400">{ts}</span>
        </div>
        <div
          className={`relative px-3 py-2 rounded-2xl text-[13px] leading-relaxed shadow-sm ${
            isUser ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm'
          }`}
        >
          <div className="space-y-2 whitespace-pre-wrap">
            {content.split('```').map((part, index) => {
              if (index % 2 === 1) {
                const [lang, ...code] = part.split('\n');
                const codeBody = code.join('\n');
                const codeId = `${msgId}-${index}`;
                return (
                  <div key={index} className="my-1.5 -mx-1 rounded-lg overflow-hidden border border-gray-700 bg-gray-900">
                    <div className="flex items-center justify-between px-2.5 py-1 bg-gray-800 border-b border-gray-700">
                      <span className="text-[10px] font-mono text-gray-400">{lang || 'text'}</span>
                      <button
                        type="button"
                        onClick={() => copyToClipboard(codeBody, codeId)}
                        className="text-gray-400 active:text-white"
                        aria-label="Copy code"
                      >
                        {copiedId === codeId ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
                      </button>
                    </div>
                    <pre className="p-2.5 text-[11px] font-mono text-gray-300 overflow-x-auto">
                      <code>{codeBody}</code>
                    </pre>
                  </div>
                );
              }
              return <p key={index}>{part}</p>;
            })}
          </div>
        </div>
        {role === 'assistant' && (
          <div className="flex items-center gap-2 mt-1 px-1">
            <button
              className="p-1 text-gray-400 active:text-gray-600 active:bg-gray-100 rounded"
              onClick={() => copyToClipboard(content, msgId)}
              aria-label="Copy response"
              title="Copy response"
            >
              {copiedId === msgId ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ChatMobileView({
  chat,
  apiKey,
  selectedModel,
}: {
  chat: ChatEngine;
  apiKey: string;
  selectedModel: ModelOption | null;
}) {
  const { messages, input, setInput, isStreaming, error, copiedId, handleSend, handleStop, copyToClipboard } = chat;
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  const empty = messages.length === 0 && !error;
  const modelLabel = selectedModel ? `${selectedModel.provider} / ${selectedModel.name}` : 'Assistant';

  return (
    <>
      <div className="flex-1 overflow-y-auto bg-gray-50/60 px-3 pt-3 pb-2 space-y-3">
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700">
            <div className="flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-none" />
              <div>
                <div className="font-medium">Error</div>
                <div className="mt-1 whitespace-pre-wrap break-all">{error}</div>
              </div>
            </div>
          </div>
        )}

        {empty ? (
          <div className="flex flex-col items-center justify-center h-full text-center px-4 py-12">
            <div className="w-14 h-14 bg-blue-50 rounded-2xl flex items-center justify-center mb-4 ring-1 ring-blue-100">
              <Sparkles className="h-7 w-7 text-blue-600" />
            </div>
            <h2 className="text-base font-semibold text-gray-900 mb-1.5">Start a conversation</h2>
            <p className="text-xs text-gray-500 mb-5 leading-relaxed">
              Test your configured models in real-time through the DeltaLLM gateway.
            </p>
            <div className="grid grid-cols-1 gap-2 w-full">
              {STARTER_CHIPS.map((chip) => (
                <button
                  key={chip}
                  onClick={() => setInput(chip)}
                  className="text-left p-3 bg-white border border-gray-200 rounded-xl text-sm text-gray-700 active:bg-blue-50 active:border-blue-300 transition-colors"
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((m) => (
              <ChatMobileBubble
                key={m.id}
                role={m.role}
                content={m.content}
                ts={m.timestamp}
                modelLabel={modelLabel}
                copyToClipboard={copyToClipboard}
                copiedId={copiedId}
                msgId={m.id}
              />
            ))}
            {isStreaming && messages[messages.length - 1]?.role !== 'assistant' && (
              <div className="flex gap-2">
                <div className="flex-none">
                  <div className="w-7 h-7 rounded-full flex items-center justify-center shadow-sm bg-white border border-gray-200 text-gray-700">
                    <Bot className="w-4 h-4" />
                  </div>
                </div>
                <div className="px-4 py-3 rounded-2xl rounded-tl-sm bg-white border border-gray-200 shadow-sm">
                  <div className="flex space-x-1.5 items-center h-2">
                    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" />
                    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      <div className="flex-none bg-white border-t border-gray-200 px-3 py-2.5">
        <div className="flex items-end gap-2 bg-white border border-gray-200 rounded-xl shadow-sm focus-within:border-blue-300 p-1.5">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={apiKey ? 'Message the model...' : 'Enter API key first'}
            rows={Math.min(4, Math.max(1, input.split('\n').length))}
            className="flex-1 max-h-32 min-h-[36px] bg-transparent border-0 resize-none py-1.5 px-2 text-sm focus:ring-0 focus:outline-none text-gray-900 placeholder:text-gray-400"
            disabled={!apiKey || !selectedModel}
          />
          {isStreaming ? (
            <button
              onClick={handleStop}
              className="w-9 h-9 flex items-center justify-center text-white bg-red-500 active:bg-red-600 rounded-lg shadow-sm flex-none"
              aria-label="Stop generating"
              title="Stop"
            >
              <StopCircle className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim() || !apiKey || !selectedModel}
              className="w-9 h-9 flex items-center justify-center text-white bg-blue-600 active:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400 rounded-lg shadow-sm flex-none"
              aria-label="Send"
              title="Send"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
        <div className="flex items-center justify-between mt-1.5 px-1">
          <div className="flex items-center gap-1 text-[10px] text-gray-400">
            <CornerDownLeft className="w-2.5 h-2.5" />
            <span>Tap send to submit</span>
          </div>
          <span className="text-[10px] text-gray-400">{input.length} / 4096</span>
        </div>
      </div>
    </>
  );
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function TTSMobileView({ apiKey, tts }: { apiKey: string; tts: TTSEngine }) {
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
    <div className="flex-1 overflow-y-auto p-3 space-y-4 bg-gray-50/60">
      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1.5">Voice</label>
        {voices.length > 1 ? (
          <div className="grid grid-cols-3 gap-1.5">
            {voices.map((v) => (
              <button
                key={v}
                onClick={() => setVoice(v)}
                className={`px-2 py-1.5 text-xs font-medium rounded-md border ${
                  voice === v ? 'bg-blue-50 border-blue-300 text-blue-700' : 'bg-white border-gray-200 text-gray-600'
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
            className="w-full px-3 py-2 text-sm border border-gray-200 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs font-medium text-gray-700">Speed</label>
            <span className="text-[10px] text-gray-500 font-mono">{speed.toFixed(2)}x</span>
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
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1.5">Format</label>
          <div className="relative">
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              className="w-full appearance-none bg-white border border-gray-200 rounded-md py-2 pl-3 pr-7 text-sm text-gray-700 uppercase"
            >
              <option value="mp3">MP3</option>
              <option value="opus">Opus</option>
              <option value="aac">AAC</option>
              <option value="flac">FLAC</option>
              <option value="wav">WAV</option>
              <option value="pcm">PCM</option>
            </select>
            <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
          </div>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-medium text-gray-700">Input Text</label>
          <span className="text-[10px] text-gray-400">{text.length} / 4096</span>
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, 4096))}
          rows={5}
          placeholder="Enter the text you want to convert to speech..."
          className="w-full p-2.5 text-sm bg-white border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none leading-relaxed"
        />
      </div>

      <button
        onClick={handleGenerate}
        disabled={!text.trim() || isGenerating || !apiKey}
        className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg active:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400 shadow-sm"
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
      {!apiKey && <div className="text-[11px] text-amber-600">Enter an API key first</div>}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-none" />
            <div>
              <div className="font-medium">Error</div>
              <div className="mt-1 whitespace-pre-wrap break-all">{error}</div>
            </div>
          </div>
        </div>
      )}

      {audioUrl && (
        <div className="bg-white border border-gray-200 rounded-xl p-3 space-y-3 shadow-sm">
          <audio ref={audioRef} src={audioUrl} preload="metadata" />
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 min-w-0">
              <div className="w-8 h-8 bg-blue-50 rounded-lg flex items-center justify-center flex-none">
                <FileAudio className="w-4 h-4 text-blue-600" />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium text-gray-900 truncate">Generated Audio</div>
                <div className="text-[10px] text-gray-500 truncate">
                  {voice} · {format.toUpperCase()}
                  {duration > 0 && ` · ${duration.toFixed(1)}s`}
                </div>
              </div>
            </div>
            <button
              onClick={handleDownload}
              className="w-9 h-9 flex-none rounded-full bg-gray-100 text-gray-600 flex items-center justify-center active:bg-gray-200"
              aria-label="Download audio"
              title="Download"
            >
              <Download className="w-4 h-4" />
            </button>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={togglePlay}
              className="w-9 h-9 flex-none rounded-full bg-blue-600 text-white flex items-center justify-center active:bg-blue-700 shadow-sm"
              aria-label={isPlaying ? 'Pause' : 'Play'}
            >
              {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
            </button>
            <div className="flex-1 min-w-0">
              <div
                className="h-1.5 bg-gray-200 rounded-full overflow-hidden cursor-pointer"
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  seekTo((e.clientX - rect.left) / rect.width);
                }}
              >
                <div className="h-full bg-blue-600 rounded-full transition-all duration-100" style={{ width: `${progress}%` }} />
              </div>
              <div className="flex items-center justify-between mt-1">
                <span className="text-[10px] font-mono text-gray-500">
                  {duration > 0 ? `${((duration * progress) / 100).toFixed(1)}s` : '0.0s'}
                </span>
                <span className="text-[10px] font-mono text-gray-500">
                  {duration > 0 ? `${duration.toFixed(1)}s` : '—'}
                </span>
              </div>
            </div>
          </div>
          {stats && (
            <div className="grid grid-cols-4 gap-2 pt-2 border-t border-gray-100">
              {[
                { label: 'Chars', value: stats.characters.toString() },
                { label: 'Dur', value: duration > 0 ? `${duration.toFixed(1)}s` : '—' },
                { label: 'Size', value: formatBytes(stats.fileSize) },
                { label: 'Latency', value: `${(stats.latencyMs / 1000).toFixed(1)}s` },
              ].map((s) => (
                <div key={s.label} className="text-center">
                  <div className="text-[9px] text-gray-400 uppercase tracking-wider">{s.label}</div>
                  <div className="text-xs font-semibold text-gray-900 truncate">{s.value}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function STTMobileView({ apiKey, stt }: { apiKey: string; stt: STTEngine }) {
  const {
    selectedFile,
    resetOutput,
    language,
    setLanguage,
    responseFormat,
    setResponseFormat,
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

  const fileInputRef = useRef<HTMLInputElement>(null);
  const formatTime = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-4 bg-gray-50/60">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1.5">Language</label>
          <div className="relative">
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="w-full appearance-none bg-white border border-gray-200 rounded-md py-2 pl-3 pr-7 text-sm text-gray-700"
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
            <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1.5">Format</label>
          <div className="relative">
            <select
              value={responseFormat}
              onChange={(e) => setResponseFormat(e.target.value)}
              className="w-full appearance-none bg-white border border-gray-200 rounded-md py-2 pl-3 pr-7 text-sm text-gray-700"
            >
              <option value="json">JSON</option>
              <option value="text">Plain Text</option>
              <option value="srt">SRT</option>
              <option value="verbose_json">Verbose JSON</option>
              <option value="vtt">VTT</option>
            </select>
            <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
          </div>
        </div>
      </div>

      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        className={`w-full border-2 border-dashed rounded-xl p-5 text-center active:bg-gray-100 ${
          selectedFile ? 'border-green-300 bg-green-50/50' : 'border-gray-300 bg-gray-50'
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
          <div className="space-y-1.5">
            <div className="w-10 h-10 bg-green-100 rounded-xl flex items-center justify-center mx-auto">
              <FileAudio className="w-5 h-5 text-green-600" />
            </div>
            <div className="text-sm font-medium text-gray-900 truncate">{selectedFile.name}</div>
            <div className="text-[11px] text-gray-500">
              {formatSize(selectedFile.size)} · {selectedFile.type || 'audio'}
            </div>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                resetOutput();
              }}
              className="text-[11px] text-red-500 active:text-red-600"
            >
              Remove
            </button>
          </div>
        ) : (
          <>
            <div className="w-10 h-10 bg-white rounded-xl flex items-center justify-center mx-auto mb-2 shadow-sm">
              <Upload className="w-5 h-5 text-gray-400" />
            </div>
            <div className="text-sm font-medium text-gray-700">Tap to upload audio</div>
            <div className="text-[11px] text-gray-500 mt-0.5">MP3, M4A, WAV, WEBM · Max 25 MB</div>
          </>
        )}
      </button>

      <div className="flex flex-col items-center text-center bg-white border border-gray-200 rounded-xl p-5">
        <button
          onClick={() => (isRecording ? stopRecording() : startRecording())}
          className={`w-14 h-14 rounded-full flex items-center justify-center shadow-sm mb-2 ${
            isRecording ? 'bg-red-500 text-white animate-pulse' : 'bg-gray-100 text-gray-500'
          }`}
          aria-label={isRecording ? 'Stop recording' : 'Start recording'}
        >
          {isRecording ? <Square className="w-5 h-5" /> : <Mic className="w-6 h-6" />}
        </button>
        <div className="text-sm font-medium text-gray-900">
          {isRecording ? `Recording... ${formatTime(recordingTime)}` : 'Record from mic'}
        </div>
        <div className="text-[11px] text-gray-500 mt-0.5">{isRecording ? 'Tap to stop' : 'Tap to start'}</div>
      </div>

      {selectedFile && !isRecording && (
        <button
          onClick={handleTranscribe}
          disabled={isTranscribing || !apiKey}
          className="w-full flex items-center justify-center gap-2 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg active:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400 shadow-sm"
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
      )}
      {!apiKey && selectedFile && <div className="text-[11px] text-amber-600">Enter an API key first</div>}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700">
          <div className="flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-none" />
            <div>
              <div className="font-medium">Error</div>
              <div className="mt-1 whitespace-pre-wrap break-all">{error}</div>
            </div>
          </div>
        </div>
      )}

      {transcription !== null && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50">
            <div className="flex items-center gap-2 min-w-0">
              <MessageSquare className="w-4 h-4 text-gray-500 flex-none" />
              <span className="text-xs font-medium text-gray-900 truncate">Transcription</span>
              {stats?.detectedLang && (
                <span className="px-1.5 py-0.5 bg-green-100 text-green-700 text-[10px] font-medium rounded">
                  {stats.detectedLang}
                </span>
              )}
            </div>
            <button
              onClick={copyTranscription}
              className="flex items-center gap-1 px-2 py-1 text-[11px] text-gray-500 active:text-gray-700 active:bg-gray-100 rounded"
            >
              {copiedTranscription ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
              {copiedTranscription ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="p-3 text-sm text-gray-800 leading-relaxed whitespace-pre-wrap max-h-56 overflow-y-auto break-words">
            {transcription}
          </div>
          {stats && (
            <div className="flex items-center gap-4 px-3 py-2 border-t border-gray-100 bg-gray-50">
              <div className="text-[11px]">
                <span className="text-gray-400">Words: </span>
                <span className="font-semibold text-gray-700">{stats.words}</span>
              </div>
              <div className="text-[11px]">
                <span className="text-gray-400">Latency: </span>
                <span className="font-semibold text-gray-700">{(stats.latencyMs / 1000).toFixed(1)}s</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function PlaygroundMobile({
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
  const [showApiKey, setShowApiKey] = useState(false);
  const [showParams, setShowParams] = useState(false);
  const [showInspect, setShowInspect] = useState(false);
  const [showModelSheet, setShowModelSheet] = useState(false);
  const [inspectTab, setInspectTab] = useState<'stats' | 'request'>('stats');

  const paramsBtnRef = useRef<HTMLButtonElement | null>(null);
  const inspectBtnRef = useRef<HTMLButtonElement | null>(null);
  const modelBtnRef = useRef<HTMLButtonElement | null>(null);

  const paramsReturnRef = useRef<HTMLElement | null>(null);
  const inspectReturnRef = useRef<HTMLElement | null>(null);
  const modelReturnRef = useRef<HTMLElement | null>(null);

  const paramsCloseRef = useRef<HTMLButtonElement>(null);
  const inspectCloseRef = useRef<HTMLButtonElement>(null);
  const modelCloseRef = useRef<HTMLButtonElement>(null);

  const openParams = () => {
    paramsReturnRef.current = paramsBtnRef.current;
    setShowParams(true);
  };
  const openInspect = () => {
    inspectReturnRef.current = inspectBtnRef.current;
    setShowInspect(true);
  };
  const openModelSheet = () => {
    modelReturnRef.current = modelBtnRef.current;
    setShowModelSheet(true);
  };

  const { params, setParams, lastStats, messages, handleReset } = chat;

  const modelLabel = selectedModel ? `${selectedModel.provider} / ${selectedModel.name}` : 'No model';

  return (
    <div className="flex flex-col h-full bg-gray-50 font-sans antialiased text-gray-900 relative overflow-hidden">
      {/* Sticky app bar */}
      <header className="flex-none sticky top-0 z-20 bg-white border-b border-gray-200 px-3 h-12 flex items-center justify-between">
        <div className="flex items-center gap-1.5 min-w-0">
          <Sparkles className="w-4 h-4 text-blue-600 flex-none" />
          <h1 className="font-semibold text-base truncate">Playground</h1>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleReset}
            className="p-2 text-gray-500 active:bg-gray-100 rounded-full"
            aria-label="Reset conversation"
            title="Reset"
          >
            <RotateCcw className="w-4 h-4" />
          </button>
          <button
            ref={inspectBtnRef}
            onClick={openInspect}
            className="p-2 text-gray-500 active:bg-gray-100 rounded-full"
            aria-label="Open inspect drawer"
            aria-haspopup="dialog"
            title="Inspect"
          >
            <Activity className="w-4 h-4" />
          </button>
        </div>
      </header>

      {/* Mode pills + toolbar */}
      <div className="flex-none bg-white border-b border-gray-200 px-3 pt-2.5 pb-3 space-y-2.5 z-10">
        <ModePills mode={mode} onChange={setMode} />

        <button
          ref={modelBtnRef}
          onClick={openModelSheet}
          disabled={currentModels.length === 0}
          className="w-full flex items-center justify-between bg-gray-50 border border-gray-200 rounded-md px-3 py-2 text-left active:bg-gray-100 disabled:opacity-60"
          aria-haspopup="dialog"
        >
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={`block w-2 h-2 rounded-full flex-none ${
                selectedModel ? (selectedModel.status === 'online' ? 'bg-green-500' : 'bg-yellow-500') : 'bg-gray-300'
              }`}
            />
            <div className="flex flex-col min-w-0">
              <span className="text-[10px] uppercase tracking-wider text-gray-500 font-medium">Model</span>
              <span className="text-sm font-medium text-gray-900 truncate">{modelLabel}</span>
            </div>
          </div>
          <ChevronDown className="w-4 h-4 text-gray-400 flex-none ml-2" />
        </button>

        <div className="flex items-center gap-2">
          <div className="relative flex-1 min-w-0">
            <Key className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type={showApiKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
              className="w-full bg-white border border-gray-200 rounded-md py-2 pl-8 pr-8 text-sm text-gray-700 font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 truncate"
            />
            <button
              onClick={() => setShowApiKey(!showApiKey)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 active:text-gray-600"
              aria-label={showApiKey ? 'Hide API key' : 'Show API key'}
              type="button"
            >
              {showApiKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            </button>
          </div>
          {mode === 'chat' && (
            <button
              ref={paramsBtnRef}
              onClick={openParams}
              className="flex-none flex items-center gap-1 px-2.5 py-2 text-xs font-medium text-gray-700 bg-white border border-gray-200 rounded-md active:bg-gray-50"
              aria-label="Open parameters"
              aria-haspopup="dialog"
            >
              <SlidersHorizontal className="w-3.5 h-3.5" />
              Params
            </button>
          )}
        </div>

        {noModelsForMode && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
            {modeEmptyCopy(mode)}
          </div>
        )}
      </div>

      {/* Main content per mode */}
      {mode === 'chat' && !noModelsForMode && <ChatMobileView chat={chat} apiKey={apiKey} selectedModel={selectedModel} />}
      {mode === 'tts' && (selectedModel ? <TTSMobileView apiKey={apiKey} tts={tts} /> : null)}
      {mode === 'stt' && (selectedModel ? <STTMobileView apiKey={apiKey} stt={stt} /> : null)}
      {(mode === 'chat' && noModelsForMode) || (!selectedModel && (mode === 'tts' || mode === 'stt')) ? (
        <div className="flex-1 flex items-center justify-center bg-gray-50/50 p-6">
          <div className="max-w-sm rounded-2xl border border-dashed border-gray-300 bg-white px-6 py-8 text-center shadow-sm">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50">
              <Sparkles className="h-6 w-6 text-blue-600" />
            </div>
            <h2 className="mb-1 text-base font-semibold text-gray-900">{modeShortLabel(mode)} unavailable</h2>
            <p className="text-xs leading-5 text-gray-500">{modeEmptyCopy(mode)}</p>
          </div>
        </div>
      ) : null}

      {/* Parameters bottom sheet */}
      <BottomSheet
        open={showParams}
        onClose={() => setShowParams(false)}
        titleId="playground-params-title"
        firstFocusRef={paramsCloseRef as React.RefObject<HTMLElement>}
        returnFocusRef={paramsReturnRef}
      >
        <div className="sticky top-0 bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between rounded-t-2xl">
          <h3 id="playground-params-title" className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-blue-600" />
            Parameters
          </h3>
          <button
            ref={paramsCloseRef}
            onClick={() => setShowParams(false)}
            className="p-1.5 -mr-1.5 text-gray-400 active:text-gray-600 rounded"
            aria-label="Close parameters"
            type="button"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-4 space-y-5 overflow-y-auto">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">System Prompt</label>
            <textarea
              value={params.systemPrompt}
              onChange={(e) => setParams((p) => ({ ...p, systemPrompt: e.target.value }))}
              className="w-full h-20 p-2.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            />
          </div>
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-gray-700">Temperature</label>
              <span className="text-xs text-gray-500 font-mono">{params.temperature.toFixed(1)}</span>
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
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-medium text-gray-700">Top P</label>
              <span className="text-xs text-gray-500 font-mono">{params.topP.toFixed(2)}</span>
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
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Max Tokens</label>
            <input
              type="number"
              value={params.maxTokens}
              onChange={(e) => setParams((p) => ({ ...p, maxTokens: parseInt(e.target.value) || 0 }))}
              className="w-full px-3 py-2 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">Freq. Penalty</label>
              <input
                type="number"
                value={params.freqPenalty}
                min={-2}
                max={2}
                step={0.1}
                onChange={(e) => setParams((p) => ({ ...p, freqPenalty: parseFloat(e.target.value) || 0 }))}
                className="w-full px-2.5 py-2 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">Pres. Penalty</label>
              <input
                type="number"
                value={params.presPenalty}
                min={-2}
                max={2}
                step={0.1}
                onChange={(e) => setParams((p) => ({ ...p, presPenalty: parseFloat(e.target.value) || 0 }))}
                className="w-full px-2.5 py-2 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <button
            onClick={() => setShowParams(false)}
            className="w-full py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg active:bg-blue-700"
            type="button"
          >
            Done
          </button>
        </div>
      </BottomSheet>

      {/* Inspect bottom sheet */}
      <BottomSheet
        open={showInspect}
        onClose={() => setShowInspect(false)}
        titleId="playground-inspect-title"
        firstFocusRef={inspectCloseRef as React.RefObject<HTMLElement>}
        returnFocusRef={inspectReturnRef}
      >
        <div className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between rounded-t-2xl">
          <h3 id="playground-inspect-title" className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <Activity className="w-4 h-4 text-blue-600" />
            Inspect
          </h3>
          <button
            ref={inspectCloseRef}
            onClick={() => setShowInspect(false)}
            className="p-1.5 -mr-1.5 text-gray-400 active:text-gray-600 rounded"
            aria-label="Close inspect"
            type="button"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex border-b border-gray-200 px-2 py-2 gap-1">
          <button
            onClick={() => setInspectTab('stats')}
            className={`flex-1 py-1.5 px-2 text-xs font-medium rounded-md flex items-center justify-center gap-1.5 ${
              inspectTab === 'stats' ? 'bg-gray-100 text-gray-900' : 'text-gray-500'
            }`}
            type="button"
          >
            <Activity className="w-3.5 h-3.5" />
            Stats
          </button>
          <button
            onClick={() => setInspectTab('request')}
            className={`flex-1 py-1.5 px-2 text-xs font-medium rounded-md flex items-center justify-center gap-1.5 ${
              inspectTab === 'request' ? 'bg-gray-100 text-gray-900' : 'text-gray-500'
            }`}
            type="button"
          >
            <Code className="w-3.5 h-3.5" />
            Request
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {inspectTab === 'stats' ? (
            <div className="space-y-5">
              <div>
                <h4 className="text-[10px] font-bold text-gray-900 uppercase tracking-wider mb-2">Last Request Usage</h4>
                {lastStats ? (
                  <div className="grid grid-cols-2 gap-2">
                    <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                      <div className="text-[10px] text-gray-500 mb-1">Prompt Tokens</div>
                      <div className="text-lg font-semibold text-gray-900">{lastStats.promptTokens}</div>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                      <div className="text-[10px] text-gray-500 mb-1">Completion Tokens</div>
                      <div className="text-lg font-semibold text-gray-900">{lastStats.completionTokens}</div>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                      <div className="text-[10px] text-gray-500 mb-1">Total Tokens</div>
                      <div className="text-base font-semibold text-gray-900">{lastStats.totalTokens}</div>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                      <div className="text-[10px] text-gray-500 mb-1">Latency</div>
                      <div className="text-base font-semibold text-gray-900">
                        {(lastStats.latencyMs / 1000).toFixed(2)}s
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="text-center py-6 text-gray-400 text-xs">Send a message to see usage</div>
                )}
              </div>
              <div>
                <h4 className="text-[10px] font-bold text-gray-900 uppercase tracking-wider mb-2">Session Info</h4>
                <div className="space-y-1.5">
                  <Row label="Model" value={selectedModel?.name || '—'} />
                  <Row label="Provider" value={selectedModel?.provider || '—'} />
                  <Row label="Messages" value={String(messages.length)} />
                  <Row label="Mode" value={modeShortLabel(mode)} />
                  <Row label="Endpoint" value={modeEndpoint(mode)} mono />
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <h4 className="text-[10px] font-bold text-gray-900 uppercase tracking-wider mb-2">Request Body</h4>
                <pre className="bg-gray-900 rounded-lg p-3 text-[10px] font-mono text-gray-300 overflow-auto max-h-64 whitespace-pre-wrap break-all">
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
                      ? JSON.stringify(
                          { model: selectedModel?.name, input: '...', voice: tts.voice, speed: tts.speed, response_format: tts.format },
                          null,
                          2,
                        )
                      : JSON.stringify(
                          { model: selectedModel?.name, file: '(binary)', language: stt.language || 'auto', response_format: stt.responseFormat },
                          null,
                          2,
                        )}
                </pre>
              </div>
              <div>
                <h4 className="text-[10px] font-bold text-gray-900 uppercase tracking-wider mb-2">cURL</h4>
                <pre className="bg-gray-900 rounded-lg p-3 text-[10px] font-mono text-gray-300 overflow-auto max-h-40 whitespace-pre-wrap break-all">
                  {mode === 'chat'
                    ? `curl ${window.location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ model: selectedModel?.name, messages: [{ role: 'user', content: '...' }], stream: true })}'`
                    : mode === 'tts'
                      ? `curl ${window.location.origin}/v1/audio/speech \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ model: selectedModel?.name, input: '...', voice: tts.voice })}' \\\n  --output speech.${tts.format}`
                      : `curl ${window.location.origin}/v1/audio/transcriptions \\\n  -H "Authorization: Bearer $API_KEY" \\\n  -F file=@audio.mp3 \\\n  -F model=${selectedModel?.name}`}
                </pre>
              </div>
            </div>
          )}
        </div>
      </BottomSheet>

      {/* Model picker sheet */}
      <BottomSheet
        open={showModelSheet}
        onClose={() => setShowModelSheet(false)}
        titleId="playground-model-title"
        firstFocusRef={modelCloseRef as React.RefObject<HTMLElement>}
        returnFocusRef={modelReturnRef}
        maxHeightClass="max-h-[70vh]"
      >
        <div className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between rounded-t-2xl">
          <h3 id="playground-model-title" className="text-sm font-semibold text-gray-900">
            Select {modeShortLabel(mode).toLowerCase()} model
          </h3>
          <button
            ref={modelCloseRef}
            onClick={() => setShowModelSheet(false)}
            className="p-1.5 -mr-1.5 text-gray-400 active:text-gray-600 rounded"
            aria-label="Close model picker"
            type="button"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {currentModels.length === 0 ? (
            <div className="px-6 py-8 text-center text-xs text-gray-500">{modeEmptyCopy(mode)}</div>
          ) : (
            currentModels.map((m) => {
              const isSelected = selectedModel?.id === m.id;
              return (
                <button
                  key={m.id}
                  onClick={() => {
                    setSelectedModel(m);
                    setShowModelSheet(false);
                  }}
                  className={`w-full flex items-center gap-3 px-4 py-3 active:bg-gray-50 text-left ${
                    isSelected ? 'bg-blue-50/40' : ''
                  }`}
                  type="button"
                >
                  <span
                    className={`flex-none w-2 h-2 rounded-full ${m.status === 'online' ? 'bg-green-500' : 'bg-yellow-500'}`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-gray-900 truncate">{m.name}</div>
                    <div className="text-[11px] text-gray-500 truncate">{m.provider}</div>
                  </div>
                  {isSelected && <Check className="w-4 h-4 text-blue-600 flex-none" />}
                </button>
              );
            })
          )}
        </div>
      </BottomSheet>
    </div>
  );
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between text-xs gap-3">
      <span className="text-gray-500 flex-none">{label}</span>
      <span className={`text-gray-900 font-medium truncate ${mono ? 'font-mono text-blue-600 text-[10px]' : ''}`}>
        {value}
      </span>
    </div>
  );
}
