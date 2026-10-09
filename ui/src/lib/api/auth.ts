import { apiFetch } from './transport';
import type { InvitationAcceptResult } from '../api';

export interface SelfRegistrationPublicConfig {
  enabled: boolean;
  mode: string | null;
  sandbox_access_enabled: boolean;
}

export interface AuthSsoConfig {
  sso_enabled: boolean;
  provider?: string;
  self_registration?: SelfRegistrationPublicConfig;
}

export const auth = {
  me: () => apiFetch<unknown>('/auth/me', { headers: new Headers({ 'Content-Type': 'application/json' }) }),
  internalLogin: (payload: { email: string; password: string; mfa_code?: string }) =>
    apiFetch<unknown>('/auth/internal/login', { method: 'POST', json: payload }),
  masterLogin: (masterKey: string) =>
    apiFetch<unknown>('/auth/master/login', { method: 'POST', json: { master_key: masterKey } }),
  internalLogout: () => apiFetch<unknown>('/auth/internal/logout', { method: 'POST' }),
  changePassword: (current_password: string | null, new_password: string) =>
    apiFetch<unknown>('/auth/internal/change-password', { method: 'POST', json: { current_password, new_password } }),
  invitation: (token: string) => apiFetch<unknown>(`/auth/invitations/${encodeURIComponent(token)}`),
  acceptInvitation: (payload: { token: string; password?: string | null }) =>
    apiFetch<InvitationAcceptResult>('/auth/invitations/accept', { method: 'POST', json: payload }),
  forgotPassword: (email: string) =>
    apiFetch<{ requested: boolean }>('/auth/internal/forgot-password', { method: 'POST', json: { email } }),
  validateResetPasswordToken: (token: string) =>
    apiFetch<{ valid: boolean; email?: string }>(`/auth/internal/reset-password/${encodeURIComponent(token)}`),
  resetPassword: (token: string, new_password: string) =>
    apiFetch<{ changed: boolean }>('/auth/internal/reset-password', { method: 'POST', json: { token, new_password } }),
  ssoConfig: () => apiFetch<AuthSsoConfig>('/auth/sso-config'),
  ssoLogin: (state: string, returnTo = '/') => apiFetch<{ authorize_url: string }>(
    `/auth/login?state=${encodeURIComponent(state)}&return_to=${encodeURIComponent(returnTo)}`,
  ),
  mfaEnrollStart: () => apiFetch<{ secret: string; otpauth_url: string }>('/auth/mfa/enroll/start', { method: 'POST' }),
  mfaEnrollConfirm: (code: string) => apiFetch<{ mfa_enabled: boolean }>('/auth/mfa/enroll/confirm', { method: 'POST', json: { code } }),
  mfaVerify: (code: string) => apiFetch<{ mfa_verified: boolean }>('/auth/mfa/verify', { method: 'POST', json: { code } }),
};
