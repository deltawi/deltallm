import { useState } from 'react';
import type { FormEvent } from 'react';
import { ShieldCheck } from 'lucide-react';
import { auth as authApi } from '../lib/api';
import { useAuth } from '../lib/auth';

export default function MFAVerify() {
  const { refreshSession, logout } = useAuth();
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (code.length !== 6) {
      setError('Please enter a 6-digit code');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await authApi.mfaVerify(code);
      await refreshSession();
    } catch (err: any) {
      setError(err?.message || 'Invalid MFA code');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4">
            <ShieldCheck className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Verify Your Session</h1>
          <p className="text-gray-500 mt-2">Enter the code from your authenticator app to continue.</p>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Verification Code</label>
              <input
                type="text"
                value={code}
                onChange={(event) => { setCode(event.target.value.replace(/\D/g, '').slice(0, 6)); setError(''); }}
                placeholder="Enter 6-digit code"
                autoComplete="one-time-code"
                maxLength={6}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono tracking-wider text-center text-lg"
              />
            </div>
            <button
              type="submit"
              disabled={loading || code.length !== 6}
              className="w-full bg-blue-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {loading ? 'Verifying...' : 'Verify MFA'}
            </button>
          </form>

          <button
            type="button"
            onClick={() => { void logout(); }}
            className="mt-4 w-full border border-gray-300 text-gray-700 py-2.5 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
          >
            Sign Out
          </button>
        </div>
      </div>
    </div>
  );
}
