import { LockKeyhole, ShieldCheck } from 'lucide-react';
import { personInputClass, PLATFORM_ROLES } from '../../lib/peopleForm';

interface Props { id: string; role: string; onChange: (role: string) => void; invitation?: boolean }

export default function PlatformRoleField({ id, role, onChange, invitation = false }: Props) {
  return <div className="space-y-1.5">
    {invitation ? <>
      <p className="text-sm font-medium text-gray-700">Platform role</p>
      <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-900"><LockKeyhole aria-hidden="true" className="h-4 w-4 text-gray-500" />Organization User</div>
      <p className="text-xs leading-5 text-gray-500">Email invitations use the Organization User role.</p>
    </> : <>
      <label htmlFor={id} className="block text-sm font-medium text-gray-700">Platform role</label>
      <select id={id} value={role} onChange={(event) => onChange(event.target.value)} data-person-field="platformRole" data-autofocus="true" className={personInputClass}>
        {!PLATFORM_ROLES.some((option) => option.value === role) && <option value={role}>{role}</option>}
        {PLATFORM_ROLES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
      {role === 'platform_admin' && <p className="flex items-start gap-2 rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-500"><ShieldCheck aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />Platform admins have full access to the control plane.</p>}
    </>}
  </div>;
}
