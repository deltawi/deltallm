import type { ModelDeploymentDetail } from '../../../lib/api';
import { summarizeModelCost } from '../../../lib/modelCost';
import { normalizeProvider, providerDisplayName } from '../../../lib/providers';
import { MetaCopyValue, MetaLink, MetaSeparator, MetaState } from '../../admin/shells';
import { EMPTY_DEPLOYMENT_HEALTH, formatCompactTokens, formatInteger, type ModelDetailTabId } from './modelDetailFormat';

interface ModelDetailMetaProps {
  model: ModelDeploymentDetail;
  mode: string;
  sharing: string | null;
  onOpenTab: (tab: ModelDetailTabId) => void;
}

function healthPart(model: ModelDeploymentDetail, onOpenTab: (tab: ModelDetailTabId) => void) {
  const health = model.health || EMPTY_DEPLOYMENT_HEALTH;
  if (model.healthy) return <MetaState tone="good">Healthy</MetaState>;
  if (model.health_status === 'unknown' || model.healthy == null) return <MetaState tone="off">Not checked</MetaState>;
  const failures = health.consecutive_failures ?? 0;
  const text = failures > 0 ? `Unhealthy · ${failures} failed ${failures === 1 ? 'check' : 'checks'}` : 'Unhealthy';
  return <MetaLink warn onClick={() => onOpenTab('connection')} label={`${text}. Open Connection`}>{text}</MetaLink>;
}

// One line under the title: health, model name, provider, limits, price and sharing. Each part opens the tab that owns it.
export default function ModelDetailMeta({ model, mode, sharing, onOpenTab }: ModelDetailMetaProps) {
  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const provider = providerDisplayName(normalizeProvider(model.provider, lp.model));
  const upstream = [provider, lp.model].filter(Boolean).join(' · ');
  const context = formatCompactTokens(mi.max_tokens);
  const rpm = formatInteger(lp.rpm ?? mi.rpm_limit);
  const limits = [context ? `${context} context` : null, rpm ? `${rpm} RPM` : null].filter(Boolean).join(' · ');
  const cost = summarizeModelCost(mode, mi);
  const price = cost.kind === 'priced' ? `${cost.primary} ${cost.unit.split(' · ')[0]}` : 'No price set';
  const parts = [
    healthPart(model, onOpenTab),
    <MetaCopyValue value={model.api_model_id || model.model_name} label="model name" />,
    upstream ? <MetaLink onClick={() => onOpenTab('connection')} label={`${upstream}. Open Connection`}>{upstream}</MetaLink> : null,
    limits ? <MetaLink onClick={() => onOpenTab('limits')} label={`${limits}. Open Limits`}>{limits}</MetaLink> : null,
    <MetaLink onClick={() => onOpenTab('pricing')} warn={cost.kind === 'unset'} label={`${price}. Open Pricing`}>{price}</MetaLink>,
    sharing ? <MetaLink onClick={() => onOpenTab('settings')} label={`${sharing}. Open Settings`}>{sharing}</MetaLink> : null,
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
      {parts.map((part, index) => (
        <span key={index} className="contents">
          {index > 0 ? <MetaSeparator /> : null}
          {part}
        </span>
      ))}
    </div>
  );
}
