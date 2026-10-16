export type SnippetLanguage = 'curl' | 'python' | 'javascript';

export const SNIPPET_LANGUAGES: ReadonlyArray<{ id: SnippetLanguage; label: string }> = [
  { id: 'curl', label: 'cURL' },
  { id: 'python', label: 'Python' },
  { id: 'javascript', label: 'JavaScript' },
];

export function gatewayBaseUrl(): string {
  return `${typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000'}/v1`;
}

function shellSingleQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function curlJson(base: string, endpoint: string, body: Record<string, unknown>): string {
  return [
    `curl -sS ${base}/${endpoint} \\`,
    '  -H "Authorization: Bearer YOUR_API_KEY" \\',
    '  -H "Content-Type: application/json" \\',
    `  -d ${shellSingleQuote(JSON.stringify(body))}`,
  ].join('\n');
}

export function buildCurl(base: string, model: string, mode: string, instanceName: string, meta?: Record<string, unknown>): string {
  const metadata = meta && Object.keys(meta).length > 0 ? { metadata: meta } : {};
  if (mode === 'audio_transcription') {
    return [
      `curl -sS ${base}/audio/transcriptions \\`,
      '  -H "Authorization: Bearer YOUR_API_KEY" \\',
      `  -F "model=${model}" \\`,
      '  -F "file=@sample.wav" \\',
      '  -F "response_format=json"',
    ].join('\n');
  }
  if (mode === 'audio_speech') {
    return `${curlJson(base, 'audio/speech', { model, voice: 'alloy', input: `Hello from ${instanceName}.`, ...metadata })} \\\n  -o speech.mp3`;
  }
  if (mode === 'embedding') return curlJson(base, 'embeddings', { model, input: `Hello from ${instanceName}.`, ...metadata });
  if (mode === 'image_generation') {
    return curlJson(base, 'images/generations', { model, prompt: 'A minimal blue robot', size: '1024x1024', ...metadata });
  }
  if (mode === 'rerank') {
    return curlJson(base, 'rerank', {
      model,
      query: 'Which doc explains ML?',
      documents: ['Machine learning is AI.', 'The weather is warm.'],
      top_n: 2,
    });
  }
  return curlJson(base, 'chat/completions', {
    model,
    messages: [{ role: 'user', content: 'Say hello in one sentence.' }],
    ...metadata,
  });
}

export function buildPython(base: string, model: string, mode: string, instanceName: string, meta?: Record<string, unknown>): string {
  const metaLine = meta && Object.keys(meta).length > 0 ? `    metadata=${JSON.stringify(meta)},\n` : '';
  const header = ['from openai import OpenAI', '', 'client = OpenAI(', `    base_url="${base}",`, '    api_key="YOUR_API_KEY",', ')', ''].join('\n');
  if (mode === 'audio_transcription') {
    return `${header}with open("sample.wav", "rb") as f:\n    t = client.audio.transcriptions.create(model="${model}", file=f, response_format="json")\nprint(t.text)`;
  }
  if (mode === 'embedding') {
    return `${header}r = client.embeddings.create(\n    model="${model}",\n    input=${JSON.stringify(`Hello from ${instanceName}.`)},\n${metaLine})\nprint(r.data[0].embedding[:5])`;
  }
  if (mode === 'image_generation') {
    return `${header}r = client.images.generate(\n    model="${model}",\n    prompt="A minimal blue robot",\n    size="1024x1024",\n${metaLine})\nprint(r.data[0].url)`;
  }
  if (mode === 'audio_speech') {
    return `${header}speech = client.audio.speech.create(\n    model="${model}",\n    voice="alloy",\n    input=${JSON.stringify(`Hello from ${instanceName}.`)},\n${metaLine})\nspeech.stream_to_file("speech.mp3")`;
  }
  return `${header}r = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "Say hello in one sentence."}],\n${metaLine})\nprint(r.choices[0].message.content)`;
}

