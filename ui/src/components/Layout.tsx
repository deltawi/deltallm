import { useMemo, useState } from 'react';
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
  Menu,
  X,
  CheckCircle2,
  PanelLeftClose,
  PanelLeftOpen,
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
    icon: LayoutDashboard,
    children: [
      { type: 'item', to: '/models', icon: Box, label: 'Models', pageKey: 'models' },
      { type: 'item', to: '/route-groups', icon: Workflow, label: 'Route Groups', pageKey: 'route_groups' },
      { type: 'item', to: '/prompts', icon: FileText, label: 'Prompt Registry', pageKey: 'prompts' },
      { type: 'item', to: '/mcp-servers', icon: Activity, label: 'MCP Servers', pageKey: 'mcp_servers' },
      { type: 'item', to: '/mcp-approvals', icon: CheckCircle2, label: 'Tool Approvals', pageKey: 'mcp_approvals' },
      { type: 'item', to: '/playground', icon: Zap, label: 'Playground', pageKey: 'playground' },
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
  const label = role === 'platform_admin' ? 'Admin' : 'User';
  return (
    <span className="inline-block px-1.5 py-0.5 text-[10px] font-medium text-violet-700 bg-violet-50 rounded-sm">
      {label}
    </span>
  );
}

function navItemClass(isActive: boolean, isCollapsed: boolean) {
  if (isCollapsed) {
    return clsx(
      'flex items-center justify-center w-10 py-2 mx-auto rounded-lg text-sm font-medium transition-colors',
      isActive
        ? 'bg-violet-50 text-violet-700'
        : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
    );
  }
  return clsx(
    'flex w-full min-w-0 items-center gap-3 px-4 py-2 text-sm font-medium transition-colors border-l-2',
    isActive
      ? 'bg-violet-50 text-violet-700 border-violet-600'
      : 'text-gray-600 border-transparent hover:bg-gray-50 hover:text-gray-900'
  );
}

