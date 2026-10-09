import type { UIAccess } from './authorization';

export type AuthMode = 'session' | 'master_key';
export type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'retryable_error' | 'fatal_error';

export interface ExternalWorkspace {
  integration_id: string;
  binding_id: string;
  organization_id: string;
  team_id: string;
  inference_user_id: string;
}

export interface SessionInfo {
  authenticated: boolean;
  auth_mode?: AuthMode | null;
  session_source?: 'operator' | 'external_customer' | null;
  workspace?: ExternalWorkspace | null;
  expires_at?: string | null;
  account_id?: string | null;
  email?: string | null;
  role?: string | null;
  effective_permissions?: string[];
  ui_access?: Partial<UIAccess> | null;
  organization_memberships?: Array<Record<string, unknown>>;
  team_memberships?: Array<Record<string, unknown>>;
  mfa_enabled?: boolean;
  mfa_verified?: boolean;
  mfa_prompt?: boolean;
  force_password_change?: boolean;
}

export function sessionPrincipal(session: SessionInfo | null): string {
  if (!session?.authenticated) return 'anonymous';
  return JSON.stringify([session.auth_mode, session.account_id, session.session_source,
    session.workspace?.integration_id, session.workspace?.binding_id,
    session.workspace?.organization_id, session.workspace?.team_id]);
}
