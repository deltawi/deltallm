import { useCallback, useEffect, useRef, useState } from 'react';
import type { Message, ModelOption, RequestStats } from './types';

export interface ChatParams {
  systemPrompt: string;
  temperature: number;
  maxTokens: number;
  topP: number;
  freqPenalty: number;
  presPenalty: number;
}

export interface ChatEngine {
  messages: Message[];
  input: string;
  setInput: (v: string) => void;
  isStreaming: boolean;
  lastStats: RequestStats | null;
  error: string | null;
  copiedId: string | null;
  params: ChatParams;
  setParams: React.Dispatch<React.SetStateAction<ChatParams>>;
  handleSend: () => Promise<void>;
  handleStop: () => void;
  handleReset: () => void;
  copyToClipboard: (text: string, id: string) => void;
  formatTimestamp: () => string;
}

const DEFAULT_PARAMS: ChatParams = {
  systemPrompt: 'You are a helpful AI assistant.',
  temperature: 0.7,
  maxTokens: 2048,
  topP: 1,
  freqPenalty: 0,
  presPenalty: 0,
};

function nowTimestamp() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function useChatEngine(opts: { apiKey: string; selectedModel: ModelOption | null }): ChatEngine {
  const { apiKey, selectedModel } = opts;
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [lastStats, setLastStats] = useState<RequestStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [params, setParams] = useState<ChatParams>(DEFAULT_PARAMS);
  const abortRef = useRef<AbortController | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (copyTimer.current) clearTimeout(copyTimer.current);
    };
  }, []);

  const handleSend = useCallback(async () => {
    if (!input.trim() || !apiKey || !selectedModel) return;

    const newUserMsg: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      timestamp: nowTimestamp(),
    };

    setMessages((prev) => [...prev, newUserMsg]);
    setInput('');
    setIsStreaming(true);
    setError(null);
    setLastStats(null);

    const startTime = performance.now();
    const controller = new AbortController();
    abortRef.current = controller;

    const chatMessages: { role: string; content: string }[] = [];
    if (params.systemPrompt.trim()) {
      chatMessages.push({ role: 'system', content: params.systemPrompt });
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
          temperature: params.temperature,
          max_tokens: params.maxTokens,
          top_p: params.topP,
          frequency_penalty: params.freqPenalty,
          presence_penalty: params.presPenalty,
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
              setMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last?.role === 'assistant' && last.id === 'streaming') {
                  return [...prev.slice(0, -1), { ...last, content: assistantContent }];
                }
                return [
                  ...prev,
                  {
                    id: 'streaming',
                    role: 'assistant',
                    content: assistantContent,
                    timestamp: nowTimestamp(),
                  },
                ];
              });
            }
            if (parsed.usage) usage = parsed.usage;
          } catch {
            /* ignore partial JSON */
          }
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

      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.id === 'streaming') {
          return [...prev.slice(0, -1), { ...last, id: Date.now().toString() }];
        }
        if (!assistantContent) {
          return [
            ...prev,
            {
              id: Date.now().toString(),
              role: 'assistant',
              content: assistantContent || '(empty response)',
              timestamp: nowTimestamp(),
            },
          ];
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
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.id === 'streaming') return prev.slice(0, -1);
          return prev;
        });
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [apiKey, selectedModel, input, messages, params]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.id === 'streaming') {
        if (!last.content.trim()) return prev.slice(0, -1);
        return [...prev.slice(0, -1), { ...last, id: Date.now().toString() }];
      }
      return prev;
    });
  }, []);

  const handleReset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setMessages([]);
    setInput('');
    setError(null);
    setLastStats(null);
  }, []);

  const copyToClipboard = useCallback((text: string, id: string) => {
    void navigator.clipboard.writeText(text);
    setCopiedId(id);
    if (copyTimer.current) clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 2000);
  }, []);

  return {
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
    formatTimestamp: nowTimestamp,
  };
}
