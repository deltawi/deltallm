import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { organizations, rbac, teams, users, type Principal, type ScopedAssetAccess } from '../lib/api';
import { Plus, UserCog, ShieldCheck, Search, ChevronDown, ChevronRight, Building2, UsersRound, Trash2 } from 'lucide-react';
import Modal from '../components/Modal';
import AssetAccessEditor from '../components/access/AssetAccessEditor';
import { IndexShell } from '../components/admin/shells';
import InvitationPanel from '../components/admin/InvitationPanel';

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

export default function RBACAccounts() {
  const [searchParams] = useSearchParams();
  const [principals, setPrincipals] = useState<Principal[]>([]);
  const [principalPagination, setPrincipalPagination] = useState({ total: 0, limit: 20, offset: 0, has_more: false });
  const [orgList, setOrgList] = useState<any[]>([]);
  const [teamList, setTeamList] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchInput, setSearchInput] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [pageOffset, setPageOffset] = useState(0);
  const pageSize = 20;
  const inviteOrganizationId = searchParams.get('invite_org_id');
  const inviteTeamId = searchParams.get('invite_team_id');

  const [showAccountModal, setShowAccountModal] = useState(false);
  const [editAccount, setEditAccount] = useState<Principal | null>(null);
  const [formEmail, setFormEmail] = useState('');
  const [formRole, setFormRole] = useState('org_user');
  const [formPassword, setFormPassword] = useState('');
  const [formActive, setFormActive] = useState(true);

  const [expandedAccount, setExpandedAccount] = useState<string | null>(null);

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
  const [userAssetLoading, setUserAssetLoading] = useState(false);
  const [userAssetError, setUserAssetError] = useState('');

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [allPrincipals, orgs, tms] = await Promise.all([
        rbac.principals.list({ search: searchTerm, limit: pageSize, offset: pageOffset }),
        organizations.list({ limit: 500 }).catch(() => ({ data: [], pagination: { total: 0, limit: 500, offset: 0, has_more: false } })),
        teams.list({ limit: 500 }).catch(() => ({ data: [], pagination: { total: 0, limit: 500, offset: 0, has_more: false } })),
      ]);
      setPrincipals(allPrincipals?.data || []);
      setPrincipalPagination(allPrincipals?.pagination || { total: 0, limit: pageSize, offset: pageOffset, has_more: false });
      setOrgList(orgs?.data || orgs || []);
      setTeamList(tms?.data || tms || []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load accounts');
    } finally {
      setLoading(false);
    }
  }, [pageOffset, pageSize, searchTerm]);

  useEffect(() => { loadData(); }, [loadData]);
  useEffect(() => {
    const t = setTimeout(() => {
      setSearchTerm(searchInput);
      setPageOffset(0);
    }, 250);
    return () => clearTimeout(t);
  }, [searchInput]);

  const openCreateAccount = () => {
    setEditAccount(null);
    setFormEmail('');
    setFormRole('org_user');
    setFormPassword('');
    setFormActive(true);
    setError('');
    setShowAccountModal(true);
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
      await loadData();
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
      await loadData();
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
      await loadData();
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
      await loadData();
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
      await loadData();
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
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete team membership');
    } finally {
      setSaving(false);
    }
  };

  const openUserAssetAccess = async (acct: Principal) => {
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
    try {
      const access = await users.assetAccess(acct.runtime_user_id, { include_targets: true });
      setUserAssetAccess(access);
      setUserAssetMode(access.mode === 'restrict' ? 'restrict' : 'inherit');
      setUserAssetSelectedKeys(access.selected_callable_keys || []);
    } catch (err: any) {
      setUserAssetError(err?.message || 'Failed to load runtime user asset access');
    } finally {
      setUserAssetLoading(false);
    }
  };

  const saveUserAssetAccess = async () => {
    if (!selectedRuntimeUser?.runtime_user_id) return;
    setSaving(true);
    setUserAssetError('');
    try {
      const response = await users.updateAssetAccess(selectedRuntimeUser.runtime_user_id, {
        mode: userAssetMode,
        selected_callable_keys: userAssetMode === 'inherit' ? [] : userAssetSelectedKeys,
      });
      setUserAssetAccess(response);
      setUserAssetMode(response.mode === 'restrict' ? 'restrict' : 'inherit');
      setUserAssetSelectedKeys(response.selected_callable_keys || []);
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

  return (
    <IndexShell
      title="People & Access"
      titleIcon={UserCog}
      count={principalPagination.total}
      description="Manage people, RBAC roles, and organization/team memberships"
      action={(
        <button
          onClick={openCreateAccount}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          <Plus className="h-4 w-4" />
          Add User
        </button>
      )}
      toolbar={(
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search accounts..."
            className="w-full rounded-lg border border-gray-300 py-2 pl-10 pr-4 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}
    >
      <div className="mb-6">
        <InvitationPanel
          orgList={orgList}
          teamList={teamList}
          initialOrganizationId={inviteOrganizationId}
          initialTeamId={inviteTeamId}
        />
      </div>
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-blue-600" />
        </div>
      ) : principals.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white py-12 text-center">
          <UserCog className="mx-auto mb-3 h-12 w-12 text-gray-300" />
          <p className="text-gray-500">No accounts found</p>
        </div>
      ) : (
        <div className="space-y-2">
          {principals.map((acct) => {
            const isExpanded = expandedAccount === acct.account_id;
            const acctOrgMs = acct.organization_memberships || [];
            const acctTeamMs = acct.team_memberships || [];

            return (
              <div key={acct.account_id} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                <div
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-gray-50 transition-colors"
                  onClick={() => setExpandedAccount(isExpanded ? null : acct.account_id)}
                >
                  <div className="flex-shrink-0">
                    {isExpanded ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900 truncate">{acct.email}</span>
                      <RoleBadge role={acct.role} />
                      {!acct.is_active && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                          Disabled
                        </span>
                      )}
                      {acct.mfa_enabled && (
                        <ShieldCheck className="w-4 h-4 text-green-500" />
                      )}
                    </div>
                    <div className="flex items-center gap-4 mt-1">
                      <span className="text-xs text-gray-400">
                        Last login: {formatDate(acct.last_login_at)}
                      </span>
                      <span className="text-xs text-gray-400">
                        {acctOrgMs.length} org{acctOrgMs.length !== 1 ? 's' : ''} · {acctTeamMs.length} team{acctTeamMs.length !== 1 ? 's' : ''}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={(e) => { e.stopPropagation(); openUserAssetAccess(acct); }}
                      disabled={!acct.runtime_user_id}
                      title={acct.runtime_user_id ? 'Manage runtime asset access' : 'No linked runtime user'}
                      className={`text-sm font-medium ${
                        acct.runtime_user_id
                          ? 'text-indigo-600 hover:text-indigo-800'
                          : 'cursor-not-allowed text-gray-300'
                      }`}
                    >
                      Asset Access
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); openEditAccount(acct); }}
                      className="text-sm text-blue-600 hover:text-blue-800 font-medium"
                    >
                      Edit
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); deleteAccount(acct.account_id, acct.email); }}
                      className="text-sm text-red-600 hover:text-red-800 font-medium"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="border-t border-gray-100 px-5 py-4 bg-gray-50/50">
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                      <div>
                        <div className="flex items-center justify-between mb-3">
                          <h4 className="text-sm font-medium text-gray-700 flex items-center gap-1.5">
                            <Building2 className="w-4 h-4" />
                            Organization Memberships
                          </h4>
                          <button
                            onClick={() => openAddOrgMembership(acct.account_id)}
                            className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                          >
                            + Add
                          </button>
                        </div>
                        {acctOrgMs.length === 0 ? (
                          <p className="text-xs text-gray-400 italic">No organization memberships</p>
                        ) : (
                          <div className="space-y-2">
                            {acctOrgMs.map((m) => (
                              <div key={m.membership_id} className="flex items-center justify-between bg-white rounded-lg px-3 py-2 border border-gray-200">
                                <div>
                                  <span className="text-sm text-gray-900">{getOrgName(m.organization_id)}</span>
                                </div>
                                <div className="flex items-center gap-2">
                                  <RoleBadge role={m.role} />
                                  <button
                                    onClick={() => deleteOrgMembership(m.membership_id)}
                                    className="p-1 hover:bg-red-50 rounded"
                                    title="Remove organization membership"
                                  >
                                    <Trash2 className="w-3.5 h-3.5 text-red-500" />
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                      <div>
                        <div className="flex items-center justify-between mb-3">
                          <h4 className="text-sm font-medium text-gray-700 flex items-center gap-1.5">
                            <UsersRound className="w-4 h-4" />
                            Team Memberships
                          </h4>
                          <button
                            onClick={() => openAddTeamMembership(acct.account_id)}
                            className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                          >
                            + Add
                          </button>
                        </div>
                        {acctTeamMs.length === 0 ? (
                          <p className="text-xs text-gray-400 italic">No team memberships</p>
                        ) : (
                          <div className="space-y-2">
                            {acctTeamMs.map((m) => (
                              <div key={m.membership_id} className="flex items-center justify-between bg-white rounded-lg px-3 py-2 border border-gray-200">
                                <div>
                                  <span className="text-sm text-gray-900">{getTeamName(m.team_id)}</span>
                                </div>
                                <div className="flex items-center gap-2">
                                  <RoleBadge role={m.role} />
                                  <button
                                    onClick={() => deleteTeamMembership(m.membership_id)}
                                    className="p-1 hover:bg-red-50 rounded"
                                    title="Remove team membership"
                                  >
                                    <Trash2 className="w-3.5 h-3.5 text-red-500" />
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="mt-4 pt-3 border-t border-gray-200 flex items-center gap-4 text-xs text-gray-400">
                      <span>ID: {acct.account_id}</span>
                      <span>Created: {formatDate(acct.created_at)}</span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-4 flex items-center justify-between text-xs text-gray-500">
        <span>
          Showing {principals.length} of {principalPagination.total}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPageOffset(Math.max(0, pageOffset - pageSize))}
            disabled={pageOffset === 0 || loading}
            className="px-3 py-1.5 border border-gray-300 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50"
          >
            Previous
          </button>
          <button
            onClick={() => setPageOffset(pageOffset + pageSize)}
            disabled={!principalPagination.has_more || loading}
            className="px-3 py-1.5 border border-gray-300 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed hover:bg-gray-50"
          >
            Next
          </button>
        </div>
      </div>

      <Modal open={showAccountModal} onClose={() => setShowAccountModal(false)} title={editAccount ? 'Edit Account' : 'Create Account'}>
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
              {editAccount ? 'New Password (leave blank to keep current)' : 'Password'}
            </label>
            <input
              type="password"
              value={formPassword}
              onChange={(e) => setFormPassword(e.target.value)}
              placeholder={editAccount ? 'Leave blank to keep current' : 'At least 12 characters'}
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
              {saving ? 'Saving...' : editAccount ? 'Update Account' : 'Create Account'}
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
            onModeChange={(mode) => setUserAssetMode(mode === 'restrict' ? 'restrict' : 'inherit')}
            targets={userAssetAccess?.selectable_targets || []}
            selectedKeys={userAssetSelectedKeys}
            onSelectedKeysChange={setUserAssetSelectedKeys}
            loading={userAssetLoading}
            disabled={saving || !userAssetAccess}
            primaryActionLabel={userAssetMode === 'restrict' ? 'Select All Visible' : undefined}
            onPrimaryAction={userAssetMode === 'restrict' ? (() => {
              const next = (userAssetAccess?.selectable_targets || [])
                .filter((item) => item.selectable)
                .map((item) => item.callable_key)
                .sort();
              setUserAssetSelectedKeys(next);
            }) : undefined}
            secondaryActionLabel={userAssetMode === 'restrict' && userAssetSelectedKeys.length > 0 ? 'Clear Selection' : undefined}
            onSecondaryAction={userAssetMode === 'restrict' && userAssetSelectedKeys.length > 0 ? (() => setUserAssetSelectedKeys([])) : undefined}
          />
          {userAssetAccess && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
              Effective visible assets: {userAssetAccess.summary.effective_total} · Direct selections: {userAssetSelectedKeys.length}
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
              disabled={saving || !userAssetAccess}
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
