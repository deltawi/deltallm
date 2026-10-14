import {
  BadgeDollarSign,
  BarChart3,
  Box,
  Building2,
  FileText,
  FlaskConical,
  Key,
  KeyRound,
  Layers,
  LayoutDashboard,
  Plug,
  ScrollText,
  Settings,
  ShieldCheck,
  Users,
  UsersRound,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import { canAccessPage, type UIAccess, type UIAccessKey } from './authorization';

export type NavItem = {
  to: string;
  icon: LucideIcon;
  label: string;
  pageKey: UIAccessKey;
};

export type NavSection = {
  key: string;
  // Sections without a label render at the top of the menu with no heading.
  label?: string;
  items: NavItem[];
};

export const NAV_SECTIONS: NavSection[] = [
  {
    key: 'home',
    items: [
      { to: '/', icon: LayoutDashboard, label: 'Dashboard', pageKey: 'dashboard' },
      { to: '/playground', icon: FlaskConical, label: 'Playground', pageKey: 'playground' },
    ],
  },
  {
    key: 'gateway',
    label: 'Gateway',
    items: [
      { to: '/models', icon: Box, label: 'Models', pageKey: 'models' },
      { to: '/route-groups', icon: Workflow, label: 'Route groups', pageKey: 'route_groups' },
      { to: '/named-credentials', icon: KeyRound, label: 'Provider credentials', pageKey: 'named_credentials' },
      { to: '/prompts', icon: FileText, label: 'Prompts', pageKey: 'prompts' },
      { to: '/mcp-servers', icon: Plug, label: 'MCP', pageKey: 'mcp_servers' },
      { to: '/guardrails', icon: ShieldCheck, label: 'Guardrails', pageKey: 'guardrails' },
    ],
  },
  {
    key: 'access',
    label: 'Access',
    items: [
      { to: '/keys', icon: Key, label: 'API keys', pageKey: 'keys' },
      { to: '/organizations', icon: Building2, label: 'Organizations', pageKey: 'organizations' },
      { to: '/teams', icon: UsersRound, label: 'Teams', pageKey: 'teams' },
      { to: '/users', icon: Users, label: 'People', pageKey: 'people_access' },
      { to: '/tiers', icon: BadgeDollarSign, label: 'Tiers', pageKey: 'tiers' },
    ],
  },
  {
    key: 'activity',
    label: 'Activity',
    items: [
      { to: '/usage', icon: BarChart3, label: 'Usage', pageKey: 'usage' },
      { to: '/batches', icon: Layers, label: 'Batch jobs', pageKey: 'batches' },
      { to: '/audit', icon: ScrollText, label: 'Audit logs', pageKey: 'audit' },
    ],
  },
];

// Pinned to the bottom of the menu, above the account.
export const NAV_FOOTER_ITEMS: NavItem[] = [
  { to: '/settings', icon: Settings, label: 'Settings', pageKey: 'settings' },
];

export const DOCS_URL = 'https://docs.deltallm.io/';

export function isNavRouteActive(pathname: string, to: string): boolean {
  if (to === '/') return pathname === '/';
  return pathname === to || pathname.startsWith(`${to}/`);
}

// Users who can only read their own spend see the Usage page as "My usage".
export function usageNavLabel(isPlatformAdmin: boolean, permissions: readonly string[]): string {
  if (isPlatformAdmin) return 'Usage';
  const broader = ['platform.admin', 'spend.read', 'spend.read.team'];
  return permissions.some((permission) => broader.includes(permission)) ? 'Usage' : 'My usage';
}

export function visibleNavSections(
  uiAccess: UIAccess,
  labelFor: (item: NavItem) => string = (item) => item.label,
): NavSection[] {
  return NAV_SECTIONS.reduce<NavSection[]>((sections, section) => {
    const items = section.items
      .filter((item) => canAccessPage(uiAccess, item.pageKey))
      .map((item) => ({ ...item, label: labelFor(item) }));
    if (items.length > 0) sections.push({ ...section, items });
    return sections;
  }, []);
}

export function visibleNavFooterItems(uiAccess: UIAccess): NavItem[] {
  return NAV_FOOTER_ITEMS.filter((item) => canAccessPage(uiAccess, item.pageKey));
}

// Menu section that holds a route, used as the page breadcrumb prefix.
export function navSectionLabel(to: string): string | undefined {
  return NAV_SECTIONS.find((section) => section.items.some((item) => item.to === to))?.label;
}

// Breadcrumb trail for pages below a list page: menu section, then the list page as a link.
export function navTrail(to: string): Array<{ label: string; to?: string }> {
  for (const section of NAV_SECTIONS) {
    const item = section.items.find((entry) => entry.to === to);
    if (item) return [...(section.label ? [{ label: section.label }] : []), { label: item.label, to: item.to }];
  }
  return [];
}
