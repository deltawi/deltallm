import { useState } from 'react';
import { Filter, Download } from 'lucide-react';
import { useOrganizations } from '@/hooks/useOrganizations';
import { api } from '@/services/api';
import { useQuery } from '@tanstack/react-query';
import { DataTable } from '@/components/DataTable';
import type { AuditLog } from '@/types';

export function AuditLogs() {
  const [selectedOrg, setSelectedOrg] = useState<string>('');
  const [selectedAction, setSelectedAction] = useState<string>('');

  const { data: organizations } = useOrganizations();
  const { data: logsData } = useQuery({
    queryKey: ['audit-logs', selectedOrg, selectedAction],
    queryFn: () =>
      api.getAuditLogs({
        org_id: selectedOrg || undefined,
        action: selectedAction || undefined,
        limit: 100,
      }),
  });

  const actionTypes = [
    'org:create',
    'org:update',
    'org:delete',
    'org:member_add',
    'org:member_remove',
    'org:member_role_update',
    'team:create',
    'team:update',
    'team:delete',
    'team:member_add',
    'team:member_remove',
    'budget:set_org_limit',
    'budget:set_team_limit',
  ];

  const columns = [
    {
      key: 'timestamp',
      header: 'Timestamp',
      render: (log: AuditLog) => (
        <span className="text-sm text-gray-600">
          {new Date(log.created_at).toLocaleString()}
        </span>
      ),
    },
    {
      key: 'action',
      header: 'Action',
      render: (log: AuditLog) => (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
          {log.action}
        </span>
      ),
    },
    {
      key: 'resource',
      header: 'Resource',
      render: (log: AuditLog) => (
        <div>
          <p className="text-sm font-medium text-gray-900">
            {log.resource_type || 'N/A'}
          </p>
          <p className="text-xs text-gray-500">{log.resource_id}</p>
        </div>
      ),
    },
    {
      key: 'user',
      header: 'User',
      render: (log: AuditLog) => (
        <span className="text-sm text-gray-600">
          {log.user?.email || log.user_id || 'System'}
        </span>
      ),
    },
    {
      key: 'changes',
      header: 'Changes',
      render: (log: AuditLog) => {
        if (!log.new_values) return <span className="text-gray-400">-</span>;
        return (
          <div className="max-w-xs">
            {Object.entries(log.new_values).slice(0, 3).map(([key, value]) => (
              <p key={key} className="text-xs text-gray-600 truncate">
                <span className="font-medium">{key}:</span>{' '}
                {JSON.stringify(value)}
              </p>
            ))}
            {Object.keys(log.new_values).length > 3 && (
              <p className="text-xs text-gray-400">
                +{Object.keys(log.new_values).length - 3} more
              </p>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Audit Logs</h1>
          <p className="text-gray-600 mt-1">
            Track all changes across your organizations
          </p>
        </div>
        <button className="flex items-center px-4 py-2 text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50">
          <Download className="w-4 h-4 mr-2" />
          Export
        </button>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-xl border border-gray-200 p-4 mb-6">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center">
            <Filter className="w-4 h-4 text-gray-400 mr-2" />
            <span className="text-sm font-medium text-gray-700">Filters:</span>
          </div>
          
          <select
            value={selectedOrg}
            onChange={(e) => setSelectedOrg(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          >
            <option value="">All Organizations</option>
            {organizations?.items.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>

          <select
            value={selectedAction}
            onChange={(e) => setSelectedAction(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          >
            <option value="">All Actions</option>
            {actionTypes.map((action) => (
              <option key={action} value={action}>
                {action}
              </option>
            ))}
          </select>

          {(selectedOrg || selectedAction) && (
            <button
              onClick={() => {
                setSelectedOrg('');
                setSelectedAction('');
              }}
              className="text-sm text-primary-600 hover:text-primary-700"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Logs Table */}
      <DataTable
        columns={columns}
        data={logsData?.items || []}
        keyExtractor={(log) => log.id}
        pagination={
          logsData
            ? {
                page: logsData.page,
                pageSize: logsData.page_size,
                total: logsData.total,
                onPageChange: () => {},
              }
            : undefined
        }
      />
    </div>
  );
}
