import type { ComponentProps, ElementType } from 'react';
import { AlertTriangle, Brain, CheckCircle2, FileText, GitBranch, HeartPulse, Layers, Mic, Shuffle, Users, XCircle, Zap } from 'lucide-react';
import type { RouteGroup, RoutePolicy } from '../../lib/api';
import { ROUTE_GROUP_MODE_COLORS, effectiveRouteGroupStrategy, routeGroupStrategyLabel } from '../../lib/routeGroups';
import { DetailMetricCard } from '../admin/shells';
import ManagedAssetAccessSummary from '../ManagedAssetAccessSummary';

const MODE_ICONS: Record<string, ElementType> = { chat: Brain, embedding: Zap, audio_speech: Mic, audio_transcription: Mic, image_generation: Layers, rerank: GitBranch };

function routingSummary(group: RouteGroup, publishedPolicy: RoutePolicy | null) {
  const strategy = effectiveRouteGroupStrategy(publishedPolicy?.policy_json ?? null, group.routing_strategy);
  const label = strategy === 'priority-based-routing' ? 'Primary & fallback' : strategy ? routeGroupStrategyLabel(strategy) : 'Group default';
  return { label, icon: strategy === 'simple-shuffle' ? Shuffle : GitBranch };
}

interface BadgesProps {
  group: RouteGroup;
  publishedPolicy: RoutePolicy | null;
  teamOptions: ComponentProps<typeof ManagedAssetAccessSummary>['teamOptions'];
  organizationOptions: ComponentProps<typeof ManagedAssetAccessSummary>['organizationOptions'];
}

// Mode, live state, routing and access badges shown next to the route group's title.
export function RouteGroupBadges({ group, publishedPolicy, teamOptions, organizationOptions }: BadgesProps) {
  const ModeIcon = MODE_ICONS[group.mode] || Layers;
  const modeColor = ROUTE_GROUP_MODE_COLORS[group.mode] || 'bg-gray-100 text-gray-700';
  const routing = routingSummary(group, publishedPolicy);
  return (
    <>
      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${modeColor}`}>
        <ModeIcon className="h-3.5 w-3.5" />
        {group.mode.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
      </span>
      <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${group.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
        {group.enabled ? <><CheckCircle2 className="h-3.5 w-3.5" /> Live</> : <><XCircle className="h-3.5 w-3.5" /> Off</>}
      </span>
      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-700">
        <routing.icon className="h-3.5 w-3.5" />
        {publishedPolicy ? `Published v${publishedPolicy.version} · ${routing.label}` : `${routing.label} routing`}
      </span>
      {group.access ? (
        <ManagedAssetAccessSummary
          access={group.access}
          teamOptions={teamOptions}
          organizationOptions={organizationOptions}
        />
      ) : null}
    </>
  );
}

interface StatsProps {
  group: RouteGroup;
  publishedPolicy: RoutePolicy | null;
  members: number;
  healthyMembers: number;
  unknownHealth: number;
  missingMembers: number;
  promptKey: string | null;
}

// Key figures for the route group header.
export function RouteGroupStats({ group, publishedPolicy, members, healthyMembers, unknownHealth, missingMembers, promptKey }: StatsProps) {
  const routing = routingSummary(group, publishedPolicy);
  return (
    <>
      <DetailMetricCard icon={Users} label="Members" value={String(members)} tone="blue" />
      <DetailMetricCard
        icon={HeartPulse}
        label="Healthy"
        value={members > 0 ? `${healthyMembers}/${members}` : '—'}
        sub={unknownHealth ? `${unknownHealth} unknown` : undefined}
        tone="green"
      />
      <DetailMetricCard icon={routing.icon} label="Policy" value={publishedPolicy ? `v${publishedPolicy.version} published` : routing.label} tone="violet" />
      <DetailMetricCard icon={FileText} label="Prompt" value={promptKey ? promptKey : 'None bound'} tone="gray" />
      {missingMembers > 0 && (
        <DetailMetricCard icon={AlertTriangle} label="Registry gaps" value={`${missingMembers} missing`} tone="amber" />
      )}
    </>
  );
}
