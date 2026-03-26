import React, { useState, useRef, useEffect } from 'react';
import {
  ChevronRight, Sparkles, Settings2, SlidersHorizontal, Info, History,
  Activity, PlaySquare, StopCircle, CornerDownLeft, Copy, Check,
  Bot, User, Code, RotateCcw, AlertTriangle, Zap, TerminalSquare,
  PanelRightClose, PanelRightOpen, Search, X, ChevronDown,
  MessageSquare, Volume2, Mic, Upload, Download, Play, Pause,
  Eye, EyeOff, Key, FileAudio, Square
} from 'lucide-react';

type PlaygroundMode = 'chat' | 'tts' | 'stt';
type MessageRole = 'system' | 'user' | 'assistant';

interface Message {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
}

const MOCK_MODELS = [
  { id: 'gpt-4o', name: 'gpt-4o', provider: 'OpenAI', status: 'online' },
  { id: 'claude-3.5-sonnet', name: 'claude-3.5-sonnet', provider: 'Anthropic', status: 'online' },
  { id: 'gemini-1.5-pro', name: 'gemini-1.5-pro', provider: 'Google', status: 'degraded' },
  { id: 'mixtral-8x7b', name: 'mixtral-8x7b', provider: 'Mistral', status: 'online' },
  { id: 'llama-3-70b', name: 'llama-3-70b-instruct', provider: 'Groq', status: 'online' },
];

const TTS_MODELS = [
  { id: 'tts-1', name: 'tts-1', provider: 'OpenAI', status: 'online' },
  { id: 'tts-1-hd', name: 'tts-1-hd', provider: 'OpenAI', status: 'online' },
];

const STT_MODELS = [
  { id: 'whisper-1', name: 'whisper-1', provider: 'OpenAI', status: 'online' },
  { id: 'whisper-large-v3', name: 'whisper-large-v3', provider: 'Groq', status: 'online' },
];

const TTS_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'];

const MOCK_MESSAGES: Message[] = [
  {
    id: 'm1',
    role: 'user',
    content: 'Can you write a simple Python function to calculate the Fibonacci sequence?',
    timestamp: '10:24:00 AM'
  },
  {
    id: 'm2',
    role: 'assistant',
    content: 'Certainly! Here is a simple Python function to generate the Fibonacci sequence up to a specified number of terms using a generator:\n\n```python\ndef fibonacci(n):\n    """Generate the Fibonacci sequence up to n terms."""\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b\n\n# Example usage:\nprint(list(fibonacci(10)))\n# Output: [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]\n```\n\nThis approach is memory efficient because it yields one number at a time instead of computing and storing the entire list in memory at once.',
    timestamp: '10:24:03 AM'
  },
  {
    id: 'm3',
    role: 'user',
    content: 'Thanks! How would I modify it to just return the nth number instead?',
    timestamp: '10:24:15 AM'
  }
];

const MOCK_HISTORY = [
  { id: 'h1', title: 'Python Fibonacci generator', time: '10:24 AM', tokens: 142 },
  { id: 'h2', title: 'Explain quantum computing', time: 'Yesterday', tokens: 845 },
  { id: 'h3', title: 'Translate JSON to Go structs', time: 'Mar 12', tokens: 1024 },
];

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

