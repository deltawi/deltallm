import { useAuth } from '../../lib/auth';
import { resolveUiAccess } from '../../lib/authorization';
import { ApiError } from '../../lib/api';
import { useSelectorOptions } from '../../lib/useSelectorOptions';
import type { PolicyGuidedValues, PolicyMemberOption } from '../../lib/routeGroups';
import Button from '../Button';
import PolicySelectorEditor from './PolicySelectorEditor';

interface Props {
  routeGroupId: string;
  values: PolicyGuidedValues;
  onChange: (next: PolicyGuidedValues) => void;
  members: PolicyMemberOption[];
  workloadMode: string;
}

export default function RouteGroupSelectorEditor(props: Props) {
  const { authMode, session } = useAuth();
  const access = resolveUiAccess(authMode, session);
  const scope = JSON.stringify([authMode, session?.account_id, session?.effective_permissions, access, props.routeGroupId]);
  return <SelectorLookup key={scope} {...props} scope={scope} allowed={access.route_groups && props.workloadMode === 'chat'} />;
}

function SelectorLookup({ scope, allowed, ...props }: Props & { scope: string; allowed: boolean }) {
  const lookup = useSelectorOptions(props.routeGroupId, scope, props.values.selector.classifier, allowed);
  const page = lookup.data;
  const selected = page?.selected?.deployment_id === props.values.selector.classifier ? page.selected : null;
  const options = [...(page?.data ?? [])];
  if (selected && !options.some((item) => item.deployment_id === selected.deployment_id)) options.push(selected);
  const unavailable = !lookup.loading && !lookup.stale && page && props.values.selector.state === 'enabled'
    && !selected;
  const error = lookup.error instanceof ApiError && lookup.error.status === 403
    ? 'You do not have permission to view selector deployments.'
    : lookup.error ? 'Selector deployments could not be refreshed. Your selection has not changed.' : null;
  return <PolicySelectorEditor {...props} selectorOptions={options} lookupControls={allowed ? <div className="space-y-2">
    <label className="block space-y-1 text-sm">
      <span>Find a selector deployment</span>
      <input type="search" maxLength={128} value={lookup.search} onChange={(event) => lookup.setSearch(event.target.value)}
        className="w-full rounded-lg border border-slate-200 px-3 py-2" placeholder="Search by name, provider or deployment ID" />
    </label>
    {lookup.loading && <p role="status" className="text-xs text-slate-500">Loading selector deployments…</p>}
    {lookup.stale && <p className="text-xs text-slate-500">Showing the previous results while this search refreshes.</p>}
    {page && !lookup.loading && !lookup.stale && !lookup.error && page.data.length === 0 && <p role="status" className="text-xs text-slate-500">No chat deployments match this search.</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error} <Button size="sm" variant="secondary" onClick={() => void lookup.refresh()}>Retry</Button></p>}
    {unavailable && <p role="alert" className="text-sm text-red-700">The selected deployment is no longer available. Choose another selector or None.</p>}
    {selected?.unavailable_reason && <p role="status" className="text-sm text-amber-800">{selected.unavailable_reason}</p>}
    {(lookup.offset > 0 || page?.has_more) && <div className="flex gap-2">
      <Button size="sm" variant="secondary" disabled={lookup.offset === 0 || lookup.loading || lookup.stale} onClick={lookup.previous}>Previous selectors</Button>
      <Button size="sm" variant="secondary" disabled={!page?.has_more || lookup.loading || lookup.stale} onClick={lookup.next}>More selectors</Button>
    </div>}
  </div> : undefined} />;
}
