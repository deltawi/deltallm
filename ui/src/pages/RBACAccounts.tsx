import { useState, useEffect, useCallback } from 'react';
import { rbac, organizations, teams, type RBACAccount, type OrgMembership, type TeamMembership } from '../lib/api';
import { Plus, UserCog, ShieldCheck, Search, ChevronDown, ChevronRight, Building2, UsersRound } from 'lucide-react';
import Modal from '../components/Modal';

const PLATFORM_ROLES = [
  { value: 'platform_admin', label: 'Platform Admin' },
  { value: 'platform_co_admin', label: 'Platform Co-Admin' },
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
    platform_co_admin: 'bg-indigo-100 text-indigo-800',
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
  const [accounts, setAccounts] = useState<RBACAccount[]>([]);
  const [orgMemberships, setOrgMemberships] = useState<OrgMembership[]>([]);
  const [teamMemberships, setTeamMemberships] = useState<TeamMembership[]>([]);
  const [orgList, setOrgList] = useState<any[]>([]);
  const [teamList, setTeamList] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  const [showAccountModal, setShowAccountModal] = useState(false);
  const [editAccount, setEditAccount] = useState<RBACAccount | null>(null);
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

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [accts, orgMs, teamMs, orgs, tms] = await Promise.all([
        rbac.accounts.list(),
        rbac.orgMemberships.list(),
        rbac.teamMemberships.list(),
        organizations.list().catch(() => []),
        teams.list().catch(() => []),
      ]);
      setAccounts(accts);
      setOrgMemberships(orgMs);
      setTeamMemberships(teamMs);
      setOrgList(orgs);
      setTeamList(tms);
    } catch (err: any) {
      setError(err?.message || 'Failed to load accounts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const openCreateAccount = () => {
    setEditAccount(null);
    setFormEmail('');
    setFormRole('org_user');
    setFormPassword('');
    setFormActive(true);
    setError('');
    setShowAccountModal(true);
  };

  const openEditAccount = (acct: RBACAccount) => {
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

  const filteredAccounts = accounts.filter(a =>
    a.email.toLowerCase().includes(searchTerm.toLowerCase()) ||
    a.role.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const getAccountOrgMemberships = (accountId: string) =>
    orgMemberships.filter(m => m.account_id === accountId);

  const getAccountTeamMemberships = (accountId: string) =>
    teamMemberships.filter(m => m.account_id === accountId);

  const getOrgName = (orgId: string) => {
    const org = orgList.find(o => o.organization_id === orgId);
    return org?.organization_name || orgId.slice(0, 12) + '...';
  };

  const getTeamName = (teamId: string) => {
    const team = teamList.find(t => t.team_id === teamId);
    return team?.team_alias || teamId.slice(0, 12) + '...';
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Access Control</h1>
          <p className="text-sm text-gray-500 mt-1">Manage platform accounts, organization and team memberships</p>
        </div>
        <button
          onClick={openCreateAccount}
          className="inline-flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          Add Account
        </button>
      </div>

      <div className="mb-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search accounts..."
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
        </div>
      ) : filteredAccounts.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-xl border border-gray-200">
          <UserCog className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500">No accounts found</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filteredAccounts.map((acct) => {
            const isExpanded = expandedAccount === acct.account_id;
            const acctOrgMs = getAccountOrgMemberships(acct.account_id);
            const acctTeamMs = getAccountTeamMemberships(acct.account_id);

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
                  <button
                    onClick={(e) => { e.stopPropagation(); openEditAccount(acct); }}
                    className="text-sm text-blue-600 hover:text-blue-800 font-medium"
                  >
                    Edit
                  </button>
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
                                <RoleBadge role={m.role} />
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
                                <RoleBadge role={m.role} />
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
    </div>
  );
}
