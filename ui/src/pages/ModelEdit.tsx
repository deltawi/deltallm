import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { models } from '../lib/api';
import { modelDetailPath } from '../lib/modelRoutes';
import ModelForm from '../components/ModelForm';
import { formFromModel, type ModelPayload } from '../components/modelFormShared';
import { ArrowLeft } from 'lucide-react';
import { useToast } from '../components/ToastProvider';
import { mutationOutcome } from '../lib/mutationOutcome';
import { useAuth } from '../lib/auth';
import { isPlatformAdminSession } from '../lib/authorization';
import ManagedAssetAccessPanel from '../components/ManagedAssetAccessPanel';
import { useManagedAssetAudienceOptions } from '../lib/useManagedAssetAudienceOptions';

export default function ModelEdit() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const navigate = useNavigate();
  const { pushToast } = useToast();
  const { session, authMode } = useAuth();
  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const { teamOptions, organizationOptions, loading: audiencesLoading, error: audiencesError, refetch: refetchAudiences } = useManagedAssetAudienceOptions(
    session,
    isPlatformAdmin,
  );
  const { data: model, loading, refetch } = useApi(
    (signal) => models.get(deploymentId!, signal),
    [deploymentId],
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[400px]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-brand-primary" />
      </div>
    );
  }

  if (!model) {
    return (
      <div className="p-6">
        <button onClick={() => navigate('/models')} className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 mb-4">
          <ArrowLeft className="w-4 h-4" /> Back to Models
        </button>
        <p className="text-gray-500">Model not found.</p>
      </div>
    );
  }

  if (!isPlatformAdmin && !model.access?.capabilities.write) {
    return (
      <div className="p-6">
        <button onClick={() => navigate(modelDetailPath(deploymentId!))} className="mb-4 flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeft className="h-4 w-4" /> Back to Model
        </button>
        <p className="text-gray-500">You have read-only access to this model.</p>
      </div>
    );
  }

  const { form: initialValues, defaultParams: initialDefaultParams } = formFromModel(model);
  const handleSubmit = async (payload: ModelPayload) => {
    setError(null);
    setSaving(true);
    try {
      const result = await models.update(deploymentId!, payload);
      const outcome = mutationOutcome('Model deployment was updated.', result.warnings);
      pushToast({
        tone: outcome.tone,
        title: outcome.tone === 'info' ? 'Model updated with warning' : 'Model updated',
        message: outcome.message,
      });
      navigate(modelDetailPath(deploymentId!));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update model');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto">
      <button onClick={() => navigate(modelDetailPath(deploymentId!))} className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 mb-5 transition-colors">
        <ArrowLeft className="w-4 h-4" /> Back to Model
      </button>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Edit Model Deployment</h1>
      {model.access?.governance_source === 'creator' ? (
        <ManagedAssetAccessPanel
          access={model.access}
          assetLabel="Model"
          teamOptions={teamOptions}
          organizationOptions={organizationOptions}
          allowPublic={isPlatformAdmin}
          audiencesLoading={audiencesLoading}
          audiencesError={audiencesError}
          onRetryAudiences={refetchAudiences}
          onSaved={() => refetch()}
          className="mb-6"
        />
      ) : model.access?.governance_source === 'platform' ? (
        <div className="mb-6 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900 shadow-sm">
          <div className="font-semibold">Availability is managed by tiers</div>
          <p className="mt-1 text-xs text-blue-700">
            Organization access, pricing, and rate limits for this platform model are configured in Tiers.
          </p>
        </div>
      ) : null}
      <ModelForm
        initialValues={initialValues}
        initialDefaultParams={initialDefaultParams}
        initialModelInfo={model.model_info}
        allowInlineCredentials={isPlatformAdmin}
        namedCredentialDefaultAccess={{ grants: [] }}
        namedCredentialTeamOptions={teamOptions}
        namedCredentialOrganizationOptions={organizationOptions}
        allowPublicNamedCredentials={isPlatformAdmin}
        namedCredentialAudiencesLoading={audiencesLoading}
        namedCredentialAudiencesError={audiencesError}
        onRetryNamedCredentialAudiences={refetchAudiences}
        lockApiModelId
        namespaceRequired={!isPlatformAdmin && Boolean((model.api_model_id || model.model_name).includes('/'))}
        onSubmit={handleSubmit}
        onCancel={() => navigate(modelDetailPath(deploymentId!))}
        submitLabel="Save Changes"
        saving={saving}
        error={error}
      />
    </div>
  );
}
