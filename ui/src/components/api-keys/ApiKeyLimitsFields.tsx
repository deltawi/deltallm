import { useState } from 'react';
import type { SelfServicePolicy } from '../../lib/api';
import type { KeyFormState } from '../../lib/apiKeyForm';
import OutputTpmField from '../admin/OutputTpmField';
import ModelOutputTpmEditor from '../admin/ModelOutputTpmEditor';

export default function ApiKeyLimitsFields({ form, onChange, selfService, editing, policy }: { form: KeyFormState; onChange: (next: KeyFormState) => void; selfService: boolean; editing: boolean; policy: SelfServicePolicy | null }) {
  const [expanded, setExpanded] = useState(() => form.rph_limit !== '' || form.rpd_limit !== '' || form.tpd_limit !== '');
  const field = (key: 'max_budget' | 'rpm_limit' | 'tpm_limit' | 'rph_limit' | 'rpd_limit' | 'tpd_limit', label: string) => <label className="min-w-0 space-y-1 text-sm font-medium text-gray-700">{label}
    <input type="number" value={form[key]} onChange={(e) => onChange({ ...form, [key]: e.target.value })} max={key === 'max_budget' && selfService ? policy?.self_service_budget_ceiling ?? undefined : undefined} placeholder="No added key limit" className="w-full min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary" />
  </label>;
  return <div className="space-y-5">
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {field('max_budget', 'Max budget (USD)')}
      {(selfService || !editing) && <label className="min-w-0 space-y-1 text-sm font-medium text-gray-700">Expiry date {selfService && policy?.self_service_require_expiry ? '*' : '(optional)'}
        <input type="datetime-local" value={form.expires} onChange={(e) => onChange({ ...form, expires: e.target.value })} className="block w-full min-w-0 max-w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary" />
        <span className="block text-xs font-normal text-gray-500">Your local time{selfService && policy?.self_service_max_expiry_days != null ? ` · within ${policy.self_service_max_expiry_days} days` : ''}.</span>
      </label>}
      {field('rpm_limit', 'Requests per minute (RPM)')}
      {field('tpm_limit', 'Tokens per minute (TPM)')}
      <OutputTpmField value={form.output_tpm_limit} onChange={(output_tpm_limit) => onChange({ ...form, output_tpm_limit })} />
    </div>
    <ModelOutputTpmEditor rows={form.model_output_tpm_limit} onChange={(model_output_tpm_limit) => onChange({ ...form, model_output_tpm_limit })} />
    {selfService && policy?.self_service_budget_ceiling != null && <p className="text-xs text-gray-500">Team budget ceiling: ${policy.self_service_budget_ceiling}.</p>}
    <details className="rounded-lg border border-gray-200 p-3" open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className="cursor-pointer text-sm font-medium text-gray-800">Hourly and daily limits</summary>
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        {field('rph_limit', 'Requests per hour (RPH)')}
        {field('rpd_limit', 'Requests per day (RPD)')}
        {field('tpd_limit', 'Tokens per day (TPD)')}
      </div>
    </details>
    <p className="rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-500">An empty limit adds no key limit. Limits for the team and organization still apply.</p>
  </div>;
}
