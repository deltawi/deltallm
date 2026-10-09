import { ArrowDown, ArrowUp, Check } from 'lucide-react';
import { configuredWeightShares, moveGuidedPolicyMember, orderedGuidedMemberIds, type PolicyGuidedValues, type PolicyMemberOption } from '../../lib/routeGroups';

export default function PolicyMemberPicker({ values, members, onChange }: { values: PolicyGuidedValues; members: PolicyMemberOption[]; onChange: (next: PolicyGuidedValues) => void }) {
  const ids = orderedGuidedMemberIds(values, members);
  const enabled = members.filter((member) => member.enabled).map((member) => member.deployment_id);
  const selected = ids.flatMap((id) => { const member = members.find((item) => item.deployment_id === id); return member ? [member] : []; });
  const ordered = [...selected, ...members.filter((member) => !ids.includes(member.deployment_id))];
  const weighted = values.strategy === 'weighted';
  const priority = values.strategy === 'priority-based-routing';
  const shares = weighted ? configuredWeightShares(values, members) : null;
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-gray-800">Target deployments</h3>
      <div className="flex rounded-lg border border-gray-200 bg-gray-50 p-1">
        <button type="button" aria-pressed={values.memberSelection === 'inherit'} onClick={() => onChange({ ...values, memberSelection: 'inherit', memberIds: enabled })} className={`rounded-md px-2.5 py-1.5 text-xs font-medium ${values.memberSelection === 'inherit' ? 'bg-white text-brand-primary-ink shadow-sm' : 'text-gray-500'}`}>Inherit enabled</button>
        <button type="button" aria-pressed={values.memberSelection === 'explicit'} onClick={() => onChange({ ...values, memberSelection: 'explicit', memberIds: ids })} className={`rounded-md px-2.5 py-1.5 text-xs font-medium ${values.memberSelection === 'explicit' ? 'bg-white text-brand-primary-ink shadow-sm' : 'text-gray-500'}`}>Choose subset</button>
      </div>
    </div>
    <p className="text-xs leading-5 text-gray-500">{values.memberSelection === 'inherit' ? 'Uses enabled group members. A checkbox, weight, or order change creates an explicit list.' : 'Only selected deployments are used. New group members are excluded until selected.'}{priority && ' Use the arrows to set the primary and fallback order.'}</p>
    {members.length === 0 ? <p className="rounded-lg border border-dashed border-gray-200 p-5 text-center text-sm text-gray-500">Add group members in the Models tab first.</p> : <div className="overflow-hidden rounded-lg border border-gray-200">
      {ordered.map((member) => {
        const id = member.deployment_id;
        const included = ids.includes(id) && member.enabled;
        const index = ids.indexOf(id);
        return <div key={id} className={`flex flex-wrap items-center gap-3 border-b border-gray-100 p-3 last:border-0 ${included ? 'bg-white' : 'bg-gray-50 text-gray-500'}`}>
          <button type="button" role="checkbox" aria-label={`Include ${id}`} aria-checked={included} disabled={!member.enabled} onClick={() => onChange({ ...values, memberSelection: 'explicit', memberIds: included ? ids.filter((item) => item !== id) : [...ids, id] })} className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${included ? 'border-brand-primary bg-brand-primary text-brand-on-primary' : 'border-gray-300 bg-white'} disabled:cursor-not-allowed`}>
            {included && <Check className="h-3.5 w-3.5" />}
          </button>
          <div className="min-w-0 flex-1 basis-32">
            <p className="break-all font-mono text-sm text-gray-900">{id}</p>
            <p className="mt-0.5 text-xs text-gray-500">{member.model_name || member.provider || 'Deployment'}{!member.enabled ? ' · Disabled in group' : ''}</p>
          </div>
          {weighted && included && <label className="flex items-center gap-2 text-xs text-gray-500">Weight
            <input type="number" min="1" step="1" aria-label={`Weight for ${id}`} value={values.memberWeights[id] || ''} placeholder={member.weight == null ? 'inherit' : String(member.weight)} onChange={(event) => onChange({ ...values, memberSelection: 'explicit', memberIds: ids, memberWeights: { ...values.memberWeights, [id]: event.target.value } })} className="w-20 rounded-md border border-gray-200 px-2 py-1.5 text-right text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary" />
            <span className="w-12 text-right font-medium text-brand-primary-ink">{shares ? `${Math.round(shares[id])}%` : '—'}</span>
          </label>}
          {priority && included && <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-brand-primary-ink">{values.memberSelection === 'inherit' ? member.priority == null ? 'Inherited priority' : `Priority ${member.priority}` : index === 0 ? 'Primary' : `Fallback ${index}`}</span>
            <button type="button" aria-label={`Move ${id} earlier`} disabled={index <= 0} onClick={() => onChange(moveGuidedPolicyMember(values, members, id, -1))} className="rounded-md border border-gray-200 p-1.5 text-gray-500 hover:bg-brand-primary-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:opacity-30"><ArrowUp className="h-4 w-4" /></button>
            <button type="button" aria-label={`Move ${id} later`} disabled={index === ids.length - 1} onClick={() => onChange(moveGuidedPolicyMember(values, members, id, 1))} className="rounded-md border border-gray-200 p-1.5 text-gray-500 hover:bg-brand-primary-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:opacity-30"><ArrowDown className="h-4 w-4" /></button>
          </div>}
          {!included && <span className="text-xs">Excluded</span>}
        </div>;
      })}
    </div>}
    {weighted && <p className="text-xs text-gray-500">Configured shares use known weights. Runtime eligibility can change the traffic split.</p>}
    {priority && values.memberSelection === 'inherit' && <p className="text-xs text-gray-500">Unspecified priorities follow deployment defaults. Equal priorities use random selection. Use the arrows to set a fixed order.</p>}
  </div>;
}
