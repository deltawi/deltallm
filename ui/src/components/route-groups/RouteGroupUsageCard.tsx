import { useState } from 'react';
import { Brain, Copy, Tag } from 'lucide-react';

interface BoundPromptSummary {
  templateKey: string;
  label?: string | null;
  requiredVariables: string[];
}

interface RouteGroupUsageCardProps {
  groupKey: string;
  mode: string;
  liveTrafficEnabled: boolean;
  boundPrompt: BoundPromptSummary | null;
}

type Lang = 'curl' | 'python' | 'javascript';

function baseUrl(): string {
  return `${typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000'}/v1`;
}

function endpoint(mode: string): string {
  if (mode === 'embedding') return '/embeddings';
  if (mode === 'image_generation') return '/images/generations';
  if (mode === 'audio_speech') return '/audio/speech';
  if (mode === 'audio_transcription') return '/audio/transcriptions';
  if (mode === 'rerank') return '/rerank';
  return '/chat/completions';
}

function buildCurl(base: string, model: string, mode: string, meta?: Record<string, unknown>): string {
  const metaStr = meta && Object.keys(meta).length > 0 ? `, "metadata": ${JSON.stringify(meta)}` : '';
  if (mode === 'audio_transcription') {
    return [
      `curl -sS ${base}/audio/transcriptions \\`,
      '  -H "Authorization: Bearer YOUR_API_KEY" \\',
      `  -F "model=${model}" \\`,
      '  -F "file=@sample.wav" \\',
      '  -F "response_format=json"',
    ].join('\n');
  }
  if (mode === 'embedding') {
    return [
      `curl -sS ${base}/embeddings \\`,
      '  -H "Authorization: Bearer YOUR_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      `  -d '{"model":"${model}","input":"Hello from DeltaLLM."${metaStr}}'`,
    ].join('\n');
  }
  if (mode === 'image_generation') {
    return [
      `curl -sS ${base}/images/generations \\`,
      '  -H "Authorization: Bearer YOUR_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      `  -d '{"model":"${model}","prompt":"A minimal blue robot","size":"1024x1024"${metaStr}}'`,
    ].join('\n');
  }
  if (mode === 'rerank') {
    return [
      `curl -sS ${base}/rerank \\`,
      '  -H "Authorization: Bearer YOUR_API_KEY" \\',
      '  -H "Content-Type: application/json" \\',
      `  -d '{"model":"${model}","query":"Which doc explains ML?","documents":["Machine learning is AI.","The weather is warm."],"top_n":2}'`,
    ].join('\n');
  }
  return [
    `curl -sS ${base}/chat/completions \\`,
    '  -H "Authorization: Bearer YOUR_API_KEY" \\',
    '  -H "Content-Type: application/json" \\',
    `  -d '{"model":"${model}","messages":[{"role":"user","content":"Say hello in one sentence."}]${metaStr}}'`,
  ].join('\n');
}

function buildPython(base: string, model: string, mode: string, meta?: Record<string, unknown>): string {
  const metaLine = meta && Object.keys(meta).length > 0 ? `    metadata=${JSON.stringify(meta)},\n` : '';
  const header = ['from openai import OpenAI', '', 'client = OpenAI(', `    base_url="${base}",`, '    api_key="YOUR_API_KEY",', ')', ''].join('\n');
  if (mode === 'audio_transcription') {
    return `${header}with open("sample.wav", "rb") as f:\n    t = client.audio.transcriptions.create(model="${model}", file=f, response_format="json")\nprint(t.text)`;
  }
  if (mode === 'embedding') {
    return `${header}r = client.embeddings.create(\n    model="${model}",\n    input="Hello from DeltaLLM.",\n${metaLine})\nprint(r.data[0].embedding[:5])`;
  }
  if (mode === 'image_generation') {
    return `${header}r = client.images.generate(\n    model="${model}",\n    prompt="A minimal blue robot",\n    size="1024x1024",\n${metaLine})\nprint(r.data[0].url)`;
  }
  if (mode === 'audio_speech') {
    return `${header}speech = client.audio.speech.create(\n    model="${model}",\n    voice="alloy",\n    input="Hello from DeltaLLM.",\n${metaLine})\nspeech.stream_to_file("speech.mp3")`;
  }
  return `${header}r = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "Say hello in one sentence."}],\n${metaLine})\nprint(r.choices[0].message.content)`;
}

