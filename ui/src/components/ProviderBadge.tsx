import { Bot } from 'lucide-react';
import { useState } from 'react';
import { PROVIDER_LOGOS } from '../lib/providerLogos';
import { normalizeProvider, providerDisplayName } from '../lib/providers';

const PROVIDER_TONES: Record<string, string> = {
  anthropic: 'bg-amber-50 text-amber-700 border-amber-100',
  azure: 'bg-sky-50 text-sky-700 border-sky-100',
  azure_openai: 'bg-sky-50 text-sky-700 border-sky-100',
  bedrock: 'bg-stone-100 text-stone-700 border-stone-200',
  deepinfra: 'bg-cyan-50 text-cyan-700 border-cyan-100',
  deepseek: 'bg-blue-50 text-blue-700 border-blue-100',
  elevenlabs: 'bg-neutral-100 text-neutral-800 border-neutral-200',
  fireworks: 'bg-orange-50 text-orange-700 border-orange-100',
  gemini: 'bg-blue-50 text-blue-700 border-blue-100',
  groq: 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-100',
  lmstudio: 'bg-slate-100 text-slate-700 border-slate-200',
  minimax: 'bg-rose-50 text-rose-700 border-rose-100',
  ollama: 'bg-teal-50 text-teal-700 border-teal-100',
  openai: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  openrouter: 'bg-violet-50 text-violet-700 border-violet-100',
  perplexity: 'bg-rose-50 text-rose-700 border-rose-100',
  qwen: 'bg-violet-50 text-violet-700 border-violet-100',
  tencent: 'bg-sky-50 text-sky-700 border-sky-100',
  together: 'bg-indigo-50 text-indigo-700 border-indigo-100',
  unknown: 'bg-gray-100 text-gray-700 border-gray-200',
  vllm: 'bg-lime-50 text-lime-700 border-lime-100',
  zai: 'bg-neutral-100 text-neutral-800 border-neutral-200',
};

interface ProviderBadgeProps {
  provider?: string | null;
  model?: string | null;
  compact?: boolean;
}

export default function ProviderBadge({ provider, model, compact = false }: ProviderBadgeProps) {
  const key = normalizeProvider(provider, model);
  const tone = PROVIDER_TONES[key] || PROVIDER_TONES.unknown;
  const logoUrl = PROVIDER_LOGOS[key];
  const [failedLogoKey, setFailedLogoKey] = useState<string | null>(null);
  const label = providerDisplayName(key);
  const logoFailureKey = logoUrl ? `${key}:${logoUrl}` : null;
  const shouldShowLogo = Boolean(logoUrl && failedLogoKey !== logoFailureKey);

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>
      {shouldShowLogo ? (
        <img
          src={logoUrl}
          alt={`${label} logo`}
          className="h-3.5 w-3.5 rounded-sm object-contain"
          onError={() => setFailedLogoKey(logoFailureKey)}
          loading="lazy"
        />
      ) : (
        <Bot className="h-3.5 w-3.5" />
      )}
      {!compact && <span>{label}</span>}
    </span>
  );
}
