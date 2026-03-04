import type { LucideIcon } from 'lucide-react';
import {
  Bot,
  Brain,
  Cloud,
  CloudCog,
  Cpu,
  Flame,
  Orbit,
  Search,
  Sparkles,
  SquareCode,
  Triangle,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { normalizeProvider, providerDisplayName } from '../lib/providers';

type ProviderStyle = {
  Icon?: LucideIcon;
  logoUrl?: string;
  tone: string;
};

const PROVIDER_STYLES: Record<string, ProviderStyle> = {
  openai: { Icon: Sparkles, logoUrl: 'https://cdn.simpleicons.org/openai', tone: 'bg-emerald-50 text-emerald-700 border-emerald-100' },
  anthropic: { Icon: Brain, logoUrl: 'https://cdn.simpleicons.org/anthropic', tone: 'bg-amber-50 text-amber-700 border-amber-100' },
  azure: { Icon: Cloud, logoUrl: 'https://cdn.simpleicons.org/microsoftazure', tone: 'bg-sky-50 text-sky-700 border-sky-100' },
  azure_openai: { Icon: Cloud, logoUrl: 'https://cdn.simpleicons.org/microsoftazure', tone: 'bg-sky-50 text-sky-700 border-sky-100' },
  openrouter: { Icon: Orbit, logoUrl: 'https://cdn.simpleicons.org/openrouter', tone: 'bg-violet-50 text-violet-700 border-violet-100' },
  groq: { Icon: Cpu, logoUrl: 'https://cdn.simpleicons.org/groq', tone: 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-100' },
  together: { Icon: SquareCode, logoUrl: 'https://cdn.simpleicons.org/togetherai', tone: 'bg-indigo-50 text-indigo-700 border-indigo-100' },
  fireworks: { Icon: Flame, logoUrl: 'https://cdn.simpleicons.org/fireworks', tone: 'bg-orange-50 text-orange-700 border-orange-100' },
  deepinfra: { Icon: CloudCog, logoUrl: 'https://cdn.simpleicons.org/deepinfra', tone: 'bg-cyan-50 text-cyan-700 border-cyan-100' },
  perplexity: { Icon: Search, logoUrl: 'https://cdn.simpleicons.org/perplexity', tone: 'bg-rose-50 text-rose-700 border-rose-100' },
  gemini: { Icon: Sparkles, logoUrl: 'https://cdn.simpleicons.org/googlegemini', tone: 'bg-blue-50 text-blue-700 border-blue-100' },
  bedrock: { Icon: Triangle, logoUrl: 'https://cdn.simpleicons.org/amazonwebservices', tone: 'bg-stone-100 text-stone-700 border-stone-200' },
  vllm: { Icon: Bot, tone: 'bg-lime-50 text-lime-700 border-lime-100' },
  lmstudio: { Icon: Bot, logoUrl: 'https://cdn.simpleicons.org/lmstudio', tone: 'bg-slate-100 text-slate-700 border-slate-200' },
  ollama: { Icon: Bot, logoUrl: 'https://cdn.simpleicons.org/ollama', tone: 'bg-teal-50 text-teal-700 border-teal-100' },
  unknown: { Icon: Bot, tone: 'bg-gray-100 text-gray-700 border-gray-200' },
};

interface ProviderBadgeProps {
  provider?: string | null;
  model?: string | null;
  compact?: boolean;
}

export default function ProviderBadge({ provider, model, compact = false }: ProviderBadgeProps) {
  const key = normalizeProvider(provider, model);
  const style = PROVIDER_STYLES[key] || PROVIDER_STYLES.unknown;
  const [logoFailed, setLogoFailed] = useState(false);
  const label = providerDisplayName(key);
  const FallbackIcon = style.Icon || Bot;
  const shouldShowLogo = useMemo(() => Boolean(style.logoUrl && !logoFailed), [style.logoUrl, logoFailed]);

  useEffect(() => {
    setLogoFailed(false);
  }, [key, style.logoUrl]);

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${style.tone}`}>
      {shouldShowLogo ? (
        <img
          src={style.logoUrl}
          alt={`${label} logo`}
          className="h-3.5 w-3.5 rounded-sm object-contain"
          onError={() => setLogoFailed(true)}
          loading="lazy"
        />
      ) : (
        <FallbackIcon className="h-3.5 w-3.5" />
      )}
      {!compact && <span>{label}</span>}
    </span>
  );
}
