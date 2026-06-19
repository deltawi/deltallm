import type { AuthSsoConfig, Principal } from './api';

export function hasSandboxSelfRegistration(config: AuthSsoConfig | null | undefined): boolean {
  return Boolean(config?.sso_enabled && config.self_registration?.sandbox_access_enabled);
}

export function isSelfRegisteredPrincipal(principal: Principal | null | undefined): boolean {
  return Boolean(principal?.self_registration?.is_self_registered);
}

export function formatOptionalLimit(value: number | null | undefined): string {
  if (value == null) return 'No limit';
  return Number(value).toLocaleString();
}

export function formatOptionalBudget(value: number | null | undefined): string {
  if (value == null) return 'No limit';
  return `$${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}