function TTSView() {
  const [ttsText, setTtsText] = useState(
    'Welcome to DeltaLLM! This is a demonstration of the text-to-speech endpoint. You can use this playground to test different voices, speeds, and models before integrating them into your application.'
  );
  const [selectedVoice, setSelectedVoice] = useState('nova');
  const [speed, setSpeed] = useState(1.0);
  const [responseFormat, setResponseFormat] = useState('mp3');
  const [isGenerating, setIsGenerating] = useState(false);
  const [audioReady, setAudioReady] = useState(true);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    if (!isPlaying) return;
    const interval = setInterval(() => {
      setProgress(prev => {
        if (prev >= 100) { setIsPlaying(false); return 0; }
        return prev + 2;
      });
    }, 100);
    return () => clearInterval(interval);
  }, [isPlaying]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto p-6 space-y-6">
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1.5">Voice</label>
            <div className="grid grid-cols-3 gap-1.5">
              {TTS_VOICES.map((v) => (
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
            onChange={(e) => setTtsText(e.target.value)}
            className="w-full h-40 p-3 text-sm bg-white border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none leading-relaxed"
            placeholder="Enter the text you want to convert to speech..."
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setIsGenerating(true);
              setTimeout(() => { setIsGenerating(false); setAudioReady(true); }, 2000);
            }}
            disabled={!ttsText.trim() || isGenerating}
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
          {isGenerating && (
            <span className="text-xs text-gray-500">Processing with voice "{selectedVoice}" at {speed}x speed...</span>
          )}
        </div>

        {audioReady && !isGenerating && (
          <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                  <FileAudio className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-900">Generated Audio</div>
                  <div className="text-xs text-gray-500">
                    Voice: {selectedVoice} · Format: {responseFormat.toUpperCase()} · Duration: ~5.2s
                  </div>
                </div>
              </div>
              <button className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-md transition-colors">
                <Download className="w-3.5 h-3.5" />
                Download
              </button>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={() => setIsPlaying(!isPlaying)}
                className="w-10 h-10 bg-blue-600 text-white rounded-full flex items-center justify-center hover:bg-blue-700 shadow-sm transition-colors flex-none"
              >
                {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </button>
              <div className="flex-1">
                <div className="w-full bg-gray-100 rounded-full h-2 cursor-pointer">
                  <div
                    className="bg-blue-600 h-2 rounded-full transition-all duration-100"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <div className="flex justify-between mt-1 text-[10px] text-gray-400 font-mono">
                  <span>{Math.floor(progress * 5.2 / 100)}.{Math.floor((progress * 5.2 / 100 % 1) * 10)}s</span>
                  <span>5.2s</span>
                </div>
              </div>
            </div>

            <div className="mt-4 flex items-center gap-4">
              <div className="flex-1 flex items-center gap-6">
                {[
                  { label: 'Characters', value: ttsText.length.toString() },
                  { label: 'Duration', value: '5.2s' },
                  { label: 'File Size', value: '84 KB' },
                  { label: 'Latency', value: '1.3s' },
                  { label: 'Cost', value: '$0.0045' },
                ].map((stat) => (
                  <div key={stat.label} className="text-center">
                    <div className="text-xs text-gray-400">{stat.label}</div>
                    <div className="text-sm font-semibold text-gray-900">{stat.value}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function STTView() {
  const [dragOver, setDragOver] = useState(false);
  const [hasFile, setHasFile] = useState(true);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcriptionDone, setTranscriptionDone] = useState(true);
  const [language, setLanguage] = useState('auto');
  const [responseFormat, setResponseFormat] = useState('json');
  const [outputPrompt, setOutputPrompt] = useState('');
  const [isRecording, setIsRecording] = useState(false);

  const mockTranscription = `Good morning everyone. Thank you for joining today's standup. Let's go through each team's updates quickly.

The backend team finished the authentication refactor yesterday. All unit tests are passing and we've deployed it to staging. We're now moving on to the rate limiting middleware which should take about two days.

On the frontend side, the dashboard redesign is about 80% complete. We still need to integrate the new analytics charts and the settings panel. Sarah, can you pair with David on the chart component today?

For infrastructure, we've upgraded the Kubernetes cluster to version 1.29 and migrated the databases to the new instance types. Latency is down about 15% across the board which is great news.

Any blockers? No? Perfect. Let's reconvene tomorrow at the same time.`;

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
              <option value="auto">Auto-detect</option>
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
            onDrop={(e) => { e.preventDefault(); setDragOver(false); setHasFile(true); }}
            className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors cursor-pointer ${
              dragOver ? 'border-blue-400 bg-blue-50' : hasFile ? 'border-green-300 bg-green-50/50' : 'border-gray-300 bg-gray-50 hover:border-gray-400'
            }`}
          >
            {hasFile ? (
              <div className="space-y-2">
                <div className="w-12 h-12 bg-green-100 rounded-xl flex items-center justify-center mx-auto">
                  <FileAudio className="w-6 h-6 text-green-600" />
                </div>
                <div className="text-sm font-medium text-gray-900">team-standup-recording.mp3</div>
                <div className="text-xs text-gray-500">3.2 MB · 2:34 duration · MP3</div>
                <button
                  onClick={(e) => { e.stopPropagation(); setHasFile(false); setTranscriptionDone(false); }}
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
              onClick={() => setIsRecording(!isRecording)}
              className={`w-16 h-16 rounded-full flex items-center justify-center transition-all shadow-sm mb-3 ${
                isRecording
                  ? 'bg-red-500 text-white animate-pulse shadow-red-200 shadow-lg'
                  : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
              }`}
            >
              {isRecording ? <Square className="w-6 h-6" /> : <Mic className="w-7 h-7" />}
            </button>
            <div className="text-sm font-medium text-gray-900">
              {isRecording ? 'Recording... 0:12' : 'Record Audio'}
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

        {hasFile && (
          <button
            onClick={() => {
              setIsTranscribing(true);
              setTimeout(() => { setIsTranscribing(false); setTranscriptionDone(true); }, 2500);
            }}
            disabled={isTranscribing}
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
        )}

        {transcriptionDone && (
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 bg-gray-50">
              <div className="flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-gray-500" />
                <span className="text-sm font-medium text-gray-900">Transcription Result</span>
                <span className="px-1.5 py-0.5 bg-green-100 text-green-700 text-[10px] font-medium rounded">
                  {language === 'auto' ? 'Detected: English' : language.toUpperCase()}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button className="flex items-center gap-1 px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded">
                  <Copy className="w-3 h-3" />
                  Copy
                </button>
                <button className="flex items-center gap-1 px-2 py-1 text-xs text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded">
                  <Download className="w-3 h-3" />
                  Export
                </button>
              </div>
            </div>

            <div className="p-4 text-sm text-gray-800 leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto">
              {mockTranscription}
            </div>

            <div className="flex items-center gap-6 px-4 py-3 border-t border-gray-100 bg-gray-50">
              {[
                { label: 'Words', value: '156' },
                { label: 'Duration', value: '2:34' },
                { label: 'Language', value: 'English' },
                { label: 'Latency', value: '3.8s' },
                { label: 'Cost', value: '$0.0154' },
              ].map((stat) => (
                <div key={stat.label}>
                  <span className="text-xs text-gray-400">{stat.label}: </span>
                  <span className="text-xs font-semibold text-gray-700">{stat.value}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Playground() {
  const [mode, setMode] = useState<PlaygroundMode>('chat');
  const [messages, setMessages] = useState<Message[]>(MOCK_MESSAGES);
  const [input, setInput] = useState('');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [selectedModel, setSelectedModel] = useState(MOCK_MODELS[0]);
  const [apiKey, setApiKey] = useState('sk-deltallm-prod-8f2a9c1e3d4b');
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeTab, setActiveTab] = useState<'stats' | 'request' | 'history'>('stats');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const [systemPrompt, setSystemPrompt] = useState('You are a helpful AI assistant. Always format code blocks with the correct language tag.');
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(2048);
  const [topP, setTopP] = useState(1);
  const [showConfig, setShowConfig] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const currentModels = mode === 'chat' ? MOCK_MODELS : mode === 'tts' ? TTS_MODELS : STT_MODELS;

  useEffect(() => {
    setSelectedModel(currentModels[0]);
  }, [mode]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isStreaming]);

  const handleSend = () => {
    if (!input.trim()) return;

    const newUserMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    };

    setMessages([...messages, newUserMsg]);
    setInput('');
    setIsStreaming(true);

    setTimeout(() => {
      setIsStreaming(false);
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: 'To return just the nth Fibonacci number, you can use an iterative approach or recursion. Here is the iterative approach which is faster and avoids stack overflow limits:\n\n```python\ndef get_nth_fibonacci(n):\n    if n <= 0:\n        return 0\n    elif n == 1:\n        return 1\n        \n    a, b = 0, 1\n    for _ in range(2, n + 1):\n        a, b = b, a + b\n    return b\n\n# Get the 10th number (0-indexed)\nprint(get_nth_fibonacci(10)) # Output: 55\n```',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      }]);
    }, 2000);
  };

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

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans text-slate-900 overflow-hidden">
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
              onClick={() => { setMessages([]); setInput(''); }}
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
                      value={selectedModel.id}
                      onChange={(e) => setSelectedModel(currentModels.find(m => m.id === e.target.value) || selectedModel)}
                    >
                      {currentModels.map(m => (
                        <option key={m.id} value={m.id}>{m.provider} / {m.name}</option>
                      ))}
                    </select>
                    <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-gray-500">
                      <ChevronDown className="h-4 w-4" />
                    </div>
                    <div
                      className={`absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border border-white ${
                        selectedModel.status === 'online' ? 'bg-green-500' : 'bg-yellow-500'
                      }`}
                      title={`Status: ${selectedModel.status}`}
                    />
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
                        onChange={(e) => setMaxTokens(parseInt(e.target.value))}
                        className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-medium text-gray-700 mb-1 block">Freq. Penalty</label>
                        <input type="number" defaultValue="0" min="-2" max="2" step="0.1" className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500" />
                      </div>
                      <div>
                        <label className="text-xs font-medium text-gray-700 mb-1 block">Pres. Penalty</label>
                        <input type="number" defaultValue="0" min="-2" max="2" step="0.1" className="w-full px-2.5 py-1.5 text-sm bg-white border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500" />
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {mode === 'chat' && (
            <>
              <div className="flex-1 overflow-y-auto bg-gray-50/50 p-4 sm:p-6 scroll-smooth">
                <div className="max-w-4xl mx-auto space-y-6 pb-4">
                  {messages.length === 0 ? (
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
                              {msg.role === 'user' ? 'You' : selectedModel.provider + ' / ' + selectedModel.name}
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

                  {isStreaming && (
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
                  <div className="flex items-end gap-2 bg-white border border-gray-300 rounded-xl shadow-sm focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500 p-1.5 transition-all">
                    <textarea
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={handleKeyDown}
                      placeholder="Message the model... (⌘ Enter to send)"
                      className="flex-1 max-h-48 min-h-[44px] bg-transparent border-0 resize-none py-2.5 px-3 text-sm focus:ring-0 text-gray-900 placeholder:text-gray-400"
                      rows={Math.min(5, Math.max(1, input.split('\n').length))}
                    />
                    <div className="flex flex-col justify-end pb-1 pr-1">
                      {isStreaming ? (
                        <button
                          onClick={() => setIsStreaming(false)}
                          className="p-2 text-white bg-red-500 hover:bg-red-600 rounded-lg shadow-sm transition-colors flex items-center justify-center group"
                          title="Stop generating"
                        >
                          <StopCircle className="w-5 h-5 group-hover:scale-110 transition-transform" />
                        </button>
                      ) : (
                        <button
                          onClick={handleSend}
                          disabled={!input.trim()}
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
                    <div className="flex items-center gap-1.5 text-[11px] text-gray-500">
                      <Activity className="w-3 h-3 text-green-500" />
                      <span>42/100 RPM</span>
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}

          {mode === 'tts' && <TTSView />}
          {mode === 'stt' && <STTView />}
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
              <button
                onClick={() => setActiveTab('history')}
                className={`flex-1 py-1.5 px-2 text-xs font-medium rounded-md flex items-center justify-center gap-1.5 ${
                  activeTab === 'history' ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                }`}
              >
                <History className="w-3.5 h-3.5" />
                History
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              {activeTab === 'stats' && (
                <div className="space-y-6">
                  <div>
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">Last Request Usage</h3>
                    <div className="grid grid-cols-2 gap-3 mb-3">
                      <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                        <div className="text-[10px] text-gray-500 mb-1">
                          {mode === 'chat' ? 'Prompt Tokens' : mode === 'tts' ? 'Characters' : 'Audio Duration'}
                        </div>
                        <div className="text-xl font-semibold text-gray-900">
                          {mode === 'chat' ? '42' : mode === 'tts' ? '245' : '2:34'}
                        </div>
                      </div>
                      <div className="bg-gray-50 rounded-lg p-3 border border-gray-100">
                        <div className="text-[10px] text-gray-500 mb-1">
                          {mode === 'chat' ? 'Completion Tokens' : mode === 'tts' ? 'Audio Duration' : 'Words'}
                        </div>
                        <div className="text-xl font-semibold text-gray-900">
                          {mode === 'chat' ? '184' : mode === 'tts' ? '5.2s' : '156'}
                        </div>
                      </div>
                      <div className="bg-blue-50/50 rounded-lg p-3 border border-blue-100 col-span-2">
                        <div className="flex items-center justify-between">
                          <div className="text-[10px] font-medium text-blue-600 uppercase tracking-wide">
                            {mode === 'chat' ? 'Total Tokens' : 'Estimated Cost'}
                          </div>
                          <div className="text-xs text-blue-500 font-mono">
                            {mode === 'chat' ? '~ $0.0042' : mode === 'tts' ? '~ $0.0045' : '~ $0.0154'}
                          </div>
                        </div>
                        <div className="text-2xl font-bold text-blue-900 mt-0.5">
                          {mode === 'chat' ? '226' : mode === 'tts' ? '$0.0045' : '$0.0154'}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="border-t border-gray-100 pt-5">
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">Latency</h3>
                    <div className="space-y-3">
                      <div>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-gray-500">
                            {mode === 'chat' ? 'Time to First Token' : 'Processing Time'}
                          </span>
                          <span className="font-mono text-gray-900">
                            {mode === 'chat' ? '420ms' : mode === 'tts' ? '1.3s' : '3.8s'}
                          </span>
                        </div>
                        <div className="w-full bg-gray-100 rounded-full h-1.5">
                          <div className="bg-green-500 h-1.5 rounded-full" style={{ width: mode === 'chat' ? '20%' : mode === 'tts' ? '40%' : '70%' }}></div>
                        </div>
                      </div>
                      <div>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-gray-500">Total Response Time</span>
                          <span className="font-mono text-gray-900">
                            {mode === 'chat' ? '2.1s' : mode === 'tts' ? '1.8s' : '4.2s'}
                          </span>
                        </div>
                        <div className="w-full bg-gray-100 rounded-full h-1.5">
                          <div className="bg-blue-500 h-1.5 rounded-full" style={{ width: '80%' }}></div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="border-t border-gray-100 pt-5">
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-3">Gateway Routing</h3>
                    <div className="bg-gray-50 rounded-lg p-3 border border-gray-100 space-y-2">
                      <div className="flex justify-between items-center text-xs">
                        <span className="text-gray-500">Route Group</span>
                        <span className="font-medium text-gray-900">Default Fallback</span>
                      </div>
                      <div className="flex justify-between items-center text-xs">
                        <span className="text-gray-500">Resolved Provider</span>
                        <span className="flex items-center gap-1 font-medium text-gray-900">
                          <div className="w-2 h-2 rounded-full bg-green-500"></div>
                          {selectedModel.provider}
                        </span>
                      </div>
                      <div className="flex justify-between items-center text-xs">
                        <span className="text-gray-500">Endpoint</span>
                        <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-200 text-gray-600">
                          {mode === 'chat' ? '/v1/chat/completions' : mode === 'tts' ? '/v1/audio/speech' : '/v1/audio/transcriptions'}
                        </span>
                      </div>
                      <div className="flex justify-between items-center text-xs">
                        <span className="text-gray-500">Cache</span>
                        <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-200 text-gray-600">MISS</span>
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'request' && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider">Payloads</h3>
                  </div>
                  <div className="space-y-3">
                    <div className="border border-gray-200 rounded-lg overflow-hidden">
                      <div className="bg-gray-50 px-3 py-2 border-b border-gray-200 flex justify-between items-center">
                        <span className="text-xs font-medium text-gray-700">Request JSON</span>
                        <button className="text-gray-400 hover:text-gray-600"><Copy className="w-3.5 h-3.5" /></button>
                      </div>
                      <pre className="p-3 text-[11px] font-mono text-gray-600 overflow-x-auto bg-white max-h-60 overflow-y-auto">
{mode === 'chat' ? `{
  "model": "${selectedModel.id}",
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful..."
    },
    {
      "role": "user",
      "content": "Can you write..."
    }
  ],
  "temperature": ${temperature},
  "max_tokens": ${maxTokens},
  "top_p": ${topP}
}` : mode === 'tts' ? `{
  "model": "${selectedModel.id}",
  "input": "Welcome to DeltaLLM...",
  "voice": "nova",
  "speed": 1.0,
  "response_format": "mp3"
}` : `{
  "model": "${selectedModel.id}",
  "file": "<audio_binary>",
  "language": "auto",
  "response_format": "json"
}`}
                      </pre>
                    </div>

                    <div className="border border-gray-200 rounded-lg overflow-hidden">
                      <div className="bg-gray-50 px-3 py-2 border-b border-gray-200 flex justify-between items-center">
                        <span className="text-xs font-medium text-gray-700">Response JSON</span>
                        <button className="text-gray-400 hover:text-gray-600"><Copy className="w-3.5 h-3.5" /></button>
                      </div>
                      <pre className="p-3 text-[11px] font-mono text-gray-600 overflow-x-auto bg-white max-h-60 overflow-y-auto">
{mode === 'chat' ? `{
  "id": "chatcmpl-8xyz...",
  "object": "chat.completion",
  "model": "${selectedModel.id}",
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Certainly!..."
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 42,
    "completion_tokens": 184,
    "total_tokens": 226
  }
}` : mode === 'tts' ? `// Binary audio response
Content-Type: audio/mpeg
Content-Length: 86016
X-Request-Id: req_tts_abc123
X-Processing-Time: 1.3s` : `{
  "text": "Good morning everyone...",
  "task": "transcribe",
  "language": "english",
  "duration": 154.2,
  "words": 156
}`}
                      </pre>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'history' && (
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider">Recent Sessions</h3>
                    <button className="text-xs text-blue-600 hover:text-blue-800">Clear all</button>
                  </div>
                  <div className="space-y-2">
                    {MOCK_HISTORY.map((item) => (
                      <button
                        key={item.id}
                        className="w-full text-left p-3 rounded-lg border border-transparent hover:bg-gray-50 hover:border-gray-200 transition-colors group"
                      >
                        <div className="flex justify-between items-start mb-1">
                          <span className="text-sm font-medium text-gray-900 truncate pr-2 group-hover:text-blue-600">{item.title}</span>
                        </div>
                        <div className="flex justify-between items-center text-[11px] text-gray-500">
                          <span>{item.time}</span>
                          <span className="flex items-center gap-1"><Activity className="w-3 h-3" />{item.tokens} tkns</span>
                        </div>
                      </button>
                    ))}
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
