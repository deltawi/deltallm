import type { ReactNode } from 'react';
import { ArrowDown, ArrowRight, ArrowUp, Check, GitBranch, ListOrdered, Shuffle, X, type LucideIcon } from 'lucide-react';
import {
  addBackup,
  chooseSimpleRouting,
  describeSimpleRouting,
  memberLabels,
  moveBackup,
  percentagesFromWeights,
  removeBackup,
  routableMemberIds,
  SIMPLE_CHOICES,
  splitEvenly,
  splitTotal,
  type RoutingMember,
  type SimpleChoice,
  type SimpleRouting,
  type TextSegment,
} from '../../lib/routeGroupRouting';
import { focusMovedRow, keepFocusAfterMove } from '../../lib/keepFocusAfterMove';
import { shareColor } from '../../lib/routeGroupSummary';
import { ModelCell, ShareBar } from './RouteGroupModelCell';

const CHOICE_ICONS: Record<SimpleChoice, LucideIcon> = { even: Shuffle, backup: ListOrdered, split: GitBranch };
const ORDINALS = ['1st', '2nd', '3rd'];

export function Sentence({ segments }: { segments: TextSegment[] }) {
  return <>{segments.map((segment, index) => (typeof segment === 'string' ? segment : <strong key={index} className="font-semibold text-gray-900">{segment.strong}</strong>))}</>;
}

function percentValue(routing: SimpleRouting, id: string): number {
  const value = Number(routing.percents[id] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function Stepper({ id, label, value, disabled, onChange }: { id: string; label: string; value: string; disabled: boolean; onChange: (next: string) => void }) {
  const step = (delta: number) => onChange(String(Math.min(100, Math.max(0, (Number(value) || 0) + delta))));
  const buttonClass = 'px-2 text-gray-500 hover:bg-gray-50 hover:text-gray-900 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary';
  return (
    <span className="inline-flex h-7 items-stretch overflow-hidden rounded-md border border-gray-200 bg-white text-sm">
      <button type="button" className={buttonClass} disabled={disabled || Number(value) <= 0} aria-label={`Lower ${label} by 5%`} onClick={() => step(-5)}>−</button>
      <label className="flex items-center border-x border-gray-200 pr-1.5">
        <span className="sr-only">Share for {label}</span>
        <input id={`share-${id}`} inputMode="numeric" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value.replace(/[^\d]/g, '').slice(0, 3))}
          className="w-9 bg-transparent text-right tabular-nums focus:outline-none" />
        <span aria-hidden="true" className="text-xs text-gray-400">%</span>
      </label>
      <button type="button" className={buttonClass} disabled={disabled || Number(value) >= 100} aria-label={`Raise ${label} by 5%`} onClick={() => step(5)}>+</button>
    </span>
  );
}

function MemberList({ children, footer }: { children: ReactNode; footer: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-gray-200">
      <ul className="divide-y divide-gray-100">{children}</ul>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-600">{footer}</div>
    </div>
  );
}

function IconButton({ label, disabled, onClick, children }: {
  label: string; disabled: boolean; onClick: (button: HTMLButtonElement) => void; children: ReactNode;
}) {
  return (
    <button type="button" aria-label={label} title={label} disabled={disabled} onClick={(event) => onClick(event.currentTarget)}
      className="rounded-md border border-gray-200 p-1 text-gray-500 hover:bg-gray-50 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary">
      <span aria-hidden="true">{children}</span>
    </button>
  );
}

function LinkButton({ children, onClick }: { children: ReactNode; onClick: (button: HTMLButtonElement) => void }) {
  return <button type="button" onClick={(event) => onClick(event.currentTarget)} className="font-medium text-brand-primary-ink underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary">{children}</button>;
}

interface SimpleRoutingChoicesProps {
  routing: SimpleRouting;
  members: RoutingMember[];
  disabled: boolean;
  onChange: (next: SimpleRouting) => void;
  onAddModel?: () => void;
}

