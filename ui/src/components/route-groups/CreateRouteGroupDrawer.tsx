import type React from 'react';
import Modal from '../Modal';
import ManagedAssetAccessFields from '../ManagedAssetAccessFields';
import type { ManagedAssetGrantInput, ModelDeploymentDetail } from '../../lib/api';
import { groupKeySuffixFromName, ROUTE_GROUP_MODE_OPTIONS } from '../../lib/routeGroups';
import CreateGroupModelPicker from './CreateGroupModelPicker';

interface CreateDrawerProps {
  open: boolean;
  onClose: () => void;
  form: { group_key: string; name: string; mode: string };
  setForm: React.Dispatch<React.SetStateAction<{ group_key: string; name: string; mode: string }>>;
  formError: string | null;
  setFormError: React.Dispatch<React.SetStateAction<string | null>>;
  creating: boolean;
  onCreate: () => void;
  grants: ManagedAssetGrantInput[];
  teamOptions: Array<{ id: string; label: string }>;
  organizationOptions: Array<{ id: string; label: string }>;
  audiencesLoading: boolean;
  audiencesError: string | null;
  onRetryAudiences: () => void;
  allowPublic: boolean;
  useGeneratedPrefix: boolean;
  onGrantsChange: (grants: ManagedAssetGrantInput[]) => void;
  models: ModelDeploymentDetail[];
  onModelsChange: (models: ModelDeploymentDetail[]) => void;
}

export default function CreateDrawer({
  open,
  onClose,
  form,
  setForm,
  formError,
  setFormError,
  creating,
  onCreate,
  grants,
  teamOptions,
  organizationOptions,
  audiencesLoading,
  audiencesError,
  onRetryAudiences,
  allowPublic,
  useGeneratedPrefix,
  onGrantsChange,
  models,
  onModelsChange,
}: CreateDrawerProps) {
  if (!open) return null;
  const generatedKeyPreview = groupKeySuffixFromName(form.name) || 'group-name';
  return (
    <Modal open={open} onClose={onClose} title="Create Model Group" description="Name the group and pick its models. You can add models later." wide footer={(
        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-4">
          <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button
            onClick={onCreate}
            disabled={creating}
            className="rounded-lg bg-brand-primary px-4 py-2 text-sm font-medium text-brand-on-primary hover:bg-brand-primary-hover disabled:opacity-50"
          >
            {creating ? 'Creating…' : models.length > 0 ? 'Create group' : 'Create and continue →'}
          </button>
        </div>
    )}>
        {/* Drawer body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {useGeneratedPrefix ? (
            <div>
              <label htmlFor="create-group-key" className="mb-1 block text-sm font-medium text-gray-700">
                Group Key <span className="text-red-500">*</span>
              </label>
              <div className="flex rounded-lg border border-gray-300 focus-within:ring-2 focus-within:ring-brand-primary">
                <span className="flex items-center rounded-l-lg border-r border-gray-200 bg-gray-50 px-3 font-mono text-xs text-gray-500">
                  grp-XXXX-
                </span>
                <input
                  id="create-group-key"
                  value={form.name}
                  onChange={(e) => {
                    setForm({ ...form, name: e.target.value });
                    if (formError) setFormError(null);
                  }}
                  placeholder="Customer Support"
                  maxLength={64}
                  data-autofocus="true"
                  className="min-w-0 flex-1 rounded-r-lg px-3 py-2 text-sm focus:outline-none"
                />
              </div>
              <p className="mt-1 text-xs text-gray-400">
                Your exact entry is also the display name. Generated key:{' '}
                <code>grp-XXXX-{generatedKeyPreview}</code>
              </p>
            </div>
          ) : (
            <div>
              <label htmlFor="create-group-key" className="mb-1 block text-sm font-medium text-gray-700">
                Group Key <span className="text-red-500">*</span>
              </label>
              <input
                id="create-group-key"
                value={form.group_key}
                onChange={(e) => {
                  setForm({ ...form, group_key: e.target.value });
                  if (formError) setFormError(null);
                }}
                placeholder="prod-chat-primary"
                data-autofocus="true"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
              />
              <p className="mt-1 text-xs text-gray-400">Stable key used by clients, policies, and bindings.</p>
            </div>
          )}

          {formError && !formError.startsWith('Select a ') ? (
            <div role="alert" className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{formError}</div>
          ) : null}

          <div className={`grid gap-3 ${useGeneratedPrefix ? 'grid-cols-1' : 'grid-cols-2'}`}>
            <div>
              <label htmlFor="create-group-mode" className="mb-1 block text-sm font-medium text-gray-700">Workload Type</label>
              <select
                id="create-group-mode"
                value={form.mode}
                onChange={(e) => {
                  setForm({ ...form, mode: e.target.value });
                  // Picked models must support the workload type.
                  if (models.length > 0) onModelsChange([]);
                }}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
              >
                {ROUTE_GROUP_MODE_OPTIONS.map((m) => (
                  <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>
                ))}
              </select>
            </div>
            {!useGeneratedPrefix ? (
              <div>
                <label htmlFor="create-group-name" className="mb-1 block text-sm font-medium text-gray-700">Display Name</label>
                <input
                  id="create-group-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="Production Chat"
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary"
                />
              </div>
            ) : null}
          </div>

          <CreateGroupModelPicker mode={form.mode} picked={models} disabled={creating} onChange={onModelsChange} />

          <div className="rounded-xl border border-slate-200 p-4">
            <ManagedAssetAccessFields
              grants={grants}
              teamOptions={teamOptions}
              organizationOptions={organizationOptions}
              allowPublic={allowPublic}
              audiencesLoading={audiencesLoading}
              audiencesError={audiencesError}
              onRetryAudiences={onRetryAudiences}
              error={formError?.startsWith('Select a ') ? formError : null}
              onChange={onGrantsChange}
            />
          </div>
        </div>

    </Modal>
  );
}
