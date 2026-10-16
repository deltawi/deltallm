import { useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';
import type { RouteGroup, RouteGroupMemberDetail, RoutePolicy } from '../../lib/api';
import { routableMemberIds, routingLabel } from '../../lib/routeGroupRouting';
import { modelsSummary, workloadLabel } from '../../lib/routeGroupSummary';

export type RouteGroupTabId = 'models' | 'routing' | 'prompt' | 'settings';




function MetaLink({ children, onClick, warn = false, label }: { children: ReactNode; onClick: () => void; warn?: boolean; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className={`rounded-sm border-b border-dotted text-xs transition hover:border-solid focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary ${warn ? 'border-amber-400 text-amber-700 hover:text-amber-800' : 'border-gray-300 text-gray-600 hover:text-gray-900'}`}
    >
      {children}
    </button>
  );
}

function Separator() {
  return <span aria-hidden="true" className="h-3 w-px bg-gray-200" />;
}

export function RouteGroupModeBadge({ mode }: { mode: string }) {
  return (
    <span className="rounded-md bg-brand-primary-soft px-1.5 py-0.5 text-[11px] font-semibold text-brand-primary-ink">
      {workloadLabel(mode)}
    </span>
  );
}

interface RouteGroupMetaProps {
  group: RouteGroup;
  members: RouteGroupMemberDetail[];
  policy: RoutePolicy | null;
  promptKey: string | null;
  sharing: string | null;
  onOpenTab: (tab: RouteGroupTabId) => void;
}

// One line under the title: state, key, models, routing, prompt and sharing. Each part opens the tab that owns it.
export default function RouteGroupMeta({ group, members, policy, promptKey, sharing, onOpenTab }: RouteGroupMetaProps) {
  const [copied, setCopied] = useState(false);
  const models = modelsSummary(members);
  const routing = routingLabel(policy?.policy_json ?? null, group.routing_strategy, routableMemberIds(members).length);
  const copyKey = () => {
    void navigator.clipboard?.writeText(group.group_key).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }).catch(() => setCopied(false));
  };
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
      <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${group.enabled ? 'text-emerald-700' : 'text-gray-500'}`}>
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${group.enabled ? 'bg-emerald-500' : 'bg-gray-400'}`} />
        {group.enabled ? 'Live' : 'Off'}
      </span>
      <Separator />
      <span className="inline-flex items-center gap-1">
        <code className="font-mono text-xs text-gray-700">{group.group_key}</code>
        <button
          type="button"
          onClick={copyKey}
          aria-label="Copy group key"
          title="Copy group key"
          className="rounded p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
        >
          {copied ? <Check className="h-3 w-3 text-emerald-600" aria-hidden="true" /> : <Copy className="h-3 w-3" aria-hidden="true" />}
        </button>
        <span role="status" className="sr-only">{copied ? 'Group key copied' : ''}</span>
      </span>
      <Separator />
      <MetaLink onClick={() => onOpenTab('models')} warn={models.warn} label={`${models.text}. Open Models`}>{models.text}</MetaLink>
      <Separator />
      <MetaLink onClick={() => onOpenTab('routing')} label={`Routing: ${routing}. Open Routing`}>{routing}</MetaLink>
      {promptKey ? (
        <>
          <Separator />
          <MetaLink onClick={() => onOpenTab('prompt')} label={`Prompt: ${promptKey}. Open Prompt`}>Prompt: {promptKey}</MetaLink>
        </>
      ) : null}
      {sharing ? (
        <>
          <Separator />
          <MetaLink onClick={() => onOpenTab('settings')} label={`${sharing}. Open Settings`}>{sharing}</MetaLink>
        </>
      ) : null}
    </div>
  );
}
