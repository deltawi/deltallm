import type { ManagedAssetAccess, RouteGroupMemberDetail } from './api';
import { memberLabels } from './routeGroupRouting';
import type { ManagedAssetAudienceOption } from './useManagedAssetAudienceOptions';
import type { useRoutePolicySimulation } from './useRoutePolicySimulation';

export function workloadLabel(mode: string): string {
  return mode.replace(/_/g, ' ').replace(/^\w/, (character) => character.toUpperCase());
}

export function sharingSummary(
  access: ManagedAssetAccess,
  teamOptions: ManagedAssetAudienceOption[] = [],
  organizationOptions: ManagedAssetAudienceOption[] = [],
): string {
  const grants = Array.isArray(access.grants) ? access.grants : [];
  if (grants.some((grant) => grant.subject_type === 'public') || access.visibility === 'public') return 'Public';
  if (grants.length === 0) return 'Private';
  const labels = grants.map((grant) => {
    const options = grant.subject_type === 'team' ? teamOptions : organizationOptions;
    return options.find((option) => option.id === grant.subject_id)?.label ?? null;
  });
  // Audience names are not always loaded; never show a raw ID in their place.
  if (labels.some((label) => !label)) {
    const count = (type: string, noun: string) => {
      const total = grants.filter((grant) => grant.subject_type === type).length;
      return total > 0 ? `${total} ${noun}${total === 1 ? '' : 's'}` : null;
    };
    return `Shared with ${[count('team', 'team'), count('organization', 'organization')].filter(Boolean).join(' and ')}`;
  }
  return labels.length === 1 ? `Shared with ${labels[0]}` : `Shared with ${labels[0]} and ${labels.length - 1} more`;
}

export function modelsSummary(members: RouteGroupMemberDetail[]): { text: string; warn: boolean } {
  const active = members.filter((member) => member.enabled);
  if (members.length === 0) return { text: 'No models yet', warn: true };
  // Same words as the Models tab: false is unhealthy, no health record is "not found".
  const unhealthy = active.filter((member) => member.healthy === false).length;
  const missing = active.filter((member) => member.healthy == null).length;
  const count = `${members.length} ${members.length === 1 ? 'model' : 'models'}`;
  if (active.length === 0) return { text: `${count}, all paused`, warn: true };
  const problems = [unhealthy > 0 ? `${unhealthy} unhealthy` : null, missing > 0 ? `${missing} not found` : null].filter(Boolean);
  if (problems.length > 0) return { text: `${count}, ${problems.join(', ')}`, warn: true };
  return { text: `${count}, ${active.length === 1 ? 'healthy' : 'all healthy'}`, warn: false };
}

// Fixed series colors so one model keeps its color in every bar on the page.
const SHARE_COLORS = ['#4f46e5', '#0ea5e9', '#14b8a6', '#f59e0b', '#ec4899', '#8b5cf6', '#84cc16', '#64748b'];

export function shareColor(index: number): string {
  return SHARE_COLORS[index % SHARE_COLORS.length];
}

export interface DraftIssue {
  level: 'error' | 'warning' | 'ok';
  text: string;
}

/** Problems that block publishing, risks to read first, and what is fine. */
export function draftIssues(error: string | null, members: RouteGroupMemberDetail[], includedIds: string[]): DraftIssue[] {
  const labels = memberLabels(members);
  const issues: DraftIssue[] = error ? [{ level: 'error', text: error }] : [];
  const included = members.filter((member) => includedIds.includes(member.deployment_id));
  for (const member of included) {
    if (member.healthy === false) issues.push({ level: 'warning', text: `${labels[member.deployment_id]} is unhealthy. It gets no traffic until it recovers.` });
    if (member.healthy == null) issues.push({ level: 'warning', text: `${labels[member.deployment_id]} is not in the model list. Requests cannot use it.` });
  }
  const healthy = included.filter((member) => member.healthy === true).length;
  if (healthy > 0) issues.push({ level: 'ok', text: `${healthy} included ${healthy === 1 ? 'deployment is' : 'deployments are'} healthy.` });
  return issues;
}

type Simulation = ReturnType<typeof useRoutePolicySimulation>;

/** One line about the latest dry run of this exact draft. */
export function dryRunSummary(simulation: Simulation): string {
  const result = simulation.stale ? null : simulation.data;
  if (!result) return 'Dry run on this draft: not run yet.';
  return `Dry run on this draft: ${result.summary.served_requests} of ${result.iterations} served, ${result.summary.fallback_requests} fallbacks, ${result.summary.failed_requests} failed.`;
}
