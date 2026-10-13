import { ArrowUpCircle, Plus } from 'lucide-react';
import type { PromptBinding, PromptLabel } from '../../lib/api';
import { fmtLocalDay } from '../../lib/format';
import { bindingsForLabel, PRODUCTION_LABEL } from '../../lib/promptVersions';
import Button from '../Button';
import { bindingTargetText, type BindingTargetOptions } from './promptBindingTargets';

interface PromptLabelCardsProps {
  labels: PromptLabel[];
  latestVersion: number | null;
  bindings: PromptBinding[];
  showUsage: boolean;
  canWrite: boolean;
  targetOptions: BindingTargetOptions;
  onView: (label: string) => void;
  onChangeVersion: (label: string) => void;
  onPromote: (label: PromptLabel) => void;
  onAdd: () => void;
}

export default function PromptLabelCards({
  labels,
  latestVersion,
  bindings,
  showUsage,
  canWrite,
  targetOptions,
  onView,
  onChangeVersion,
  onPromote,
  onAdd,
}: PromptLabelCardsProps) {
  const production = labels.find((label) => label.label === PRODUCTION_LABEL) ?? null;

  return (
    <section aria-labelledby="prompt-labels-heading" className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h2 id="prompt-labels-heading" className="text-xs font-semibold uppercase tracking-wider text-gray-500">Labels</h2>
        {canWrite ? (
          <Button variant="outline" size="sm" onClick={onAdd}>
            <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add label
          </Button>
        ) : null}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {labels.map((label) => {
          const live = label.label === PRODUCTION_LABEL;
          const users = bindingsForLabel(bindings, label.label);
          const canPromote = canWrite && !live && production?.version !== label.version;
          const setAt = label.updated_at || label.created_at;
          const setOn = setAt ? fmtLocalDay(setAt) : null;
          return (
            <article
              key={label.prompt_label_id}
              className={`flex min-w-0 flex-col gap-2 rounded-xl border bg-white p-4 shadow-sm ${live ? 'border-emerald-300' : 'border-gray-200'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <h3 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-gray-900">
                  <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${live ? 'bg-emerald-500 ring-4 ring-emerald-100' : 'bg-amber-500'}`} />
                  <span className="truncate" title={label.label}>{label.label}</span>
                </h3>
                <span className="font-mono text-sm font-medium text-gray-900">v{label.version}</span>
              </div>
              {setOn || label.version === latestVersion ? (
                <p className="text-xs text-gray-500">{[setOn ? `Set ${setOn}` : null, label.version === latestVersion ? 'latest version' : null].filter(Boolean).join(' · ')}</p>
              ) : null}
              {showUsage ? (
                <p className="text-xs text-gray-500">
                  {users.length === 0 ? 'No bindings use this label.' : `Used by ${users.map((binding) => bindingTargetText(binding, targetOptions)).join(', ')}`}
                </p>
              ) : null}
              <div className="mt-auto flex flex-wrap gap-2 pt-1">
                <Button variant="outline" size="sm" onClick={() => onView(label.label)}>View prompt</Button>
                {canWrite ? (
                  <Button variant="outline" size="sm" onClick={() => onChangeVersion(label.label)}>Change version</Button>
                ) : null}
                {canPromote ? (
                  <Button size="sm" onClick={() => onPromote(label)}>
                    <ArrowUpCircle className="h-3.5 w-3.5" aria-hidden="true" />
                    {production ? `Promote v${label.version} to production` : `Set production to v${label.version}`}
                  </Button>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
