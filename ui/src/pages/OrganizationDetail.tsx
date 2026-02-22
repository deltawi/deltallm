import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import { organizations, teams as teamsApi } from '../lib/api';
import Card from '../components/Card';
import DataTable from '../components/DataTable';
import Modal from '../components/Modal';
import { ArrowLeft, Building2, Users, DollarSign, Gauge, Pencil, Plus, User } from 'lucide-react';

function StatCard({ icon: Icon, label, value, subValue, color }: { icon: any; label: string; value: string; subValue?: string; color: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
      <div className="flex items-center gap-3 mb-3">
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon className="w-4 h-4" />
        </div>
        <span className="text-sm text-gray-500">{label}</span>
      </div>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {subValue && <p className="text-xs text-gray-400 mt-1">{subValue}</p>}
    </div>
  );
}

function BudgetBar({ spend, max_budget }: { spend: number; max_budget: number | null }) {
  if (!max_budget) return <span className="text-gray-400 text-xs">No limit</span>;
  const pct = Math.min(100, (spend / max_budget) * 100);
  return (
    <div className="w-24">
      <div className="flex justify-between text-xs mb-0.5">
        <span>${spend.toFixed(2)}</span>
        <span className="text-gray-400">${max_budget}</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-blue-500'}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function OrganizationDetail() {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();

  const { data: org, loading: orgLoading, refetch: refetchOrg } = useApi(() => organizations.get(orgId!), [orgId]);
  const { data: orgTeams, loading: teamsLoading, refetch: refetchTeams } = useApi(() => organizations.teams(orgId!), [orgId]);
  const { data: orgMembers, loading: membersLoading } = useApi(() => organizations.members(orgId!), [orgId]);

  const [showEdit, setShowEdit] = useState(false);
  const [form, setForm] = useState({ organization_name: '', max_budget: '', rpm_limit: '', tpm_limit: '' });
  const [showCreateTeam, setShowCreateTeam] = useState(false);
  const [teamForm, setTeamForm] = useState({ team_alias: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });

  const openEdit = () => {
    if (!org) return;
    setForm({
      organization_name: org.organization_name || '',
      max_budget: org.max_budget != null ? String(org.max_budget) : '',
      rpm_limit: org.rpm_limit != null ? String(org.rpm_limit) : '',
      tpm_limit: org.tpm_limit != null ? String(org.tpm_limit) : '',
    });
    setShowEdit(true);
  };

  const handleSaveOrg = async () => {
    await organizations.update(orgId!, {
      organization_name: form.organization_name || undefined,
      max_budget: form.max_budget ? Number(form.max_budget) : undefined,
      rpm_limit: form.rpm_limit ? Number(form.rpm_limit) : undefined,
      tpm_limit: form.tpm_limit ? Number(form.tpm_limit) : undefined,
    });
    setShowEdit(false);
    refetchOrg();
  };

  const handleCreateTeam = async () => {
    await teamsApi.create({
      team_alias: teamForm.team_alias || undefined,
      organization_id: orgId,
      max_budget: teamForm.max_budget ? Number(teamForm.max_budget) : undefined,
      rpm_limit: teamForm.rpm_limit ? Number(teamForm.rpm_limit) : undefined,
      tpm_limit: teamForm.tpm_limit ? Number(teamForm.tpm_limit) : undefined,
      models: teamForm.models ? teamForm.models.split(',').map(m => m.trim()).filter(Boolean) : [],
    });
    setShowCreateTeam(false);
    setTeamForm({ team_alias: '', max_budget: '', rpm_limit: '', tpm_limit: '', models: '' });
    refetchTeams();
  };

  if (orgLoading) {
    return (
      <div className="p-6 flex items-center justify-center py-24">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!org) {
    return (
      <div className="p-6">
        <p className="text-gray-500">Organization not found.</p>
        <Link to="/organizations" className="text-blue-600 text-sm mt-2 inline-block">Back to Organizations</Link>
      </div>
    );
  }

  const teamColumns = [
    { key: 'team_alias', header: 'Name', render: (r: any) => (
      <Link to={`/teams/${r.team_id}`} className="font-medium text-blue-600 hover:text-blue-700">{r.team_alias || r.team_id}</Link>
    ) },
    { key: 'team_id', header: 'Team ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{r.team_id}</code> },
    { key: 'member_count', header: 'Members', render: (r: any) => (
      <span className="flex items-center gap-1"><Users className="w-3.5 h-3.5 text-gray-400" /> {r.member_count || 0}</span>
    ) },
    { key: 'budget', header: 'Budget', render: (r: any) => <BudgetBar spend={r.spend || 0} max_budget={r.max_budget} /> },
    { key: 'models', header: 'Models', render: (r: any) => r.models?.length ? <span className="text-xs">{r.models.join(', ')}</span> : <span className="text-gray-400 text-xs">All</span> },
  ];

  const memberColumns = [
    { key: 'user_email', header: 'Email', render: (r: any) => r.user_email || <span className="text-gray-400">--</span> },
    { key: 'user_id', header: 'User ID', render: (r: any) => <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded font-mono">{r.user_id}</code> },
    { key: 'team_alias', header: 'Team', render: (r: any) => (
      <Link to={`/teams/${r.team_id}`} className="text-sm text-blue-600 hover:text-blue-700">{r.team_alias || r.team_id}</Link>
    ) },
    { key: 'user_role', header: 'Role', render: (r: any) => (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700">{r.user_role}</span>
    ) },
    { key: 'spend', header: 'Spend', render: (r: any) => <span className="text-sm">${(r.spend || 0).toFixed(2)}</span> },
  ];

  return (
    <div className="p-4 sm:p-6 max-w-6xl">
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-6">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <button onClick={() => navigate('/organizations')} className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors">
            <ArrowLeft className="w-5 h-5 text-gray-500" />
          </button>
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-50 rounded-lg">
              <Building2 className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">{org.organization_name || org.organization_id}</h1>
              <p className="text-xs text-gray-400 font-mono mt-0.5">{org.organization_id}</p>
            </div>
          </div>
        </div>
        <button onClick={openEdit} className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
          <Pencil className="w-4 h-4" /> Edit
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard icon={DollarSign} label="Spend" value={`$${(org.spend || 0).toFixed(2)}`} subValue={org.max_budget ? `of $${org.max_budget} budget` : 'No budget limit'} color="bg-green-50 text-green-600" />
        <StatCard icon={Users} label="Teams" value={String(orgTeams?.length || 0)} color="bg-blue-50 text-blue-600" />
        <StatCard icon={User} label="Members" value={String(orgMembers?.length || 0)} subValue="Across all teams" color="bg-teal-50 text-teal-600" />
        <StatCard icon={Gauge} label="RPM Limit" value={org.rpm_limit != null ? org.rpm_limit.toLocaleString() : 'Unlimited'} subValue="Requests per minute" color="bg-purple-50 text-purple-600" />
      </div>

      <div className="space-y-6">
        <Card
          title="Teams"
          action={
            <button onClick={() => setShowCreateTeam(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
              <Plus className="w-3.5 h-3.5" /> Add Team
            </button>
          }
        >
          <DataTable
            columns={teamColumns}
            data={orgTeams || []}
            loading={teamsLoading}
            emptyMessage="No teams in this organization"
            onRowClick={(r) => navigate(`/teams/${r.team_id}`)}
          />
        </Card>

        <Card title="Members">
          <DataTable
            columns={memberColumns}
            data={orgMembers || []}
            loading={membersLoading}
            emptyMessage="No members in this organization's teams"
          />
        </Card>
      </div>

      <Modal open={showEdit} onClose={() => setShowEdit(false)} title="Edit Organization">
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Organization Name</label>
            <input value={form.organization_name} onChange={(e) => setForm({ ...form, organization_name: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
            <input type="number" value={form.max_budget} onChange={(e) => setForm({ ...form, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={form.rpm_limit} onChange={(e) => setForm({ ...form, rpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={form.tpm_limit} onChange={(e) => setForm({ ...form, tpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowEdit(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleSaveOrg} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">Save Changes</button>
          </div>
        </div>
      </Modal>

      <Modal open={showCreateTeam} onClose={() => setShowCreateTeam(false)} title="Add Team to Organization">
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Team Name</label>
            <input value={teamForm.team_alias} onChange={(e) => setTeamForm({ ...teamForm, team_alias: e.target.value })} placeholder="Engineering" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Max Budget ($)</label>
              <input type="number" value={teamForm.max_budget} onChange={(e) => setTeamForm({ ...teamForm, max_budget: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Allowed Models</label>
              <input value={teamForm.models} onChange={(e) => setTeamForm({ ...teamForm, models: e.target.value })} placeholder="gpt-4o, claude-3" className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">RPM Limit</label>
              <input type="number" value={teamForm.rpm_limit} onChange={(e) => setTeamForm({ ...teamForm, rpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">TPM Limit</label>
              <input type="number" value={teamForm.tpm_limit} onChange={(e) => setTeamForm({ ...teamForm, tpm_limit: e.target.value })} className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button onClick={() => setShowCreateTeam(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">Cancel</button>
            <button onClick={handleCreateTeam} className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">Create Team</button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
