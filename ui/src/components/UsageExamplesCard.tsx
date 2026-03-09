import { useState } from 'react';
import Card from './Card';

export type ExampleLanguage = 'curl' | 'python' | 'javascript';
export type UsageMode = 'chat' | 'embedding' | 'image_generation' | 'audio_speech' | 'audio_transcription' | 'rerank';

interface UsageExamplesCardProps {
  title: string;
  description: string;
  modelName: string;
  mode: string;
  warning?: React.ReactNode;
  context?: React.ReactNode;
  requestMetadata?: Record<string, unknown>;
}

function normalizeMode(mode: string): UsageMode {
  if (mode === 'embedding' || mode === 'image_generation' || mode === 'audio_speech' || mode === 'audio_transcription' || mode === 'rerank') {
    return mode;
  }
  return 'chat';
}

function gatewayBaseUrl(): string {
  return `${typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000'}/v1`;
}

function buildJsonBody(
  modelName: string,
  mode: UsageMode,
  requestMetadata?: Record<string, unknown>,
): Record<string, unknown> {
  const metadata = requestMetadata && Object.keys(requestMetadata).length > 0 ? { metadata: requestMetadata } : {};

  if (mode === 'embedding') {
    return {
      model: modelName,
      input: 'The quick brown fox jumps over the lazy dog.',
      ...metadata,
    };
  }

  if (mode === 'image_generation') {
    return {
      model: modelName,
      prompt: 'A minimal product illustration of a blue robot',
      size: '1024x1024',
      ...metadata,
    };
  }

  if (mode === 'audio_speech') {
    return {
      model: modelName,
      input: 'Hello from DeltaLLM.',
      voice: 'alloy',
      response_format: 'mp3',
      ...metadata,
    };
  }

  if (mode === 'rerank') {
    return {
      model: modelName,
      query: 'Which document explains machine learning?',
      documents: [
        'Machine learning is a branch of AI focused on pattern recognition.',
        'The weather is sunny and warm.',
        'This recipe explains how to make bread.',
      ],
      top_n: 2,
      ...metadata,
    };
  }

  return {
    model: modelName,
    messages: [{ role: 'user', content: 'Say hello in one sentence.' }],
    ...metadata,
  };
}

function buildCurlSnippet(baseUrl: string, modelName: string, mode: UsageMode, requestMetadata?: Record<string, unknown>): string {
  if (mode === 'audio_transcription') {
    return [
      `curl -sS ${baseUrl}/audio/transcriptions \\`,
      '  -H "Authorization: Bearer YOUR_API_KEY" \\',
      '  -F "file=@sample.wav" \\',
      `  -F "model=${modelName}" \\`,
      '  -F "response_format=json"',
    ].join('\n');
  }

  const endpoint =
    mode === 'embedding'
      ? '/embeddings'
      : mode === 'image_generation'
        ? '/images/generations'
        : mode === 'audio_speech'
          ? '/audio/speech'
          : mode === 'rerank'
            ? '/rerank'
            : '/chat/completions';
  const body = buildJsonBody(modelName, mode, requestMetadata);
  const lines = [
    `curl -sS ${baseUrl}${endpoint} \\`,
    '  -H "Authorization: Bearer YOUR_API_KEY" \\',
    '  -H "Content-Type: application/json" \\',
    `  -d '${JSON.stringify(body, null, 2)}'`,
  ];

  if (mode === 'audio_speech') {
    lines[lines.length - 1] = `${lines[lines.length - 1]} \\`;
    lines.push('  --output speech.mp3');
  }

  return lines.join('\n');
}

