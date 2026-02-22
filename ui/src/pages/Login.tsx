import { useState, useEffect } from 'react';
import type { FormEvent } from 'react';
import { useAuth } from '../lib/auth';
import { auth as authApi } from '../lib/api';
import { Zap, Mail, KeyRound, Globe } from 'lucide-react';

type Tab = 'credentials' | 'master_key' | 'sso';

const SSO_PROVIDER_LABELS: Record<string, string> = {
  microsoft: 'Microsoft',
  google: 'Google',
  okta: 'Okta',
  oidc: 'SSO',
};

export default function Login() {
  const { loginWithCredentials, loginWithMasterKey, isLoading } = useAuth();
  const [tab, setTab] = useState<Tab>('credentials');

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [showMfa, setShowMfa] = useState(false);

  const [masterKey, setMasterKey] = useState('');

  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [ssoProvider, setSsoProvider] = useState('oidc');
  const [ssoLoading, setSsoLoading] = useState(true);

  useEffect(() => {
    authApi.ssoConfig()
      .then((cfg) => {
        setSsoEnabled(cfg.sso_enabled);
        if (cfg.provider) setSsoProvider(cfg.provider);
      })
      .catch(() => {
        setSsoEnabled(false);
      })
      .finally(() => setSsoLoading(false));
  }, []);

  if (isLoading || ssoLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  const handleCredentialLogin = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) {
      setError('Please enter your email and password');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await loginWithCredentials(email.trim(), password.trim(), mfaCode.trim() || undefined);
    } catch (err: any) {
      const msg = err?.message || 'Login failed';
      if (msg.toLowerCase().includes('mfa') || msg.toLowerCase().includes('invalid credentials')) {
        if (!showMfa) setShowMfa(true);
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleMasterKeyLogin = async (e: FormEvent) => {
    e.preventDefault();
    if (!masterKey.trim()) {
      setError('Please enter your master key');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await loginWithMasterKey(masterKey.trim());
    } catch (err: any) {
      setError(err?.message || 'Invalid master key');
    } finally {
      setLoading(false);
    }
  };

  const handleSsoLogin = async () => {
    setLoading(true);
    setError('');
    try {
      const state = crypto.randomUUID();
      const { authorize_url } = await authApi.ssoLogin(state);
      window.location.href = authorize_url;
    } catch (err: any) {
      setError(err?.message || 'Failed to start SSO login');
      setLoading(false);
    }
  };

  const providerLabel = SSO_PROVIDER_LABELS[ssoProvider] || 'SSO';

  const tabs: { key: Tab; label: string; icon: typeof Mail }[] = [
    { key: 'credentials', label: 'Email Login', icon: Mail },
  ];
  if (ssoEnabled) {
    tabs.push({ key: 'sso', label: providerLabel, icon: Globe });
  }
  tabs.push({ key: 'master_key', label: 'Master Key', icon: KeyRound });

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4">
            <Zap className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">DeltaLLM Admin</h1>
          <p className="text-gray-500 mt-2">Sign in to continue</p>
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="flex border-b border-gray-200">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => { setTab(t.key); setError(''); }}
                className={`flex-1 flex items-center justify-center gap-2 py-3 text-sm font-medium transition-colors ${
                  tab === t.key
                    ? 'text-blue-600 border-b-2 border-blue-600 bg-blue-50/50'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <t.icon className="w-4 h-4" />
                {t.label}
              </button>
            ))}
          </div>

          <div className="p-6">
            {error && (
              <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                {error}
              </div>
            )}

            {tab === 'credentials' && (
              <form onSubmit={handleCredentialLogin}>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => { setEmail(e.target.value); setError(''); }}
                    placeholder="admin@example.com"
                    autoComplete="email"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Password</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => { setPassword(e.target.value); setError(''); }}
                    placeholder="Enter your password"
                    autoComplete="current-password"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
                {showMfa && (
                  <div className="mb-4">
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">MFA Code</label>
                    <input
                      type="text"
                      value={mfaCode}
                      onChange={(e) => { setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6)); setError(''); }}
                      placeholder="6-digit code"
                      autoComplete="one-time-code"
                      maxLength={6}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono tracking-wider"
                    />
                  </div>
                )}
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-blue-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
                >
                  {loading ? 'Signing in...' : 'Sign In'}
                </button>
              </form>
            )}

            {tab === 'sso' && (
              <div className="space-y-4">
                <p className="text-sm text-gray-600 text-center">
                  Sign in with your organization's {providerLabel} identity provider.
                </p>
                <button
                  onClick={handleSsoLogin}
                  disabled={loading}
                  className="w-full bg-blue-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  <Globe className="w-4 h-4" />
                  {loading ? 'Redirecting...' : `Sign In with ${providerLabel}`}
                </button>
              </div>
            )}

            {tab === 'master_key' && (
              <form onSubmit={handleMasterKeyLogin}>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Master Key</label>
                  <input
                    type="password"
                    value={masterKey}
                    onChange={(e) => { setMasterKey(e.target.value); setError(''); }}
                    placeholder="sk-..."
                    autoComplete="off"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-blue-600 text-white py-2.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
                >
                  {loading ? 'Signing in...' : 'Sign In with Master Key'}
                </button>
              </form>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
