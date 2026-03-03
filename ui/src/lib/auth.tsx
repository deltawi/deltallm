import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { auth as authApi, models, setMasterKey } from './api';

type AuthMode = 'session' | 'master_key';

export interface SessionInfo {
  authenticated: boolean;
  account_id?: string | null;
  email?: string | null;
  role?: string | null;
  mfa_enabled?: boolean;
  mfa_prompt?: boolean;
  force_password_change?: boolean;
}

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  authMode: AuthMode | null;
  session: SessionInfo | null;
  mfaSkipped: boolean;
  loginWithCredentials: (email: string, password: string, mfaCode?: string) => Promise<void>;
  loginWithMasterKey: (masterKey: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
  skipMfa: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const MASTER_KEY_STORAGE = 'deltallm_master_key';
const MFA_SKIP_STORAGE = 'deltallm_mfa_skip';

function getStoredMasterKey(): string | null {
  try {
    return sessionStorage.getItem(MASTER_KEY_STORAGE);
  } catch {
    return null;
  }
}

function setStoredMasterKey(value: string | null) {
  try {
    if (!value) sessionStorage.removeItem(MASTER_KEY_STORAGE);
    else sessionStorage.setItem(MASTER_KEY_STORAGE, value);
  } catch {
    // ignore
  }
}

function getStoredMfaSkip(): boolean {
  try {
    return sessionStorage.getItem(MFA_SKIP_STORAGE) === '1';
  } catch {
    return false;
  }
}

function setStoredMfaSkip(value: boolean) {
  try {
    if (value) sessionStorage.setItem(MFA_SKIP_STORAGE, '1');
    else sessionStorage.removeItem(MFA_SKIP_STORAGE);
  } catch {
    // ignore
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [authMode, setAuthMode] = useState<AuthMode | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [mfaSkipped, setMfaSkipped] = useState(getStoredMfaSkip());

  const refreshSession = useCallback(async () => {
    const me = await authApi.me();
    if (me?.authenticated) {
      setSession(me);
      setAuthMode('session');
      setStoredMasterKey(null);
      setMasterKey(null);
      return;
    }
    setSession(me || { authenticated: false });
    if (getStoredMasterKey()) {
      setAuthMode('master_key');
    } else {
      setAuthMode(null);
    }
  }, []);

  useEffect(() => {
    const mk = getStoredMasterKey();
    if (mk) setMasterKey(mk);
    refreshSession()
      .catch(() => {
        // If session check fails (backend down), fall back to master key if present.
        const stored = getStoredMasterKey();
        if (stored) {
          setAuthMode('master_key');
          setSession(null);
        } else {
          setAuthMode(null);
          setSession(null);
        }
      })
      .finally(() => setIsLoading(false));
  }, [refreshSession]);

  const loginWithCredentials = useCallback(async (email: string, password: string, mfaCode?: string) => {
    await authApi.internalLogin({ email, password, mfa_code: mfaCode });
    await refreshSession();
    setStoredMfaSkip(false);
    setMfaSkipped(false);
  }, [refreshSession]);

  const loginWithMasterKey = useCallback(async (key: string) => {
    const value = key.trim();
    if (!value) throw new Error('Master key is required');

    // Set first so validation request uses it.
    setStoredMasterKey(value);
    setMasterKey(value);

    try {
      await models.list();
    } catch (err: any) {
      setStoredMasterKey(null);
      setMasterKey(null);
      throw err;
    }

    setAuthMode('master_key');
    setSession({ authenticated: true, role: 'platform_admin' });
  }, []);

  const logout = useCallback(async () => {
    const mode = authMode;
    setAuthMode(null);
    setSession({ authenticated: false });
    setStoredMasterKey(null);
    setMasterKey(null);
    setStoredMfaSkip(false);
    setMfaSkipped(false);
    if (mode === 'session') {
      try {
        await authApi.internalLogout();
      } catch {
        // ignore
      }
    }
  }, [authMode]);

  const skipMfa = useCallback(() => {
    setStoredMfaSkip(true);
    setMfaSkipped(true);
  }, []);

  const isAuthenticated = authMode === 'master_key'
    ? !!getStoredMasterKey()
    : !!session?.authenticated;

  const value = useMemo<AuthContextValue>(() => ({
    isAuthenticated,
    isLoading,
    authMode,
    session,
    mfaSkipped,
    loginWithCredentials,
    loginWithMasterKey,
    logout,
    refreshSession,
    skipMfa,
  }), [
    isAuthenticated,
    isLoading,
    authMode,
    session,
    mfaSkipped,
    loginWithCredentials,
    loginWithMasterKey,
    logout,
    refreshSession,
    skipMfa,
  ]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

