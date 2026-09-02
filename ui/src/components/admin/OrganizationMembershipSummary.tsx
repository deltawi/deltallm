import { Building2, Users } from 'lucide-react';

import {
  formatOrganizationCount,
  normalizeOrganizationCount,
} from '../../lib/organizationCounts';

interface OrganizationMembershipSummaryProps {
  memberCount: number | null | undefined;
  teamCount: number | null | undefined;
}

function countAriaLabel(count: number | null, singular: string, plural: string): string {
  if (count === null) return `${singular} count unavailable`;
  return `${count} ${count === 1 ? singular.toLowerCase() : plural.toLowerCase()}`;
}

export function OrganizationMembershipSummary({
  memberCount,
  teamCount,
}: OrganizationMembershipSummaryProps) {
  const normalizedMemberCount = normalizeOrganizationCount(memberCount);
  const normalizedTeamCount = normalizeOrganizationCount(teamCount);

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-1 text-gray-700">
        <Users className="w-3.5 h-3.5 text-gray-400" aria-hidden="true" />
        <span
          className="text-sm font-medium"
          aria-label={countAriaLabel(normalizedMemberCount, 'Member', 'Members')}
        >
          {formatOrganizationCount(normalizedMemberCount)}
        </span>
      </div>
      <span className="text-gray-300" aria-hidden="true">·</span>
      <div className="flex items-center gap-1 text-gray-500 text-xs">
        <Building2 className="w-3.5 h-3.5 text-gray-400" aria-hidden="true" />
        <span aria-label={countAriaLabel(normalizedTeamCount, 'Team', 'Teams')}>
          {formatOrganizationCount(normalizedTeamCount)} teams
        </span>
      </div>
    </div>
  );
}
