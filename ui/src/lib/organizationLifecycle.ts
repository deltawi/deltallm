export const ORGANIZATION_LIFECYCLE_STATES = [
  'active',
  'deletion_pending',
  'purging',
  'deletion_failed',
] as const;

export type KnownOrganizationLifecycleState = typeof ORGANIZATION_LIFECYCLE_STATES[number];
export type OrganizationLifecycleState = KnownOrganizationLifecycleState | 'unavailable';

export interface OrganizationLifecycleTransition {
  lifecycleState: KnownOrganizationLifecycleState;
  deletionNotBeforeAt?: string | null;
}

export type OrganizationLifecyclePresentation = {
  label: string;
  badgeClassName: string;
  noticeTitle: string | null;
  noticeClassName: string | null;
};

const PRESENTATION: Record<OrganizationLifecycleState, OrganizationLifecyclePresentation> = {
  active: {
    label: 'Active',
    badgeClassName: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
    noticeTitle: null,
    noticeClassName: null,
  },
  deletion_pending: {
    label: 'Deletion pending',
    badgeClassName: 'bg-amber-50 text-amber-800 ring-amber-200',
    noticeTitle: 'Deletion scheduled — access is disabled',
    noticeClassName: 'border-amber-200 bg-amber-50 text-amber-950',
  },
  purging: {
    label: 'Purging',
    badgeClassName: 'bg-red-50 text-red-700 ring-red-200',
    noticeTitle: 'Permanent deletion is in progress',
    noticeClassName: 'border-red-200 bg-red-50 text-red-950',
  },
  deletion_failed: {
    label: 'Deletion failed',
    badgeClassName: 'bg-red-50 text-red-700 ring-red-200',
    noticeTitle: 'Deletion needs attention — access remains disabled',
    noticeClassName: 'border-red-200 bg-red-50 text-red-950',
  },
  unavailable: {
    label: 'Status unavailable',
    badgeClassName: 'bg-slate-100 text-slate-700 ring-slate-300',
    noticeTitle: 'Organization status could not be verified',
    noticeClassName: 'border-slate-300 bg-slate-50 text-slate-900',
  },
};

const IRREVERSIBLE_DELETION_PHASES = new Set([
  'resolve_owned_assets',
  'purge_sensitive_history',
  'remove_scoped_access',
  'revoke_credentials',
  'remove_tenant_state',
  'finalize',
  'completed',
]);

export function organizationLifecycleTransitionForDeletionJob(job: {
  status: string;
  phase: string;
  not_before_at: string | null;
}): OrganizationLifecycleTransition {
  if (job.status === 'restored') return { lifecycleState: 'active' };
  if (job.status === 'failed') return { lifecycleState: 'deletion_failed' };
  if (IRREVERSIBLE_DELETION_PHASES.has(job.phase)) return { lifecycleState: 'purging' };
  return {
    lifecycleState: 'deletion_pending',
    deletionNotBeforeAt: job.not_before_at,
  };
}

export function normalizeOrganizationLifecycleState(value: unknown): OrganizationLifecycleState {
  return typeof value === 'string'
    && (ORGANIZATION_LIFECYCLE_STATES as readonly string[]).includes(value)
    ? value as KnownOrganizationLifecycleState
    : 'unavailable';
}

export function organizationLifecyclePresentation(
  state: OrganizationLifecycleState,
): OrganizationLifecyclePresentation {
  return PRESENTATION[state];
}

export function isOrganizationActive(state: OrganizationLifecycleState): boolean {
  return state === 'active';
}

export function formatOrganizationDeletionDeadline(value: string | null | undefined): string | null {
  if (!value) return null;
  const deadline = new Date(value);
  if (Number.isNaN(deadline.getTime())) return null;
  return deadline.toLocaleString();
}
