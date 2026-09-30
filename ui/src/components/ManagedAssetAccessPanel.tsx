import { useEffect, useMemo, useState } from 'react';
import {
  managedAssets,
  managedAssetAccessInput,
  type ManagedAssetAccess,
  type ManagedAssetAccessInput,
  type ManagedAssetGrantInput,
} from '../lib/api';
import type { ManagedAssetAudienceOption } from '../lib/useManagedAssetAudienceOptions';
import { mutationOutcome } from '../lib/mutationOutcome';
import ManagedAssetAccessFields from './ManagedAssetAccessFields';
import { useToast } from './ToastProvider';

interface ManagedAssetAccessPanelProps {
  access: ManagedAssetAccess;
  assetLabel: string;
  teamOptions: ManagedAssetAudienceOption[];
  organizationOptions: ManagedAssetAudienceOption[];
  allowPublic: boolean;
  audiencesLoading?: boolean;
  audiencesError?: string | null;
  onRetryAudiences?: () => void;
  onSaved?: (access: ManagedAssetAccess) => void | Promise<void>;
  onDirtyChange?: (dirty: boolean) => void;
  className?: string;
}

function inputFromAccess(access: ManagedAssetAccess): ManagedAssetAccessInput {
  if (Array.isArray(access.grants)) return { grants: access.grants };
  if (access.visibility === 'team' || access.visibility === 'organization') {
    return {
      grants: access.subject_id ? [{
        subject_type: access.visibility,
        subject_id: access.subject_id,
        access_role: access.access_role || 'reader',
      }] : [],
    };
  }
  return {
    grants: access.visibility === 'public'
      ? [{ subject_type: 'public', subject_id: null, access_role: 'reader' }]
      : [],
  };
}

function sameAccess(left: ManagedAssetAccessInput, right: ManagedAssetAccessInput): boolean {
  const normalize = (grants: ManagedAssetGrantInput[]) => [...grants]
    .sort((a, b) => `${a.subject_type}:${a.subject_id || ''}`.localeCompare(`${b.subject_type}:${b.subject_id || ''}`));
  return JSON.stringify(normalize(left.grants)) === JSON.stringify(normalize(right.grants));
}

function ManagedAssetAccessPanelState({
  access,
  assetLabel,
  teamOptions,
  organizationOptions,
  allowPublic,
  audiencesLoading = false,
  audiencesError = null,
  onRetryAudiences,
  onSaved,
  onDirtyChange,
  className = '',
}: ManagedAssetAccessPanelProps) {
  const { pushToast } = useToast();
  const [currentAccess, setCurrentAccess] = useState(access);
  const [draft, setDraft] = useState<ManagedAssetAccessInput | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const baseline = useMemo(() => inputFromAccess(currentAccess), [currentAccess]);
  const effective = draft || baseline;
  const dirty = draft !== null && !sameAccess(draft, baseline);
  const canManage = currentAccess.capabilities.manage_access;

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  const updateDraft = (next: ManagedAssetAccessInput) => {
    setError(null);
    setDraft(sameAccess(next, baseline) ? null : next);
  };

  const save = async () => {
    if (!draft || !dirty || !canManage) return;
    setSaving(true);
    setError(null);
    let savedAccess: ManagedAssetAccess;
    try {
      const result = await managedAssets.updateAccess(currentAccess.managed_asset_id, {
        ...managedAssetAccessInput(draft.grants, currentAccess.policy_version),
      });
      savedAccess = result.access;
      setCurrentAccess(result.access);
      setDraft(null);
      const outcome = mutationOutcome(
        `${assetLabel} sharing and access were saved.`,
        result.warnings || [],
      );
      pushToast({
        tone: outcome.tone,
        title: result.warnings?.length ? 'Access saved with warning' : 'Access updated',
        message: outcome.message,
      });
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : `Failed to update ${assetLabel.toLowerCase()} access.`;
      setError(message);
      pushToast({ tone: 'error', title: 'Access update failed', message });
      setSaving(false);
      return;
    }
    if (onSaved) {
      try {
        await onSaved(savedAccess);
      } catch {
        pushToast({
          tone: 'info',
          title: 'Access saved',
          message: 'The access change was saved, but this page could not refresh automatically.',
        });
      }
    }
    setSaving(false);
  };

  return (
    <section className={`rounded-xl border border-gray-200 bg-white p-4 shadow-sm ${className}`.trim()}>
      {canManage ? (
        <>
          <ManagedAssetAccessFields
            grants={effective.grants}
            teamOptions={teamOptions}
            organizationOptions={organizationOptions}
            allowPublic={allowPublic}
            audiencesLoading={audiencesLoading}
            audiencesError={audiencesError}
            onRetryAudiences={onRetryAudiences}
            error={error}
            disabled={saving}
            onChange={(grants) => updateDraft({ grants })}
          />
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => { setDraft(null); setError(null); }}
              disabled={!dirty || saving}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Discard
            </button>
            <button
              type="button"
              onClick={() => { void save(); }}
              disabled={
                !dirty
                || saving
                || Boolean(audiencesError)
              }
              className="rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
            >
              {saving ? 'Saving access…' : 'Save access'}
            </button>
          </div>
        </>
      ) : (
        <div>
          <ManagedAssetAccessFields
            grants={effective.grants}
            teamOptions={teamOptions}
            organizationOptions={organizationOptions}
            allowPublic={allowPublic}
            disabled
            onChange={() => undefined}
          />
          <p className="mt-3 text-xs text-gray-500">Only the Owner can change sharing and access.</p>
        </div>
      )}
    </section>
  );
}

export default function ManagedAssetAccessPanel(props: ManagedAssetAccessPanelProps) {
  return (
    <ManagedAssetAccessPanelState
      key={`${props.access.managed_asset_id}:${props.access.policy_version}`}
      {...props}
    />
  );
}
