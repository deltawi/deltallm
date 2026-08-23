import { useState, useEffect } from 'react';
import type { FormEvent } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { auth as authApi, type AuthSsoConfig } from '../lib/api';
import { hasSandboxSelfRegistration } from '../lib/selfRegistration';
import { returnToFromSearch } from '../lib/authRedirect';
import { Mail, KeyRound, Globe } from 'lucide-react';
import BrandLogo from '../components/BrandLogo';
import Button from '../components/Button';

type Tab = 'credentials' | 'master_key' | 'sso';

const SSO_PROVIDER_LABELS: Record<string, string> = {
  microsoft: 'Microsoft',
  google: 'Google',
  okta: 'Okta',
  oidc: 'SSO',
};

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export default function Login() {
  const { loginWithCredentials, loginWithMasterKey, isLoading } = useAuth();
  const location = useLocation();
  const returnTo = returnToFromSearch(location.search);
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
  const [sandboxSelfRegistration, setSandboxSelfRegistration] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(true);

  useEffect(() => {
    authApi.ssoConfig()
      .then((cfg: AuthSsoConfig) => {
        setSsoEnabled(cfg.sso_enabled);
        setSandboxSelfRegistration(hasSandboxSelfRegistration(cfg));
        if (cfg.provider) setSsoProvider(cfg.provider);
      })
      .catch(() => {
        setSsoEnabled(false);
        setSandboxSelfRegistration(false);
      })
      .finally(() => setSsoLoading(false));
  }, []);

  if (isLoading || ssoLoading) {
    return (
      <div className="brand-auth-background min-h-screen flex items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-brand-primary" />
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
    } catch (err: unknown) {
      const msg = errorMessage(err, 'Login failed');
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
    } catch (err: unknown) {
      setError(errorMessage(err, 'Invalid master key'));
    } finally {
      setLoading(false);
    }
  };

  const handleSsoLogin = async () => {
    setLoading(true);
    setError('');
    try {
      const state = crypto.randomUUID();
      const { authorize_url } = await authApi.ssoLogin(state, returnTo);
      window.location.href = authorize_url;
    } catch (err: unknown) {
      setError(errorMessage(err, 'Failed to start SSO login'));
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
    <div className="brand-auth-background min-h-screen flex items-center justify-center px-4">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <BrandLogo
            variant="reveal"
            className="mb-4 justify-center"
            markClassName="h-16 w-16 rounded-2xl"
            fullClassName="h-16"
          />
          <h1 className="text-2xl font-bold text-gray-900">Admin Console</h1>
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
                    ? 'border-b-2 border-brand-primary bg-brand-primary-soft text-brand-primary-ink'
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
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-brand-primary"
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
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-brand-primary"
                  />
                </div>
                <div className="mb-4 flex justify-end">
                  <Link to="/forgot-password" className="text-sm font-medium text-brand-primary-ink hover:text-brand-primary-ink-hover">
                    Forgot password?
                  </Link>
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
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm tracking-wider focus:border-transparent focus:outline-none focus:ring-2 focus:ring-brand-primary"
                    />
                  </div>
                )}
                <Button
                  type="submit"
                  disabled={loading}
                  size="lg"
                  fullWidth
                >
                  {loading ? 'Signing in...' : 'Sign In'}
                </Button>
              </form>
            )}

            {tab === 'sso' && (
              <div className="space-y-4">
                <p className="text-sm text-gray-600 text-center">
                  {sandboxSelfRegistration
                    ? `Sign in with your organization's ${providerLabel} identity provider for managed sandbox access.`
                    : `Sign in with your organization's ${providerLabel} identity provider.`}
                </p>
                {sandboxSelfRegistration ? (
                  <div className="rounded-lg border border-brand-primary/20 bg-brand-primary-soft px-3 py-2 text-xs text-brand-primary-ink">
                    Eligible first-time users are placed in an admin-managed developer sandbox with limited budgets, rate limits, and self-service key policy.
                  </div>
                ) : null}
                <Button
                  onClick={handleSsoLogin}
                  disabled={loading}
                  size="lg"
                  fullWidth
                >
                  <Globe className="w-4 h-4" />
                  {loading ? 'Redirecting...' : `Sign In with ${providerLabel}`}
                </Button>
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
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-brand-primary"
                  />
                </div>
                <Button
                  type="submit"
                  disabled={loading}
                  size="lg"
                  fullWidth
                >
                  {loading ? 'Signing in...' : 'Sign In with Master Key'}
                </Button>
              </form>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
