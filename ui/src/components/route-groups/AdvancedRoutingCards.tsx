import { useState, type ReactNode } from 'react';
import { ArrowDown, ArrowRight, ArrowUp, Check, GitBranch, ListOrdered, Shuffle, X, type LucideIcon } from 'lucide-react';
import ToggleSwitch from '../ToggleSwitch';
import type { RouteGroupMemberDetail } from '../../lib/api';
import {
  moveGuidedPolicyMember,
  orderedGuidedMemberIds,
  RETRYABLE_ERROR_OPTIONS,
  routeGroupStrategyOptions,
  withGuidedPolicyStrategy,
  type PolicyGuidedValues,
} from '../../lib/routeGroups';
import { keepFocusAfterMove } from '../../lib/keepFocusAfterMove';
import { RETRY_CLASS_LABELS } from '../../lib/routeGroupPolicyDiff';
import { memberLabels, strategyLabel } from '../../lib/routeGroupRouting';
import { HealthText, ModelCell } from './RouteGroupModelCell';

const MAIN_STRATEGIES: ReadonlyArray<{ id: string; icon: LucideIcon; hint: string }> = [
  { id: 'weighted', icon: GitBranch, hint: 'Send a set share of traffic to each deployment.' },
  { id: 'priority-based-routing', icon: ListOrdered, hint: 'Try deployments in order; move on when one fails.' },
  { id: 'simple-shuffle', icon: Shuffle, hint: 'Pick a healthy deployment at random.' },
];

const OTHER_HINTS: Record<string, string> = {
  'least-busy': 'Send to the deployment with the fewest requests in progress.',
  'latency-based-routing': 'Prefer the deployment with the lowest recent latency.',
  'cost-based-routing': 'Prefer the deployment with the lowest price.',
  'usage-based-routing': 'Prefer the deployment with the most free capacity in its limits.',
  'rate-limit-aware': 'Avoid deployments that are close to their rate limits.',
  'tag-based-routing': 'Legacy. Pick deployments by request tags.',
};

export const inputClass = 'control-field w-full rounded-lg border border-gray-200 bg-white px-3 focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-50';

export function NumberedCard({ step, title, description, changed, action, children }: {
  step: number;
  title: string;
  description: ReactNode;
  changed?: boolean;
  action?: ReactNode;
  children?: ReactNode;
}) {
  const id = `advanced-card-${step}`;
  return (
    <section aria-labelledby={id} className={`rounded-xl border bg-white shadow-sm ${changed ? 'border-brand-primary/40' : 'border-gray-200'}`}>
      <header className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 flex-1 basis-64 gap-2.5">
          <span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-brand-primary-soft text-[11px] font-semibold text-brand-primary-ink">{step}</span>
          <div className="min-w-0">
            <h3 id={id} className="flex flex-wrap items-center gap-2 text-sm font-semibold text-gray-900">
              {title}
              {changed ? <span className="rounded-full bg-brand-primary-soft px-2 py-0.5 text-[10px] font-semibold text-brand-primary-ink">Changed</span> : null}
            </h3>
            <p className="mt-0.5 text-xs text-gray-500">{description}</p>
          </div>
        </div>
        {action}
      </header>
      {children ? <div className="border-t border-gray-100">{children}</div> : null}
    </section>
  );
}

interface CardProps {
  values: PolicyGuidedValues;
  disabled: boolean;
  changed: boolean;
  onChange: (next: PolicyGuidedValues) => void;
}

