import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { auth as authApi } from '../lib/api';
import { useAuth } from '../lib/auth';
import PublicAuthShell from '../components/auth/PublicAuthShell';

function renderScopeSummary(metadata: Record<string, unknown> | null | undefined): string[] {
  const summary: string[] = [];
  for (const item of (metadata?.organization_invites as Array<Record<string, unknown>> | undefined) || []) {
    summary.push(`${item.organization_name || item.organization_id} (${item.role || 'member'})`);
  }
  for (const item of (metadata?.team_invites as Array<Record<string, unknown>> | undefined) || []) {
    summary.push(`${item.team_alias || item.team_id} (${item.role || 'viewer'})`);
  }
  return summary;
}

export default function AcceptInvite() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { refreshSession } = useAuth();
  const token = params.get('token') || '';

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [password, setPassword] = useState('');
  const [invite, setInvite] = useState<any>(null);

  useEffect(() => {
    if (!token) {
      setInvite({ valid: false });
      setLoading(false);
      return;
    }
    authApi.invitation(token)
      .then((payload) => setInvite(payload))
      .catch((err: any) => {
        setError(err?.message || 'Unable to validate invitation');
        setInvite({ valid: false });
      })
      .finally(() => setLoading(false));
  }, [token]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!token) {
      setError('Invitation token is missing');
      return;
    }
    if (invite?.password_required && !password.trim()) {
      setError('Password is required to activate this account');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const result = await authApi.acceptInvitation({ token, password: password.trim() || null });
      if (result.session_established) {
        await refreshSession();
        navigate('/');
        return;
      }
      navigate('/login', {
        replace: true,
        state: {
          invitationAccepted: true,
          email: result.email,
          nextStep: result.next_step,
        },
      });
    } catch (err: any) {
      setError(err?.message || 'Unable to accept invitation');
    } finally {
      setSaving(false);
    }
  };

  return (
    <PublicAuthShell title="Accept Invitation" description="Activate your account and sign in.">
      {loading ? (
        <div className="flex items-center justify-center py-10">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
        </div>
      ) : !invite?.valid ? (
        <div className="space-y-4 text-sm text-gray-700">
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-700">
            {error || 'This invitation is invalid or has expired.'}
          </div>
          <Link to="/login" className="inline-flex text-blue-600 hover:text-blue-700 font-medium">
            Return to sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-700 space-y-2">
            <p>
              Invitation for <span className="font-medium text-gray-900">{invite.email}</span>
            </p>
            {invite.inviter_email && (
              <p>Invited by <span className="font-medium text-gray-900">{invite.inviter_email}</span></p>
            )}
            {invite.password_required ? (
              <p>Set a password to activate this account.</p>
            ) : (
              <p>No password setup is required for this account.</p>
            )}
            {renderScopeSummary(invite.metadata).length > 0 && (
              <div>
                <p className="mb-1 font-medium text-gray-900">Access</p>
                <ul className="space-y-1">
                  {renderScopeSummary(invite.metadata).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          {invite.password_required && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Choose a password</label>
              <input
                type="password"
                value={password}
                onChange={(event) => { setPassword(event.target.value); setError(''); }}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                autoComplete="new-password"
                placeholder="At least 12 characters"
              />
            </div>
          )}
          <button
            type="submit"
            disabled={saving}
            className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? 'Activating…' : 'Accept Invitation'}
          </button>
        </form>
      )}
    </PublicAuthShell>
  );
}
