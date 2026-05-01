import { useCallback, useEffect, useRef, useState } from 'react';
import type { ModelOption } from './types';

export interface STTStats {
  words: number;
  latencyMs: number;
  detectedLang?: string;
}

export interface STTEngine {
  selectedFile: File | null;
  setSelectedFile: (f: File | null) => void;
  language: string;
  setLanguage: (v: string) => void;
  responseFormat: string;
  setResponseFormat: (v: string) => void;
  outputPrompt: string;
  setOutputPrompt: (v: string) => void;
  isTranscribing: boolean;
  transcription: string | null;
  isRecording: boolean;
  recordingTime: number;
  error: string | null;
  stats: STTStats | null;
  copiedTranscription: boolean;
  handleFile: (f: File) => void;
  handleTranscribe: () => Promise<void>;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  copyTranscription: () => void;
  resetOutput: () => void;
}

export function useSTTEngine(opts: { apiKey: string; selectedModel: ModelOption | null }): STTEngine {
  const { apiKey, selectedModel } = opts;
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [language, setLanguage] = useState('');
  const [responseFormat, setResponseFormat] = useState('json');
  const [outputPrompt, setOutputPrompt] = useState('');
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcription, setTranscription] = useState<string | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<STTStats | null>(null);
  const [copiedTranscription, setCopiedTranscription] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isStartingRecordingRef = useRef(false);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        try {
          mediaRecorderRef.current.stop();
        } catch {
          /* ignore */
        }
      }
    };
  }, []);

  const handleFile = useCallback((file: File) => {
    const maxSize = 25 * 1024 * 1024;
    if (file.size > maxSize) {
      setError('File size exceeds 25 MB limit');
      return;
    }
    setSelectedFile(file);
    setTranscription(null);
    setError(null);
    setStats(null);
  }, []);

  const startRecording = useCallback(async () => {
    if (isStartingRecordingRef.current) return;
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      return;
    }
    isStartingRecordingRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : '';
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const actualMime = recorder.mimeType || 'audio/webm';
        const ext = actualMime.includes('webm') ? 'webm' : actualMime.includes('mp4') ? 'mp4' : 'webm';
        const blob = new Blob(chunksRef.current, { type: actualMime });
        if (blob.size < 1000) {
          setError('Recording too short — please record at least 1 second of audio');
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        const file = new File([blob], `recording.${ext}`, { type: actualMime });
        handleFile(file);
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorderRef.current = recorder;
      recorder.start(1000);
      setIsRecording(true);
      setRecordingTime(0);
      timerRef.current = setInterval(() => setRecordingTime((t) => t + 1), 1000);
    } catch {
      setError('Microphone access denied');
    } finally {
      isStartingRecordingRef.current = false;
    }
  }, [handleFile]);

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop();
    setIsRecording(false);
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const handleTranscribe = useCallback(async () => {
    if (!selectedFile || !apiKey || !selectedModel) return;
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
      let textOut: string;
      if (ct.includes('application/json')) {
        const json = await res.json();
        textOut = json.text ?? JSON.stringify(json, null, 2);
        setStats({
          words: (json.text || '').split(/\s+/).filter(Boolean).length,
          latencyMs,
          detectedLang: json.language,
        });
      } else {
        textOut = await res.text();
        setStats({ words: textOut.split(/\s+/).filter(Boolean).length, latencyMs });
      }
      setTranscription(textOut);
    } catch (err: any) {
      setError(err.message || 'Transcription request failed');
    } finally {
      setIsTranscribing(false);
    }
  }, [selectedFile, apiKey, selectedModel, language, responseFormat, outputPrompt]);

  const copyTranscription = useCallback(() => {
    if (!transcription) return;
    void navigator.clipboard.writeText(transcription);
    setCopiedTranscription(true);
    if (copiedTimer.current) clearTimeout(copiedTimer.current);
    copiedTimer.current = setTimeout(() => setCopiedTranscription(false), 2000);
  }, [transcription]);

  const resetOutput = useCallback(() => {
    setSelectedFile(null);
    setTranscription(null);
    setStats(null);
    setError(null);
  }, []);

  return {
    selectedFile,
    setSelectedFile,
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
    resetOutput,
  };
}
