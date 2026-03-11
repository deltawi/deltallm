import { useEffect, useMemo, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import {
  LayoutDashboard,
  Box,
  Activity,
  Key,
  Users,
  UsersRound,
  Building2,
  BarChart3,
  Layers,
  Workflow,
  FileText,
  Shield,
  Settings,
  LogOut,
  Zap,
  Sparkles,
  Menu,
  X,
  ChevronDown,
  ChevronRight,
  type LucideIcon,
} from 'lucide-react';
import clsx from 'clsx';

type NavItem = {
  type: 'item';
  to: string;
  icon: LucideIcon;
  label: string;
  adminOnly?: boolean;
  requiredPermission?: string;
};

type NavGroup = {
  type: 'group';
  key: string;
  label: string;
  icon: LucideIcon;
  children: NavItem[];
};

type NavEntry = NavItem | NavGroup;

const navEntries: NavEntry[] = [
  { type: 'item', to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { type: 'item', to: '/keys', icon: Key, label: 'API Keys' },
  {
    type: 'group',
    key: 'ai-gateway',
    label: 'AI Gateway',
    icon: Sparkles,
    children: [
      { type: 'item', to: '/models', icon: Box, label: 'Models' },
      { type: 'item', to: '/route-groups', icon: Workflow, label: 'Route Groups', adminOnly: true },
      { type: 'item', to: '/prompts', icon: FileText, label: 'Prompt Registry', adminOnly: true },
      { type: 'item', to: '/mcp-servers', icon: Activity, label: 'MCP Servers', requiredPermission: 'key.read' },
    ],
  },
  {
    type: 'group',
    key: 'access',
    label: 'Access',
    icon: UsersRound,
    children: [
      { type: 'item', to: '/organizations', icon: Building2, label: 'Organizations' },
      { type: 'item', to: '/teams', icon: UsersRound, label: 'Teams' },
      { type: 'item', to: '/users', icon: Users, label: 'People & Access' },
    ],
  },
  { type: 'item', to: '/usage', icon: BarChart3, label: 'Usage' },
  { type: 'item', to: '/audit', icon: Shield, label: 'Audit Logs', requiredPermission: 'audit.read' },
  { type: 'item', to: '/batches', icon: Layers, label: 'Batch Jobs' },
  { type: 'item', to: '/guardrails', icon: Shield, label: 'Guardrails', adminOnly: true },
  { type: 'item', to: '/settings', icon: Settings, label: 'Settings', adminOnly: true },
];

function isRouteActive(pathname: string, to: string) {
  if (to === '/') return pathname === '/';
  return pathname === to || pathname.startsWith(`${to}/`);
}

function canViewItem(item: NavItem, isPlatformAdmin: boolean, permissions: Set<string>) {
  if (item.adminOnly && !isPlatformAdmin) return false;
  if (item.requiredPermission && !isPlatformAdmin && !permissions.has(item.requiredPermission)) return false;
  return true;
}

function RoleBadge({ role }: { role: string }) {
  const colors: Record<string, string> = {
    platform_admin: 'bg-purple-500/20 text-purple-300',
    org_user: 'bg-gray-500/20 text-gray-400',
  };
  const label = role === 'platform_admin' ? 'Admin' : 'User';
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${colors[role] || 'bg-gray-500/20 text-gray-400'}`}>
      {label}
    </span>
  );
}

function topLevelNavClass(isActive: boolean) {
  return clsx(
    'mx-3 flex items-center gap-3 rounded-xl px-4 py-2.5 text-sm transition-colors',
    isActive
      ? 'bg-gray-800 text-white ring-1 ring-inset ring-gray-700'
      : 'text-gray-400 hover:bg-gray-800/60 hover:text-white'
  );
}

function childNavClass(isActive: boolean) {
  return clsx(
    'flex items-center gap-3 rounded-lg px-4 py-2.5 text-sm transition-colors',
    isActive
      ? 'bg-gray-800 text-white ring-1 ring-inset ring-gray-700'
      : 'text-gray-400 hover:bg-gray-800/60 hover:text-white'
  );
}

function parentNavClass(isActive: boolean) {
  return clsx(
    'mx-3 flex w-full items-center gap-3 rounded-xl px-4 py-2.5 text-sm transition-colors',
    isActive
      ? 'bg-gray-800 text-white ring-1 ring-inset ring-gray-700'
      : 'text-gray-400 hover:bg-gray-800/60 hover:text-white'
  );
}

function childNavPanelClass() {
  return 'mx-3 mt-1 border-l border-gray-800 pl-5';
}

function SidebarContent({
  visibleEntries,
  displayEmail,
  displayRole,
  logout,
  expandedGroups,
  onToggleGroup,
  pathname,
  onNavClick,
}: {
  visibleEntries: NavEntry[];
  displayEmail: string;
  displayRole: string;
  logout: () => void;
  expandedGroups: Record<string, boolean>;
  onToggleGroup: (groupKey: string) => void;
  pathname: string;
  onNavClick?: () => void;
}) {
  return (
    <>
      <div className="p-5 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Zap className="w-6 h-6 text-blue-400" />
          <span className="text-lg font-bold">DeltaLLM</span>
        </div>
        <p className="text-xs text-gray-400 mt-1">Admin Dashboard</p>
      </div>
      <nav className="flex-1 py-4 overflow-y-auto">
        {visibleEntries.map((entry) => {
          if (entry.type === 'item') {
            const Icon = entry.icon;
            return (
              <NavLink
                key={entry.to}
                to={entry.to}
                end={entry.to === '/'}
                onClick={onNavClick}
                className={({ isActive }) => topLevelNavClass(isActive)}
              >
                <Icon className="w-4 h-4" />
                {entry.label}
              </NavLink>
            );
          }

          const Icon = entry.icon;
          const isExpanded = expandedGroups[entry.key] ?? false;
          const isGroupActive = entry.children.some((child) => isRouteActive(pathname, child.to));
          const groupPanelId = `${entry.key}-nav-group`;
          return (
            <section key={entry.key} className="my-1">
              <button
                type="button"
                onClick={() => onToggleGroup(entry.key)}
                aria-expanded={isExpanded}
                aria-controls={groupPanelId}
                className={parentNavClass(isGroupActive || isExpanded)}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1 text-left">{entry.label}</span>
                {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </button>
              {isExpanded && (
                <div id={groupPanelId} className={childNavPanelClass()} role="group" aria-label={entry.label}>
                  {entry.children.map((child) => {
                    const ChildIcon = child.icon;
                    return (
                      <NavLink
                        key={child.to}
                        to={child.to}
                        end={child.to === '/'}
                        onClick={onNavClick}
                        className={({ isActive }) => childNavClass(isActive)}
                      >
                        <ChildIcon className="h-4 w-4" />
                        {child.label}
                      </NavLink>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </nav>
      <div className="p-4 border-t border-gray-800">
        <div className="mb-3">
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-300 truncate">{displayEmail}</span>
            {displayRole && <RoleBadge role={displayRole} />}
          </div>
        </div>
        <button
          onClick={logout}
          className="flex items-center gap-2 text-sm text-gray-400 hover:text-white transition-colors w-full"
        >
          <LogOut className="w-4 h-4" />
          Sign Out
        </button>
      </div>
    </>
  );
}

export default function Layout() {
  const { logout, session, authMode } = useAuth();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    'ai-gateway': false,
    access: false,
  });

  const displayEmail = authMode === 'master_key' ? 'Master Key' : (session?.email || 'Unknown');
  const displayRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const isPlatformAdmin = displayRole === 'platform_admin';
  const permissionKeys = useMemo(
    () => (session?.effective_permissions || []).map((item) => String(item)).sort(),
    [session?.effective_permissions]
  );
  const permissions = useMemo(() => new Set(permissionKeys), [permissionKeys]);

  const visibleEntries = useMemo(
    () =>
      navEntries.reduce<NavEntry[]>((items, entry) => {
        if (entry.type === 'item') {
          if (canViewItem(entry, isPlatformAdmin, permissions)) items.push(entry);
          return items;
        }
        const children = entry.children.filter((child) => canViewItem(child, isPlatformAdmin, permissions));
        if (children.length > 0) items.push({ ...entry, children });
        return items;
      }, []),
    [isPlatformAdmin, permissions]
  );

  useEffect(() => {
    const activeGroup = visibleEntries.find(
      (entry): entry is NavGroup => entry.type === 'group' && entry.children.some((child) => isRouteActive(location.pathname, child.to))
    );
    if (!activeGroup) return;
    setExpandedGroups((current) => (current[activeGroup.key] ? current : { ...current, [activeGroup.key]: true }));
  }, [location.pathname, visibleEntries]);

  const toggleGroup = (groupKey: string) => {
    setExpandedGroups((current) => ({ ...current, [groupKey]: !current[groupKey] }));
  };

  return (
    <div className="flex h-screen bg-gray-50">
      <aside className="hidden md:flex w-64 bg-gray-900 text-white flex-col shrink-0">
        <SidebarContent
          visibleEntries={visibleEntries}
          displayEmail={displayEmail}
          displayRole={displayRole}
          logout={logout}
          expandedGroups={expandedGroups}
          onToggleGroup={toggleGroup}
          pathname={location.pathname}
        />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="fixed inset-0 bg-black/50" onClick={() => setMobileOpen(false)} />
          <aside className="fixed inset-y-0 left-0 w-64 bg-gray-900 text-white flex flex-col z-50">
            <button
              onClick={() => setMobileOpen(false)}
              className="absolute top-4 right-4 p-1 text-gray-400 hover:text-white"
            >
              <X className="w-5 h-5" />
            </button>
            <SidebarContent
              visibleEntries={visibleEntries}
              displayEmail={displayEmail}
              displayRole={displayRole}
              logout={logout}
              expandedGroups={expandedGroups}
              onToggleGroup={toggleGroup}
              pathname={location.pathname}
              onNavClick={() => setMobileOpen(false)}
            />
          </aside>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <header className="md:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-200 shrink-0">
          <button onClick={() => setMobileOpen(true)} className="p-1.5 hover:bg-gray-100 rounded-lg">
            <Menu className="w-5 h-5 text-gray-700" />
          </button>
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-blue-600" />
            <span className="font-semibold text-gray-900">DeltaLLM</span>
          </div>
        </header>
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
