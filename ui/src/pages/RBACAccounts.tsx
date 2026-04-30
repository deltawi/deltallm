import { useState, useEffect, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { invitations, organizations, rbac, teams, users, type Invitation, type Principal, type PrincipalSummary, type ScopedAssetAccess } from '../lib/api';
import { Plus, UserCog, ShieldCheck, Search, Building2, UsersRound, Trash2, Mail } from 'lucide-react';
import Modal from '../components/Modal';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { ContentCard, IndexShell } from '../components/admin/shells';
import InvitationPanel from '../components/admin/InvitationPanel';
import ProvisionPersonModal from '../components/admin/ProvisionPersonModal';
import { useToast } from '../components/ToastProvider';

const PLATFORM_ROLES = [
  { value: 'platform_admin', label: 'Platform Admin' },
  { value: 'org_user', label: 'Organization User' },
];

const ORG_ROLES = [
  { value: 'org_member', label: 'Member' },
  { value: 'org_owner', label: 'Owner' },
  { value: 'org_admin', label: 'Admin' },
  { value: 'org_billing', label: 'Billing' },
  { value: 'org_auditor', label: 'Auditor' },
];

const TEAM_ROLES = [
  { value: 'team_admin', label: 'Admin' },
  { value: 'team_developer', label: 'Developer' },
  { value: 'team_viewer', label: 'Viewer' },
];

function formatDate(d: string | null) {
  if (!d) return 'Never';
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function RoleBadge({ role }: { role: string }) {
  const colors: Record<string, string> = {
    platform_admin: 'bg-purple-100 text-purple-800',
    org_user: 'bg-gray-100 text-gray-700',
    org_owner: 'bg-amber-100 text-amber-800',
    org_admin: 'bg-blue-100 text-blue-800',
    org_member: 'bg-gray-100 text-gray-700',
    org_billing: 'bg-green-100 text-green-800',
    org_auditor: 'bg-teal-100 text-teal-800',
    team_admin: 'bg-blue-100 text-blue-800',
    team_developer: 'bg-cyan-100 text-cyan-800',
    team_viewer: 'bg-gray-100 text-gray-700',
  };
  const label = role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${colors[role] || 'bg-gray-100 text-gray-700'}`}>
      {label}
    </span>
  );
}

function MembershipCountBadge({ count, label }: { count: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
      <span>{count}</span>
      <span>{label}</span>
    </span>
  );
}

type PeopleAccessTab = 'users' | 'invitations';
type InvitationStatusFilter = Invitation['status'] | 'active';
const EMPTY_PAGINATION = { total: 0, limit: 20, offset: 0, has_more: false };
const USER_ACCESS_GROUP_PAGE_SIZE = 50;
const EMPTY_SUMMARY: PrincipalSummary = {
  total_accounts: 0,
  active_accounts: 0,
  platform_admins: 0,
  mfa_enabled_accounts: 0,
  organization_memberships: 0,
  team_memberships: 0,
};

export default function RBACAccounts() {
  const { pushToast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [viewTab, setViewTab] = useState<PeopleAccessTab>('users');
  const [principals, setPrincipals] = useState<Principal[]>([]);
  const [principalPagination, setPrincipalPagination] = useState(EMPTY_PAGINATION);
  const [principalSummary, setPrincipalSummary] = useState<PrincipalSummary>(EMPTY_SUMMARY);
  const [principalLoading, setPrincipalLoading] = useState(true);
  const [principalError, setPrincipalError] = useState('');
  const [principalSearchInput, setPrincipalSearchInput] = useState('');
  const [principalSearchTerm, setPrincipalSearchTerm] = useState('');
  const [principalPageOffset, setPrincipalPageOffset] = useState(0);
  const [invitationItems, setInvitationItems] = useState<Invitation[]>([]);
  const [invitationPagination, setInvitationPagination] = useState(EMPTY_PAGINATION);
  const [invitationLoading, setInvitationLoading] = useState(false);
  const [invitationSaving, setInvitationSaving] = useState(false);
  const [invitationError, setInvitationError] = useState('');
  const [invitationSearchInput, setInvitationSearchInput] = useState('');
  const [invitationSearchTerm, setInvitationSearchTerm] = useState('');
  const [invitationStatusFilter, setInvitationStatusFilter] = useState<InvitationStatusFilter>('active');
  const [invitationPageOffset, setInvitationPageOffset] = useState(0);
  const [orgList, setOrgList] = useState<any[]>([]);
  const [teamList, setTeamList] = useState<any[]>([]);
  const [referenceLoading, setReferenceLoading] = useState(true);
  const pageSize = 20;
  const inviteOrganizationId = searchParams.get('invite_org_id');
  const inviteTeamId = searchParams.get('invite_team_id');

  const [showProvisionModal, setShowProvisionModal] = useState(false);
  const [showAccountModal, setShowAccountModal] = useState(false);
  const [editAccount, setEditAccount] = useState<Principal | null>(null);
  const [formEmail, setFormEmail] = useState('');
  const [formRole, setFormRole] = useState('org_user');
  const [formPassword, setFormPassword] = useState('');
  const [formActive, setFormActive] = useState(true);

  const [selectedAccount, setSelectedAccount] = useState<Principal | null>(null);

  const [showOrgMembershipModal, setShowOrgMembershipModal] = useState(false);
  const [membershipAccountId, setMembershipAccountId] = useState('');
  const [membershipOrgId, setMembershipOrgId] = useState('');
  const [membershipOrgRole, setMembershipOrgRole] = useState('org_member');

  const [showTeamMembershipModal, setShowTeamMembershipModal] = useState(false);
  const [membershipTeamId, setMembershipTeamId] = useState('');
  const [membershipTeamRole, setMembershipTeamRole] = useState('team_viewer');
  const [showUserAccessModal, setShowUserAccessModal] = useState(false);
  const [selectedRuntimeUser, setSelectedRuntimeUser] = useState<Principal | null>(null);
  const [userAssetAccess, setUserAssetAccess] = useState<ScopedAssetAccess | null>(null);
  const [userAssetMode, setUserAssetMode] = useState<'inherit' | 'restrict'>('inherit');
  const [userAssetSelectedKeys, setUserAssetSelectedKeys] = useState<string[]>([]);
  const [userAssetSelectedAccessGroupKeys, setUserAssetSelectedAccessGroupKeys] = useState<string[]>([]);
  const [userAssetSearchInput, setUserAssetSearchInput] = useState('');
  const [userAssetSearch, setUserAssetSearch] = useState('');
  const [userAccessGroupPageOffset, setUserAccessGroupPageOffset] = useState(0);
  const [userAssetLoading, setUserAssetLoading] = useState(false);
  const [userAssetError, setUserAssetError] = useState('');
  const userAssetSelectionInitializedRef = useRef(false);
  const userAssetRequestIdRef = useRef(0);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const userAssetAccessInitialized = userAssetSelectionInitializedRef.current;
  const canSaveUserAssetAccess = Boolean(userAssetAccess) || (userAssetMode === 'inherit' && userAssetAccessInitialized);
  const userAssetModeControlsDisabled = saving || userAssetLoading || (!userAssetAccess && !userAssetAccessInitialized);

  const loadReferenceData = useCallback(async () => {
    setReferenceLoading(true);
    try {
      const [orgs, tms] = await Promise.all([
        organizations.list({ limit: 500 }).catch(() => ({ data: [], pagination: { total: 0, limit: 500, offset: 0, has_more: false } })),
        teams.list({ limit: 500 }).catch(() => ({ data: [], pagination: { total: 0, limit: 500, offset: 0, has_more: false } })),
      ]);
      setOrgList(orgs?.data || orgs || []);
      setTeamList(tms?.data || tms || []);
    } finally {
      setReferenceLoading(false);
    }
  }, []);

  const loadPrincipals = useCallback(async () => {
    setPrincipalLoading(true);
    setPrincipalError('');
    try {
      const [response, summary] = await Promise.all([
        rbac.principals.list({ search: principalSearchTerm, limit: pageSize, offset: principalPageOffset }),
        rbac.principals.summary().catch(() => null),
      ]);
      setPrincipals(response?.data || []);
      setPrincipalPagination(response?.pagination || { ...EMPTY_PAGINATION, limit: pageSize, offset: principalPageOffset });
      if (summary) {
        setPrincipalSummary(summary);
      }
    } catch (err: any) {
      setPrincipalError(err?.message || 'Failed to load accounts');
    } finally {
      setPrincipalLoading(false);
    }
  }, [pageSize, principalPageOffset, principalSearchTerm]);

  const loadInvitations = useCallback(async () => {
    setInvitationLoading(true);
    setInvitationError('');
    try {
      const response = await invitations.list({
        status: invitationStatusFilter,
        search: invitationSearchTerm || undefined,
        limit: pageSize,
        offset: invitationPageOffset,
      });
      setInvitationItems(response?.data || []);
      setInvitationPagination(response?.pagination || { ...EMPTY_PAGINATION, limit: pageSize, offset: invitationPageOffset });
    } catch (err: any) {
      setInvitationError(err?.message || 'Failed to load invitations');
    } finally {
      setInvitationLoading(false);
    }
  }, [invitationPageOffset, invitationSearchTerm, invitationStatusFilter, pageSize]);

  useEffect(() => { loadReferenceData(); }, [loadReferenceData]);
  useEffect(() => { loadPrincipals(); }, [loadPrincipals]);
  useEffect(() => {
    const t = setTimeout(() => {
      setPrincipalSearchTerm(principalSearchInput);
      setPrincipalPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [principalSearchInput]);
  useEffect(() => {
    const t = setTimeout(() => {
      setInvitationSearchTerm(invitationSearchInput.trim());
      setInvitationPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [invitationSearchInput]);
  useEffect(() => {
    if (viewTab !== 'invitations') return;
    loadInvitations();
  }, [loadInvitations, viewTab]);
  useEffect(() => {
    if (!selectedAccount) return;
    const next = principals.find((acct) => acct.account_id === selectedAccount.account_id);
    if (next) {
      setSelectedAccount(next);
    }
  }, [principals, selectedAccount]);
  useEffect(() => {
    if (!inviteOrganizationId && !inviteTeamId) return;
    if (referenceLoading) return;
    setShowProvisionModal(true);
  }, [inviteOrganizationId, inviteTeamId, referenceLoading]);
  useEffect(() => {
    const t = setTimeout(() => {
      setUserAssetSearch(userAssetSearchInput.trim());
      setUserAccessGroupPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [userAssetSearchInput]);

  const loadUserAssetAccess = useCallback(async (
    runtimeUserId: string,
    options: { search?: string; offset?: number; preserveSelections?: boolean } = {},
  ) => {
    const requestId = userAssetRequestIdRef.current + 1;
    userAssetRequestIdRef.current = requestId;
    setUserAssetLoading(true);
    setUserAssetError('');
    try {
      const access = await users.assetAccess(runtimeUserId, {
        include_targets: true,
        access_group_search: options.search || undefined,
        access_group_limit: USER_ACCESS_GROUP_PAGE_SIZE,
        access_group_offset: options.offset ?? 0,
      });
      if (requestId !== userAssetRequestIdRef.current) return;
      setUserAssetAccess(access);
      if (!options.preserveSelections) {
        setUserAssetMode(access.mode === 'restrict' ? 'restrict' : 'inherit');
        setUserAssetSelectedKeys(access.selected_callable_keys || []);
        setUserAssetSelectedAccessGroupKeys(access.selected_access_group_keys || []);
        userAssetSelectionInitializedRef.current = true;
      }
    } catch (err: unknown) {
      if (requestId !== userAssetRequestIdRef.current) return;
      setUserAssetAccess(null);
      setUserAssetError(err instanceof Error ? err.message : 'Failed to load runtime user asset access');
    } finally {
      if (requestId === userAssetRequestIdRef.current) {
        setUserAssetLoading(false);
      }
    }
  }, []);
  useEffect(() => {
    if (!showUserAccessModal || !selectedRuntimeUser?.runtime_user_id) return;
    void loadUserAssetAccess(selectedRuntimeUser.runtime_user_id, {
      search: userAssetSearch,
      offset: userAccessGroupPageOffset,
      preserveSelections: userAssetSelectionInitializedRef.current,
    });
  }, [
    loadUserAssetAccess,
    selectedRuntimeUser?.runtime_user_id,
    showUserAccessModal,
    userAccessGroupPageOffset,
    userAssetSearch,
  ]);

  const clearInvitePrefill = useCallback(() => {
    if (!inviteOrganizationId && !inviteTeamId) return;
    const next = new URLSearchParams(searchParams);
    next.delete('invite_org_id');
    next.delete('invite_team_id');
    setSearchParams(next, { replace: true });
  }, [inviteOrganizationId, inviteTeamId, searchParams, setSearchParams]);

  const openCreateAccount = () => {
    setError('');
    setShowProvisionModal(true);
  };

  const openEditAccount = (acct: Principal) => {
    setEditAccount(acct);
    setFormEmail(acct.email);
    setFormRole(acct.role);
    setFormPassword('');
    setFormActive(acct.is_active);
    setError('');
    setShowAccountModal(true);
  };

  const saveAccount = async () => {
    if (!formEmail.trim()) { setError('Email is required'); return; }
    setSaving(true);
    setError('');
    try {
      const data: any = { email: formEmail.trim(), role: formRole, is_active: formActive };
      if (formPassword.trim()) data.password = formPassword.trim();
      await rbac.accounts.upsert(data);
      setShowAccountModal(false);
      await loadPrincipals();
    } catch (err: any) {
      setError(err?.message || 'Failed to save account');
    } finally {
      setSaving(false);
    }
  };

  const openAddOrgMembership = (accountId: string) => {
    setMembershipAccountId(accountId);
    setMembershipOrgId(orgList[0]?.organization_id || '');
    setMembershipOrgRole('org_member');
    setError('');
    setShowOrgMembershipModal(true);
  };

  const saveOrgMembership = async () => {
    if (!membershipOrgId) { setError('Select an organization'); return; }
    setSaving(true);
    setError('');
    try {
      await rbac.orgMemberships.upsert({
        account_id: membershipAccountId,
        organization_id: membershipOrgId,
        role: membershipOrgRole,
      });
      setShowOrgMembershipModal(false);
      await loadPrincipals();
    } catch (err: any) {
      setError(err?.message || 'Failed to save membership');
    } finally {
      setSaving(false);
    }
  };

  const openAddTeamMembership = (accountId: string) => {
    setMembershipAccountId(accountId);
    setMembershipTeamId(teamList[0]?.team_id || '');
    setMembershipTeamRole('team_viewer');
    setError('');
    setShowTeamMembershipModal(true);
  };

  const saveTeamMembership = async () => {
    if (!membershipTeamId) { setError('Select a team'); return; }
    setSaving(true);
    setError('');
    try {
      await rbac.teamMemberships.upsert({
        account_id: membershipAccountId,
        team_id: membershipTeamId,
        role: membershipTeamRole,
      });
      setShowTeamMembershipModal(false);
      await loadPrincipals();
    } catch (err: any) {
      setError(err?.message || 'Failed to save membership');
    } finally {
      setSaving(false);
    }
  };

  const deleteAccount = async (accountId: string, email: string) => {
    if (!confirm(`Delete account "${email}" and all memberships?`)) return;
    setSaving(true);
    setError('');
    try {
      await rbac.accounts.delete(accountId);
      if (selectedAccount?.account_id === accountId) {
        setSelectedAccount(null);
      }
      await loadPrincipals();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete account');
    } finally {
      setSaving(false);
    }
  };

  const deleteOrgMembership = async (membershipId: string) => {
    if (!confirm('Remove this organization membership?')) return;
    setSaving(true);
    setError('');
    try {
      await rbac.orgMemberships.delete(membershipId);
      await loadPrincipals();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete organization membership');
    } finally {
      setSaving(false);
    }
  };

  const deleteTeamMembership = async (membershipId: string) => {
    if (!confirm('Remove this team membership?')) return;
    setSaving(true);
    setError('');
    try {
      await rbac.teamMemberships.delete(membershipId);
      await loadPrincipals();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete team membership');
    } finally {
      setSaving(false);
    }
  };

  const openUserAssetAccess = (acct: Principal) => {
    userAssetRequestIdRef.current += 1;
    userAssetSelectionInitializedRef.current = false;
    setUserAssetMode('inherit');
    setUserAssetSelectedKeys([]);
    setUserAssetSelectedAccessGroupKeys([]);
    setUserAssetSearchInput('');
    setUserAssetSearch('');
    setUserAccessGroupPageOffset(0);
    if (!acct.runtime_user_id) {
      setSelectedRuntimeUser(acct);
      setShowUserAccessModal(true);
      setUserAssetLoading(false);
      setUserAssetAccess(null);
      setUserAssetError('This account is not linked to a runtime user yet.');
      return;
    }
    setSelectedRuntimeUser(acct);
    setShowUserAccessModal(true);
    setUserAssetLoading(true);
    setUserAssetError('');
    setUserAssetAccess(null);
  };

  const saveUserAssetAccess = async () => {
    if (!selectedRuntimeUser?.runtime_user_id) return;
    if (userAssetLoading || !canSaveUserAssetAccess) {
      setUserAssetError('Wait for runtime user asset access to finish loading before saving.');
      return;
    }
    userAssetRequestIdRef.current += 1;
    setSaving(true);
    setUserAssetError('');
    try {
      const response = await users.updateAssetAccess(selectedRuntimeUser.runtime_user_id, {
        mode: userAssetMode,
        selected_callable_keys: userAssetMode === 'inherit' ? [] : userAssetSelectedKeys,
        selected_access_group_keys: userAssetMode === 'inherit' ? [] : userAssetSelectedAccessGroupKeys,
      });
      setUserAssetAccess(response);
      setUserAssetMode(response.mode === 'restrict' ? 'restrict' : 'inherit');
      setUserAssetSelectedKeys(response.selected_callable_keys || []);
      setUserAssetSelectedAccessGroupKeys(response.selected_access_group_keys || []);
      userAssetSelectionInitializedRef.current = true;
      setShowUserAccessModal(false);
    } catch (err: any) {
      setUserAssetError(err?.message || 'Failed to update runtime user asset access');
    } finally {
      setSaving(false);
    }
  };

  const getOrgName = (orgId: string) => {
    const org = orgList.find(o => o.organization_id === orgId);
    return org?.organization_name || orgId.slice(0, 12) + '...';
  };

  const getTeamName = (teamId: string) => {
    const team = teamList.find(t => t.team_id === teamId);
    return team?.team_alias || teamId.slice(0, 12) + '...';
  };

  const handleInvitationResend = async (invitationId: string) => {
    setInvitationSaving(true);
    setInvitationError('');
    try {
      await invitations.resend(invitationId);
      pushToast({ tone: 'success', message: 'Invitation resent.' });
      await loadInvitations();
    } catch (err: any) {
      setInvitationError(err?.message || 'Failed to resend invitation');
    } finally {
      setInvitationSaving(false);
    }
  };

  const handleInvitationCancel = async (invitationId: string) => {
    if (!window.confirm('Cancel this invitation?')) return;
    setInvitationSaving(true);
    setInvitationError('');
    try {
      await invitations.cancel(invitationId);
      pushToast({ tone: 'success', message: 'Invitation cancelled.' });
      await loadInvitations();
    } catch (err: any) {
      setInvitationError(err?.message || 'Failed to cancel invitation');
    } finally {
      setInvitationSaving(false);
    }
  };

  const totalAccounts = principalPagination.total;
  const currentPage = Math.floor(principalPageOffset / pageSize) + 1;
  const totalPages = Math.max(1, Math.ceil((principalPagination.total || 0) / pageSize));
  const hasPrev = principalPageOffset > 0;
  const hasNext = principalPagination.has_more;
  const visibleCount = viewTab === 'users' ? principalPagination.total : invitationPagination.total;
  const hasUserAssetSelections = userAssetSelectedKeys.length > 0 || userAssetSelectedAccessGroupKeys.length > 0;
  const showPageNotice =
    viewTab === 'users' &&
    !!principalError &&
    !showProvisionModal &&
    !selectedAccount &&
    !showAccountModal &&
    !showOrgMembershipModal &&
    !showTeamMembershipModal &&
    !showUserAccessModal;

  return (
    <IndexShell
      title="People & Access"
      titleIcon={UserCog}
      count={visibleCount}
      description={viewTab === 'users' ? 'Manage people, RBAC roles, and organization/team memberships. Summary cards reflect all platform accounts.' : 'Track and manage invitations across their lifecycle.'}
      notice={showPageNotice ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{principalError}</div>
      ) : undefined}
      action={(
        <button
          onClick={openCreateAccount}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          Add Person
        </button>
      )}
      toolbar={(
        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex rounded-lg border border-gray-300 bg-white p-0.5">
            <button
              onClick={() => setViewTab('users')}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewTab === 'users' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'}`}
            >
              <UsersRound className="h-3.5 w-3.5" />
              Users
            </button>
            <button
              onClick={() => setViewTab('invitations')}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewTab === 'invitations' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-600 hover:bg-gray-50'}`}
            >
              <Mail className="h-3.5 w-3.5" />
              Invitations
            </button>
          </div>
          <div className="relative flex-1 min-w-[260px]">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              value={viewTab === 'users' ? principalSearchInput : invitationSearchInput}
              onChange={(e) => {
                if (viewTab === 'users') {
                  setPrincipalSearchInput(e.target.value);
                  return;
                }
                setInvitationSearchInput(e.target.value);
              }}
              placeholder={viewTab === 'users' ? 'Search accounts...' : 'Search invitations...'}
              className="w-full rounded-lg border border-gray-300 py-2 pl-10 pr-4 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          {viewTab === 'invitations' ? (
            <select
              value={invitationStatusFilter}
              onChange={(e) => {
                setInvitationStatusFilter(e.target.value as InvitationStatusFilter);
                setInvitationPageOffset(0);
              }}
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="active">Active</option>
              <option value="sent">Sent</option>
              <option value="pending">Pending</option>
              <option value="accepted">Accepted</option>
              <option value="cancelled">Cancelled</option>
              <option value="expired">Expired</option>
            </select>
          ) : null}
        </div>
      )}
      summaryItems={[
        { label: 'Active accounts', value: String(principalSummary.active_accounts) },
        { label: 'Platform admins', value: String(principalSummary.platform_admins) },
        { label: 'MFA enabled', value: String(principalSummary.mfa_enabled_accounts), icon: ShieldCheck, iconClassName: 'text-green-600' },
        { label: 'Org memberships', value: String(principalSummary.organization_memberships), icon: Building2, iconClassName: 'text-blue-600' },
        { label: 'Team memberships', value: String(principalSummary.team_memberships), icon: UsersRound, iconClassName: 'text-violet-600' },
      ]}
    >
      <ContentCard>
        {viewTab === 'users' ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 bg-gray-50">
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Account</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Platform Role</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Access</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Last Login</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Status</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-gray-400">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {principalLoading ? (
                    <tr>
                      <td colSpan={6} className="px-4 py-12 text-center">
                        <div className="inline-flex items-center gap-3 text-sm text-gray-500">
                          <div className="h-5 w-5 animate-spin rounded-full border-b-2 border-blue-600" />
                          Loading accounts…
                        </div>
                      </td>
                    </tr>
                  ) : principals.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-4 py-14 text-center">
                        <UserCog className="mx-auto mb-3 h-10 w-10 text-gray-300" />
                        <p className="text-sm font-medium text-gray-600">No accounts found</p>
                        <p className="mt-1 text-xs text-gray-400">
                          {principalSearchTerm ? 'Try a different search term.' : 'Create a platform account or send an invitation to get started.'}
                        </p>
                        {!principalSearchTerm ? (
                          <button
                            onClick={openCreateAccount}
                            className="mt-4 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
                          >
                            <Plus className="h-4 w-4" />
                            Add Person
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ) : (
                    principals.map((acct, index) => {
                      const acctOrgMs = acct.organization_memberships || [];
                      const acctTeamMs = acct.team_memberships || [];
                      return (
                        <tr
                          key={acct.account_id}
                          onClick={() => { setError(''); setSelectedAccount(acct); }}
                          className={`cursor-pointer border-b border-gray-100 transition-colors hover:bg-blue-50/40 ${
                            index === principals.length - 1 ? 'border-b-0' : ''
                          }`}
                        >
                          <td className="px-4 py-3.5">
                            <div className="flex items-center gap-3">
                              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-blue-100 to-blue-200 text-xs font-bold text-blue-700">
                                {(acct.email || '?')[0]?.toUpperCase()}
                              </div>
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="truncate font-semibold text-gray-900">{acct.email}</span>
                                  {acct.mfa_enabled ? <ShieldCheck className="h-4 w-4 shrink-0 text-green-500" /> : null}
                                </div>
                                <div className="mt-1 flex items-center gap-2 text-[11px] text-gray-400">
                                  <code className="font-mono">{acct.account_id}</code>
                                  {acct.runtime_user_id ? (
                                    <span className="inline-flex items-center rounded-full bg-indigo-50 px-2 py-0.5 font-medium text-indigo-600">
                                      Runtime linked
                                    </span>
                                  ) : null}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-4 py-3.5">
                            <RoleBadge role={acct.role} />
                          </td>
                          <td className="px-4 py-3.5">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <MembershipCountBadge count={acctOrgMs.length} label={acctOrgMs.length === 1 ? 'org' : 'orgs'} />
                              <MembershipCountBadge count={acctTeamMs.length} label={acctTeamMs.length === 1 ? 'team' : 'teams'} />
                            </div>
                          </td>
                          <td className="px-4 py-3.5">
                            <span className="text-xs text-gray-600">{formatDate(acct.last_login_at)}</span>
                          </td>
                          <td className="px-4 py-3.5">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span
                                className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                                  acct.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-red-100 text-red-700'
                                }`}
                              >
                                {acct.is_active ? 'Active' : 'Disabled'}
                              </span>
                              {acct.force_password_change ? (
                                <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                                  Password reset required
                                </span>
                              ) : null}
                            </div>
                          </td>
                          <td className="px-4 py-3.5" onClick={(e) => e.stopPropagation()}>
                            <div className="flex items-center justify-end gap-1.5">
                              <button
                                onClick={() => { setError(''); setSelectedAccount(acct); }}
                                className="rounded-lg bg-blue-50 px-2.5 py-1.5 text-xs font-medium text-blue-600 transition-colors hover:bg-blue-100"
                              >
                                View
                              </button>
                              <button
                                onClick={() => openUserAssetAccess(acct)}
                                disabled={!acct.runtime_user_id}
                                title={acct.runtime_user_id ? 'Manage runtime asset access' : 'No linked runtime user'}
                                className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors ${
                                  acct.runtime_user_id
                                    ? 'bg-indigo-50 text-indigo-600 hover:bg-indigo-100'
                                    : 'cursor-not-allowed bg-gray-100 text-gray-300'
                                }`}
                              >
                                Asset Access
                              </button>
                              <button
                                onClick={() => openEditAccount(acct)}
                                className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-100"
                              >
                                Edit
                              </button>
                              <button
                                onClick={() => deleteAccount(acct.account_id, acct.email)}
                                className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-50"
                              >
                                Delete
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-gray-200 bg-gray-50 px-4 py-3">
              <span className="text-xs text-gray-500">
                {principalLoading
                  ? 'Loading…'
                  : `Showing ${Math.min(principalPageOffset + 1, totalAccounts || 0)}–${Math.min(principalPageOffset + principals.length, totalAccounts || 0)} of ${totalAccounts}`}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-400">
                  Page {currentPage} of {totalPages}
                </span>
                <button
                  onClick={() => setPrincipalPageOffset(Math.max(0, principalPageOffset - pageSize))}
                  disabled={!hasPrev || principalLoading}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  onClick={() => setPrincipalPageOffset(principalPageOffset + pageSize)}
                  disabled={!hasNext || principalLoading}
                  className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        ) : (
          <InvitationPanel
            items={invitationItems}
            loading={invitationLoading}
            saving={invitationSaving}
            error={invitationError}
            pagination={invitationPagination}
            onPageChange={setInvitationPageOffset}
            onResend={handleInvitationResend}
            onCancel={handleInvitationCancel}
          />
        )}
      </ContentCard>

      <Modal
        open={!!selectedAccount}
        onClose={() => setSelectedAccount(null)}
        title={selectedAccount ? `Access Details · ${selectedAccount.email}` : 'Access Details'}
      >
        {selectedAccount ? (
          <div className="space-y-5">
            {error ? (
              <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
            ) : null}
            <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-4">
              <div className="flex flex-wrap items-center gap-2">
                <RoleBadge role={selectedAccount.role} />
                <span
                  className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                    selectedAccount.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-red-100 text-red-700'
                  }`}
                >
                  {selectedAccount.is_active ? 'Active' : 'Disabled'}
                </span>
                {selectedAccount.mfa_enabled ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700">
                    <ShieldCheck className="h-3.5 w-3.5" />
                    MFA enabled
                  </span>
                ) : null}
                {selectedAccount.runtime_user_id ? (
                  <span className="inline-flex items-center rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-600">
                    Runtime user linked
                  </span>
                ) : null}
              </div>
              <div className="mt-3 grid grid-cols-1 gap-2 text-xs text-gray-500 sm:grid-cols-2">
                <span>ID: {selectedAccount.account_id}</span>
                <span>Created: {formatDate(selectedAccount.created_at)}</span>
                <span>Last login: {formatDate(selectedAccount.last_login_at)}</span>
                {selectedAccount.runtime_user_id ? <span>Runtime user: {selectedAccount.runtime_user_id}</span> : null}
              </div>
            </div>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <h4 className="flex items-center gap-1.5 text-sm font-medium text-gray-700">
                    <Building2 className="h-4 w-4" />
                    Organization Memberships
                  </h4>
                  <button
                    onClick={() => openAddOrgMembership(selectedAccount.account_id)}
                    className="text-xs font-medium text-blue-600 hover:text-blue-800"
                  >
                    + Add
                  </button>
                </div>
                {selectedAccount.organization_memberships.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-center text-xs text-gray-400">
                    No organization memberships
                  </div>
                ) : (
                  <div className="space-y-2">
                    {selectedAccount.organization_memberships.map((membership) => (
                      <div key={membership.membership_id} className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-3 py-2.5">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-gray-900">{getOrgName(membership.organization_id)}</p>
                          <p className="mt-0.5 text-[11px] text-gray-400">{membership.organization_id}</p>
                        </div>
                        <div className="flex items-center gap-2 pl-3">
                          <RoleBadge role={membership.role} />
                          <button
                            onClick={() => deleteOrgMembership(membership.membership_id)}
                            className="rounded-lg p-1 hover:bg-red-50"
                            title="Remove organization membership"
                          >
                            <Trash2 className="h-3.5 w-3.5 text-red-500" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <div className="mb-3 flex items-center justify-between">
                  <h4 className="flex items-center gap-1.5 text-sm font-medium text-gray-700">
                    <UsersRound className="h-4 w-4" />
                    Team Memberships
                  </h4>
                  <button
                    onClick={() => openAddTeamMembership(selectedAccount.account_id)}
                    className="text-xs font-medium text-blue-600 hover:text-blue-800"
                  >
                    + Add
                  </button>
                </div>
                {selectedAccount.team_memberships.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-center text-xs text-gray-400">
                    No team memberships
                  </div>
                ) : (
                  <div className="space-y-2">
                    {selectedAccount.team_memberships.map((membership) => (
                      <div key={membership.membership_id} className="flex items-center justify-between rounded-xl border border-gray-200 bg-white px-3 py-2.5">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium text-gray-900">{getTeamName(membership.team_id)}</p>
                          <p className="mt-0.5 text-[11px] text-gray-400">{membership.team_id}</p>
                        </div>
                        <div className="flex items-center gap-2 pl-3">
                          <RoleBadge role={membership.role} />
                          <button
                            onClick={() => deleteTeamMembership(membership.membership_id)}
                            className="rounded-lg p-1 hover:bg-red-50"
                            title="Remove team membership"
                          >
                            <Trash2 className="h-3.5 w-3.5 text-red-500" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : null}
      </Modal>

      <ProvisionPersonModal
        open={showProvisionModal}
        onClose={() => {
          setShowProvisionModal(false);
          clearInvitePrefill();
        }}
        onSuccess={async (result) => {
          clearInvitePrefill();
          const refreshTasks: Promise<unknown>[] = [loadPrincipals()];
          if (result.mode === 'invite_email' || viewTab === 'invitations') {
            refreshTasks.push(loadInvitations());
          }
          await Promise.all(refreshTasks);
        }}
        orgList={orgList}
        teamList={teamList}
        initialOrganizationId={inviteOrganizationId}
        initialTeamId={inviteTeamId}
      />

      <Modal open={showAccountModal} onClose={() => setShowAccountModal(false)} title="Edit Account">
        <div className="space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Email</label>
            <input
              type="email"
              value={formEmail}
              onChange={(e) => setFormEmail(e.target.value)}
              placeholder="user@example.com"
              disabled={!!editAccount}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-100"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Platform Role</label>
            <select
              value={formRole}
              onChange={(e) => setFormRole(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              {PLATFORM_ROLES.map(r => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              New Password (leave blank to keep current)
            </label>
            <input
              type="password"
              value={formPassword}
              onChange={(e) => setFormPassword(e.target.value)}
              placeholder="Leave blank to keep current"
              autoComplete="new-password"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="account-active"
              checked={formActive}
              onChange={(e) => setFormActive(e.target.checked)}
              className="rounded border-gray-300"
            />
            <label htmlFor="account-active" className="text-sm text-gray-700">Account active</label>
          </div>
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => setShowAccountModal(false)}
              className="flex-1 border border-gray-300 text-gray-700 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={saveAccount}
              disabled={saving}
              className="flex-1 bg-blue-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Update Account'}
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        open={showUserAccessModal}
        onClose={() => { if (!saving) setShowUserAccessModal(false); }}
        title={selectedRuntimeUser ? `Runtime Asset Access · ${selectedRuntimeUser.email}` : 'Runtime Asset Access'}
      >
        <div className="space-y-4">
          {userAssetError && (
            <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">{userAssetError}</div>
          )}
          {!userAssetError && selectedRuntimeUser?.runtime_user_id && (
            <p className="text-sm text-gray-500">
              This edits the runtime user scope used by callable-target governance.
            </p>
          )}
          <AssetAccessEditor
            title="User Asset Access"
            description="Restrict this runtime user below the inherited team and organization asset set when needed."
            mode={userAssetMode}
            allowModeSelection
            onModeChange={(mode) => {
              const nextMode = mode === 'restrict' ? 'restrict' : 'inherit';
              setUserAssetMode(nextMode);
              if (nextMode === 'inherit') {
                setUserAssetSelectedKeys([]);
                setUserAssetSelectedAccessGroupKeys([]);
              }
            }}
            targets={userAssetAccess?.selectable_targets || []}
            selectedKeys={userAssetSelectedKeys}
            onSelectedKeysChange={setUserAssetSelectedKeys}
            accessGroups={userAssetAccess?.selectable_access_groups || []}
            selectedAccessGroupKeys={userAssetSelectedAccessGroupKeys}
            onSelectedAccessGroupKeysChange={setUserAssetSelectedAccessGroupKeys}
            targetsLoading={userAssetLoading}
            accessGroupsLoading={userAssetLoading}
            disabled={saving || userAssetLoading || !userAssetAccess}
            modeControlsDisabled={userAssetModeControlsDisabled}
            searchValue={userAssetSearchInput}
            onSearchValueChange={setUserAssetSearchInput}
            accessGroupPagination={userAssetAccess?.access_group_pagination}
            onAccessGroupPageChange={setUserAccessGroupPageOffset}
            primaryActionLabel={userAssetMode === 'restrict' ? 'Select All Visible' : undefined}
            onPrimaryAction={userAssetMode === 'restrict' ? (() => {
              const next = (userAssetAccess?.selectable_targets || [])
                .filter((item) => item.selectable)
                .map((item) => item.callable_key)
                .sort();
              setUserAssetSelectedKeys(next);
            }) : undefined}
            secondaryActionLabel={userAssetMode === 'restrict' && hasUserAssetSelections ? 'Clear Selection' : undefined}
            onSecondaryAction={userAssetMode === 'restrict' && hasUserAssetSelections ? (() => {
              setUserAssetSelectedKeys([]);
              setUserAssetSelectedAccessGroupKeys([]);
            }) : undefined}
          />
          {userAssetAccess && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
              Effective visible assets: {userAssetAccess.summary.effective_total} · Direct selections: {userAssetSelectedKeys.length} · Access groups: {userAssetSelectedAccessGroupKeys.length}
            </div>
          )}
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => setShowUserAccessModal(false)}
              disabled={saving}
              className="flex-1 border border-gray-300 text-gray-700 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={saveUserAssetAccess}
              disabled={saving || userAssetLoading || !canSaveUserAssetAccess}
              className="flex-1 bg-indigo-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Save Access'}
            </button>
          </div>
        </div>
      </Modal>

      <Modal open={showOrgMembershipModal} onClose={() => setShowOrgMembershipModal(false)} title="Add Organization Membership">
        <div className="space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Organization</label>
            <select
              value={membershipOrgId}
              onChange={(e) => setMembershipOrgId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              {orgList.length === 0 && <option value="">No organizations available</option>}
              {orgList.map(o => (
                <option key={o.organization_id} value={o.organization_id}>
                  {o.organization_name || o.organization_id}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Role</label>
            <select
              value={membershipOrgRole}
              onChange={(e) => setMembershipOrgRole(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              {ORG_ROLES.map(r => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => setShowOrgMembershipModal(false)}
              className="flex-1 border border-gray-300 text-gray-700 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={saveOrgMembership}
              disabled={saving}
              className="flex-1 bg-blue-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Add Membership'}
            </button>
          </div>
        </div>
      </Modal>

      <Modal open={showTeamMembershipModal} onClose={() => setShowTeamMembershipModal(false)} title="Add Team Membership">
        <div className="space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Team</label>
            <select
              value={membershipTeamId}
              onChange={(e) => setMembershipTeamId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              {teamList.length === 0 && <option value="">No teams available</option>}
              {teamList.map(t => (
                <option key={t.team_id} value={t.team_id}>
                  {t.team_alias || t.team_id}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Role</label>
            <select
              value={membershipTeamRole}
              onChange={(e) => setMembershipTeamRole(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              {TEAM_ROLES.map(r => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </select>
          </div>
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => setShowTeamMembershipModal(false)}
              className="flex-1 border border-gray-300 text-gray-700 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={saveTeamMembership}
              disabled={saving}
              className="flex-1 bg-blue-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Add Membership'}
            </button>
          </div>
        </div>
      </Modal>
    </IndexShell>
  );
}
