import { useMemo, useState } from 'react';
import { Info } from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../Button';
import SideDrawer from '../SideDrawer';
import { IconTabs } from '../admin/shells';
import { promptRegistry, type RouteGroup } from '../../lib/api';
import { useBranding } from '../../lib/brandingContext';
import { useApi } from '../../lib/hooks';
import {
  playgroundPathFor,
  requiredPromptVariables,
  routeGroupSnippets,
  SNIPPET_LANGUAGES,
  type SnippetLanguage,
} from '../../lib/routeGroupSnippets';

export interface BoundPrompt {
  templateKey: string;
  label: string | null;
}

function usePromptVariables(prompt: BoundPrompt | null): string[] {
  const detail = useApi(
    (signal) => (prompt ? promptRegistry.getTemplate(prompt.templateKey, signal) : Promise.resolve(null)),
    [prompt?.templateKey],
  );
  return useMemo(() => {
    const data = detail.data;
    if (!prompt || !data) return [];
    const labelled = prompt.label ? data.labels.find((item) => item.label === prompt.label)?.version : null;
    const version = labelled
      ?? data.versions.find((item) => item.status === 'published')?.version
      ?? data.versions[0]?.version;
    return requiredPromptVariables(data.versions.find((item) => item.version === version)?.variables_schema);
  }, [detail.data, prompt]);
}

// "Use this group": example requests, copy, and shortcuts to try the group.
export default function UseGroupDrawer({ group, prompt, onClose }: {
  group: RouteGroup;
  prompt: BoundPrompt | null;
  onClose: () => void;
}) {
  const { branding } = useBranding();
  const [language, setLanguage] = useState<SnippetLanguage>('curl');
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const variables = usePromptVariables(prompt);
  const snippets = routeGroupSnippets(group.group_key, group.mode, branding.instance_name, variables);
  const playground = playgroundPathFor(group.group_key, group.mode);
  const copy = () => {
    const write = navigator.clipboard?.writeText(snippets[language]);
    if (!write) { setCopyState('failed'); return; }
    write.then(() => setCopyState('copied'), () => setCopyState('failed'));
  };
  return (
    <SideDrawer
      title="Use this group"
      description={<>Call it like any model: pass the group key as <code className="font-mono">model</code>.</>}
      saving={false}
      onClose={onClose}
      size="md"
      overlayLabel="Close use this group"
    >
      <div className="space-y-4">
        {!group.enabled ? (
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            This group does not accept requests now. Turn on <strong>Accept requests</strong> in Settings first.
          </p>
        ) : null}
        <IconTabs id="use-group-code" label="Code language" variant="segment" items={SNIPPET_LANGUAGES} active={language}
          onChange={(next) => { setLanguage(next); setCopyState('idle'); }} />
        <pre role="tabpanel" id={`use-group-code-panel-${language}`} aria-labelledby={`use-group-code-tab-${language}`} tabIndex={0}
          className="max-h-80 overflow-auto rounded-lg bg-gray-950 p-4 font-mono text-xs leading-relaxed text-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary">{snippets[language]}</pre>
        <p className="flex gap-2 rounded-lg border border-brand-primary/20 bg-brand-primary-soft px-3 py-2 text-xs leading-5 text-brand-primary-ink">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            Use an API key that can access this group.
            {prompt ? (
              <> The <strong>{prompt.templateKey}</strong> prompt is applied automatically{variables.length > 0
                ? <>; send its variables in <code className="font-mono">metadata.prompt_variables</code>.</>
                : '.'}</>
            ) : null}
          </span>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={copy}>{copyState === 'copied' ? 'Copied' : 'Copy'}</Button>
          {playground ? <Link to={playground} className="inline-flex h-8 items-center rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium text-gray-700 hover:bg-gray-50">Open in Playground</Link> : null}
          <Link to="/keys" className="inline-flex h-8 items-center px-2 text-xs font-medium text-gray-600 hover:text-gray-900">Create API key</Link>
          <span role="status" className="text-xs text-red-700">{copyState === 'failed' ? 'Could not copy. Select the code and copy it.' : ''}</span>
        </div>
      </div>
    </SideDrawer>
  );
}