function SidebarContent({
  visibleEntries,
  displayEmail,
  displayRole,
  logout,
  pathname,
  onNavClick,
  showExpanded,
  collapsed,
  onToggleCollapsed,
}: {
  visibleEntries: NavEntry[];
  displayEmail: string;
  displayRole: string;
  logout: () => void;
  pathname: string;
  onNavClick?: () => void;
  showExpanded: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}) {
  return (
    <>
      <div className="flex items-center justify-between px-3 py-4 min-w-0 shrink-0">
        <div className={clsx('flex items-center gap-2.5 min-w-0', !showExpanded && 'justify-center w-full')}>
          <div className="w-8 h-8 rounded-[10px] bg-violet-100 border border-violet-200/60 flex items-center justify-center shrink-0 shadow-sm">
            <Zap className="w-4 h-4 text-violet-600 fill-violet-600" />
          </div>
          {showExpanded && <span className="text-lg font-bold text-gray-900 truncate">DeltaLLM</span>}
        </div>
        {showExpanded && (
          <button
            type="button"
            onClick={onToggleCollapsed}
            className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors shrink-0"
            title={collapsed ? 'Pin sidebar open' : 'Collapse sidebar'}
          >
            {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          </button>
        )}
      </div>

      <nav className="flex-1 overflow-x-hidden overflow-y-auto py-2">
        {visibleEntries.map((entry) => {
          if (entry.type === 'item') {
            const Icon = entry.icon;
            const active = isRouteActive(pathname, entry.to);
            return (
              <div key={entry.to} className="mb-0.5">
                <NavLink
                  to={entry.to}
                  end={entry.to === '/'}
                  onClick={onNavClick}
                  title={!showExpanded ? entry.label : undefined}
                  className={() => navItemClass(active, !showExpanded)}
                >
                  <Icon className={clsx('w-4 h-4 shrink-0', active ? 'text-violet-600' : 'text-gray-400')} />
                  {showExpanded && <span className="min-w-0 truncate">{entry.label}</span>}
                </NavLink>
              </div>
            );
          }

          return (
            <div key={entry.key} className="mt-4 mb-4">
              {showExpanded && (
                <h3 className="px-5 mb-2 text-xs font-semibold tracking-wider text-gray-400 uppercase truncate">
                  {entry.label}
                </h3>
              )}
              {!showExpanded && <div className="w-5 h-px bg-gray-200 mx-auto my-2" />}
              <div className="space-y-0.5">
                {entry.children.map((child) => {
                  const ChildIcon = child.icon;
                  const active = isRouteActive(pathname, child.to);
                  return (
                    <NavLink
                      key={child.to}
                      to={child.to}
                      end={child.to === '/'}
                      onClick={onNavClick}
                      title={!showExpanded ? child.label : undefined}
                      className={() => navItemClass(active, !showExpanded)}
                    >
                      <ChildIcon className={clsx('w-4 h-4 shrink-0', active ? 'text-violet-600' : 'text-gray-400')} />
                      {showExpanded && <span className="min-w-0 truncate">{child.label}</span>}
                    </NavLink>
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      <div className="p-3 border-t border-gray-100 shrink-0">
        {showExpanded ? (
          <div className="flex items-center justify-between min-w-0">
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex items-center justify-center w-8 h-8 rounded-full bg-violet-100 text-violet-700 font-medium shrink-0 text-sm">
                {displayEmail.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 truncate" title={displayEmail}>
                  {displayEmail}
                </p>
                {displayRole && <RoleBadge role={displayRole} />}
              </div>
            </div>
            <button
              onClick={logout}
              className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-50 rounded-lg transition-colors shrink-0"
              aria-label="Sign out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        ) : (
          <div className="flex justify-center">
            <div
              className="w-8 h-8 rounded-full bg-violet-100 text-violet-700 font-medium flex items-center justify-center text-sm"
              title={displayEmail}
            >
              {displayEmail.charAt(0).toUpperCase()}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

export default function Layout() {
  const { logout, session, authMode } = useAuth();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const {
    width: sidebarWidth,
    collapsed,
    showExpanded,
    isResizing,
    startResizing,
    resetWidth,
    toggleCollapsed,
    setHovered,
  } = useDesktopSidebarWidth();

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

  return (
    <div className="flex h-screen bg-gray-50">
      <aside
        onMouseEnter={() => collapsed && setHovered(true)}
        onMouseLeave={() => { setHovered(false); }}
        className={clsx(
          'group relative hidden shrink-0 flex-col overflow-hidden bg-white border-r border-gray-200 md:flex transition-[width] z-10',
          isResizing ? 'duration-0' : 'duration-200'
        )}
        style={{ width: sidebarWidth }}
      >
        <SidebarContent
          visibleEntries={visibleEntries}
          displayEmail={displayEmail}
          displayRole={displayRole}
          logout={logout}
          pathname={location.pathname}
          showExpanded={showExpanded}
          collapsed={collapsed}
          onToggleCollapsed={toggleCollapsed}
        />

        {!collapsed && (
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
                isResizing ? 'bg-violet-500' : 'bg-transparent group-hover:bg-gray-300'
              )}
            />
            <span
              className={clsx(
                'pointer-events-none absolute right-0.5 top-1/2 flex h-10 w-3 -translate-y-1/2 items-center justify-center rounded-full border bg-white text-[8px] text-gray-400 opacity-0 shadow-sm transition-opacity',
                isResizing ? 'opacity-100 border-violet-400 text-violet-500' : 'border-gray-200 group-hover:opacity-100'
              )}
            >
              ||
            </span>
          </button>
        )}
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="fixed inset-0 bg-black/50" onClick={() => setMobileOpen(false)} />
          <aside className="fixed inset-y-0 left-0 w-64 bg-white flex flex-col z-50 shadow-xl">
            <button
              onClick={() => setMobileOpen(false)}
              className="absolute top-4 right-4 p-1 text-gray-400 hover:text-gray-900"
            >
              <X className="w-5 h-5" />
            </button>
            <SidebarContent
              visibleEntries={visibleEntries}
              displayEmail={displayEmail}
              displayRole={displayRole}
              logout={logout}
              pathname={location.pathname}
              onNavClick={() => setMobileOpen(false)}
              showExpanded={true}
              collapsed={false}
              onToggleCollapsed={() => {}}
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
            <div className="w-7 h-7 rounded-[8px] bg-violet-100 border border-violet-200/60 flex items-center justify-center shadow-sm">
              <Zap className="w-3.5 h-3.5 text-violet-600 fill-violet-600" />
            </div>
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
