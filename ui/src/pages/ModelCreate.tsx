import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { models } from '../lib/api';
import { modelDetailPath } from '../lib/modelRoutes';
import ModelForm, { EMPTY_FORM, buildModelPayload } from '../components/ModelForm';
import { ArrowLeft } from 'lucide-react';

export default function ModelCreate() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (payload: ReturnType<typeof buildModelPayload>) => {
    setError(null);
    setSaving(true);
    try {
      const result = await models.create(payload);
      const deploymentId = result?.deployment_id || result?.id;
      if (deploymentId) {
        navigate(modelDetailPath(deploymentId));
      } else {
        navigate('/models');
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to create model');
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
      <ModelForm
        initialValues={{ ...EMPTY_FORM }}
        onSubmit={handleSubmit}
        onCancel={() => navigate('/models')}
        submitLabel="Create"
        saving={saving}
        error={error}
      />
    </div>
  );
}
