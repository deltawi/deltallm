import { createContext, useContext } from 'react';
import type { SessionFailure } from './authSession';
import type { SignedOutReason } from './sessionLifetime';
import { sessionPrincipal, type AuthMode, type AuthStatus, type SessionInfo } from './authTypes';

type AuthErrorState = Exclude<SessionFailure, { kind: 'anonymous' }>;

export interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  authStatus: AuthStatus;
  authMode: AuthMode | null;
  session: SessionInfo | null;
  authError: AuthErrorState | null;
  isLoggingOut: boolean;
  logoutError: string | null;
  mfaSkipped: boolean;
  /** Why the last session ended on its own; null after a sign-in or a manual sign-out. */
  signedOutReason: SignedOutReason | null;
  /** Browser time when an idle session will be signed out, while the warning shows. */
  idleWarningDeadline: number | null;
  idleTimeoutMs: number | null;
  staySignedIn: () => Promise<void>;
  loginWithCredentials: (email: string, password: string, mfaCode?: string) => Promise<void>;
  loginWithMasterKey: (masterKey: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshSession: () => Promise<void>;
  retrySession: () => Promise<void>;
  skipMfa: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}

export function useSessionPrincipal(): string {
  const context = useContext(AuthContext);
  return sessionPrincipal(context?.session || null);
}
