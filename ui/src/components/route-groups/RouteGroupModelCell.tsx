import { useState } from 'react';
import { PROVIDER_LOGOS } from '../../lib/providerLogos';
import { normalizeProvider, providerDisplayName } from '../../lib/providers';

export function ModelAvatar({ provider, model }: { provider?: string | null; model?: string | null }) {
  const key = normalizeProvider(provider, model);
  const logo = PROVIDER_LOGOS[key];
  const [failed, setFailed] = useState(false);
  return (
    <span aria-hidden="true" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-white">
      {logo && !failed
        ? <img src={logo} alt="" loading="lazy" className="h-4 w-4 object-contain" onError={() => setFailed(true)} />
        : <span className="text-[11px] font-semibold uppercase text-gray-500">{(model || key).charAt(0)}</span>}
    </span>
  );
}

export function ModelCell({ name, provider, deploymentId, muted = false }: {
  name: string;
  provider?: string | null;
  deploymentId?: string;
  muted?: boolean;
}) {
  const providerName = provider ? providerDisplayName(normalizeProvider(provider, name)) : null;
  return (
    <div className={`flex min-w-0 items-center gap-2.5 ${muted ? 'opacity-60' : ''}`}>
      <ModelAvatar provider={provider} model={name} />
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-gray-900">{name}</p>
        <p className="truncate font-mono text-[11px] text-gray-500">
          {[providerName, deploymentId && deploymentId !== name ? deploymentId : null].filter(Boolean).join(' · ')}
        </p>
      </div>
    </div>
  );
}

const HEALTH_STATES = {
  paused: { label: 'Paused', text: 'text-gray-500', dot: 'bg-gray-400' },
  healthy: { label: 'Healthy', text: 'text-emerald-700', dot: 'bg-emerald-500' },
  unhealthy: { label: 'Unhealthy', text: 'text-red-700', dot: 'bg-red-500' },
  // The deployment is in the group but no longer in the model registry.
  missing: { label: 'Not found', text: 'text-amber-700', dot: 'bg-amber-500' },
} as const;

export function HealthText({ healthy, enabled = true }: { healthy: boolean | null | undefined; enabled?: boolean }) {
  const state = HEALTH_STATES[!enabled ? 'paused' : healthy === true ? 'healthy' : healthy === false ? 'unhealthy' : 'missing'];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${state.text}`}>
      <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${state.dot}`} />
      {state.label}
    </span>
  );
}

export interface ShareSegment {
  id: string;
  label: string;
  value: number;
  color: string;
}

/** A stacked bar of traffic shares. The label gives the same numbers as text. */
export function ShareBar({ segments, label, className = 'h-1.5' }: { segments: ShareSegment[]; label: string; className?: string }) {
  const visible = segments.filter((segment) => segment.value > 0);
  const summary = visible.map((segment) => `${segment.label} ${Math.round(segment.value)}%`).join(', ');
  return (
    <div role="img" aria-label={`${label}: ${summary || 'no traffic'}`} className={`flex w-full gap-0.5 overflow-hidden rounded-full bg-gray-100 ${className}`}>
      {visible.map((segment) => (
        <span key={segment.id} className="h-full" style={{ width: `${segment.value}%`, background: segment.color }} />
      ))}
    </div>
  );
}
