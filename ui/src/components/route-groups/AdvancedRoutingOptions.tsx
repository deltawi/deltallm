import { useState } from 'react';
import { Info } from 'lucide-react';
import Button from '../Button';
import ToggleSwitch from '../ToggleSwitch';
import type { RouteGroupMemberDetail } from '../../lib/api';
import { chooseSelector } from '../../lib/routeGroupSelector';
import { supportsContextRouting, type PolicyGuidedValues } from '../../lib/routeGroups';
import { inputClass, NumberedCard } from './AdvancedRoutingCards';
import RouteGroupSelectorEditor from './RouteGroupSelectorEditor';

interface OptionCardProps {
  values: PolicyGuidedValues;
  workloadMode: string;
  disabled: boolean;
  changed: boolean;
  onChange: (next: PolicyGuidedValues) => void;
}

function StatePill({ on }: { on: boolean }) {
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${on ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-gray-200 text-gray-500'}`}>{on ? 'On' : 'Off'}</span>;
}

export function ContextCard({ values, workloadMode, disabled, changed, onChange }: OptionCardProps) {
  const supported = supportsContextRouting(workloadMode);
  const on = values.contextMode !== 'disabled';
  const update = <K extends keyof PolicyGuidedValues>(key: K, value: PolicyGuidedValues[K]) => onChange({ ...values, [key]: value });
  return (
    <NumberedCard
      step={4}
      title="Context-aware routing"
      changed={changed}
      description={supported
        ? 'Skip deployments whose context window is too small for the request. Uses each model’s context size.'
        : `Works only for chat and embedding groups. This group uses ${workloadMode.replace(/_/g, ' ')}.`}
      action={supported ? (
        <span className="flex items-center gap-2">
          <StatePill on={on} />
          <ToggleSwitch checked={on} disabled={disabled} activeColor="rgb(var(--brand-primary))" aria-label="Context-aware routing"
            onCheckedChange={(next) => update('contextMode', next ? 'eligible-only' : 'disabled')} />
        </span>
      ) : undefined}
    >
      {!supported && on ? (
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-xs text-red-700" role="alert">
          The saved policy turns this on, so it cannot be published for this workload.
          <Button size="sm" variant="outline" disabled={disabled} onClick={() => update('contextMode', 'disabled')}>Turn it off</Button>
        </div>
      ) : supported && on ? (
        <div className="grid gap-3 px-4 py-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-gray-700">
            <span>Which deployments</span>
            <select className={inputClass} value={values.contextMode} disabled={disabled} onChange={(event) => update('contextMode', event.target.value as PolicyGuidedValues['contextMode'])}>
              <option value="eligible-only">Any deployment the request fits</option>
              <option value="smallest-sufficient">The smallest deployment the request fits</option>
            </select>
          </label>
          <label className="block space-y-1 text-xs font-medium text-gray-700">
            <span>Context size not known</span>
            <select className={inputClass} value={values.contextUnknownCapacity} disabled={disabled} onChange={(event) => update('contextUnknownCapacity', event.target.value as PolicyGuidedValues['contextUnknownCapacity'])}>
              <option value="allow">Use the deployment</option>
              <option value="exclude">Skip the deployment</option>
            </select>
          </label>
          <label className="block space-y-1 text-xs font-medium text-gray-700">
            <span>Expected output tokens</span>
            <input className={inputClass} type="number" min="0" step="1" value={values.contextDefaultOutputTokens} disabled={disabled} onChange={(event) => update('contextDefaultOutputTokens', event.target.value)} />
            <span className="block font-normal text-gray-500">Used when the request does not set a limit.</span>
          </label>
          <label className="block space-y-1 text-xs font-medium text-gray-700">
            <span>Safety margin tokens</span>
            <input className={inputClass} type="number" min="0" step="1" value={values.contextSafetyMarginTokens} disabled={disabled} onChange={(event) => update('contextSafetyMarginTokens', event.target.value)} />
          </label>
        </div>
      ) : null}
    </NumberedCard>
  );
}

export function SelectorCard({ values, workloadMode, disabled, changed, onChange, routeGroupId, members }: OptionCardProps & {
  routeGroupId: string;
  members: RouteGroupMemberDetail[];
}) {
  const enabled = values.selector.state === 'enabled' || values.selector.state === 'unsupported';
  const [open, setOpen] = useState(enabled);
  const chat = workloadMode === 'chat';
  const shown = chat && (enabled || open);
  return (
    <NumberedCard
      step={5}
      title="Model selector"
      changed={changed}
      description={chat
        ? 'A small model reads each request and sends easy ones to cheaper models, hard ones to stronger models.'
        : 'Works only for chat groups.'}
      action={chat ? (
        <span className="flex items-center gap-2">
          <StatePill on={enabled} />
          <ToggleSwitch checked={shown} disabled={disabled} activeColor="rgb(var(--brand-primary))" aria-label="Model selector"
            onCheckedChange={(next) => {
              setOpen(next);
              if (!next && enabled) onChange(chooseSelector(values, ''));
            }} />
        </span>
      ) : undefined}
    >
      {shown ? (
        <div className="space-y-3 px-4 py-3">
          <p className="flex gap-2 rounded-lg border border-brand-primary/20 bg-brand-primary-soft px-3 py-2 text-xs text-brand-primary-ink">
            <Info aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>The selector needs spend ingestion in <code className="font-mono">outbox</code> mode. If the gateway uses another mode, publishing returns an error that says so.</span>
          </p>
          <fieldset disabled={disabled}>
            <RouteGroupSelectorEditor routeGroupId={routeGroupId} values={values} onChange={onChange} members={members} workloadMode={workloadMode} bare />
          </fieldset>
        </div>
      ) : null}
    </NumberedCard>
  );
}