// The three plain choices, a sentence that says what happens, and the per-model controls of the chosen one.
export default function SimpleRoutingChoices({ routing, members, disabled, onChange, onAddModel }: SimpleRoutingChoicesProps) {
  const labels = memberLabels(members);
  const routable = routableMemberIds(members);
  const byId = new Map(members.map((member) => [member.deployment_id, member]));
  const colorOf = (id: string) => shareColor(Math.max(0, routable.indexOf(id)));
  const total = splitTotal(routing, members);
  const evenShares = percentagesFromWeights(Object.fromEntries(routable.map((id) => [id, 1])));
  const barSegments = routable.map((id) => ({
    id, label: labels[id], color: colorOf(id),
    value: routing.choice === 'split' ? percentValue(routing, id) : evenShares[id] ?? 0,
  }));
  const unhealthy = routable.filter((id) => byId.get(id)?.healthy === false
    && (routing.choice !== 'split' || percentValue(routing, id) > 0)
    && (routing.choice !== 'backup' || routing.order.includes(id)));
  const addModel = onAddModel ? <LinkButton onClick={onAddModel}>+ Add a model</LinkButton> : null;

  return (
    <div className="space-y-3">
      <div role="radiogroup" aria-label="How to share requests" className="grid gap-2 sm:grid-cols-3">
        {SIMPLE_CHOICES.map((choice) => {
          const Icon = CHOICE_ICONS[choice.id];
          const selected = routing.choice === choice.id;
          return (
            <button
              key={choice.id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => onChange(chooseSimpleRouting(routing, choice.id, members))}
              className={`flex items-start gap-2.5 rounded-lg border p-2.5 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:cursor-not-allowed ${selected ? 'border-brand-primary bg-brand-primary-soft ring-1 ring-brand-primary' : 'border-gray-200 bg-white hover:bg-gray-50'}`}
            >
              <span aria-hidden="true" className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500"><Icon className="h-3.5 w-3.5" /></span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-sm font-medium text-gray-900">
                  {choice.label}
                  {choice.id === 'even' ? <span className="rounded-full border border-emerald-200 bg-emerald-50 px-1.5 text-[10px] font-semibold text-emerald-700">Default</span> : null}
                </span>
                <span className="block text-xs text-gray-500">{choice.hint}</span>
              </span>
              <span aria-hidden="true" className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ${selected ? 'border-brand-primary bg-brand-primary' : 'border-gray-300 bg-white'}`}>
                {selected ? <span className="h-1.5 w-1.5 rounded-full bg-white" /> : null}
              </span>
            </button>
          );
        })}
      </div>

      <div className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5">
        <p className="text-sm leading-6 text-gray-700"><Sentence segments={describeSimpleRouting(routing, members)} /></p>
        {routing.choice === 'backup' ? (
          <ol aria-label="Order of models" className="flex flex-wrap items-center gap-1.5 text-xs">
            {routing.order.map((id, index) => (
              <li key={id} className="flex items-center gap-1.5">
                {index > 0 ? <ArrowRight aria-hidden="true" className="h-3 w-3 text-gray-400" /> : null}
                <span className={`rounded-md border px-2 py-0.5 ${index === 0 ? 'border-brand-primary text-brand-primary-ink' : 'border-gray-200 bg-white text-gray-700'}`}>
                  {ORDINALS[index] ?? `${index + 1}th`} · {labels[id] ?? id}
                </span>
              </li>
            ))}
          </ol>
        ) : <ShareBar segments={barSegments} label="Planned traffic" />}
      </div>

      {routing.choice === 'split' ? (
        <MemberList footer={<>
          <span className={total === 100 ? 'text-emerald-700' : 'text-amber-700'}>
            Total <strong className="tabular-nums">{total}%</strong>{total === 100 ? <Check aria-hidden="true" className="ml-1 inline h-3 w-3" /> : ' (must be 100%)'}
          </span>
          {!disabled ? <span className="flex gap-3"><LinkButton onClick={() => onChange({ ...routing, percents: splitEvenly(routable) })}>Split evenly</LinkButton>{addModel}</span> : null}
        </>}>
          {routable.map((id) => (
            <li key={id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2">
              <ModelCell name={labels[id]} provider={byId.get(id)?.provider} />
              <span className="flex items-center gap-3">
                {byId.get(id)?.healthy === false ? <span className="text-xs font-medium text-red-700">Unhealthy</span> : null}
                <Stepper id={id} label={labels[id]} value={routing.percents[id] ?? '0'} disabled={disabled}
                  onChange={(next) => onChange({ ...routing, percents: { ...routing.percents, [id]: next } })} />
                <span aria-hidden="true" className="hidden h-1.5 w-20 overflow-hidden rounded-full bg-gray-100 sm:block">
                  <span className="block h-full rounded-full" style={{ width: `${Math.min(100, percentValue(routing, id))}%`, background: colorOf(id) }} />
                </span>
              </span>
            </li>
          ))}
        </MemberList>
      ) : routing.choice === 'backup' ? (
        <MemberList footer={<><span>Models you add later are not used until you add them here.</span>{!disabled ? addModel : null}</>}>
          {routing.order.map((id, index) => (
            <li key={id} data-row-id={id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2">
              <ModelCell name={labels[id] ?? id} provider={byId.get(id)?.provider} />
              <span className="flex items-center gap-1.5">
                {byId.get(id)?.healthy === false ? <span className="text-xs font-medium text-red-700">Unhealthy</span> : null}
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${index === 0 ? 'bg-brand-primary-soft text-brand-primary-ink' : 'bg-gray-100 text-gray-600'}`}>{index === 0 ? 'Primary' : `Backup ${index}`}</span>
                <IconButton label={`Move ${labels[id] ?? id} up`} disabled={disabled || index === 0} onClick={(button) => { onChange(moveBackup(routing, id, -1)); keepFocusAfterMove(button); }}><ArrowUp className="h-3.5 w-3.5" /></IconButton>
                <IconButton label={`Move ${labels[id] ?? id} down`} disabled={disabled || index === routing.order.length - 1} onClick={(button) => { onChange(moveBackup(routing, id, 1)); keepFocusAfterMove(button); }}><ArrowDown className="h-3.5 w-3.5" /></IconButton>
                <IconButton label={`Stop using ${labels[id] ?? id}`} disabled={disabled || routing.order.length <= 1} onClick={(button) => { const list = button.closest('ul'); onChange(removeBackup(routing, id)); focusMovedRow(list, id); }}><X className="h-3.5 w-3.5" /></IconButton>
              </span>
            </li>
          ))}
          {routable.filter((id) => !routing.order.includes(id)).map((id) => (
            <li key={id} data-row-id={id} className="flex flex-wrap items-center justify-between gap-3 bg-gray-50 px-3 py-2">
              <ModelCell name={labels[id]} provider={byId.get(id)?.provider} muted />
              <span className="flex items-center gap-2 text-xs text-gray-500">
                Not used
                {!disabled ? <LinkButton onClick={(button) => { const list = button.closest('ul'); onChange(addBackup(routing, id)); focusMovedRow(list, id); }}>Add as backup</LinkButton> : null}
              </span>
            </li>
          ))}
        </MemberList>
      ) : (
        <MemberList footer={<><span>New models you add get an equal share.</span>{!disabled ? addModel : null}</>}>
          {routable.map((id) => (
            <li key={id} className="flex items-center justify-between gap-3 px-3 py-2">
              <ModelCell name={labels[id]} provider={byId.get(id)?.provider} />
              <span className="text-xs tabular-nums text-gray-600">{byId.get(id)?.healthy === false ? <span className="font-medium text-red-700">Unhealthy</span> : `about ${evenShares[id] ?? 0}%`}</span>
            </li>
          ))}
        </MemberList>
      )}

      {unhealthy.map((id) => (
        <p key={id} role="status" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {labels[id]} is unhealthy right now. It gets no traffic until it recovers{routing.choice === 'backup' && routing.order[0] === id && routing.order.length > 1 ? `, so requests go to ${labels[routing.order[1]]}` : ''}.
        </p>
      ))}
    </div>
  );
}
