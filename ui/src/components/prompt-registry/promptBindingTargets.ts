import type { PromptBinding } from '../../lib/api';
import type { ManagedAssetAudienceOption } from '../../lib/useManagedAssetAudienceOptions';

export interface BindingTargetOptions {
  teamOptions: ManagedAssetAudienceOption[];
  organizationOptions: ManagedAssetAudienceOption[];
}

export interface BindingTarget {
  scope: string;
  name: string;
  href: string | null;
}

const SCOPE_NAMES: Record<PromptBinding['scope_type'], string> = {
  group: 'Route group',
  team: 'Team',
  org: 'Organization',
  key: 'API key',
};

export function bindingTarget(binding: PromptBinding, options: BindingTargetOptions): BindingTarget {
  const scope = SCOPE_NAMES[binding.scope_type] ?? binding.scope_type;
  const id = binding.scope_id;
  if (binding.scope_type === 'group') {
    // Route group bindings store the group key; the legacy key route resolves it.
    return { scope, name: id, href: `/route-groups/${encodeURIComponent(id)}` };
  }
  if (binding.scope_type === 'team') {
    return { scope, name: options.teamOptions.find((option) => option.id === id)?.label || id, href: `/teams/${encodeURIComponent(id)}` };
  }
  if (binding.scope_type === 'org') {
    return { scope, name: options.organizationOptions.find((option) => option.id === id)?.label || id, href: `/organizations/${encodeURIComponent(id)}` };
  }
  return { scope, name: id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id, href: null };
}

export function bindingTargetText(binding: PromptBinding, options: BindingTargetOptions): string {
  const target = bindingTarget(binding, options);
  return `${target.scope.toLowerCase()} ${target.name}`;
}
