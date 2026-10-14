import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import {
  BookOpen,
  ChevronsUpDown,
  ExternalLink,
  LogOut,
  Menu,
  X,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import clsx from 'clsx';
import { isPlatformAdminSession, resolveUiAccess } from '../lib/authorization';
import {
  DOCS_URL,
  isNavRouteActive,
  usageNavLabel,
  visibleNavFooterItems,
  visibleNavSections,
  type NavItem,
  type NavSection,
} from '../lib/navigation';
import { useDesktopSidebarWidth } from '../lib/useDesktopSidebarWidth';
import BrandLogo from './BrandLogo';
import { useToast } from './ToastProvider';

function RoleBadge({ role }: { role: string }) {
  const label = role === 'platform_admin' ? 'Admin' : 'User';
  return (
    <span className="inline-block rounded-sm bg-brand-secondary-soft px-1.5 py-0.5 text-[10px] font-medium text-brand-secondary-ink">
      {label}
    </span>
  );
}

function navItemClass(isActive: boolean, isCollapsed: boolean) {
  if (isCollapsed) {
    return clsx(
      'flex items-center justify-center w-10 py-2 mx-auto rounded-lg text-sm font-medium transition-colors',
      isActive
        ? 'bg-brand-secondary-soft text-brand-secondary-ink'
        : 'text-gray-600 hover:bg-brand-menu-hover hover:text-brand-menu-hover-foreground'
    );
  }
  return clsx(
    'flex w-full min-w-0 items-center gap-3 px-4 py-1.5 text-sm font-medium transition-colors border-l-2',
    isActive
      ? 'border-brand-secondary bg-brand-secondary-soft text-brand-secondary-ink'
      : 'border-transparent text-gray-600 hover:bg-brand-menu-hover hover:text-brand-menu-hover-foreground'
  );
}

function SidebarLink({
  item,
  pathname,
  showExpanded,
  onNavClick,
}: {
  item: NavItem;
  pathname: string;
  showExpanded: boolean;
  onNavClick?: () => void;
}) {
  const Icon = item.icon;
  const active = isNavRouteActive(pathname, item.to);
  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      onClick={onNavClick}
      aria-label={item.label}
      title={!showExpanded ? item.label : undefined}
      className={() => navItemClass(active, !showExpanded)}
    >
      <Icon className={clsx('w-4 h-4 shrink-0', active ? 'text-brand-secondary-ink' : 'text-gray-400')} aria-hidden="true" />
      {showExpanded && <span className="min-w-0 truncate">{item.label}</span>}
    </NavLink>
  );
}

function SidebarSection({
  section,
  pathname,
  showExpanded,
  onNavClick,
}: {
  section: NavSection;
  pathname: string;
  showExpanded: boolean;
  onNavClick?: () => void;
}) {
  const headingId = useId();
  const links = section.items.map((item) => (
    <SidebarLink key={item.to} item={item} pathname={pathname} showExpanded={showExpanded} onNavClick={onNavClick} />
  ));
  if (!section.label) {
    return <div className="space-y-0.5">{links}</div>;
  }
  return (
    <div className="mt-4" role="group" aria-labelledby={showExpanded ? headingId : undefined} aria-label={showExpanded ? undefined : section.label}>
      {showExpanded ? (
        <h3 id={headingId} className="px-5 mb-1 text-xs font-medium text-gray-400 truncate">
          {section.label}
        </h3>
      ) : (
        <div className="w-5 h-px bg-gray-200 mx-auto mb-2" aria-hidden="true" />
      )}
      <div className="space-y-0.5">{links}</div>
    </div>
  );
}

