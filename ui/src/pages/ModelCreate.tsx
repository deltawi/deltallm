import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  models,
  managedAssetAccessInput,
  type ManagedAssetAccessInput,
  type ManagedAssetGrantInput,
} from '../lib/api';
import { modelDetailPath } from '../lib/modelRoutes';
import ModelForm from '../components/ModelForm';
import ManagedAssetAccessFields from '../components/ManagedAssetAccessFields';
import { EMPTY_FORM, type ModelPayload } from '../components/modelFormShared';
import { ArrowLeft } from 'lucide-react';
import { useToast } from '../components/ToastProvider';
import { mutationOutcome } from '../lib/mutationOutcome';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';
import { useApi } from '../lib/hooks';

export default function ModelCreate() {
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { session, authMode } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const [error, setError] = useState<string | null>(null);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [grants, setGrants] = useState<ManagedAssetGrantInput[]>([]);
  const {
    data: modelIdentity,
    loading: modelIdentityLoading,
    error: modelIdentityError,
    refetch: refetchModelIdentity,
  } = useApi(
    (signal) => isPlatformAdmin
      ? Promise.resolve({
        namespace_required: false,
        api_namespace: null,
        suggested_namespace: null,
        namespace_locked: false,
      })
      : models.identity(signal),
    [isPlatformAdmin],
  );

  const { teamOptions, organizationOptions, loading: audiencesLoading, error: audiencesError, refetch: refetchAudiences } = useManagedAssetAudienceOptions(
    session,
    isPlatformAdmin,
  );

  const handleSubmit = async (payload: ModelPayload) => {
    setError(null);
    setAccessError(null);
    setSaving(true);
    try {
      const access: ManagedAssetAccessInput = managedAssetAccessInput(
        isPlatformAdmin ? [] : grants,
      );
      const result = await models.create({ ...payload, access });
      const outcome = mutationOutcome('Model deployment was created.', result.warnings);
      pushToast({
        tone: outcome.tone,
        title: outcome.tone === 'info' ? 'Model created with warning' : 'Model created',
        message: outcome.message,
      });
      const deploymentId = result.deployment_id;
      if (deploymentId) {
        navigate(modelDetailPath(deploymentId));
      } else {
        navigate('/models');
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create model');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto">
      <div className="mb-6">
        <button
          onClick={() => navigate('/models')}
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 mb-3 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" /> Back to Models
        </button>
        <h1 className="text-2xl font-bold text-gray-900">Add Model Deployment</h1>
        <p className="text-sm text-gray-500 mt-1">Configure a new model deployment with provider connection, routing, and pricing settings.</p>
      </div>
      {isPlatformAdmin ? (
        <div className="mb-6 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900 shadow-sm">
          <div className="font-semibold">Availability is managed by tiers</div>
          <p className="mt-1 text-xs text-blue-700">
            After creating this platform model, add it to one or more tiers to make it available to organizations with the appropriate pricing and rate limits.
          </p>
        </div>
      ) : (
        <div className="mb-6 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <ManagedAssetAccessFields
            grants={grants}
            teamOptions={teamOptions}
            organizationOptions={organizationOptions}
            allowPublic={false}
            audiencesLoading={audiencesLoading}
            audiencesError={audiencesError}
            onRetryAudiences={refetchAudiences}
            error={accessError}
            onChange={(nextGrants) => { setGrants(nextGrants); setAccessError(null); }}
          />
          {grants.length > 0 ? (
            <p className="mt-3 text-xs text-amber-700">
              People with model access can invoke it using the attached credential, but they cannot view or edit that credential unless it is shared with them separately.
            </p>
          ) : null}
        </div>
      )}
      {!isPlatformAdmin && modelIdentityLoading ? (
        <div className="flex min-h-40 items-center justify-center rounded-xl border border-gray-200 bg-white">
          <div className="h-7 w-7 animate-spin rounded-full border-b-2 border-brand-primary" />
        </div>
      ) : !isPlatformAdmin && (modelIdentityError || !modelIdentity) ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p>{modelIdentityError instanceof Error ? modelIdentityError.message : 'Failed to prepare your API model namespace.'}</p>
          <button type="button" onClick={() => refetchModelIdentity()} className="mt-2 font-medium underline">
            Try again
          </button>
        </div>
      ) : (
        <ModelForm
          initialValues={{
            ...EMPTY_FORM,
            api_namespace: modelIdentity?.api_namespace || modelIdentity?.suggested_namespace || '',
            api_namespace_locked: Boolean(modelIdentity?.namespace_locked),
            credential_source: isPlatformAdmin ? 'inline' : 'named',
          }}
          namespaceRequired={!isPlatformAdmin}
          allowInlineCredentials={isPlatformAdmin}
          namedCredentialDefaultAccess={{ grants: [] }}
          namedCredentialTeamOptions={teamOptions}
          namedCredentialOrganizationOptions={organizationOptions}
          allowPublicNamedCredentials={isPlatformAdmin}
          namedCredentialAudiencesLoading={audiencesLoading}
          namedCredentialAudiencesError={audiencesError}
          onRetryNamedCredentialAudiences={refetchAudiences}
          onSubmit={handleSubmit}
          onCancel={() => navigate('/models')}
          submitLabel="Create"
          saving={saving}
          error={error}
        />
      )}
    </div>
  );
}