function buildPythonSnippet(baseUrl: string, modelName: string, mode: UsageMode, requestMetadata?: Record<string, unknown>): string {
  if (mode === 'audio_transcription') {
    return [
      'from openai import OpenAI',
      '',
      'client = OpenAI(',
      `    base_url="${baseUrl}",`,
      '    api_key="YOUR_API_KEY",',
      ')',
      '',
      'with open("sample.wav", "rb") as audio_file:',
      '    transcript = client.audio.transcriptions.create(',
      `        model="${modelName}",`,
      '        file=audio_file,',
      '        response_format="json",',
      '    )',
      '',
      'print(transcript.text)',
    ].join('\n');
  }

  if (mode === 'rerank') {
    const body = buildJsonBody(modelName, mode, requestMetadata);
    return [
      'import requests',
      '',
      `response = requests.post("${baseUrl}/rerank",`,
      '    headers={',
      '        "Authorization": "Bearer YOUR_API_KEY",',
      '        "Content-Type": "application/json",',
      '    },',
      `    json=${JSON.stringify(body, null, 4)},`,
      ')',
      '',
      'print(response.json())',
    ].join('\n');
  }

  const body = buildJsonBody(modelName, mode, requestMetadata);

  if (mode === 'embedding') {
    return [
      'from openai import OpenAI',
      '',
      'client = OpenAI(',
      `    base_url="${baseUrl}",`,
      '    api_key="YOUR_API_KEY",',
      ')',
      '',
      'response = client.embeddings.create(',
      `    model="${modelName}",`,
      `    input=${JSON.stringify(body.input)},`,
      ')',
      '',
      'print(response.data[0].embedding[:5])',
    ].join('\n');
  }

  if (mode === 'image_generation') {
    return [
      'from openai import OpenAI',
      '',
      'client = OpenAI(',
      `    base_url="${baseUrl}",`,
      '    api_key="YOUR_API_KEY",',
      ')',
      '',
      'response = client.images.generate(',
      `    model="${modelName}",`,
      `    prompt=${JSON.stringify(body.prompt)},`,
      `    size=${JSON.stringify(body.size)},`,
      ')',
      '',
      'print(response.data[0].url)',
    ].join('\n');
  }

  if (mode === 'audio_speech') {
    return [
      'from openai import OpenAI',
      '',
      'client = OpenAI(',
      `    base_url="${baseUrl}",`,
      '    api_key="YOUR_API_KEY",',
      ')',
      '',
      'speech = client.audio.speech.create(',
      `    model="${modelName}",`,
      `    voice=${JSON.stringify(body.voice)},`,
      `    input=${JSON.stringify(body.input)},`,
      `    response_format=${JSON.stringify(body.response_format)},`,
      ')',
      '',
      'speech.stream_to_file("speech.mp3")',
    ].join('\n');
  }

  const metadataLine = requestMetadata && Object.keys(requestMetadata).length > 0
    ? `    metadata=${JSON.stringify({ prompt_variables: requestMetadata.prompt_variables ?? requestMetadata }, null, 4).replace(/\n/g, '\n    ')},`
    : null;

  return [
    'from openai import OpenAI',
    '',
    'client = OpenAI(',
    `    base_url="${baseUrl}",`,
    '    api_key="YOUR_API_KEY",',
    ')',
    '',
    'response = client.chat.completions.create(',
    `    model="${modelName}",`,
    '    messages=[{"role": "user", "content": "Say hello in one sentence."}],',
    ...(metadataLine ? [metadataLine] : []),
    ')',
    '',
    'print(response.choices[0].message.content)',
  ].join('\n');
}

