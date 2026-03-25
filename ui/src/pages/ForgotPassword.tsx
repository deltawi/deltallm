import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { auth as authApi } from '../lib/api';
import PublicAuthShell from '../components/auth/PublicAuthShell';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) {
      setError('Email is required');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await authApi.forgotPassword(email.trim());
      setSubmitted(true);
    } catch (err: any) {
      setError(err?.message || 'Unable to request password reset');
    } finally {
      setLoading(false);
    }
  };

  return (
    <PublicAuthShell title="Forgot Password" description="Request a one-time reset link for your account.">
      {submitted ? (
        <div className="space-y-4 text-sm text-gray-700">
          <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-green-800">
            If an account exists for that email, a reset link has been sent.
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
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Email</label>
            <input
              type="email"
              value={email}
              onChange={(event) => { setEmail(event.target.value); setError(''); }}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              placeholder="user@example.com"
              autoComplete="email"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? 'Requesting…' : 'Send Reset Link'}
          </button>
        </form>
      )}
    </PublicAuthShell>
  );
}
