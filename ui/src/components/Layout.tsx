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
  CheckCircle2,
  type LucideIcon,
} from 'lucide-react';
import clsx from 'clsx';
import { canAccessPage, resolveUiAccess, type UIAccess, type UIAccessKey } from '../lib/authorization';
import { useDesktopSidebarWidth } from '../lib/useDesktopSidebarWidth';

type NavItem = {
  type: 'item';
  to: string;
  icon: LucideIcon;
  label: string;
  pageKey: UIAccessKey;
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
  { type: 'item', to: '/', icon: LayoutDashboard, label: 'Dashboard', pageKey: 'dashboard' },
  { type: 'item', to: '/keys', icon: Key, label: 'API Keys', pageKey: 'keys' },
  {
    type: 'group',
    key: 'ai-gateway',
    label: 'AI Gateway',
    icon: Sparkles,
    children: [
      { type: 'item', to: '/models', icon: Box, label: 'Models', pageKey: 'models' },
      { type: 'item', to: '/route-groups', icon: Workflow, label: 'Route Groups', pageKey: 'route_groups' },
      { type: 'item', to: '/prompts', icon: FileText, label: 'Prompt Registry', pageKey: 'prompts' },
      { type: 'item', to: '/mcp-servers', icon: Activity, label: 'MCP Servers', pageKey: 'mcp_servers' },
      { type: 'item', to: '/mcp-approvals', icon: CheckCircle2, label: 'Tool Approvals', pageKey: 'mcp_approvals' },
    ],
  },
  {
    type: 'group',
    key: 'access',
    label: 'Access',
    icon: UsersRound,
    children: [
      { type: 'item', to: '/organizations', icon: Building2, label: 'Organizations', pageKey: 'organizations' },
      { type: 'item', to: '/teams', icon: UsersRound, label: 'Teams', pageKey: 'teams' },
      { type: 'item', to: '/users', icon: Users, label: 'People & Access', pageKey: 'people_access' },
    ],
  },
  { type: 'item', to: '/usage', icon: BarChart3, label: 'Usage', pageKey: 'usage' },
  { type: 'item', to: '/audit', icon: Shield, label: 'Audit Logs', pageKey: 'audit' },
  { type: 'item', to: '/batches', icon: Layers, label: 'Batch Jobs', pageKey: 'batches' },
  { type: 'item', to: '/guardrails', icon: Shield, label: 'Guardrails', pageKey: 'guardrails' },
  { type: 'item', to: '/settings', icon: Settings, label: 'Settings', pageKey: 'settings' },
];

function isRouteActive(pathname: string, to: string) {
  if (to === '/') return pathname === '/';
  return pathname === to || pathname.startsWith(`${to}/`);
}

function canViewItem(item: NavItem, uiAccess: UIAccess) {
  return canAccessPage(uiAccess, item.pageKey);
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
    'flex w-full min-w-0 items-center gap-3 rounded-xl px-4 py-2.5 text-sm transition-colors',
    isActive
      ? 'bg-gray-800 text-white ring-1 ring-inset ring-gray-700'
      : 'text-gray-400 hover:bg-gray-800/60 hover:text-white'
  );
}

function childNavClass(isActive: boolean) {
  return clsx(
    'flex w-full min-w-0 items-center gap-3 rounded-lg px-4 py-2.5 text-sm transition-colors',
    isActive
      ? 'bg-gray-800 text-white ring-1 ring-inset ring-gray-700'
      : 'text-gray-400 hover:bg-gray-800/60 hover:text-white'
  );
}

function parentNavClass(isActive: boolean) {
  return clsx(
    'flex w-full min-w-0 items-center gap-3 rounded-xl px-4 py-2.5 text-sm transition-colors',
    isActive
      ? 'bg-gray-800 text-white ring-1 ring-inset ring-gray-700'
      : 'text-gray-400 hover:bg-gray-800/60 hover:text-white'
  );
}

function childNavPanelClass() {
  return 'mt-1 ml-5 border-l border-gray-800 pl-2';
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
      <nav className="flex-1 overflow-x-hidden overflow-y-auto px-3 py-4">
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
                <Icon className="w-4 h-4 shrink-0" />
                <span className="min-w-0 truncate" title={entry.label}>
                  {entry.label}
                </span>
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
                <Icon className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate text-left" title={entry.label}>
                  {entry.label}
                </span>
                {isExpanded ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
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
                        <ChildIcon className="h-4 w-4 shrink-0" />
                        <span className="min-w-0 truncate" title={child.label}>
                          {child.label}
                        </span>
                      </NavLink>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </nav>
      <div className="overflow-x-hidden p-4 border-t border-gray-800">
        <div className="mb-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm text-gray-300" title={displayEmail}>
              {displayEmail}
            </span>
            {displayRole && <div className="shrink-0"><RoleBadge role={displayRole} /></div>}
          </div>
        </div>
        <button
          onClick={logout}
          className="flex w-full items-center gap-2 text-sm text-gray-400 transition-colors hover:text-white"
        >
          <LogOut className="w-4 h-4 shrink-0" />
          <span className="truncate">Sign Out</span>
        </button>
      </div>
    </>
  );
}

export default function Layout() {
  const { logout, session, authMode } = useAuth();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const {
    width: desktopSidebarWidth,
    isResizing: isSidebarResizing,
    startResizing,
    resetWidth,
  } = useDesktopSidebarWidth();
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    'ai-gateway': false,
    access: false,
  });

  const displayEmail = authMode === 'master_key' ? 'Master Key' : (session?.email || 'Unknown');
  const displayRole = session?.role || (authMode === 'master_key' ? 'platform_admin' : '');
  const uiAccess = useMemo(() => resolveUiAccess(authMode, session), [authMode, session]);

  const visibleEntries = useMemo(
    () =>
      navEntries.reduce<NavEntry[]>((items, entry) => {
        if (entry.type === 'item') {
          if (canViewItem(entry, uiAccess)) items.push(entry);
          return items;
        }
        const children = entry.children.filter((child) => canViewItem(child, uiAccess));
        if (children.length > 0) items.push({ ...entry, children });
        return items;
      }, []),
    [uiAccess]
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
      <aside
        className="group relative hidden shrink-0 flex-col overflow-hidden bg-gray-900 text-white md:flex"
        style={{ width: desktopSidebarWidth }}
      >
        <SidebarContent
          visibleEntries={visibleEntries}
          displayEmail={displayEmail}
          displayRole={displayRole}
          logout={logout}
          expandedGroups={expandedGroups}
          onToggleGroup={toggleGroup}
          pathname={location.pathname}
        />
        <button
          type="button"
          aria-label="Resize sidebar"
          onPointerDown={startResizing}
          onDoubleClick={resetWidth}
          className="absolute inset-y-0 right-0 w-3 cursor-col-resize"
        >
          <span
            className={clsx(
              'absolute inset-y-0 right-0 w-px transition-colors',
              isSidebarResizing ? 'bg-blue-400' : 'bg-transparent group-hover:bg-gray-700'
            )}
          />
          <span
            className={clsx(
              'pointer-events-none absolute right-1 top-1/2 flex h-14 w-4 -translate-y-1/2 items-center justify-center rounded-full border border-gray-700 bg-gray-800/90 text-[10px] text-gray-400 opacity-0 shadow-sm transition-opacity',
              isSidebarResizing ? 'opacity-100 border-blue-500/50 text-blue-300' : 'group-hover:opacity-100'
            )}
          >
            ||
          </span>
        </button>
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