function buildJavaScriptSnippet(baseUrl: string, modelName: string, mode: UsageMode, requestMetadata?: Record<string, unknown>): string {
  if (mode === 'audio_transcription') {
    return [
      'import fs from "fs";',
      'import OpenAI from "openai";',
      '',
      'const client = new OpenAI({',
      `  baseURL: "${baseUrl}",`,
      '  apiKey: "YOUR_API_KEY",',
      '});',
      '',
      'const transcript = await client.audio.transcriptions.create({',
      `  model: "${modelName}",`,
      '  file: fs.createReadStream("sample.wav"),',
      '  response_format: "json",',
      '});',
      '',
      'console.log(transcript.text);',
    ].join('\n');
  }

  if (mode === 'rerank') {
    const body = buildJsonBody(modelName, mode, requestMetadata);
    return [
      `const response = await fetch("${baseUrl}/rerank", {`,
      '  method: "POST",',
      '  headers: {',
      '    "Authorization": "Bearer YOUR_API_KEY",',
      '    "Content-Type": "application/json",',
      '  },',
      `  body: JSON.stringify(${JSON.stringify(body, null, 2)}),`,
      '});',
      '',
      'console.log(await response.json());',
    ].join('\n');
  }

  const body = buildJsonBody(modelName, mode, requestMetadata);

  if (mode === 'embedding') {
    return [
      'import OpenAI from "openai";',
      '',
      'const client = new OpenAI({',
      `  baseURL: "${baseUrl}",`,
      '  apiKey: "YOUR_API_KEY",',
      '});',
      '',
      'const response = await client.embeddings.create({',
      `  model: "${modelName}",`,
      `  input: ${JSON.stringify(body.input)},`,
      '});',
      '',
      'console.log(response.data[0].embedding.slice(0, 5));',
    ].join('\n');
  }

  if (mode === 'image_generation') {
    return [
      'import OpenAI from "openai";',
      '',
      'const client = new OpenAI({',
      `  baseURL: "${baseUrl}",`,
      '  apiKey: "YOUR_API_KEY",',
      '});',
      '',
      'const response = await client.images.generate({',
      `  model: "${modelName}",`,
      `  prompt: ${JSON.stringify(body.prompt)},`,
      `  size: ${JSON.stringify(body.size)},`,
      '});',
      '',
      'console.log(response.data[0].url);',
    ].join('\n');
  }

  if (mode === 'audio_speech') {
    return [
      'import fs from "fs";',
      'import OpenAI from "openai";',
      '',
      'const client = new OpenAI({',
      `  baseURL: "${baseUrl}",`,
      '  apiKey: "YOUR_API_KEY",',
      '});',
      '',
      'const speech = await client.audio.speech.create({',
      `  model: "${modelName}",`,
      `  voice: ${JSON.stringify(body.voice)},`,
      `  input: ${JSON.stringify(body.input)},`,
      `  response_format: ${JSON.stringify(body.response_format)},`,
      '});',
      '',
      'const buffer = Buffer.from(await speech.arrayBuffer());',
      'await fs.promises.writeFile("speech.mp3", buffer);',
    ].join('\n');
  }

  const metadataLine = requestMetadata && Object.keys(requestMetadata).length > 0
    ? `  metadata: ${JSON.stringify({ prompt_variables: requestMetadata.prompt_variables ?? requestMetadata }, null, 2).replace(/\n/g, '\n  ')},`
    : null;

  return [
    'import OpenAI from "openai";',
    '',
    'const client = new OpenAI({',
    `  baseURL: "${baseUrl}",`,
    '  apiKey: "YOUR_API_KEY",',
    '});',
    '',
    'const response = await client.chat.completions.create({',
    `  model: "${modelName}",`,
    '  messages: [{ role: "user", content: "Say hello in one sentence." }],',
    ...(metadataLine ? [metadataLine] : []),
    '});',
    '',
    'console.log(response.choices[0].message.content);',
  ].join('\n');
}

function exampleEndpoint(mode: UsageMode): string {
  if (mode === 'embedding') return 'POST /v1/embeddings';
  if (mode === 'image_generation') return 'POST /v1/images/generations';
  if (mode === 'audio_speech') return 'POST /v1/audio/speech';
  if (mode === 'audio_transcription') return 'POST /v1/audio/transcriptions';
  if (mode === 'rerank') return 'POST /v1/rerank';
  return 'POST /v1/chat/completions';
}

export default function UsageExamplesCard({
  title,
  description,
  modelName,
  mode,
  warning,
  context,
  requestMetadata,
}: UsageExamplesCardProps) {
  const [language, setLanguage] = useState<ExampleLanguage>('curl');
  const baseUrl = gatewayBaseUrl();
  const normalizedMode = normalizeMode(mode);

  const snippets: Record<ExampleLanguage, string> = {
    curl: buildCurlSnippet(baseUrl, modelName, normalizedMode, requestMetadata),
    python: buildPythonSnippet(baseUrl, modelName, normalizedMode, requestMetadata),
    javascript: buildJavaScriptSnippet(baseUrl, modelName, normalizedMode, requestMetadata),
  };

  return (
    <Card>
      <div className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">{title}</h2>
            <p className="mt-1 text-sm text-gray-500">{description}</p>
          </div>
          <div className="w-full sm:w-44">
            <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">Language</label>
            <select
              value={language}
              onChange={(event) => setLanguage(event.target.value as ExampleLanguage)}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="curl">curl</option>
              <option value="python">Python</option>
              <option value="javascript">JavaScript</option>
            </select>
          </div>
        </div>

        {warning ? <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">{warning}</div> : null}
        {context ? <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700">{context}</div> : null}

        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
            <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Gateway Endpoint</div>
            <div className="mt-1 text-sm font-semibold text-gray-900">{exampleEndpoint(normalizedMode)}</div>
          </div>
          <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
            <div className="text-xs font-medium uppercase tracking-wide text-gray-500">Model Parameter</div>
            <code className="mt-1 block break-all text-xs text-gray-900">{modelName}</code>
          </div>
        </div>

        <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
          Replace <code className="rounded bg-white px-1.5 py-0.5 text-xs text-blue-900">YOUR_API_KEY</code> with a master key or a virtual API key that can access this model.
        </div>

        <pre className="overflow-auto rounded-xl border border-gray-200 bg-gray-50 px-4 py-4 text-xs font-mono text-gray-900">
          {snippets[language]}
        </pre>
      </div>
    </Card>
  );
}
