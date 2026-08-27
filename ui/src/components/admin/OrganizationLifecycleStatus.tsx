import { AlertTriangle, CheckCircle2, Clock3, Loader2 } from 'lucide-react';

import {
  formatOrganizationDeletionDeadline,
  organizationLifecyclePresentation,
  type OrganizationLifecycleState,
} from '../../lib/organizationLifecycle';

type LifecycleProps = {
  state: OrganizationLifecycleState;
};

type NoticeProps = LifecycleProps & {
  deletionNotBeforeAt?: string | null;
};

function LifecycleIcon({ state, className }: LifecycleProps & { className: string }) {
  if (state === 'active') return <CheckCircle2 className={className} />;
  if (state === 'deletion_pending') return <Clock3 className={className} />;
  if (state === 'purging') return <Loader2 className={`${className} animate-spin`} />;
  return <AlertTriangle className={className} />;
}

export function OrganizationLifecycleBadge({ state }: LifecycleProps) {
  const presentation = organizationLifecyclePresentation(state);
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${presentation.badgeClassName}`}
    >
      <LifecycleIcon state={state} className="h-3.5 w-3.5" />
      {presentation.label}
    </span>
  );
}

export function OrganizationLifecycleNotice({ state, deletionNotBeforeAt }: NoticeProps) {
  if (state === 'active') return null;

  const presentation = organizationLifecyclePresentation(state);
  const deadline = formatOrganizationDeletionDeadline(deletionNotBeforeAt);
  let message: string;
  if (state === 'deletion_pending') {
    message = deadline
      ? `Runtime access and administrative changes are disabled now. Permanent deletion will not start before ${deadline}. A platform administrator can restore the organization during the recovery window.`
      : 'Runtime access and administrative changes are disabled now. A platform administrator can restore the organization while cleanup remains reversible.';
  } else if (state === 'purging') {
    message = 'Runtime access and administrative changes are disabled. Irreversible cleanup has started, so the organization can no longer be restored.';
  } else if (state === 'deletion_failed') {
    message = 'Runtime access and administrative changes remain disabled. A platform administrator must retry cleanup from the Danger zone.';
  } else {
    message = 'Administrative changes are disabled until the organization lifecycle status can be verified. Refresh this page before trying again.';
  }

  return (
    <div
      className={`flex items-start gap-3 rounded-xl border px-4 py-3 ${presentation.noticeClassName || ''}`}
      role="status"
    >
      <LifecycleIcon state={state} className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <p className="text-sm font-semibold">{presentation.noticeTitle}</p>
        <p className="mt-1 text-xs leading-relaxed opacity-90">{message}</p>
      </div>
    </div>
  );
}