export function buildJavaScript(base: string, model: string, mode: string, instanceName: string, meta?: Record<string, unknown>): string {
  const metaLine = meta && Object.keys(meta).length > 0 ? `  metadata: ${JSON.stringify(meta)},\n` : '';
  const header = ['import OpenAI from "openai";', '', 'const client = new OpenAI({', `  baseURL: "${base}",`, '  apiKey: "YOUR_API_KEY",', '});', ''].join('\n');
  if (mode === 'embedding') {
    return `${header}const r = await client.embeddings.create({\n  model: "${model}",\n  input: ${JSON.stringify(`Hello from ${instanceName}.`)},\n${metaLine}});\nconsole.log(r.data[0].embedding.slice(0, 5));`;
  }
  if (mode === 'image_generation') {
    return `${header}const r = await client.images.generate({\n  model: "${model}",\n  prompt: "A minimal blue robot",\n  size: "1024x1024",\n${metaLine}});\nconsole.log(r.data[0].url);`;
  }
  if (mode === 'audio_speech') {
    return `${header}const speech = await client.audio.speech.create({\n  model: "${model}",\n  voice: "alloy",\n  input: ${JSON.stringify(`Hello from ${instanceName}.`)},\n${metaLine}});\nconst buf = Buffer.from(await speech.arrayBuffer());\nawait fs.promises.writeFile("speech.mp3", buf);`;
  }
  return `${header}const r = await client.chat.completions.create({\n  model: "${model}",\n  messages: [{ role: "user", content: "Say hello in one sentence." }],\n${metaLine}});\nconsole.log(r.choices[0].message.content);`;
}

/** Example requests for a route group. Prompt variables become `metadata.prompt_variables` placeholders. */
export function routeGroupSnippets(
  groupKey: string,
  mode: string,
  instanceName: string,
  promptVariables: string[] = [],
  base = gatewayBaseUrl(),
): Record<SnippetLanguage, string> {
  const meta = promptVariables.length > 0
    ? { prompt_variables: Object.fromEntries(promptVariables.map((name) => [name, `<${name}>`])) }
    : undefined;
  return {
    curl: buildCurl(base, groupKey, mode, instanceName, meta),
    python: buildPython(base, groupKey, mode, instanceName, meta),
    javascript: buildJavaScript(base, groupKey, mode, instanceName, meta),
  };
}

/** The Playground mode that can call a group with this workload, if any. */
export function playgroundModeFor(mode: string): 'chat' | 'tts' | 'stt' | null {
  if (mode === 'chat') return 'chat';
  if (mode === 'audio_speech') return 'tts';
  if (mode === 'audio_transcription') return 'stt';
  return null;
}

export function playgroundPathFor(groupKey: string, mode: string): string | null {
  const playgroundMode = playgroundModeFor(mode);
  if (!playgroundMode) return null;
  return `/playground?${new URLSearchParams({ model: groupKey, mode: playgroundMode }).toString()}`;
}

export function requiredPromptVariables(schema: unknown): string[] {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return [];
  const required = (schema as Record<string, unknown>).required;
  if (!Array.isArray(required)) return [];
  return required.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

/** "$2.50 / $10.00" input and output price per one million tokens, or null when unknown. */
export function tokenPricePerMillion(info: Record<string, unknown> | null | undefined): string | null {
  const input = typeof info?.input_cost_per_token === 'number' ? info.input_cost_per_token : null;
  const output = typeof info?.output_cost_per_token === 'number' ? info.output_cost_per_token : null;
  if (input == null && output == null) return null;
  const format = (value: number | null) => {
    if (value == null) return '\u2014';
    const perMillion = value * 1_000_000;
    return `$${perMillion < 0.01 && perMillion > 0 ? perMillion.toPrecision(2) : perMillion.toFixed(2)}`;
  };
  return `${format(input)} / ${format(output)}`;
}
