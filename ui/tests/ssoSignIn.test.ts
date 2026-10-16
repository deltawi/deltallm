import assert from 'node:assert/strict';
import test from 'node:test';

import type { SignInSettingsResponse } from '../src/lib/api/ssoSignIn';
import {
  addEmailDomains,
  buildSignInPayload,
  DEFAULT_SIGN_IN_SETTINGS,
  parseEmailDomains,
  sameSignInSettings,
  showsPasswordLogin,
  validateSignInSettings,
} from '../src/lib/ssoSignIn';

const context: Pick<SignInSettingsResponse, 'sso_available' | 'self_registration_enabled' | 'caller_sign_in' | 'caller_email_domain'> = {
  sso_available: true,
  self_registration_enabled: false,
  caller_sign_in: 'sso',
  caller_email_domain: 'example.com',
};

test('the login page hides email login only when the server turns it off', () => {
  assert.equal(showsPasswordLogin({ sso_enabled: true }), true);
  assert.equal(showsPasswordLogin({ sso_enabled: true, password_login_enabled: true }), true);
  assert.equal(showsPasswordLogin({ sso_enabled: true, password_login_enabled: false }), false);
  assert.equal(showsPasswordLogin(null), true);
});

test('domains are split, lower-cased, and deduplicated', () => {
  assert.deepEqual(parseEmailDomains(' Example.com, @b.org  c.io;'), ['example.com', 'b.org', 'c.io']);
  assert.deepEqual(addEmailDomains(['example.com'], 'EXAMPLE.com b.org'), ['example.com', 'b.org']);
});

test('the payload keeps only the saved fields', () => {
  const response: SignInSettingsResponse = { ...DEFAULT_SIGN_IN_SETTINGS, ...context };
  assert.deepEqual(buildSignInPayload(response), DEFAULT_SIGN_IN_SETTINGS);
  assert.equal(sameSignInSettings(response, DEFAULT_SIGN_IN_SETTINGS), true);
  assert.equal(sameSignInSettings({ ...DEFAULT_SIGN_IN_SETTINGS, password_login_enabled: false }, DEFAULT_SIGN_IN_SETTINGS), false);
});

test('validation stops the same lockouts as the server', () => {
  const ssoOnly = { password_login_enabled: false, sso_sign_in: { allow_new_accounts: false, allowed_email_domains: ['example.com'] } };
  assert.equal(validateSignInSettings(ssoOnly, context), null);
  assert.match(validateSignInSettings(ssoOnly, { ...context, caller_sign_in: 'password' }) ?? '', /signed in with a password/);
  assert.match(validateSignInSettings(ssoOnly, { ...context, sso_available: false }) ?? '', /SSO is not running/);
  assert.match(
    validateSignInSettings({ ...ssoOnly, sso_sign_in: { ...ssoOnly.sso_sign_in, allowed_email_domains: ['other.com'] } }, context) ?? '',
    /your own email domain \(example.com\)/i,
  );
  assert.equal(
    validateSignInSettings({ ...ssoOnly, sso_sign_in: { ...ssoOnly.sso_sign_in, allowed_email_domains: ['other.com'] } }, { ...context, caller_sign_in: 'master_key', caller_email_domain: null }),
    null,
  );
  assert.match(validateSignInSettings(ssoOnly, { ...context, self_registration_enabled: true }) ?? '', /Self-registration/);
  assert.match(
    validateSignInSettings({ ...DEFAULT_SIGN_IN_SETTINGS, sso_sign_in: { allow_new_accounts: true, allowed_email_domains: ['a/b'] } }, context) ?? '',
    /not an email domain/,
  );
});
