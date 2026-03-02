import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { models } from '../lib/api';
import ModelForm, { formFromModel, buildModelPayload } from '../components/ModelForm';
import { ArrowLeft } from 'lucide-react';

export default function ModelEdit() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const navigate = useNavigate();
  const { data: model, loading } = useApi(() => models.get(deploymentId!), [deploymentId]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center min-h-[400px]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
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

  const { form: initialValues, defaultParams: initialDefaultParams } = formFromModel(model);

  const handleSubmit = async (payload: ReturnType<typeof buildModelPayload>) => {
    setError(null);
    setSaving(true);
    try {
      await models.update(deploymentId!, payload);
      navigate(`/models/${deploymentId}`);
    } catch (err: any) {
      setError(err?.message || 'Failed to update model');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto">
      <button onClick={() => navigate(`/models/${deploymentId}`)} className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 mb-5 transition-colors">
        <ArrowLeft className="w-4 h-4" /> Back to Model
      </button>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Edit Model Deployment</h1>
      <ModelForm
        initialValues={initialValues}
        initialDefaultParams={initialDefaultParams}
        onSubmit={handleSubmit}
        onCancel={() => navigate(`/models/${deploymentId}`)}
        submitLabel="Save Changes"
        saving={saving}
        error={error}
      />
    </div>
  );
}
