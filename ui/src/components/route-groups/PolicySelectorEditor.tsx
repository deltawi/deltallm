import { useId } from 'react';
import Button from '../Button';
import type { PolicyGuidedValues, PolicyMemberOption } from '../../lib/routeGroups';
import { chooseSelector, eligibleClassifiers, validateGuidedSelector, type GuidedSelector } from '../../lib/routeGroupSelector';

const inputClass = 'w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-slate-50';

interface Props {
  values: PolicyGuidedValues;
  onChange: (next: PolicyGuidedValues) => void;
  members: PolicyMemberOption[];
  workloadMode: string;
}

export default function PolicySelectorEditor({ values, onChange, members, workloadMode }: Props) {
  const errorId = useId();
  const selector = values.selector;
  const enabled = selector.state === 'enabled';
  const available = eligibleClassifiers(members, values.memberIds);
  const error = validateGuidedSelector(selector, members, values.memberIds, workloadMode);
  const classifier = members.find((member) => member.deployment_id === selector.classifier);
  const update = (next: Partial<GuidedSelector>) => onChange({ ...values, selector: { ...selector, ...next } });
  const updateLane = (index: number, key: 'id' | 'rank' | 'description', value: string) => {
    const previousId = selector.lanes[index].id;
    update({
      lanes: selector.lanes.map((lane, i) => i === index ? { ...lane, [key]: value } : lane),
      ...(key === 'id' ? {
        defaultLane: selector.defaultLane === previousId ? value : selector.defaultLane,
        assignments: Object.fromEntries(Object.entries(selector.assignments).map(([id, lane]) => [id, lane === previousId ? value : lane])),
      } : {}),
    });
  };

  return (
    <fieldset className="space-y-3 rounded-xl border border-slate-200 p-4" aria-describedby={error ? errorId : undefined}>
      <legend className="px-1 text-sm font-semibold text-slate-900">Model selector (optional)</legend>
      <p className="text-sm text-slate-600">Use a small model to send routine requests to economy models and harder requests to quality models.</p>
      {selector.state === 'unsupported' ? <p role="alert" id={errorId}>{error}</p> : <>
        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-700">Selector model</span>
          <select className={inputClass} value={enabled ? selector.classifier : ''}
            disabled={workloadMode !== 'chat'}
            onChange={(event) => onChange(chooseSelector(values, event.target.value))}>
            <option value="">None — use the routing strategy only</option>
            {enabled && !available.some((member) => member.deployment_id === selector.classifier)
              && <option value={selector.classifier}>{selector.classifier || 'Choose a selector'} (unavailable)</option>}
            {available.map((member) => <option key={member.deployment_id} value={member.deployment_id}>
              {member.model_name || member.deployment_id}{member.provider ? ` · ${member.provider}` : ''} · {member.deployment_id}
            </option>)}
          </select>
        </label>
        {workloadMode !== 'chat' ? <p className="text-xs text-slate-500">Selectors support chat groups only.</p>
          : available.length === 0 && <p className="text-xs text-slate-500">Add and select enabled chat deployments in this group first. Unknown deployment modes are not eligible.</p>}
        {enabled && <>
          <div className="rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-900">
            Each uncached request sends bounded prompt text to {classifier?.model_name || selector.classifier}
            {classifier?.provider ? ` (${classifier.provider})` : ''}. This adds one provider call and latency;
            customers pay its actual cost, including when the answer fails. Check that this provider is allowed to receive the request data.
            Publishing makes it active immediately. Cached answers skip selection.
          </div>
          <p className="text-xs text-slate-500">Review the suggested assignments below. Higher ranks mean more capability; routing can move up, never down. Context checks require known model capacities.</p>
          <div className="space-y-2">
            {values.memberIds.map((id) => <label key={id} className="flex flex-col gap-1 md:flex-row md:items-center md:gap-3">
              <span className="min-w-0 flex-1 break-all text-sm text-slate-700">{members.find((member) => member.deployment_id === id)?.model_name || id}</span>
              <select aria-label={`Answer lane for ${id}`} className={`${inputClass} md:w-44`} value={selector.assignments[id] || ''}
                onChange={(event) => update({ assignments: { ...selector.assignments, [id]: event.target.value } })}>
                <option value="">Choose lane</option>
                {selector.lanes.map((lane, index) => <option key={index} value={lane.id}>{lane.id || 'Unnamed lane'} (rank {lane.rank})</option>)}
              </select>
            </label>)}
          </div>
          <details className="rounded-lg border border-slate-200 p-3">
            <summary className="cursor-pointer text-sm font-medium">Advanced selector settings</summary>
            <div className="mt-3 space-y-3">
              <label className="block space-y-1"><span className="text-xs font-medium">Safe-default lane</span>
                <select className={inputClass} value={selector.defaultLane} onChange={(event) => update({ defaultLane: event.target.value })}>
                  {selector.lanes.map((lane, index) => <option key={index} value={lane.id}>{lane.id}</option>)}
                </select>
                <span className="block text-xs text-slate-500">Used if selection fails. Keep the highest-capability lane unless you deliberately accept a lower fallback.</span>
              </label>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="space-y-1"><span className="text-xs font-medium">Selector timeout (ms)</span>
                  <input className={inputClass} type="number" min={100} max={5000} step={1} value={selector.timeoutMs} onChange={(event) => update({ timeoutMs: event.target.value })} />
                </label>
                <label className="space-y-1"><span className="text-xs font-medium">Input limit (characters)</span>
                  <input className={inputClass} type="number" min={256} max={32768} step={1} value={selector.maxInputChars} onChange={(event) => update({ maxInputChars: event.target.value })} />
                </label>
              </div>
              {selector.lanes.map((lane, index) => <fieldset key={index} className="space-y-2 rounded-lg border border-slate-200 p-3">
                <legend className="px-1 text-xs font-medium">Lane {index + 1}</legend>
                <div className="grid gap-2 md:grid-cols-2">
                  <label className="space-y-1"><span className="text-xs">Lane ID</span><input className={inputClass} maxLength={32} value={lane.id} onChange={(event) => updateLane(index, 'id', event.target.value)} /></label>
                  <label className="space-y-1"><span className="text-xs">Capability rank</span><input className={inputClass} type="number" min={0} max={7} step={1} value={lane.rank} onChange={(event) => updateLane(index, 'rank', event.target.value)} /></label>
                </div>
                <label className="block space-y-1"><span className="text-xs">When to use this lane</span><textarea className={inputClass} maxLength={512} rows={2} value={lane.description} onChange={(event) => updateLane(index, 'description', event.target.value)} /></label>
                <Button size="sm" variant="secondary" disabled={selector.lanes.length <= 2}
                  onClick={() => update({ lanes: selector.lanes.filter((_, i) => i !== index).map((item, rank) => ({ ...item, rank: String(rank) })) })}>
                  Remove lane {lane.id}
                </Button>
              </fieldset>)}
              <Button size="sm" variant="secondary" disabled={selector.lanes.length >= 8}
                onClick={() => update({ lanes: [...selector.lanes, { id: '', rank: String(selector.lanes.length), description: '' }] })}>Add lane</Button>
            </div>
          </details>
        </>}
        {error && <p role="alert" id={errorId} className="text-sm text-red-700">{error}</p>}
      </>}
    </fieldset>
  );
}
