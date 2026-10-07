import { createContext, useContext } from 'react';
import type { SessionFailure } from './authSession';
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
