import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { auth as authApi } from '../lib/api';
import PublicAuthShell from '../components/auth/PublicAuthShell';

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') || '';

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [valid, setValid] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    if (!token) {
      setValid(false);
      setLoading(false);
      return;
    }
    authApi.validateResetPasswordToken(token)
      .then((payload) => {
        setValid(Boolean(payload?.valid));
        setEmail(payload?.email || '');
      })
      .catch((err: any) => {
        setError(err?.message || 'Unable to validate reset token');
        setValid(false);
      })
      .finally(() => setLoading(false));
  }, [token]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!token) {
      setError('Reset token is missing');
      return;
    }
    if (!password.trim()) {
      setError('New password is required');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await authApi.resetPassword(token, password);
      setSuccess(true);
      setTimeout(() => navigate('/login'), 1200);
    } catch (err: any) {
      setError(err?.message || 'Unable to reset password');
    } finally {
      setSaving(false);
    }
  };

  return (
    <PublicAuthShell title="Reset Password" description="Choose a new password for your account.">
      {loading ? (
        <div className="flex items-center justify-center py-10">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
        </div>
      ) : !valid ? (
        <div className="space-y-4 text-sm text-gray-700">
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-red-700">
            {error || 'This reset link is invalid or has expired.'}
          </div>
          <Link to="/forgot-password" className="inline-flex text-blue-600 hover:text-blue-700 font-medium">
            Request a new reset link
          </Link>
        </div>
      ) : success ? (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          Password updated. Redirecting to sign in…
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
          {email && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-700">
              Resetting password for <span className="font-medium text-gray-900">{email}</span>
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">New password</label>
            <input
              type="password"
              value={password}
              onChange={(event) => { setPassword(event.target.value); setError(''); }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              autoComplete="new-password"
              placeholder="At least 12 characters"
            />
          </div>
          <button
            type="submit"
            disabled={saving}
            className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? 'Updating…' : 'Update Password'}
          </button>
        </form>
      )}
    </PublicAuthShell>
  );
}
