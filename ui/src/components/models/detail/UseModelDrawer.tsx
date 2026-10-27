import { useState } from 'react';
import { Info } from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../../Button';
import SideDrawer from '../../SideDrawer';
import { IconTabs } from '../../admin/shells';
import { useBranding } from '../../../lib/brandingContext';
import { modelSnippets, playgroundPathFor, SNIPPET_LANGUAGES, type SnippetLanguage } from '../../../lib/routeGroupSnippets';
import { formatParamValue } from './modelDetailFormat';

// "Use this model": example requests, copy, and shortcuts to try the model.
export default function UseModelDrawer({ modelName, mode, defaults, healthy, onClose }: {
  modelName: string;
  mode: string;
  defaults: Record<string, unknown>;
  healthy: boolean | null | undefined;
  onClose: () => void;
}) {
  const { branding } = useBranding();
  const [language, setLanguage] = useState<SnippetLanguage>('curl');
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const snippets = modelSnippets(modelName, mode, branding.instance_name);
  const playground = playgroundPathFor(modelName, mode);
  const defaultList = Object.entries(defaults).map(([key, value]) => `${key} ${formatParamValue(value)}`).join(', ');
  const copy = () => {
    const write = navigator.clipboard?.writeText(snippets[language]);
    if (!write) { setCopyState('failed'); return; }
    write.then(() => setCopyState('copied'), () => setCopyState('failed'));
  };
  return (
    <SideDrawer
      title="Use this model"
      description={<>Call it from any OpenAI-compatible SDK: pass <code className="font-mono">{modelName}</code> as <code className="font-mono">model</code>.</>}
      saving={false}
      onClose={onClose}
      size="md"
      overlayLabel="Close use this model"
    >
      <div className="space-y-4">
        {healthy === false ? (
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            This model is unhealthy right now, so requests may fail until a health check passes.
          </p>
        ) : null}
        <IconTabs id="use-model-code" label="Code language" variant="segment" items={SNIPPET_LANGUAGES} active={language}
          onChange={(next) => { setLanguage(next); setCopyState('idle'); }} />
        <pre role="tabpanel" id={`use-model-code-panel-${language}`} aria-labelledby={`use-model-code-tab-${language}`} tabIndex={0}
          className="max-h-80 overflow-auto rounded-lg bg-gray-950 p-4 font-mono text-xs leading-relaxed text-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary">{snippets[language]}</pre>
        <p className="flex gap-2 rounded-lg border border-brand-primary/20 bg-brand-primary-soft px-3 py-2 text-xs leading-5 text-brand-primary-ink">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            Replace <code className="font-mono">YOUR_API_KEY</code> with a key that can access this model.
            {defaultList ? <> When a request leaves them out, the gateway adds {defaultList}.</> : null}
          </span>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={copy}>{copyState === 'copied' ? 'Copied' : 'Copy'}</Button>
          {playground ? <Link to={playground} className="control-sm inline-flex items-center rounded-md border border-gray-200 bg-white font-medium text-gray-700 hover:bg-gray-50">Open in Playground</Link> : null}
          <Link to="/keys" className="control-sm inline-flex items-center font-medium text-gray-600 hover:text-gray-900">Create API key</Link>
          <span role="status" className="text-xs text-red-700">{copyState === 'failed' ? 'Could not copy. Select the code and copy it.' : ''}</span>
        </div>
      </div>
    </SideDrawer>
  );
}
