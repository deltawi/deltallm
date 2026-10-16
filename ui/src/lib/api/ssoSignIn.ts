import { apiFetch } from './transport';

export interface SsoSignInRules {
  allow_new_accounts: boolean;
  allowed_email_domains: string[];
}

export interface SignInSettings {
  password_login_enabled: boolean;
  sso_sign_in: SsoSignInRules;
}

export type CallerSignIn = 'password' | 'sso' | 'master_key';

export interface SignInSettingsResponse extends SignInSettings {
  sso_available: boolean;
  self_registration_enabled: boolean;
  caller_sign_in: CallerSignIn;
  caller_email_domain: string | null;
}

const PATH = '/ui/api/settings/sso-sign-in';

export const ssoSignIn = {
  get: (signal?: AbortSignal) => apiFetch<SignInSettingsResponse>(PATH, { signal }),
  update: (settings: SignInSettings, signal?: AbortSignal) =>
    apiFetch<SignInSettingsResponse>(PATH, { method: 'PUT', json: settings, signal }),
};
