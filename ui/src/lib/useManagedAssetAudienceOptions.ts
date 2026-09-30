import { managedAssets } from './api';
import type { SessionInfo } from './auth';
import { useApi } from './hooks';

export type ManagedAssetAudienceOption = { id: string; label: string };

type AudienceOptions = {
  teamOptions: ManagedAssetAudienceOption[];
  organizationOptions: ManagedAssetAudienceOption[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
};

type AudienceOptionData = Pick<AudienceOptions, 'teamOptions' | 'organizationOptions'>;

const EMPTY_OPTION_DATA: AudienceOptionData = { teamOptions: [], organizationOptions: [] };

function membershipOptions(session: SessionInfo | null): AudienceOptionData {
  return {
    teamOptions: (session?.team_memberships || []).map((membership) => {
      const id = String(membership.team_id || '');
      return { id, label: String(membership.team_name || membership.name || id) };
    }).filter((option) => option.id),
    organizationOptions: (session?.organization_memberships || []).map((membership) => {
      const id = String(membership.organization_id || '');
      return { id, label: String(membership.organization_name || membership.name || id) };
    }).filter((option) => option.id),
  };
}

export function useManagedAssetAudienceOptions(
  session: SessionInfo | null,
  isPlatformAdmin: boolean,
): AudienceOptions {
  const { data, error, loading, refetch } = useApi(async (signal) => {
    if (!isPlatformAdmin) return EMPTY_OPTION_DATA;
    const [organizationOptions, teamOptions] = await Promise.all([
      managedAssets.audienceOptions('organization', { limit: 3 }, signal).then((result) => result.data),
      managedAssets.audienceOptions('team', { limit: 3 }, signal).then((result) => result.data),
    ]);
    return {
      teamOptions,
      organizationOptions,
    };
  }, [isPlatformAdmin]);

  if (isPlatformAdmin) {
    return {
      ...(data || EMPTY_OPTION_DATA),
      loading,
      error: error instanceof Error ? error.message : error ? 'Unable to load audiences.' : null,
      refetch,
    };
  }
  return { ...membershipOptions(session), loading: false, error: null, refetch };
}
