import { useEffect, useMemo, useState } from 'react';
import { Building2 } from 'lucide-react';

import {
  organizationRecordsApi,
  type OrganizationRecord,
} from '../../lib/api/organizations';
import { useApi } from '../../lib/hooks';
import { organizationLifecyclePresentation } from '../../lib/organizationLifecycle';

interface Props {
  value: string;
  disabled?: boolean;
  enabled?: boolean;
  onChange: (organizationId: string) => void;
  onSelectedOrganizationChange: (organization: OrganizationRecord | null) => void;
}

function errorMessage(error: unknown): string | null {
  if (error instanceof Error && error.message) return error.message;
  return error ? 'Unable to load organizations.' : null;
}

export default function OrganizationTeamSelector({
  value,
  disabled = false,
  enabled = true,
  onChange,
  onSelectedOrganizationChange,
}: Props) {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');

  useEffect(() => {
    const timeoutId = window.setTimeout(() => setSearch(searchInput.trim()), 250);
    return () => window.clearTimeout(timeoutId);
  }, [searchInput]);

  const { data: page, error: pageError, loading } = useApi(
    (signal) => enabled
      ? organizationRecordsApi.list({ search: search || undefined, limit: 50, offset: 0 }, signal)
      : Promise.resolve({
        data: [],
        pagination: { total: 0, limit: 50, offset: 0, has_more: false },
      }),
    [enabled, search],
  );
  const pageOrganizations = useMemo(() => page?.data ?? [], [page?.data]);
  const selectedFromPage = pageOrganizations.find(
    (organization) => organization.organization_id === value,
  ) ?? null;
  const { data: fetchedSelected, error: selectedError } = useApi(
    (signal) => enabled && value && !selectedFromPage
      ? organizationRecordsApi.get(value, signal)
      : Promise.resolve(null),
    [enabled, value, selectedFromPage?.organization_id],
  );
  const currentFetchedSelected = fetchedSelected?.organization_id === value
    ? fetchedSelected
    : null;
  const selectedOrganization = selectedFromPage ?? currentFetchedSelected;
  const organizations = useMemo(() => {
    if (!selectedOrganization) return pageOrganizations;
    return [
      selectedOrganization,
      ...pageOrganizations.filter(
        (organization) => organization.organization_id !== selectedOrganization.organization_id,
      ),
    ];
  }, [pageOrganizations, selectedOrganization]);

  useEffect(() => {
    onSelectedOrganizationChange(selectedOrganization ?? null);
  }, [onSelectedOrganizationChange, selectedOrganization]);

  const loadError = errorMessage(pageError) ?? errorMessage(selectedError);
  return (
    <div className="space-y-2">
      <label htmlFor="team-organization-search" className="sr-only">
        Search organizations
      </label>
      <input
        id="team-organization-search"
        type="search"
        value={searchInput}
        onChange={(event) => setSearchInput(event.target.value)}
        placeholder="Search organizations…"
        disabled={disabled || !enabled}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:opacity-50"
      />
      <div className="relative">
        <Building2 className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
        <select
          aria-label="Organization"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled || !enabled}
          className="w-full appearance-none rounded-lg border border-gray-300 bg-white py-2 pl-8 pr-4 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:opacity-50"
        >
          <option value="">Select an organization…</option>
          {organizations.map((organization) => {
            const lifecycle = organizationLifecyclePresentation(organization.lifecycle_state);
            const canAddTeam = organization.capabilities.add_team;
            return (
              <option
                key={organization.organization_id}
                value={organization.organization_id}
                disabled={!canAddTeam}
              >
                {organization.organization_name || organization.organization_id}
                {canAddTeam ? '' : ` — ${lifecycle.label}`}
              </option>
            );
          })}
        </select>
      </div>
      {loading ? <p className="text-xs text-gray-500">Loading organizations…</p> : null}
      {page?.pagination.has_more ? (
        <p className="text-xs text-gray-500">Refine the search to find additional organizations.</p>
      ) : null}
      {loadError ? <p className="text-xs text-red-700">{loadError}</p> : null}
    </div>
  );
}