export function StrategyCard({ values, disabled, changed, onChange }: CardProps) {
  const others = routeGroupStrategyOptions(values.strategy).filter((id) => !MAIN_STRATEGIES.some((item) => item.id === id));
  const choose = (strategy: string) => onChange(withGuidedPolicyStrategy(values, strategy));
  return (
    <NumberedCard step={1} title="How to pick a deployment" description="The rule the gateway uses to choose among the deployments below." changed={changed}>
      <div className="space-y-2 px-4 py-3">
        <div role="radiogroup" aria-label="Routing strategy" className="grid gap-2 md:grid-cols-3">
          {MAIN_STRATEGIES.map(({ id, icon: Icon, hint }) => (
            <button key={id} type="button" role="radio" aria-checked={values.strategy === id} disabled={disabled} onClick={() => choose(id)}
              className={`rounded-lg border p-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed ${values.strategy === id ? 'border-brand-primary bg-brand-primary-soft' : 'border-gray-200 hover:bg-gray-50'}`}>
              <span className={`flex items-center gap-1.5 text-sm font-medium ${values.strategy === id ? 'text-brand-primary-ink' : 'text-gray-900'}`}><Icon aria-hidden="true" className="h-3.5 w-3.5" />{strategyLabel(id)}</span>
              <span className="mt-1 block text-xs text-gray-500">{hint}</span>
            </button>
          ))}
        </div>
        <details open={others.includes(values.strategy) || undefined} className="text-xs text-gray-600">
          <summary className="cursor-pointer select-none">More strategies ({others.length})</summary>
          <div role="radiogroup" aria-label="More strategies" className="mt-2 grid gap-1.5 md:grid-cols-2">
            {others.map((id) => (
              <label key={id} className={`flex cursor-pointer items-start gap-2 rounded-lg border p-2 ${values.strategy === id ? 'border-brand-primary bg-brand-primary-soft' : 'border-gray-200'}`}>
                <input type="radio" name="advanced-strategy" className="mt-0.5" checked={values.strategy === id} disabled={disabled} onChange={() => choose(id)} />
                <span><span className="block font-medium text-gray-900">{strategyLabel(id)}</span><span className="text-gray-500">{OTHER_HINTS[id] ?? id}</span></span>
              </label>
            ))}
          </div>
        </details>
      </div>
    </NumberedCard>
  );
}

function WeightStepper({ id, label, value, placeholder, disabled, onChange }: {
  id: string; label: string; value: string; placeholder: string; disabled: boolean; onChange: (next: string) => void;
}) {
  const current = Number(value || placeholder) || 1;
  const button = 'px-2 text-gray-500 hover:bg-gray-50 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary';
  return (
    <span className="inline-flex h-7 items-stretch overflow-hidden rounded-md border border-gray-200 bg-white text-sm">
      <button type="button" className={button} disabled={disabled || current <= 1} aria-label={`Lower weight for ${label}`} onClick={() => onChange(String(current - 1))}>−</button>
      <input id={`weight-${id}`} aria-label={`Weight for ${label}`} inputMode="numeric" value={value} placeholder={placeholder} disabled={disabled}
        onChange={(event) => onChange(event.target.value.replace(/[^\d]/g, ''))} className="w-10 border-x border-gray-200 text-center tabular-nums focus:outline-none" />
      <button type="button" className={button} disabled={disabled} aria-label={`Raise weight for ${label}`} onClick={() => onChange(String(current + 1))}>+</button>
    </span>
  );
}

