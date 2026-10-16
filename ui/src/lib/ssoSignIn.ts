import type { AuthSsoConfig } from './api/auth';
import type { SignInSettings, SignInSettingsResponse } from './api/ssoSignIn';

export const DEFAULT_SIGN_IN_SETTINGS: SignInSettings = {
  password_login_enabled: true,
  sso_sign_in: { allow_new_accounts: true, allowed_email_domains: [] },
};

/** Older servers do not send the flag, and they always allow password sign-in. */
export function showsPasswordLogin(config: AuthSsoConfig | null | undefined): boolean {
  return config?.password_login_enabled !== false;
}

/** Domains typed into the chip input: split on commas and spaces, lower case, no leading "@". */
export function parseEmailDomains(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((item) => item.trim().toLowerCase().replace(/^@/, ''))
    .filter(Boolean);
}

export function addEmailDomains(current: string[], raw: string): string[] {
  const next = [...current];
  for (const domain of parseEmailDomains(raw)) {
    if (!next.includes(domain)) next.push(domain);
  }
  return next;
}

export function buildSignInPayload(settings: SignInSettings): SignInSettings {
  return {
    password_login_enabled: settings.password_login_enabled,
    sso_sign_in: {
      allow_new_accounts: settings.sso_sign_in.allow_new_accounts,
      allowed_email_domains: settings.sso_sign_in.allowed_email_domains,
    },
  };
}

export function sameSignInSettings(a: SignInSettings, b: SignInSettings): boolean {
  return JSON.stringify(buildSignInPayload(a)) === JSON.stringify(buildSignInPayload(b));
}

/**
 * The first problem that the server would also reject, as a sentence for the form.
 * The checks stop an admin from locking themselves out. The master key still works.
 */
export function validateSignInSettings(
  settings: SignInSettings,
  context: Pick<SignInSettingsResponse, 'sso_available' | 'self_registration_enabled' | 'caller_sign_in' | 'caller_email_domain'>,
): string | null {
  for (const domain of settings.sso_sign_in.allowed_email_domains) {
    if (/[@/\\]/.test(domain) || domain.startsWith('.') || domain.endsWith('.')) {
      return `"${domain}" is not an email domain. Enter a domain such as example.com.`;
    }
  }
  if (!settings.password_login_enabled) {
    if (!context.sso_available) return 'SSO is not running, so password sign-in must stay on.';
    if (context.caller_sign_in === 'password') {
      return 'You signed in with a password. Sign in with SSO first, then turn off password sign-in.';
    }
  }
  const domains = settings.sso_sign_in.allowed_email_domains;
  if (context.caller_sign_in === 'sso' && domains.length > 0 && !domains.includes(context.caller_email_domain ?? '')) {
    return `Add your own email domain (${context.caller_email_domain ?? 'unknown'}) to the list, or you cannot sign in with SSO again.`;
  }
  if (!settings.sso_sign_in.allow_new_accounts && context.self_registration_enabled) {
    return 'Self-registration creates new accounts. Turn off self_registration in config.yaml first.';
  }
  return null;
}
