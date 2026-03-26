export type UIAccess = {
  dashboard: boolean;
  models: boolean;
  model_admin: boolean;
  route_groups: boolean;
  prompts: boolean;
  mcp_servers: boolean;
  mcp_approvals: boolean;
  keys: boolean;
  organizations: boolean;
  organization_create: boolean;
  teams: boolean;
  team_create: boolean;
  people_access: boolean;
  usage: boolean;
  audit: boolean;
  batches: boolean;
  guardrails: boolean;
  playground: boolean;
  settings: boolean;
};

export type UIAccessKey = keyof UIAccess;

type SessionLike = {
  authenticated?: boolean;
  role?: string | null;
  effective_permissions?: string[] | null;
  ui_access?: Partial<UIAccess> | null;
  organization_memberships?: Array<{ role?: string | null }> | null;
};

function emptyUiAccess(): UIAccess {
  return {
    dashboard: false,
    models: false,
    model_admin: false,
    route_groups: false,
    prompts: false,
    mcp_servers: false,
    mcp_approvals: false,
    keys: false,
    organizations: false,
    organization_create: false,
    teams: false,
    team_create: false,
    people_access: false,
    usage: false,
    audit: false,
    batches: false,
    guardrails: false,
    playground: false,
    settings: false,
  };
}

function fullUiAccess(): UIAccess {
  return {
    dashboard: true,
    models: true,
    model_admin: true,
    route_groups: true,
    prompts: true,
    mcp_servers: true,
    mcp_approvals: true,
    keys: true,
    organizations: true,
    organization_create: true,
    teams: true,
    team_create: true,
    people_access: true,
    usage: true,
    audit: true,
    batches: true,
    guardrails: true,
    playground: true,
    settings: true,
  };
}

function deriveUiAccessFromPermissions(session: SessionLike | null | undefined): UIAccess {
  if (!session?.authenticated) {
    return emptyUiAccess();
  }
  const permissions = new Set((session.effective_permissions || []).map((value) => String(value)));
  const isPlatformAdmin = session.role === 'platform_admin';
  const canReadKeys = permissions.has('key.read') || permissions.has('key.update') || permissions.has('key.create_self');
  const canCreateTeamInOrganization = isPlatformAdmin || (session.organization_memberships || []).some((membership) => {
    const role = String(membership?.role || '');
    return role === 'org_admin' || role === 'org_owner';
  });

  return {
    dashboard: isPlatformAdmin || permissions.has('spend.read'),
    models: true,
    model_admin: isPlatformAdmin,
    route_groups: isPlatformAdmin,
    prompts: isPlatformAdmin,
    mcp_servers: isPlatformAdmin || permissions.has('key.read'),
    mcp_approvals: isPlatformAdmin || permissions.has('key.update'),
    keys: isPlatformAdmin || canReadKeys,
    organizations: isPlatformAdmin || permissions.has('org.read'),
    organization_create: isPlatformAdmin,
    teams: isPlatformAdmin || permissions.has('team.read'),
    team_create: canCreateTeamInOrganization,
    people_access: isPlatformAdmin,
    usage: isPlatformAdmin || permissions.has('spend.read'),
    audit: isPlatformAdmin || permissions.has('audit.read'),
    batches: isPlatformAdmin || permissions.has('key.read'),
    guardrails: isPlatformAdmin,
    playground: true,
    settings: isPlatformAdmin,
  };
}

export function resolveUiAccess(
  authMode: 'session' | 'master_key' | null,
  session: SessionLike | null | undefined,
): UIAccess {
  if (authMode === 'master_key') {
    return fullUiAccess();
  }
  if (session?.ui_access) {
    return { ...emptyUiAccess(), ...session.ui_access };
  }
  return deriveUiAccessFromPermissions(session);
}

export function canAccessPage(uiAccess: UIAccess, key: UIAccessKey): boolean {
  return Boolean(uiAccess[key]);
}

const DEFAULT_UI_ROUTE_ORDER: Array<[UIAccessKey, string]> = [
  ['dashboard', '/'],
  ['keys', '/keys'],
  ['batches', '/batches'],
  ['teams', '/teams'],
  ['organizations', '/organizations'],
  ['models', '/models'],
  ['mcp_servers', '/mcp-servers'],
  ['usage', '/usage'],
  ['audit', '/audit'],
  ['settings', '/settings'],
];

export function defaultRouteForUiAccess(uiAccess: UIAccess): string {
  const match = DEFAULT_UI_ROUTE_ORDER.find(([key]) => canAccessPage(uiAccess, key));
  return match?.[1] || '/models';
}

export function isPlatformAdminSession(
  authMode: 'session' | 'master_key' | null,
  session: SessionLike | null | undefined,
): boolean {
  return authMode === 'master_key' || session?.role === 'platform_admin';
}

export function hasPermission(
  authMode: 'session' | 'master_key' | null,
  session: SessionLike | null | undefined,
  permission: string,
): boolean {
  if (authMode === 'master_key') {
    return true;
  }
  return Boolean((session?.effective_permissions || []).includes(permission));
}
