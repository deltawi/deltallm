import { Link } from 'react-router-dom';
import type { PromptBinding, PromptLabel } from '../../lib/api';
import { bindingLabel } from '../../lib/promptVersions';
import PromptLabelTags from './PromptLabelTags';
import { bindingTarget, type BindingTargetOptions } from './promptBindingTargets';

interface PromptUsageTabProps {
  bindings: PromptBinding[];
  labels: PromptLabel[];
  targetOptions: BindingTargetOptions;
}

export default function PromptUsageTab({ bindings, labels, targetOptions }: PromptUsageTabProps) {
  if (bindings.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-gray-300 bg-white px-4 py-6 text-sm text-gray-500">
        Nothing uses this prompt yet. Bind it from a route group, team, organization, or API key to apply it to requests.
      </p>
    );
  }

  return (
    <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm" aria-label="Bindings">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-gray-900">Where this prompt applies</h2>
        <p className="text-xs text-gray-500">When you move a label, these bindings get the new version on their next request.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs text-gray-500">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">Scope</th>
              <th scope="col" className="px-4 py-2 font-medium">Name</th>
              <th scope="col" className="px-4 py-2 font-medium">Label</th>
              <th scope="col" className="px-4 py-2 font-medium">Serves</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Priority</th>
              <th scope="col" className="px-4 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {bindings.map((binding) => {
              const target = bindingTarget(binding, targetOptions);
              const label = bindingLabel(binding);
              const resolved = labels.find((item) => item.label === label);
              return (
                <tr key={binding.prompt_binding_id} className="border-t border-gray-100">
                  <td className="whitespace-nowrap px-4 py-2.5 text-gray-600">{target.scope}</td>
                  <td className="px-4 py-2.5">
                    {target.href ? (
                      <Link to={target.href} className="font-medium text-brand-primary-ink hover:underline">{target.name}</Link>
                    ) : <code className="text-xs text-gray-700" title={binding.scope_id}>{target.name}</code>}
                  </td>
                  <td className="px-4 py-2.5"><PromptLabelTags labels={[label]} /></td>
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs">
                    {resolved ? `v${resolved.version}` : <span className="font-sans font-medium text-red-700">Label missing. Requests skip this binding.</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-700">{binding.priority}</td>
                  <td className="px-4 py-2.5 text-gray-700">{binding.enabled ? 'On' : 'Off'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
