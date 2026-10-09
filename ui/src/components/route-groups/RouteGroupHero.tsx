import type { ComponentProps, ElementType } from 'react';
import { Brain, CheckCircle2, GitBranch, Layers, Mic, Pencil, Shuffle, Trash2, XCircle, Zap } from 'lucide-react';
import type { RouteGroup, RoutePolicy } from '../../lib/api';
import { ROUTE_GROUP_MODE_COLORS, effectiveRouteGroupStrategy, routeGroupStrategyLabel } from '../../lib/routeGroups';
import { InlineStat } from '../admin/shells';
import ManagedAssetAccessSummary from '../ManagedAssetAccessSummary';

const MODE_ICONS: Record<string, ElementType> = { chat: Brain, embedding: Zap, audio_speech: Mic, audio_transcription: Mic, image_generation: Layers, rerank: GitBranch };
interface Props {
  group: RouteGroup;
  publishedPolicy: RoutePolicy | null;
  members: number;
  healthyMembers: number;
  unknownHealth: number;
  missingMembers: number;
  promptKey: string | null;
  teamOptions: ComponentProps<typeof ManagedAssetAccessSummary>['teamOptions'];
  organizationOptions: ComponentProps<typeof ManagedAssetAccessSummary>['organizationOptions'];
  canWrite: boolean;
  canDelete: boolean;
  onEdit: () => void;
  onDelete: () => void;
}

export default function RouteGroupHero({ group, publishedPolicy, members, healthyMembers, unknownHealth, missingMembers, promptKey, teamOptions, organizationOptions, canWrite, canDelete, onEdit, onDelete }: Props) {
  const ModeIcon = MODE_ICONS[group.mode] || Layers;
  const modeColor = ROUTE_GROUP_MODE_COLORS[group.mode] || 'bg-gray-100 text-gray-700';
  const strategy = effectiveRouteGroupStrategy(publishedPolicy?.policy_json ?? null, group.routing_strategy);
  const routingLabel = strategy === 'priority-based-routing' ? 'Primary & fallback' : strategy ? routeGroupStrategyLabel(strategy) : 'Group default';
  const RoutingIcon = strategy === 'simple-shuffle' ? Shuffle : GitBranch;
  return (
        <div className="relative overflow-hidden border-b border-gray-200 bg-white">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-brand-primary-soft via-white to-gray-50 opacity-70" />
          <div className="pointer-events-none absolute right-0 top-0 h-40 w-40 rounded-full bg-brand-secondary-soft blur-3xl" />

          <div className="relative px-6 pb-5 pt-6">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${modeColor}`}>
                <ModeIcon className="h-3.5 w-3.5" />
                {group.mode.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
              </span>
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${group.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'}`}>
                {group.enabled ? <><CheckCircle2 className="h-3.5 w-3.5" /> Live</> : <><XCircle className="h-3.5 w-3.5" /> Off</>}
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">
                <RoutingIcon className="h-3.5 w-3.5" />
                {publishedPolicy ? `Published v${publishedPolicy.version} · ${routingLabel}` : `${routingLabel} routing`}
              </span>
              {group.access ? (
                <ManagedAssetAccessSummary
                  access={group.access}
                  teamOptions={teamOptions}
                  organizationOptions={organizationOptions}
                />
              ) : null}
            </div>

            <div className="flex items-start justify-between gap-4">
              <div>
                <h1 className="text-2xl font-bold text-gray-900">{group.name || group.group_key}</h1>
                <p className="mt-0.5 text-sm text-gray-500">
                  Group key:{' '}
                  <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-700">{group.group_key}</code>
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                {canWrite ? (
                  <button
                    onClick={onEdit}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 shadow-sm hover:bg-gray-50"
                  >
                    <Pencil className="h-4 w-4" /> Edit
                  </button>
                ) : null}
                {canDelete ? (
                  <button
                    onClick={onDelete}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-500 shadow-sm hover:bg-red-50"
                    title="Delete group"
                    aria-label="Delete group"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                ) : null}
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-6 divide-x divide-gray-100">
              <InlineStat label="Members" value={String(members)} />
              <div className="pl-6">
                <InlineStat label="Healthy" value={members > 0 ? `${healthyMembers}/${members}${unknownHealth ? ` · ${unknownHealth} unknown` : ''}` : '—'} />
              </div>
              <div className="pl-6">
                <InlineStat label="Policy" value={publishedPolicy ? `v${publishedPolicy.version} published` : routingLabel} />
              </div>
              <div className="pl-6">
                <InlineStat label="Prompt" value={promptKey ? promptKey : 'None bound'} />
              </div>
              {missingMembers > 0 && (
                <div className="pl-6">
                  <InlineStat label="Registry Gaps" value={`${missingMembers} missing`} />
                </div>
              )}
            </div>
          </div>
        </div>
  );
}
