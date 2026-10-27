import type { RouteGroup, RouteGroupMemberDetail, RoutePolicy } from '../../lib/api';
import { routableMemberIds, routingLabel } from '../../lib/routeGroupRouting';
import { modelsSummary, workloadLabel } from '../../lib/routeGroupSummary';
import { MetaCopyValue, MetaLink, MetaSeparator as Separator, MetaState } from '../admin/shells';

export type RouteGroupTabId = 'models' | 'routing' | 'prompt' | 'settings';

export function RouteGroupModeBadge({ mode }: { mode: string }) {
  return (
    <span className="rounded-md bg-brand-primary-soft px-1.5 py-0.5 text-[11px] font-semibold text-brand-primary-ink">
      {workloadLabel(mode)}
    </span>
  );
}

interface RouteGroupMetaProps {
  group: RouteGroup;
  members: RouteGroupMemberDetail[];
  policy: RoutePolicy | null;
  promptKey: string | null;
  sharing: string | null;
  onOpenTab: (tab: RouteGroupTabId) => void;
}

// One line under the title: state, key, models, routing, prompt and sharing. Each part opens the tab that owns it.
export default function RouteGroupMeta({ group, members, policy, promptKey, sharing, onOpenTab }: RouteGroupMetaProps) {
  const models = modelsSummary(members);
  const routing = routingLabel(policy?.policy_json ?? null, group.routing_strategy, routableMemberIds(members).length);
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
      <MetaState tone={group.enabled ? 'good' : 'off'}>{group.enabled ? 'Live' : 'Off'}</MetaState>
      <Separator />
      <MetaCopyValue value={group.group_key} label="group key" />
      <Separator />
      <MetaLink onClick={() => onOpenTab('models')} warn={models.warn} label={`${models.text}. Open Models`}>{models.text}</MetaLink>
      <Separator />
      <MetaLink onClick={() => onOpenTab('routing')} label={`Routing: ${routing}. Open Routing`}>{routing}</MetaLink>
      {promptKey ? (
        <>
          <Separator />
          <MetaLink onClick={() => onOpenTab('prompt')} label={`Prompt: ${promptKey}. Open Prompt`}>Prompt: {promptKey}</MetaLink>
        </>
      ) : null}
      {sharing ? (
        <>
          <Separator />
          <MetaLink onClick={() => onOpenTab('settings')} label={`${sharing}. Open Settings`}>{sharing}</MetaLink>
        </>
      ) : null}
    </div>
  );
}
