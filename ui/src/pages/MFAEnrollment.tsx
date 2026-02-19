import { useState } from 'react';
import type { FormEvent } from 'react';
import { useAuth } from '../lib/auth';
import { auth as authApi } from '../lib/api';
import { ShieldCheck, Copy, Check } from 'lucide-react';

export default function MFAEnrollment() {
  const { refreshSession, skipMfa } = useAuth();
  const [step, setStep] = useState<'prompt' | 'setup' | 'done'>('prompt');
  const [secret, setSecret] = useState('');
  const [otpauthUrl, setOtpauthUrl] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleStartEnroll = async () => {
    setLoading(true);
    setError('');
    try {
      const result = await authApi.mfaEnrollStart();
      setSecret(result.secret);
      setOtpauthUrl(result.otpauth_url);
      setStep('setup');
    } catch (err: any) {
      setError(err?.message || 'Failed to start MFA enrollment');
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = async (e: FormEvent) => {
    e.preventDefault();
    if (code.length !== 6) {
      setError('Please enter a 6-digit code');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await authApi.mfaEnrollConfirm(code);
      setStep('done');
      await refreshSession();
    } catch (err: any) {
      setError(err?.message || 'Invalid code. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleSkip = () => {
    skipMfa();
  };

  const copySecret = async () => {
    await navigator.clipboard.writeText(secret);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (step === 'done') {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
        <div className="max-w-md w-full text-center">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-green-500 rounded-2xl mb-4">
            <Check className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900 mb-2">MFA Enabled</h1>
          <p className="text-gray-500 mb-6">Two-factor authentication is now active on your account.</p>
          <button
            onClick={async () => { await refreshSession(); skipMfa(); }}
            className="bg-blue-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            Continue to Dashboard
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4">
            <ShieldCheck className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">
            {step === 'prompt' ? 'Enable Two-Factor Authentication' : 'Set Up Your Authenticator'}
          </h1>
          <p className="text-gray-500 mt-2">
            {step === 'prompt'
              ? 'Add an extra layer of security to your account'
              : 'Add this account to your authenticator app'}
          </p>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              {error}
            </div>
          )}

          {step === 'prompt' ? (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                Two-factor authentication adds an extra layer of security by requiring a code from your authenticator app when signing in.
              </p>
              <button
                onClick={handleStartEnroll}
                disabled={loading}
                className="w-full bg-blue-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
              >
                {loading ? 'Setting up...' : 'Set Up MFA'}
              </button>
              <button
                onClick={handleSkip}
                className="w-full border border-gray-300 text-gray-700 py-2.5 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
              >
                Skip for Now
              </button>
            </div>
          ) : (
            <form onSubmit={handleConfirm} className="space-y-4">
              <div>
                <p className="text-sm text-gray-600 mb-3">
                  Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.), or manually enter the secret key below.
                </p>
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 text-center">
                  <img
                    src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(otpauthUrl)}`}
                    alt="MFA QR Code"
                    className="mx-auto mb-3"
                    width={200}
                    height={200}
                  />
                  <div className="flex items-center gap-2 justify-center">
                    <code className="text-xs bg-white px-2 py-1 rounded border border-gray-200 font-mono select-all">
                      {secret}
                    </code>
                    <button
                      type="button"
                      onClick={copySecret}
                      className="p-1 text-gray-400 hover:text-gray-600"
                      title="Copy secret"
                    >
                      {copied ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4" />}
                    </button>
                  </div>
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">Verification Code</label>
                <input
                  type="text"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
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
                {loading ? 'Verifying...' : 'Verify & Enable MFA'}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
