import { AlertTriangle, Building2, Trash2, Users } from 'lucide-react';
import Button from '../../Button';
import type { SsoGroupMapping, SsoGroupTarget, SsoOrgRole, SsoTeamRole } from '../../../lib/api/ssoGroupSync';
import { mappingKey, ORG_ROLE_OPTIONS, TARGET_STATUS_LABELS, TEAM_ROLE_OPTIONS, targetFor } from '../../../lib/ssoGroupSync';

const SELECT_CLASS = 'control-field-md w-full min-w-[7.5rem] rounded-lg border border-gray-200 bg-white px-2.5 focus:outline-none focus:ring-2 focus:ring-brand-primary';

function TargetCell({ mapping, target }: { mapping: SsoGroupMapping; target: SsoGroupTarget | undefined }) {
  const isTeam = Boolean(mapping.team_id);
  const Icon = isTeam ? Users : Building2;
  const id = mapping.team_id || mapping.organization_id || '';
  const problem = target && target.status !== 'ok' ? TARGET_STATUS_LABELS[target.status] : null;
  const subtitle = isTeam
    ? `Team${target?.organization_name ? ` · ${target.organization_name}` : ''}`
    : 'Organization';
  return (
    <div className="flex min-w-0 items-start gap-2.5">
      <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-500">
        <Icon className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0">
        <span className="block truncate font-medium text-gray-900">{target?.name || id}</span>
        <span className="block truncate text-xs text-gray-500">{subtitle}</span>
        {problem && (
          <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
            <AlertTriangle className="h-3 w-3" />
            {problem}. Sync skips this row.
          </span>
        )}
      </span>
    </div>
  );
}

export default function SsoMappingTable({
  mappings,
  targets,
  onChange,
  onRemove,
}: {
  mappings: SsoGroupMapping[];
  targets: Map<string, SsoGroupTarget>;
  onChange: (index: number, mapping: SsoGroupMapping) => void;
  onRemove: (index: number) => void;
}) {
  if (mappings.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-200 px-4 py-8 text-center">
        <p className="text-sm font-medium text-gray-700">No mappings yet</p>
        <p className="mt-1 text-xs text-gray-500">Add a mapping to give a team or organization role from a claim value.</p>
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-100">
      <table className="w-full min-w-[560px] text-sm">
        <thead className="bg-gray-50 text-left text-xs font-medium text-gray-500">
          <tr>
            <th scope="col" className="px-3 py-2">Claim value</th>
            <th scope="col" className="px-3 py-2">Gives access to</th>
            <th scope="col" className="w-40 px-3 py-2">Role</th>
            <th scope="col" className="w-12 px-3 py-2"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {mappings.map((mapping, index) => {
            const target = targetFor(mapping, targets);
            const roleLabel = `Role for ${mapping.value}`;
            return (
              <tr key={mappingKey(mapping)} className="align-top">
                <td className="px-3 py-3">
                  <code className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-800">{mapping.value}</code>
                </td>
                <td className="px-3 py-3"><TargetCell mapping={mapping} target={target} /></td>
                <td className="px-3 py-3">
                  {mapping.team_id ? (
                    <select
                      aria-label={roleLabel}
                      value={mapping.team_role ?? 'team_viewer'}
                      onChange={(event) => onChange(index, { ...mapping, team_role: event.target.value as SsoTeamRole })}
                      className={SELECT_CLASS}
                    >
                      {TEAM_ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  ) : (
                    <select
                      aria-label={roleLabel}
                      value={mapping.org_role ?? 'org_member'}
                      onChange={(event) => onChange(index, { ...mapping, org_role: event.target.value as SsoOrgRole })}
                      className={SELECT_CLASS}
                    >
                      {ORG_ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  )}
                </td>
                <td className="px-3 py-3 text-right">
                  <Button variant="ghost" size="sm" onClick={() => onRemove(index)} aria-label={`Remove mapping ${mapping.value}`}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