function buildJavaScript(base: string, model: string, mode: string, meta?: Record<string, unknown>): string {
  const metaLine = meta && Object.keys(meta).length > 0 ? `  metadata: ${JSON.stringify(meta)},\n` : '';
  const header = ['import OpenAI from "openai";', '', 'const client = new OpenAI({', `  baseURL: "${base}",`, '  apiKey: "YOUR_API_KEY",', '});', ''].join('\n');
  if (mode === 'embedding') {
    return `${header}const r = await client.embeddings.create({\n  model: "${model}",\n  input: "Hello from DeltaLLM.",\n${metaLine}});\nconsole.log(r.data[0].embedding.slice(0, 5));`;
  }
  if (mode === 'image_generation') {
    return `${header}const r = await client.images.generate({\n  model: "${model}",\n  prompt: "A minimal blue robot",\n  size: "1024x1024",\n${metaLine}});\nconsole.log(r.data[0].url);`;
  }
  if (mode === 'audio_speech') {
    return `${header}const speech = await client.audio.speech.create({\n  model: "${model}",\n  voice: "alloy",\n  input: "Hello from DeltaLLM.",\n${metaLine}});\nconst buf = Buffer.from(await speech.arrayBuffer());\nawait fs.promises.writeFile("speech.mp3", buf);`;
  }
  return `${header}const r = await client.chat.completions.create({\n  model: "${model}",\n  messages: [{ role: "user", content: "Say hello in one sentence." }],\n${metaLine}});\nconsole.log(r.choices[0].message.content);`;
}

export default function RouteGroupUsageCard({ groupKey, mode, liveTrafficEnabled, boundPrompt }: RouteGroupUsageCardProps) {
  const [lang, setLang] = useState<Lang>('curl');
  const [copied, setCopied] = useState(false);

  const promptMeta: Record<string, unknown> | undefined =
    boundPrompt && boundPrompt.requiredVariables.length > 0
      ? { prompt_variables: Object.fromEntries(boundPrompt.requiredVariables.map((v) => [v, `<${v}>`])) }
      : undefined;

  const base = baseUrl();
  const snippets: Record<Lang, string> = {
    curl:       buildCurl(base, groupKey, mode, promptMeta),
    python:     buildPython(base, groupKey, mode, promptMeta),
    javascript: buildJavaScript(base, groupKey, mode, promptMeta),
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(snippets[lang]).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <div className="space-y-4">
      {/* Traffic off warning */}
      {!liveTrafficEnabled && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Live traffic is currently <strong>off</strong>. Enable it in the Settings tab before sending production requests.
        </div>
      )}

      {/* Code block */}
      <div className="overflow-hidden rounded-2xl border border-gray-200">
        {/* toolbar */}
        <div className="flex items-center justify-between border-b border-gray-800 bg-gray-950 px-4 py-2.5">
          <div className="flex gap-1 rounded-lg border border-gray-700 p-0.5">
            {(['curl', 'python', 'javascript'] as Lang[]).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => setLang(l)}
                className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                  lang === l ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {l === 'curl' ? 'cURL' : l === 'python' ? 'Python' : 'JavaScript'}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200"
          >
            <Copy className="h-3.5 w-3.5" />
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
        <pre className="overflow-x-auto bg-gray-950 px-5 py-4 text-xs leading-relaxed text-gray-100">
          {snippets[lang]}
        </pre>
      </div>

      {/* Routing context banner */}
      <div className="rounded-2xl border border-blue-100 bg-blue-50 p-4">
        <div className="flex items-start gap-3">
          <Brain className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
          <div className="text-sm">
            <div className="font-semibold text-blue-800">How this group routes traffic</div>
            <div className="mt-1 text-blue-700">
              Pass <code className="rounded bg-white px-1 py-0.5 text-xs">{groupKey}</code> as the{' '}
              <code className="rounded bg-white px-1 py-0.5 text-xs">model</code> parameter.
              The gateway selects the best available deployment in real-time based on health, weight, and the active routing policy.
              Replace <code className="rounded bg-white px-1 py-0.5 text-xs">YOUR_API_KEY</code> with a master key or a virtual API key scoped to this group.
            </div>
          </div>
        </div>
      </div>

      {/* Prompt binding banner */}
      {boundPrompt ? (
        <div className="rounded-2xl border border-violet-100 bg-violet-50 p-4">
          <div className="flex items-start gap-3">
            <Tag className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" />
            <div className="text-sm">
              <div className="font-semibold text-violet-800">Prompt auto-resolved</div>
              <div className="mt-1 text-violet-700">
                Requests resolve prompt{' '}
                <code className="rounded bg-white px-1 py-0.5 text-xs">{boundPrompt.templateKey}</code>
                {boundPrompt.label && (
                  <> at label <code className="rounded bg-white px-1 py-0.5 text-xs">{boundPrompt.label}</code></>
                )}.
                {boundPrompt.requiredVariables.length > 0 && (
                  <> Pass <code className="rounded bg-white px-1 py-0.5 text-xs">metadata.prompt_variables</code> with keys:{' '}
                  {boundPrompt.requiredVariables.map((v) => (
                    <code key={v} className="mr-1 rounded bg-white px-1 py-0.5 text-xs">{v}</code>
                  ))}.</>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3 text-sm text-gray-500">
          No prompt bound. Requests use whatever system prompt your client provides. Bind a prompt in the <strong>Advanced</strong> tab to have the gateway inject it automatically.
        </div>
      )}
    </div>
  );
}
