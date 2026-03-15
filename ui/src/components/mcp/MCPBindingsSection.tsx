import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { Pencil, Plus, Trash2, Users, X } from 'lucide-react';
import type { MCPBinding } from '../../lib/api';

export type BindingFormState = {
  scope_type: 'organization' | 'team' | 'api_key';
  scope_id: string;
  tool_allowlist: string;
  enabled: boolean;
};

export const EMPTY_BINDING: BindingFormState = {
  scope_type: 'team',
  scope_id: '',
  tool_allowlist: '',
  enabled: true,
};

export function bindingFormFromBinding(binding: MCPBinding): BindingFormState {
  return {
    scope_type: binding.scope_type,
    scope_id: binding.scope_id,
    tool_allowlist: (binding.tool_allowlist || []).join(', '),
    enabled: binding.enabled,
  };
}

function FormField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-gray-500">{label}</label>
      {children}
    </div>
  );
}

const inputCls =
  'w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900 transition focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500/30';
const selectCls = inputCls;

type MCPBindingsSectionProps = {
  bindings: MCPBinding[];
  canManageScopeConfig: boolean;
  showForm: boolean;
  editingBindingId: string | null;
  form: BindingFormState;
  setForm: Dispatch<SetStateAction<BindingFormState>>;
  saving: boolean;
  onToggleCreate: () => void;
  onCancel: () => void;
  onSave: () => void;
  onEdit: (binding: MCPBinding) => void;
  onDelete: (binding: MCPBinding) => void;
};

export default function MCPBindingsSection({
  bindings,
  canManageScopeConfig,
  showForm,
  editingBindingId,
  form,
  setForm,
  saving,
  onToggleCreate,
  onCancel,
  onSave,
  onEdit,
  onDelete,
}: MCPBindingsSectionProps) {
  return (
    <div className="space-y-5 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">Scope Bindings</h3>
          <p className="mt-0.5 text-xs text-gray-500">Control which organizations, teams, or API keys can access this server.</p>
        </div>
        {canManageScopeConfig ? (
          <button
            type="button"
            onClick={onToggleCreate}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-blue-700"
          >
            <Plus className="h-3.5 w-3.5" />
            {showForm && editingBindingId === null ? 'Close' : 'Add binding'}
          </button>
        ) : null}
      </div>

      {!canManageScopeConfig ? (
        <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
          Read-only. Scope bindings are managed by the server owner or delegated administrators.
        </div>
      ) : null}

      {showForm && canManageScopeConfig ? (
        <div className="space-y-4 rounded-xl border border-blue-100 bg-blue-50/40 p-5">
          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-gray-800">{editingBindingId ? 'Edit Binding' : 'New Binding'}</p>
            <button type="button" onClick={onCancel} className="text-gray-400 hover:text-gray-600">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Scope Type">
              <select
                className={selectCls}
                value={form.scope_type}
                disabled={editingBindingId !== null}
                onChange={(event) => setForm((value) => ({ ...value, scope_type: event.target.value as BindingFormState['scope_type'] }))}
              >
                <option value="team">Team</option>
                <option value="organization">Organization</option>
                <option value="api_key">API Key</option>
              </select>
            </FormField>
            <FormField label="Scope ID">
              <input
                className={inputCls}
                placeholder="team-id or org-id…"
                disabled={editingBindingId !== null}
                value={form.scope_id}
                onChange={(event) => setForm((value) => ({ ...value, scope_id: event.target.value }))}
              />
            </FormField>
            <FormField label="Tool Allowlist (comma-separated, blank = all)">
              <input
                className={inputCls}
                placeholder="tool_name_1, tool_name_2…"
                value={form.tool_allowlist}
                onChange={(event) => setForm((value) => ({ ...value, tool_allowlist: event.target.value }))}
              />
            </FormField>
            <FormField label="Status">
              <select
                className={selectCls}
                value={form.enabled ? 'true' : 'false'}
                onChange={(event) => setForm((value) => ({ ...value, enabled: event.target.value === 'true' }))}
              >
                <option value="true">Enabled</option>
                <option value="false">Disabled</option>
              </select>
            </FormField>
          </div>
          {editingBindingId ? (
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600">
              Scope type and scope ID are locked while editing. Delete and recreate the binding to change its identity.
            </div>
          ) : null}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onCancel} className="rounded-lg border border-gray-200 px-3 py-2 text-xs text-gray-600 hover:bg-gray-50">
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={saving || !form.scope_id.trim()}
              className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? 'Saving…' : editingBindingId ? 'Save Changes' : 'Save Binding'}
            </button>
          </div>
        </div>
      ) : null}

      {bindings.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center text-gray-400">
          <Users className="mx-auto mb-2 h-8 w-8 text-gray-200" />
          <p className="text-sm font-medium text-gray-500">No bindings yet</p>
          <p className="mt-1 text-sm">Add a binding to grant scope access to this server.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Scope</th>
                <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Allowed Tools</th>
                <th className="w-24 px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-gray-500">Status</th>
                {canManageScopeConfig ? <th className="w-16 px-5 py-3" /> : null}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {bindings.map((binding) => (
                <tr key={binding.mcp_binding_id} className="transition-colors hover:bg-gray-50/60">
                  <td className="px-5 py-3.5 font-mono text-xs text-gray-800">{binding.scope_type}:{binding.scope_id}</td>
                  <td className="px-5 py-3.5 text-xs text-gray-600">
                    {binding.tool_allowlist?.length ? binding.tool_allowlist.join(', ') : <span className="italic text-gray-400">All tools</span>}
                  </td>
                  <td className="px-5 py-3.5">
                    <span
                      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                        binding.enabled
                          ? 'border border-blue-100 bg-blue-50 text-blue-700'
                          : 'border border-gray-200 bg-gray-100 text-gray-500'
                      }`}
                    >
                      {binding.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </td>
                  {canManageScopeConfig ? (
                    <td className="px-5 py-3.5 text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => onEdit(binding)}
                          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-blue-50 hover:text-blue-600"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => onDelete(binding)}
                          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
