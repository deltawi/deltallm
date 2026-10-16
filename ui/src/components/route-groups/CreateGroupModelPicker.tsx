import { useMemo } from 'react';
import { X } from 'lucide-react';
import DeploymentSearchSelect from '../DeploymentSearchSelect';
import type { ModelDeploymentDetail } from '../../lib/api';
import { tokenPricePerMillion } from '../../lib/routeGroupSnippets';
import { useDeploymentCandidates } from '../../lib/useDeploymentCandidates';
import { ModelCell } from './RouteGroupModelCell';

interface CreateGroupModelPickerProps {
  mode: string;
  picked: ModelDeploymentDetail[];
  disabled?: boolean;
  onChange: (picked: ModelDeploymentDetail[]) => void;
}

// Optional models for a new group. With models, the group can answer requests as soon as it is created.
export default function CreateGroupModelPicker({ mode, picked, disabled = false, onChange }: CreateGroupModelPickerProps) {
  const pickedIds = useMemo(() => picked.map((model) => model.deployment_id), [picked]);
  const candidates = useDeploymentCandidates(mode, true, pickedIds);
  return (
    <fieldset disabled={disabled} className="space-y-2">
      <legend className="text-sm font-medium text-gray-700">Models <span className="font-normal text-gray-400">(optional)</span></legend>
      <p className="text-xs text-gray-500">
        Pick the models that answer requests. Traffic is spread evenly, and a failed call is retried on another model. You can change this later.
      </p>
      <DeploymentSearchSelect
        search={candidates.search}
        onSearchChange={candidates.setSearch}
        options={candidates.options}
        loading={candidates.loading}
        selectedDeploymentId=""
        placeholder={picked.length > 0 ? 'Add another model' : 'Add a model'}
        onSelect={(option) => {
          const model = candidates.options.find((item) => item.deployment_id === option.deployment_id);
          if (model) onChange([...picked, model]);
        }}
        searchPlaceholder="Search models by name, provider or ID"
        emptyText={candidates.error ? 'Models could not be loaded. Try the search again.' : 'No more models match.'}
      />
      {picked.length > 0 ? (
        <ul aria-label="Picked models" className="divide-y divide-gray-100 rounded-lg border border-gray-200">
          {picked.map((model) => {
            const name = model.model_name || model.deployment_id;
            const price = tokenPricePerMillion(model.model_info);
            return (
              <li key={model.deployment_id} className="flex items-center justify-between gap-3 px-3 py-2">
                <ModelCell name={name} provider={model.provider} deploymentId={model.deployment_id} />
                <span className="flex items-center gap-3 text-xs text-gray-500">
                  {price ? <span>{price} per 1M tokens</span> : null}
                  <button
                    type="button"
                    aria-label={`Remove ${name}`}
                    onClick={() => onChange(picked.filter((item) => item.deployment_id !== model.deployment_id))}
                    className="rounded-md border border-gray-200 p-1 text-gray-500 hover:border-red-200 hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
                  >
                    <X aria-hidden="true" className="h-3.5 w-3.5" />
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </fieldset>
  );
}
