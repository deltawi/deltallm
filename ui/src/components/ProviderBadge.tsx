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
import { PROVIDER_LOGOS } from '../lib/providerLogos';
import { normalizeProvider, providerDisplayName } from '../lib/providers';

type ProviderStyle = {
  Icon?: LucideIcon;
  logoUrl?: string;
  tone: string;
};

const PROVIDER_STYLES: Record<string, ProviderStyle> = {
  openai: { Icon: Sparkles, logoUrl: PROVIDER_LOGOS.openai, tone: 'bg-emerald-50 text-emerald-700 border-emerald-100' },
  anthropic: { Icon: Brain, logoUrl: PROVIDER_LOGOS.anthropic, tone: 'bg-amber-50 text-amber-700 border-amber-100' },
  azure: { Icon: Cloud, tone: 'bg-sky-50 text-sky-700 border-sky-100' },
  azure_openai: { Icon: Cloud, tone: 'bg-sky-50 text-sky-700 border-sky-100' },
  openrouter: { Icon: Orbit, logoUrl: PROVIDER_LOGOS.openrouter, tone: 'bg-violet-50 text-violet-700 border-violet-100' },
  groq: { Icon: Cpu, logoUrl: PROVIDER_LOGOS.groq, tone: 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-100' },
  together: { Icon: SquareCode, tone: 'bg-indigo-50 text-indigo-700 border-indigo-100' },
  fireworks: { Icon: Flame, logoUrl: PROVIDER_LOGOS.fireworks, tone: 'bg-orange-50 text-orange-700 border-orange-100' },
  deepinfra: { Icon: CloudCog, tone: 'bg-cyan-50 text-cyan-700 border-cyan-100' },
  perplexity: { Icon: Search, logoUrl: PROVIDER_LOGOS.perplexity, tone: 'bg-rose-50 text-rose-700 border-rose-100' },
  gemini: { Icon: Sparkles, logoUrl: PROVIDER_LOGOS.gemini, tone: 'bg-blue-50 text-blue-700 border-blue-100' },
  bedrock: { Icon: Triangle, tone: 'bg-stone-100 text-stone-700 border-stone-200' },
  vllm: { Icon: Bot, logoUrl: PROVIDER_LOGOS.vllm, tone: 'bg-lime-50 text-lime-700 border-lime-100' },
  lmstudio: { Icon: Bot, tone: 'bg-slate-100 text-slate-700 border-slate-200' },
  ollama: { Icon: Bot, logoUrl: PROVIDER_LOGOS.ollama, tone: 'bg-teal-50 text-teal-700 border-teal-100' },
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