export function DeploymentsCard({ values, disabled, changed, onChange, members, draftShares, liveShares, onManageMembers }: CardProps & {
  members: RouteGroupMemberDetail[];
  draftShares: Record<string, number> | null;
  liveShares: Record<string, number> | null;
  onManageMembers: () => void;
}) {
  const ids = orderedGuidedMemberIds(values, members);
  const labels = memberLabels(members);
  const weighted = values.strategy === 'weighted';
  const ordered = values.strategy === 'priority-based-routing';
  const rows = [...ids.flatMap((id) => members.filter((member) => member.deployment_id === id)), ...members.filter((member) => !ids.includes(member.deployment_id))];
  const selectorOwnsMembers = values.selector.state === 'enabled';
  const toggle = (id: string, include: boolean) => onChange({ ...values, memberSelection: 'explicit', memberIds: include ? [...ids, id] : ids.filter((item) => item !== id) });
  return (
    <NumberedCard step={2} title="Deployments" changed={changed}
      description={weighted ? 'Set each deployment’s share. The grey tick marks the live share.' : ordered ? 'Set the order. The first healthy deployment answers.' : 'Choose which deployments can answer.'}
      action={<button type="button" onClick={onManageMembers} className="inline-flex items-center gap-1 text-xs font-medium text-brand-primary-ink hover:underline">Manage group members<ArrowRight aria-hidden="true" className="h-3 w-3" /></button>}>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-sm">
          <thead>
            <tr className="text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              <th scope="col" className="w-10 px-4 py-2"><span className="sr-only">Included</span></th>
              <th scope="col" className="px-2 py-2">Model</th>
              <th scope="col" className="px-2 py-2">Health</th>
              {weighted || ordered ? <th scope="col" className="px-2 py-2">{weighted ? 'Weight' : 'Order'}</th> : null}
              <th scope="col" className="px-4 py-2">Traffic share</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((member) => {
              const id = member.deployment_id;
              const included = member.enabled && ids.includes(id);
              const index = ids.indexOf(id);
              const draft = draftShares?.[id];
              const live = liveShares?.[id];
              return (
                <tr key={id} className={`border-t border-gray-100 ${included ? '' : 'bg-gray-50'}`}>
                  <td className="px-4 py-2">
                    <input type="checkbox" aria-label={`Include ${labels[id]}`} checked={included} disabled={disabled || !member.enabled} onChange={(event) => toggle(id, event.target.checked)}
                      className="h-4 w-4 rounded border-gray-300 text-brand-primary focus:ring-brand-primary" />
                  </td>
                  <td className="px-2 py-2"><ModelCell name={labels[id]} provider={member.provider} deploymentId={id} muted={!included} /></td>
                  <td className="px-2 py-2"><HealthText healthy={member.healthy} enabled={member.enabled} /></td>
                  {weighted || ordered ? (
                    <td className="px-2 py-2">
                      {!included ? <span className="text-xs text-gray-400">—</span> : weighted ? (
                        <WeightStepper id={id} label={labels[id]} value={values.memberWeights[id] ?? ''} placeholder={String(member.weight ?? 1)} disabled={disabled}
                          onChange={(next) => onChange({ ...values, memberSelection: 'explicit', memberIds: ids, memberWeights: { ...values.memberWeights, [id]: next } })} />
                      ) : (
                        <span className="flex items-center gap-1">
                          <span className="w-16 text-xs font-medium text-gray-700">{index === 0 ? 'Primary' : `Backup ${index}`}</span>
                          <button type="button" aria-label={`Move ${labels[id]} up`} disabled={disabled || index <= 0} onClick={(event) => { onChange(moveGuidedPolicyMember(values, members, id, -1)); keepFocusAfterMove(event.currentTarget); }} className="rounded-md border border-gray-200 p-1 text-gray-500 disabled:opacity-30"><ArrowUp aria-hidden="true" className="h-3.5 w-3.5" /></button>
                          <button type="button" aria-label={`Move ${labels[id]} down`} disabled={disabled || index === ids.length - 1} onClick={(event) => { onChange(moveGuidedPolicyMember(values, members, id, 1)); keepFocusAfterMove(event.currentTarget); }} className="rounded-md border border-gray-200 p-1 text-gray-500 disabled:opacity-30"><ArrowDown aria-hidden="true" className="h-3.5 w-3.5" /></button>
                        </span>
                      )}
                    </td>
                  ) : null}
                  <td className="px-4 py-2">
                    {!included ? <span className="text-xs text-gray-500">{member.enabled ? 'Excluded' : 'Paused in group'}</span> : draft == null ? <span className="text-xs text-gray-500">Decided per request</span> : (
                      <span className="block w-36">
                        <span className="flex justify-between text-xs"><strong className="tabular-nums text-gray-900">{draft}%</strong>{live != null ? <span className="text-gray-400">live {live}%</span> : null}</span>
                        <span aria-hidden="true" className="relative mt-1 block h-1.5 rounded-full bg-gray-100">
                          <span className="block h-full rounded-full bg-brand-primary" style={{ width: `${draft}%` }} />
                          {live != null ? <span className="absolute -top-0.5 h-2.5 w-0.5 bg-gray-500" style={{ left: `${live}%` }} /> : null}
                        </span>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 px-4 py-2.5 text-xs text-gray-600">
        <span className="flex items-center gap-2">
          <ToggleSwitch checked={values.memberSelection === 'inherit' && !selectorOwnsMembers} disabled={disabled || selectorOwnsMembers} activeColor="rgb(var(--brand-primary))" aria-label="Include new group members automatically"
            onCheckedChange={(on) => onChange(on
              ? { ...values, memberSelection: 'inherit', memberIds: members.filter((member) => member.enabled).map((member) => member.deployment_id) }
              : { ...values, memberSelection: 'explicit', memberIds: ids })} />
          Include new group members automatically
        </span>
        <span className="text-gray-500">{selectorOwnsMembers ? 'The model selector needs a fixed list.' : values.memberSelection === 'inherit' ? 'Models added later get traffic at once.' : 'Members added later stay out until you include them here.'}</span>
      </div>
    </NumberedCard>
  );
}

function secondsText(ms: string): string {
  if (!ms.trim()) return '';
  const value = Number(ms) / 1000;
  return Number.isFinite(value) ? String(Number(value.toFixed(3))) : ms;
}

function millisecondsFrom(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return '';
  const value = Number(trimmed);
  // Keep invalid text so the shared validation reports it.
  return Number.isFinite(value) && value > 0 ? String(Math.max(1, Math.round(value * 1000))) : trimmed;
}

export function TimeoutsCard({ values, disabled, changed, onChange }: CardProps) {
  const [timeout, setTimeoutText] = useState(secondsText(values.timeoutMs));
  const [syncedMs, setSyncedMs] = useState(values.timeoutMs);
  if (syncedMs !== values.timeoutMs) {
    setSyncedMs(values.timeoutMs);
    if (millisecondsFrom(timeout) !== values.timeoutMs) setTimeoutText(secondsText(values.timeoutMs));
  }
  const classes = values.retryableErrors.split(',').map((item) => item.trim()).filter(Boolean);
  const unknown = classes.filter((item) => !(RETRYABLE_ERROR_OPTIONS as readonly string[]).includes(item));
  const setClasses = (next: string[]) => onChange({ ...values, retryableErrors: next.join(',') });
  return (
    <NumberedCard step={3} title="Timeouts and retries" changed={changed} description="Retries can land on another deployment. The budget is shared by the whole request.">
      <div className="space-y-3 px-4 py-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-gray-700">
            <span>Request timeout</span>
            <span className="flex overflow-hidden rounded-lg border border-gray-200 focus-within:ring-2 focus-within:ring-brand-primary">
              <input className="control-field-inset min-w-0 flex-1 px-3 focus:outline-none disabled:bg-gray-50" inputMode="decimal" value={timeout} disabled={disabled} placeholder="Not set"
                onChange={(event) => { setTimeoutText(event.target.value); onChange({ ...values, timeoutMs: millisecondsFrom(event.target.value) }); }} />
              <span className="border-l border-gray-200 bg-gray-50 px-3 py-2 font-normal text-gray-500">seconds</span>
            </span>
            <span className="block font-normal text-gray-500">Includes every retry. Empty uses the deployment timeouts.</span>
          </label>
          <label className="block space-y-1 text-xs font-medium text-gray-700">
            <span>Retries</span>
            <span className="flex overflow-hidden rounded-lg border border-gray-200 focus-within:ring-2 focus-within:ring-brand-primary">
              <input className="control-field-inset min-w-0 flex-1 px-3 focus:outline-none disabled:bg-gray-50" inputMode="numeric" value={values.retryMaxAttempts} disabled={disabled} placeholder="Gateway default"
                onChange={(event) => onChange({ ...values, retryMaxAttempts: event.target.value.trim() })} />
              <span className="border-l border-gray-200 bg-gray-50 px-3 py-2 font-normal text-gray-500">extra</span>
            </span>
            <span className="block font-normal text-gray-500">0 means one attempt only.</span>
          </label>
        </div>
        <fieldset>
          <legend className="text-xs font-medium text-gray-700">Retry when the provider returns</legend>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {RETRYABLE_ERROR_OPTIONS.map((item) => {
              const on = classes.includes(item);
              return (
                <button key={item} type="button" aria-pressed={on} disabled={disabled} onClick={() => setClasses(on ? classes.filter((value) => value !== item) : [...classes, item])}
                  className={`control-sm inline-flex items-center gap-1 rounded-full border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${on ? 'border-brand-primary bg-brand-primary-soft text-brand-primary-ink' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}>
                  {on ? <Check aria-hidden="true" className="h-3 w-3" /> : null}{RETRY_CLASS_LABELS[item]}
                </button>
              );
            })}
            {unknown.map((item) => (
              <button key={item} type="button" disabled={disabled} onClick={() => setClasses(classes.filter((value) => value !== item))} aria-label={`Remove unsupported error class ${item}`}
                className="control-sm inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 text-red-700">
                <X aria-hidden="true" className="h-3 w-3" />{item} · not supported
              </button>
            ))}
          </div>
          <p className="mt-1.5 text-xs text-gray-500">{unknown.length > 0 ? 'Remove the unsupported class before you publish.' : 'None selected uses the gateway default.'}</p>
        </fieldset>
      </div>
    </NumberedCard>
  );
}