function AccountMenu({
  displayEmail,
  displayRole,
  logout,
  isLoggingOut,
}: {
  displayEmail: string;
  displayRole: string;
  logout: () => void;
  isLoggingOut: boolean;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const menuItemClass = 'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm font-medium text-gray-700 transition-colors hover:bg-brand-menu-hover hover:text-brand-menu-hover-foreground disabled:cursor-wait disabled:opacity-50';

  return (
    <div ref={containerRef} className="relative min-w-0">
      {open && (
        <div
          id={menuId}
          className="absolute bottom-full left-0 right-0 z-20 mb-2 rounded-lg border border-gray-200 bg-white p-1 shadow-lg"
        >
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => setOpen(false)}
            className={menuItemClass}
          >
            <BookOpen className="w-4 h-4 shrink-0 text-gray-400" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">Documentation</span>
            <ExternalLink className="w-3.5 h-3.5 shrink-0 text-gray-400" aria-hidden="true" />
          </a>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              logout();
            }}
            disabled={isLoggingOut}
            className={menuItemClass}
          >
            <LogOut className="w-4 h-4 shrink-0 text-gray-400" aria-hidden="true" />
            <span className="min-w-0 truncate">{isLoggingOut ? 'Signing out…' : 'Sign out'}</span>
          </button>
        </div>
      )}
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Account menu for ${displayEmail}`}
        className="flex w-full min-w-0 items-center gap-3 rounded-lg p-1.5 text-left transition-colors hover:bg-brand-menu-hover"
      >
        <span className="flex items-center justify-center w-8 h-8 rounded-full bg-brand-secondary-soft text-brand-secondary-ink font-medium shrink-0 text-sm" aria-hidden="true">
          {displayEmail.charAt(0).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-gray-900 truncate" title={displayEmail}>
            {displayEmail}
          </span>
          {displayRole && <RoleBadge role={displayRole} />}
        </span>
        <ChevronsUpDown className="w-4 h-4 shrink-0 text-gray-400" aria-hidden="true" />
      </button>
    </div>
  );
}

function SidebarContent({
  sections,
  footerItems,
  displayEmail,
  displayRole,
  logout,
  isLoggingOut,
  pathname,
  onNavClick,
  showExpanded,
  collapsed,
  canCollapse,
  onToggleCollapsed,
}: {
  sections: NavSection[];
  footerItems: NavItem[];
  displayEmail: string;
  displayRole: string;
  logout: () => void;
  isLoggingOut: boolean;
  pathname: string;
  onNavClick?: () => void;
  showExpanded: boolean;
  collapsed: boolean;
  canCollapse: boolean;
  onToggleCollapsed: () => void;
}) {
  const toggleLabel = !showExpanded ? 'Expand sidebar' : collapsed ? 'Pin sidebar open' : 'Collapse sidebar';
  return (
    <>
      <div
        className={clsx(
          'min-w-0 shrink-0 gap-2 px-3 py-4',
          showExpanded ? 'flex items-center justify-between' : 'flex flex-col items-center gap-2',
        )}
      >
        <BrandLogo
          variant={showExpanded ? 'expanded' : 'mark'}
          className={clsx(showExpanded ? 'max-w-[9.5rem]' : 'w-full justify-center')}
        />
        {canCollapse && (
          // One button for every state, so keyboard focus stays on it when the rail expands on focus.
          <button
            type="button"
            onClick={onToggleCollapsed}
            className={clsx(
              'shrink-0 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 rounded-lg',
              showExpanded ? 'p-1.5' : 'flex h-8 w-8 items-center justify-center',
            )}
            aria-label={toggleLabel}
            title={toggleLabel}
          >
            {showExpanded && !collapsed ? <PanelLeftClose className="w-4 h-4" /> : <PanelLeftOpen className="w-4 h-4" />}
          </button>
        )}
      </div>

      <nav aria-label="Main" className="flex-1 overflow-x-hidden overflow-y-auto pb-2">
        {sections.map((section) => (
          <SidebarSection
            key={section.key}
            section={section}
            pathname={pathname}
            showExpanded={showExpanded}
            onNavClick={onNavClick}
          />
        ))}
      </nav>

      <div className="border-t border-gray-100 shrink-0">
        {footerItems.length > 0 && (
          <nav aria-label="Administration" className="space-y-0.5 pt-2">
            {footerItems.map((item) => (
              <SidebarLink key={item.to} item={item} pathname={pathname} showExpanded={showExpanded} onNavClick={onNavClick} />
            ))}
          </nav>
        )}
        <div className={clsx(showExpanded ? 'px-2 py-2' : 'p-3')}>
          {showExpanded ? (
            <AccountMenu
              displayEmail={displayEmail}
              displayRole={displayRole}
              logout={logout}
              isLoggingOut={isLoggingOut}
            />
          ) : (
            <div className="flex justify-center">
              <div
                className="w-8 h-8 rounded-full bg-brand-secondary-soft text-brand-secondary-ink font-medium flex items-center justify-center text-sm"
                title={displayEmail}
              >
                {displayEmail.charAt(0).toUpperCase()}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default function Layout() {
  const { logout, isLoggingOut, session, authMode } = useAuth();
  const { pushToast } = useToast();
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

  const isPlatformAdmin = isPlatformAdminSession(authMode, session);
  const permissions = session?.effective_permissions;
  const sections = useMemo(() => {
    const usageLabel = usageNavLabel(isPlatformAdmin, permissions || []);
    return visibleNavSections(uiAccess, (item) => (item.pageKey === 'usage' ? usageLabel : item.label));
  }, [uiAccess, isPlatformAdmin, permissions]);
  const footerItems = useMemo(() => visibleNavFooterItems(uiAccess), [uiAccess]);

  const handleLogout = useCallback(() => {
    void logout().catch((error: unknown) => {
      pushToast({
        title: 'Sign out failed',
        message: error instanceof Error && error.message
          ? error.message
          : 'Your session is still active. Please try again.',
        tone: 'error',
      });
    });
  }, [logout, pushToast]);

  return (
    <div className="flex h-screen bg-gray-50">
      <aside
        onMouseEnter={() => collapsed && setHovered(true)}
        onMouseLeave={() => { setHovered(false); }}
        onFocus={() => collapsed && setHovered(true)}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHovered(false);
        }}
        className={clsx(
          'group relative hidden shrink-0 flex-col overflow-hidden bg-white border-r border-gray-200 md:flex transition-[width] z-10',
          isResizing ? 'duration-0' : 'duration-200'
        )}
        style={{ width: sidebarWidth }}
      >
        <SidebarContent
          sections={sections}
          footerItems={footerItems}
          displayEmail={displayEmail}
          displayRole={displayRole}
          logout={handleLogout}
          isLoggingOut={isLoggingOut}
          pathname={location.pathname}
          showExpanded={showExpanded}
          collapsed={collapsed}
          canCollapse={true}
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
                isResizing ? 'bg-brand-secondary' : 'bg-transparent group-hover:bg-gray-300'
              )}
            />
            <span
              className={clsx(
                'pointer-events-none absolute right-0.5 top-1/2 flex h-10 w-3 -translate-y-1/2 items-center justify-center rounded-full border bg-white text-[8px] text-gray-400 opacity-0 shadow-sm transition-opacity',
                isResizing ? 'border-brand-secondary text-brand-secondary-ink opacity-100' : 'border-gray-200 group-hover:opacity-100'
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
              type="button"
              onClick={() => setMobileOpen(false)}
              aria-label="Close menu"
              className="absolute top-4 right-4 p-1 text-gray-400 hover:text-gray-900"
            >
              <X className="w-5 h-5" />
            </button>
            <SidebarContent
              sections={sections}
          footerItems={footerItems}
              displayEmail={displayEmail}
              displayRole={displayRole}
              logout={handleLogout}
              isLoggingOut={isLoggingOut}
              pathname={location.pathname}
              onNavClick={() => setMobileOpen(false)}
              showExpanded={true}
              collapsed={false}
              canCollapse={false}
              onToggleCollapsed={() => {}}
            />
          </aside>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <header className="md:hidden flex items-center gap-3 px-4 py-3 bg-white border-b border-gray-200 shrink-0">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
            aria-expanded={mobileOpen}
            className="p-1.5 hover:bg-gray-100 rounded-lg"
          >
            <Menu className="w-5 h-5 text-gray-700" />
          </button>
          <BrandLogo
            variant="expanded"
            markClassName="h-7 w-7 rounded-[8px]"
            fullClassName="h-7 max-w-[10rem]"
            nameClassName="text-base font-semibold"
          />
        </header>
        <main className="relative flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
