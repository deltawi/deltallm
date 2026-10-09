import { useId } from 'react';
import type { SelfServicePolicy, ServiceAccount } from '../../lib/api';
import type { KeyFormState } from '../../lib/apiKeyForm';
import Button from '../Button';

export type KeyTeamOption = { team_id: string; team_alias?: string | null; self_service_keys_enabled?: boolean };
export interface ApiKeyDetailsProps {
  form: KeyFormState;
  onChange: (next: KeyFormState) => void;
  onTeamChange: (teamId: string) => void;
  teams: KeyTeamOption[];
  selfService: boolean;
  editing: boolean;
  policy: SelfServicePolicy | null;
  serviceAccounts: ServiceAccount[];
  serviceAccountsLoading: boolean;
  serviceAccountsError?: string | null;
  newServiceAccountName: string;
  onNewServiceAccountNameChange: (value: string) => void;
  creatingServiceAccount: boolean;
  onCreateServiceAccount: () => void;
}
const inputClass = 'w-full min-w-0 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:bg-gray-50 disabled:text-gray-500';

export default function ApiKeyDetailsFields({ form, onChange, onTeamChange, teams, selfService, editing, policy, serviceAccounts, serviceAccountsLoading, serviceAccountsError, newServiceAccountName, onNewServiceAccountNameChange, creatingServiceAccount, onCreateServiceAccount }: ApiKeyDetailsProps) {
  const id = useId();
  return <div className="space-y-5">
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <label className="space-y-1 text-sm font-medium text-gray-700">Key name *
        <input data-autofocus="true" value={form.key_name} onChange={(e) => onChange({ ...form, key_name: e.target.value })} placeholder="my-key" className={inputClass} />
      </label>
      <div className="space-y-1 text-sm font-medium text-gray-700">
        <label htmlFor={`${id}-team`}>Team *</label>
        <select id={`${id}-team`} value={form.team_id} onChange={(e) => onTeamChange(e.target.value)} className={inputClass}>
          <option value="">Select a team</option>
          {form.team_id && !teams.some((team) => team.team_id === form.team_id) && <option value={form.team_id} disabled>{form.team_id} (inaccessible)</option>}
          {teams.map((team) => <option key={team.team_id} value={team.team_id}>{team.team_alias || team.team_id}</option>)}
        </select>
      </div>
    </div>
    <p className="rounded-lg border border-brand-primary/15 bg-brand-primary-soft p-3 text-xs leading-5 text-brand-primary-ink">
      {selfService ? 'Only teams with self-service keys enabled are shown. The key belongs to you and inherits team access.' : 'The team sets the access boundary. Team budgets and limits also apply to this key.'}
    </p>
    {selfService && policy && <div className="rounded-lg border border-gray-200 p-3 text-xs text-gray-600">
      <p className="mb-2 font-semibold text-gray-800">Team policy</p>
      <ul className="space-y-1">
        {policy.self_service_max_keys_per_user != null && <li>Maximum keys per user: {policy.self_service_max_keys_per_user}</li>}
        {policy.self_service_budget_ceiling != null && <li>Budget ceiling: ${policy.self_service_budget_ceiling}</li>}
        {policy.self_service_require_expiry && <li>An expiry date is required.</li>}
        {policy.self_service_max_expiry_days != null && <li>Maximum expiry: {policy.self_service_max_expiry_days} days</li>}
        {policy.self_service_max_keys_per_user == null && policy.self_service_budget_ceiling == null && !policy.self_service_require_expiry && policy.self_service_max_expiry_days == null && <li>No additional constraints.</li>}
      </ul>
    </div>}
    {!selfService && !editing && <fieldset className="space-y-3">
      <legend className="mb-2 text-sm font-medium text-gray-700">Owner *</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {(['self', 'service_account'] as const).map((mode) => <label key={mode} className={`flex cursor-pointer items-start gap-2 rounded-lg border p-3 ${form.owner_mode === mode ? 'border-brand-primary bg-brand-primary-soft' : 'border-gray-200'}`}>
          <input type="radio" name="owner_mode" value={mode} checked={form.owner_mode === mode} onChange={() => onChange({ ...form, owner_mode: mode, owner_service_account_id: mode === 'self' ? '' : form.owner_service_account_id })} className="mt-1 accent-brand-primary" />
          <span><span className="block text-sm font-medium text-gray-900">{mode === 'self' ? 'You' : 'Service account'}</span><span className="block text-xs leading-5 text-gray-500">{mode === 'self' ? 'Your current admin account.' : 'For shared services and automation.'}</span></span>
        </label>)}
      </div>
      {form.owner_mode === 'service_account' && <div className="space-y-3">
        <div className="space-y-1 text-sm font-medium text-gray-700">
          <label htmlFor={`${id}-service`}>Service account *</label>
          <select id={`${id}-service`} value={form.owner_service_account_id} onChange={(e) => onChange({ ...form, owner_service_account_id: e.target.value })} disabled={!form.team_id || serviceAccountsLoading || creatingServiceAccount} className={inputClass}>
            <option value="">{!form.team_id ? 'Select a team first' : serviceAccountsLoading ? 'Loading service accounts…' : 'Select a service account'}</option>
            {form.owner_service_account_id && !serviceAccounts.some((item) => item.service_account_id === form.owner_service_account_id) && <option value={form.owner_service_account_id} disabled>{form.owner_service_account_id} (unavailable)</option>}
            {serviceAccounts.map((item) => <option key={item.service_account_id} value={item.service_account_id}>{item.name} ({item.service_account_id})</option>)}
          </select>
        </div>
        {serviceAccountsError ? <p role="alert" className="text-xs text-red-700">{serviceAccountsError}</p> : form.team_id && !serviceAccountsLoading && serviceAccounts.length === 0 && <p className="text-xs text-gray-500">No service accounts for this team. Create one below.</p>}
        <div className="border-t border-gray-100 pt-3">
          <label htmlFor="new-key-service-account" className="mb-1 block text-xs font-medium text-gray-700">Create service account</label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input id="new-key-service-account" value={newServiceAccountName} onChange={(e) => onNewServiceAccountNameChange(e.target.value)} placeholder="ci-runner" disabled={!form.team_id || creatingServiceAccount} className={inputClass} />
            <Button variant="secondary" loading={creatingServiceAccount} disabled={!form.team_id} onClick={onCreateServiceAccount}>Create</Button>
          </div>
          <p className="mt-1 text-xs text-gray-500">The new service account will be selected.</p>
        </div>
      </div>}
    </fieldset>}
  </div>;
}
