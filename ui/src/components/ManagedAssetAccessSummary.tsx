import type { ManagedAssetAccess, ManagedAssetGrantInput } from '../lib/api';
import type { ManagedAssetAudienceOption } from '../lib/useManagedAssetAudienceOptions';

interface ManagedAssetAccessSummaryProps {
  access: ManagedAssetAccess;
  teamOptions?: ManagedAssetAudienceOption[];
  organizationOptions?: ManagedAssetAudienceOption[];
  className?: string;
}

function optionLabel(options: ManagedAssetAudienceOption[], id: string | null): string | null {
  if (!id) return null;
  return options.find((option) => option.id === id)?.label || id;
}

function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

export default function ManagedAssetAccessSummary({
  access,
  teamOptions = [],
  organizationOptions = [],
  className = '',
}: ManagedAssetAccessSummaryProps) {
  let grants: ManagedAssetGrantInput[] = access.grants || [];
  if (!Array.isArray(access.grants)) {
    if ((access.visibility === 'team' || access.visibility === 'organization') && access.subject_id) {
      grants = [{
        subject_type: access.visibility,
        subject_id: access.subject_id,
        access_role: access.access_role || 'reader',
      }];
    } else if (access.visibility === 'public') {
      grants = [{ subject_type: 'public', subject_id: null, access_role: 'reader' }];
    }
  }
  const publicGrant = grants.some((grant) => grant.subject_type === 'public');
  const audienceLabels = grants
    .filter((grant) => grant.subject_type !== 'public')
    .map((grant) => grant.subject_type === 'team'
      ? optionLabel(teamOptions, grant.subject_id)
      : optionLabel(organizationOptions, grant.subject_id))
    .filter(Boolean);
  const visibilityLabel = grants.length === 0
    ? 'Private'
    : publicGrant
      ? 'Public'
      : `Shared · ${grants.length}`;

  return (
    <div className={`flex flex-wrap items-center gap-1.5 text-xs ${className}`.trim()}>
      <span className="rounded-full bg-slate-100 px-2.5 py-1 font-semibold text-slate-700">
        {visibilityLabel}
      </span>
      {audienceLabels.length ? <span className="text-slate-600">{audienceLabels.join(', ')}</span> : null}
      <span className="text-slate-500">· You: {titleCase(access.effective_role || 'no access')}</span>
    </div>
  );
}
