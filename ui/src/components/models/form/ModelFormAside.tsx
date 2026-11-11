import { Box, Check, Circle } from 'lucide-react';
import { PanelCard } from '../../admin/shells';
import { CopyButton } from '../../batch-jobs/BatchBits';
import { providerDisplayName } from '../../../lib/providers';
import type { ModelMode } from '../../modelFormShared';
import { ProviderLogoMark } from '../ProviderPicker';
import { modelCallSnippet, type ChecklistItem } from './modelFormSummary';

interface ModelFormAsideProps {
  name: string;
  provider: string;
  model: string;
  mode: ModelMode;
  apiModelId: string;
  checklist: ChecklistItem[];
}

export default function ModelFormAside({ name, provider, model, mode, apiModelId, checklist }: ModelFormAsideProps) {
  const snippet = modelCallSnippet(mode, apiModelId);
  return (
    <div className="space-y-6 lg:sticky lg:top-6 lg:self-start">
      <PanelCard>
        <h2 className="text-sm font-semibold text-gray-900">Summary</h2>
        <div className="mt-3 flex items-center gap-3">
          {provider
            ? <ProviderLogoMark provider={provider} size="lg" />
            : (
              <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-dashed border-gray-300 text-gray-300" aria-hidden="true">
                <Box className="h-4 w-4" />
              </span>
            )}
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-gray-900">{name.trim() || 'New model'}</div>
            <div className="truncate text-xs text-gray-500">
              {provider ? `${providerDisplayName(provider)}${model.trim() ? ` · ${model.trim()}` : ''}` : 'No provider yet'}
            </div>
          </div>
        </div>
        <ul className="mt-4 space-y-2 border-t border-gray-100 pt-4" aria-label="Required fields">
          {checklist.map((item) => (
            <li key={item.label} className="flex items-center gap-2 text-sm">
              {item.done
                ? <Check className="h-4 w-4 text-emerald-600" aria-hidden="true" />
                : <Circle className="h-4 w-4 text-gray-300" aria-hidden="true" />}
              <span className={item.done ? 'text-gray-700' : 'text-gray-500'}>
                {item.label}
                {item.optional ? <span className="text-gray-400"> (optional)</span> : null}
                <span className="sr-only">{item.done ? ': done' : ': not done'}</span>
              </span>
            </li>
          ))}
        </ul>
      </PanelCard>
      <PanelCard>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">Call it</h2>
          <CopyButton value={snippet} label="Copy example request" />
        </div>
        <p className="mt-0.5 text-xs text-gray-500">Any OpenAI SDK works when a key can use this model.</p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-gray-900 p-3 font-mono text-[11px] leading-relaxed text-gray-100">{snippet}</pre>
      </PanelCard>
    </div>
  );
}
