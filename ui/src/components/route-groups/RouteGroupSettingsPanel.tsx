import { useState } from 'react';
import type { RouteGroup } from '../../lib/api/routeGroups';
import { routeGroups } from '../../lib/api';
import { isPlatformAdminSession } from '../../lib/authorization';
import { useAuth } from '../../lib/auth';
import { routeGroupMutationOutcome } from '../../lib/routeGroups';
import type { useRouteGroupMutationScope } from '../../lib/useRouteGroupMutationScope';
import ManagedAssetAccessPanel from '../ManagedAssetAccessPanel';
import { useToast } from '../ToastProvider';
import RouteGroupSettingsCard, { type GroupFormValues } from './RouteGroupSettingsCard';
import { useManagedAssetAudienceOptions } from '../../lib/useManagedAssetAudienceOptions';

interface RouteGroupSettingsPanelProps {
  routeGroupId: string;
  group: RouteGroup;
  form: GroupFormValues;
  onChange: (next: GroupFormValues) => void;
  onSaved: () => void;
  mutations: ReturnType<typeof useRouteGroupMutationScope>;
}

function mutationErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export default function RouteGroupSettingsPanel({
  routeGroupId,
  group,
  form,
  onChange,
  onSaved,
  mutations,
}: RouteGroupSettingsPanelProps) {
  const { authMode, session } = useAuth();
  const { pushToast } = useToast();
  const [saving, setSaving] = useState(false);
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const { teamOptions, organizationOptions, loading: audiencesLoading, error: audiencesError, refetch: refetchAudiences } = useManagedAssetAudienceOptions(
    session,
    isPlatformAdmin,
  );
  const access = group.access;
  const canWrite = isPlatformAdmin || !access || access.capabilities.write;
  const handleSave = async () => {
    if (!canWrite) return;
    const operation = mutations.begin();
    if (!operation) return;
    setSaving(true);
    try {
      const result = await routeGroups.update(routeGroupId, {
        name: form.name.trim() || null,
        mode: form.mode,
        enabled: form.enabled,
      }, operation.signal);
      if (!mutations.isCurrent(operation)) return;
      onSaved();
      const outcome = routeGroupMutationOutcome(
        'Route group settings were saved.',
        result.warnings,
      );
      pushToast({ tone: outcome.tone, title: 'Group updated', message: outcome.message });
    } catch (error: unknown) {
      if (!mutations.isCurrent(operation)) return;
      pushToast({
        tone: 'error',
        title: 'Update failed',
        message: mutationErrorMessage(error, 'Failed to update route group.'),
      });
    } finally {
      if (mutations.finish(operation)) setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      {access ? (
        <ManagedAssetAccessPanel
          access={access}
          assetLabel="Model group"
          teamOptions={teamOptions}
          organizationOptions={organizationOptions}
          allowPublic={isPlatformAdmin}
          audiencesLoading={audiencesLoading}
          audiencesError={audiencesError}
          onRetryAudiences={refetchAudiences}
          onSaved={() => onSaved()}
          className="max-w-3xl"
        />
      ) : null}
      <RouteGroupSettingsCard
        form={form}
        saving={saving}
        disabled={!canWrite}
        onChange={onChange}
        onSave={handleSave}
      />
    </div>
  );
}
