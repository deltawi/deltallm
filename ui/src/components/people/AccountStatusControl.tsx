export default function AccountStatusControl({ active, onChange }: { active: boolean; onChange: (active: boolean) => void }) {
  return <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-gray-200 p-3">
    <input type="checkbox" checked={active} onChange={(event) => onChange(event.target.checked)} className="shrink-0 accent-brand-primary" />
    <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-gray-900">Account active</span><span className="block text-xs leading-5 text-gray-500">{active ? 'Allow this account to sign in.' : 'Sign-in is blocked, including SSO.'}</span></span>
    <span className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-medium ${active ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-600'}`}>{active ? 'Active' : 'Disabled'}</span>
  </label>;
}
