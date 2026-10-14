import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { auth as authApi } from './api/auth';
import { resolveSession } from './authController';
import { uiMount } from './uiMount';
import { AuthContext, type AuthContextValue } from './authContext';
import type { AuthMode, AuthStatus, SessionInfo } from './authTypes';
export type { AuthStatus, SessionInfo } from './authTypes';
import {
  classifySessionCheckError,
  isValidSessionPayload,
  type SessionFailure,
} from './authSession';
import { lastSessionActivityAt, onSessionRejected } from './sessionActivity';
import {
  idleDeadline,
  idlePhase,
  sessionClock,
  signedOutReason as reasonForSignOut,
  type SessionClock,
  type SignedOutReason,
} from './sessionLifetime';
type AuthErrorState = Exclude<SessionFailure, { kind: 'anonymous' }>;

interface AuthState {
  status: AuthStatus;
  authMode: AuthMode | null;
  session: SessionInfo | null;
  error: AuthErrorState | null;
}

const MASTER_KEY_STORAGE = 'deltallm_master_key';
const MFA_SKIP_STORAGE = 'deltallm_mfa_skip';
const SESSION_TICK_MS = 5_000;
// Minimum gap between session checks triggered by focus, deadlines or failed calls.
const VERIFY_THROTTLE_MS = 15_000;
const REJECTED_VERIFY_THROTTLE_MS = 5_000;
const WARN_VERIFY_GAP_MS = 60_000;
const INTERACTION_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const;

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

const SESSION_DEADLINE_FIELDS = new Set(['expires_at', 'idle_expires_at', 'server_time']);

function sameSessionIgnoringDeadlines(current: SessionInfo | null, next: SessionInfo): boolean {
  if (!current) return false;
  const stable = (session: SessionInfo) => JSON.stringify(
    Object.entries(session)
      .filter(([key]) => !SESSION_DEADLINE_FIELDS.has(key))
      .sort(([left], [right]) => left.localeCompare(right)),
  );
  return stable(current) === stable(next);
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
  const [signedOutReason, setSignedOutReason] = useState<SignedOutReason | null>(null);
  const [idleWarningDeadline, setIdleWarningDeadline] = useState<number | null>(null);
  const [idleTimeoutMs, setIdleTimeoutMs] = useState<number | null>(null);
  const clockRef = useRef<SessionClock | null>(null);
  const verifyInFlightRef = useRef<{ promise: Promise<void>; renew: boolean } | null>(null);
  const lastVerifyAtRef = useRef(0);
  const lastInteractionAtRef = useRef(0);

  const rememberSession = useCallback((session: SessionInfo) => {
    const clock = sessionClock(session);
    clockRef.current = clock;
    setIdleTimeoutMs(clock.idleTimeoutMs);
  }, []);

  const endSessionLocally = useCallback(() => {
    const reason = reasonForSignOut(clockRef.current, lastSessionActivityAt());
    clockRef.current = null;
    setStoredMasterKey(null);
    setIdleWarningDeadline(null);
    setSignedOutReason(reason);
    setState(anonymousState());
  }, []);

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
      if (sessionAttemptRef.current === attempt && me.authenticated) {
        rememberSession(me);
        setSignedOutReason(null);
      }
      applyState(me.authenticated ? authenticatedState(me) : anonymousState());
    } catch (error: unknown) {
      const failure = classifySessionCheckError(error);
      if (failure.kind === 'anonymous') setStoredMasterKey(null);
      applyState(failureState(failure));
    }
  }, [rememberSession]);

  // Re-check the session without the loading screen. `renew` counts the check as activity.
  const verifySession = useCallback((renew = false): Promise<void> => {
    const inFlight = verifyInFlightRef.current;
    if (inFlight) {
      // A background check cannot stand in for a renewal; renew once it settles.
      return inFlight.renew || !renew ? inFlight.promise : inFlight.promise.then(() => verifySession(true));
    }
    const attempt = sessionAttemptRef.current;
    lastVerifyAtRef.current = Date.now();
    const run = (async () => {
      let next: SessionInfo;
      try {
        const value = await authApi.me({ background: !renew });
        if (!isValidSessionPayload(value)) return;
        next = value;
      } catch (error: unknown) {
        // Network or server trouble is not proof the session ended; keep the page.
        if (classifySessionCheckError(error).kind !== 'anonymous') return;
        next = { authenticated: false };
      }
      if (sessionAttemptRef.current !== attempt || logoutInFlightRef.current) return;
      if (next.authenticated) {
        rememberSession(next);
        // Keep the same session object when only deadlines moved, so effects keyed on it
        // (unsaved forms, pending saves) are not reset by a routine check.
        setState((current) => (
          current.status === 'authenticated' && sameSessionIgnoringDeadlines(current.session, next)
            ? current
            : authenticatedState(next)
        ));
      } else {
        endSessionLocally();
      }
    })().finally(() => {
      verifyInFlightRef.current = null;
    });
    verifyInFlightRef.current = { promise: run, renew };
    return run;
  }, [endSessionLocally, rememberSession]);

  const staySignedIn = useCallback(async () => {
    await verifySession(true);
    setIdleWarningDeadline(null);
  }, [verifySession]);

  const isSignedIn = state.status === 'authenticated';

  useEffect(() => {
    if (!isSignedIn) return undefined;
    const verifyIfStale = (minGapMs: number) => {
      if (Date.now() - lastVerifyAtRef.current < minGapMs) return;
      void verifySession();
    };
    const stopRejected = onSessionRejected(() => verifyIfStale(REJECTED_VERIFY_THROTTLE_MS));
    const onVisible = () => {
      if (document.visibilityState === 'visible') verifyIfStale(VERIFY_THROTTLE_MS);
    };
    const onInteraction = () => {
      lastInteractionAtRef.current = Date.now();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    INTERACTION_EVENTS.forEach((name) => {
      window.addEventListener(name, onInteraction, { capture: true, passive: true });
    });
    return () => {
      stopRejected();
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
      INTERACTION_EVENTS.forEach((name) => {
        window.removeEventListener(name, onInteraction, { capture: true });
      });
    };
  }, [isSignedIn, verifySession]);

  useEffect(() => {
    // Console-hosted sessions are refreshed by the Console; only 401s and focus apply there.
    if (!isSignedIn || uiMount().external_console) return undefined;
    const tick = () => {
      const clock = clockRef.current;
      if (!clock) return;
      const lastActivityAt = lastSessionActivityAt();
      const phase = idlePhase(clock, lastActivityAt, lastInteractionAtRef.current);
      if (phase === 'warn') {
        // Another tab may have kept the session alive; ask the gateway before prompting.
        if (Date.now() - lastVerifyAtRef.current >= WARN_VERIFY_GAP_MS) {
          void verifySession();
          return;
        }
        setIdleWarningDeadline(idleDeadline(clock, lastActivityAt));
        return;
      }
      setIdleWarningDeadline(null);
      if (phase === 'active' || Date.now() - lastVerifyAtRef.current < VERIFY_THROTTLE_MS) return;
      void verifySession(phase === 'renew');
    };
    tick();
    const timer = window.setInterval(tick, SESSION_TICK_MS);
    return () => window.clearInterval(timer);
  }, [isSignedIn, verifySession]);

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
      clockRef.current = null;
      setIdleWarningDeadline(null);
      setSignedOutReason(null);
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
    signedOutReason,
    idleWarningDeadline,
    idleTimeoutMs,
    staySignedIn,
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
    signedOutReason,
    idleWarningDeadline,
    idleTimeoutMs,
    staySignedIn,
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
