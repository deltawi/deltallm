import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { auth as authApi } from './api/auth';
import { resolveSession } from './authController';
import { uiMount } from './uiMount';
import { AuthContext, type AuthContextValue } from './authContext';
import type { AuthMode, AuthStatus, SessionInfo } from './authTypes';
export type { AuthStatus, SessionInfo } from './authTypes';
import {
  classifySessionCheckError,
  type SessionFailure,
} from './authSession';
type AuthErrorState = Exclude<SessionFailure, { kind: 'anonymous' }>;

interface AuthState {
  status: AuthStatus;
  authMode: AuthMode | null;
  session: SessionInfo | null;
  error: AuthErrorState | null;
}

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

function authenticatedState(session: SessionInfo): AuthState {
  return {
    status: 'authenticated',
    authMode: session.auth_mode === 'master_key' ? 'master_key' : 'session',
    session,
    error: null,
  };
}

function anonymousState(): AuthState {
  return { status: 'anonymous', authMode: null, session: { authenticated: false }, error: null };
}

function failureState(failure: SessionFailure): AuthState {
  if (failure.kind === 'anonymous') return anonymousState();
  return {
    status: failure.kind === 'retryable' ? 'retryable_error' : 'fatal_error',
    authMode: null,
    session: null,
    error: failure,
  };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: 'loading',
    authMode: null,
    session: null,
    error: null,
  });
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [mfaSkipped, setMfaSkipped] = useState(getStoredMfaSkip());
  const sessionAttemptRef = useRef(0);
  const logoutInFlightRef = useRef(false);

  const refreshSession = useCallback(async () => {
    if (logoutInFlightRef.current) return;
    const attempt = ++sessionAttemptRef.current;
    const applyState = (nextState: AuthState) => {
      if (sessionAttemptRef.current === attempt) setState(nextState);
    };
    setState((current) => ({ ...current, status: 'loading', error: null }));

    const external = uiMount().external_console;
    if (external) setStoredMasterKey(null);
    try {
      const me = await resolveSession(authApi, external ? null : getStoredMasterKey(), external);
      setStoredMasterKey(null);
      applyState(me.authenticated ? authenticatedState(me) : anonymousState());
    } catch (error: unknown) {
      const failure = classifySessionCheckError(error);
      if (failure.kind === 'anonymous') setStoredMasterKey(null);
      applyState(failureState(failure));
    }
  }, []);

  const retrySession = useCallback(async () => {
    await refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    void refreshSession();
    return () => {
      sessionAttemptRef.current += 1;
    };
  }, [refreshSession]);

  const loginWithCredentials = useCallback(async (email: string, password: string, mfaCode?: string) => {
    if (uiMount().external_console) throw new Error('Sign in through the Console');
    await authApi.internalLogin({ email, password, mfa_code: mfaCode });
    setStoredMfaSkip(false);
    setMfaSkipped(false);
    await refreshSession();
  }, [refreshSession]);

  const loginWithMasterKey = useCallback(async (key: string) => {
    if (uiMount().external_console) throw new Error('Sign in through the Console');
    const value = key.trim();
    if (!value) throw new Error('Master key is required');

    await authApi.masterLogin(value);
    setStoredMasterKey(null);
    await refreshSession();
  }, [refreshSession]);

  const logout = useCallback(async () => {
    if (logoutInFlightRef.current) return;
    logoutInFlightRef.current = true;
    setIsLoggingOut(true);
    setLogoutError(null);
    try {
      sessionAttemptRef.current += 1;
      await authApi.internalLogout();
      setStoredMasterKey(null);
      setStoredMfaSkip(false);
      setMfaSkipped(false);
      setState(anonymousState());
    } catch (error: unknown) {
      const message = error instanceof Error && error.message
        ? error.message
        : 'Sign out could not be completed. Your session is still active.';
      setLogoutError(message);
      throw error;
    } finally {
      logoutInFlightRef.current = false;
      setIsLoggingOut(false);
    }
  }, []);

  const skipMfa = useCallback(() => {
    setStoredMfaSkip(true);
    setMfaSkipped(true);
  }, []);

  const isAuthenticated = state.status === 'authenticated' && !!state.session?.authenticated;
  const isLoading = state.status === 'loading';

  const value = useMemo<AuthContextValue>(() => ({
    isAuthenticated,
    isLoading,
    authStatus: state.status,
    authMode: state.authMode,
    session: state.session,
    authError: state.error,
    isLoggingOut,
    logoutError,
    mfaSkipped,
    loginWithCredentials,
    loginWithMasterKey,
    logout,
    refreshSession,
    retrySession,
    skipMfa,
  }), [
    isAuthenticated,
    isLoading,
    state,
    isLoggingOut,
    logoutError,
    mfaSkipped,
    loginWithCredentials,
    loginWithMasterKey,
    logout,
    refreshSession,
    retrySession,
    skipMfa,
  ]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export { useAuth } from './authContext';
