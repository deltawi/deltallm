import { useState } from 'react';
import { Check, CircleCheck, Copy, Info } from 'lucide-react';
import Button from '../Button';
import {
  CODING_AGENTS,
  KEY_PLACEHOLDER,
  agentSetup,
  connectionState,
  gatewayOrigin,
  type CodingAgentId,
  type SetupBlock,
} from '../../lib/codingAgentSetup';

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

function useCopied() {
  const [copied, setCopied] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const copy = async (id: string, text: string) => {
    const ok = await copyText(text);
    setFailed(!ok);
    setCopied(ok ? id : null);
    if (ok) window.setTimeout(() => setCopied((current) => (current === id ? null : current)), 2000);
  };
  return { copied, failed, copy };
}

function CodeBlock({ block, copied, onCopy }: { block: SetupBlock; copied: boolean; onCopy: () => void }) {
  return (
    <div className="overflow-hidden rounded-lg border border-gray-800 bg-gray-900">
      <div className="flex items-center justify-between gap-2 border-b border-gray-800 px-3 py-1">
        <span className="truncate font-mono text-[11.5px] text-gray-400">{block.title}</span>
        <button
          type="button"
          onClick={onCopy}
          className="control-sm shrink-0 gap-1 rounded-md font-medium text-gray-300 hover:bg-gray-800 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary"
        >
          {copied ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 font-mono text-[12.5px] leading-6 text-gray-100"><code>{block.code}</code></pre>
    </div>
  );
}

export type AgentSetupPanelProps = {
  agent: CodingAgentId;
  onAgentChange: (agent: CodingAgentId) => void;
  /** The raw key, only right after creation. Without it the setup shows a placeholder. */
  rawKey: string | null;
  mainModel: string;
  smallModel: string;
  lastUsedAt?: string | null;
};

/** Agent tabs with copy-paste setup text, and the key while it can still be shown. */
export default function AgentSetupPanel({ agent, onAgentChange, rawKey, mainModel, smallModel, lastUsedAt }: AgentSetupPanelProps) {
  const { copied, failed, copy } = useCopied();
  const setup = agentSetup({ agent, origin: gatewayOrigin(), apiKey: rawKey || KEY_PLACEHOLDER, mainModel, smallModel });
  const connection = connectionState(lastUsedAt);

  return (
    <div className="grid gap-4">
      {rawKey ? (
        <div className="grid gap-1.5">
          <label htmlFor="coding-agent-key" className="text-xs font-medium text-gray-700">Key</label>
          <div className="flex gap-2">
            <input
              id="coding-agent-key"
              readOnly
              value={rawKey}
              onFocus={(event) => event.currentTarget.select()}
              className="control-field-md min-w-0 flex-1 rounded-lg border border-gray-300 bg-gray-50 px-3 font-mono text-xs text-gray-900"
            />
            <Button variant="outline" onClick={() => void copy('key', rawKey)}>
              {copied === 'key' ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
              {copied === 'key' ? 'Copied' : 'Copy key'}
            </Button>
          </div>
        </div>
      ) : (
        <p className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs leading-5 text-gray-600">
          DeltaLLM shows a key only once. Replace <code className="rounded bg-gray-100 px-1 font-mono text-[11px]">{KEY_PLACEHOLDER}</code> with the key you saved, or regenerate the key on the API keys page.
        </p>
      )}

      <div className="grid gap-3">
        <div role="tablist" aria-label="Agent" className="flex gap-1 overflow-x-auto border-b border-gray-200">
          {CODING_AGENTS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === agent}
              onClick={() => onAgentChange(item.id)}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-[13px] font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary ${
                item.id === agent ? 'border-brand-primary text-brand-primary-ink' : 'border-transparent text-gray-500 hover:text-gray-800'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <p className="text-[13px] leading-5 text-gray-600">{setup.intro}</p>
        {setup.blocks.map((block) => (
          <CodeBlock key={`${agent}-${block.id}`} block={block} copied={copied === block.id} onCopy={() => void copy(block.id, block.code)} />
        ))}
        {setup.alternate ? (
          <div>
            <Button variant="outline" size="sm" onClick={() => void copy('alternate', setup.alternate!.code)}>
              {copied === 'alternate' ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
              {copied === 'alternate' ? 'Copied' : `Copy as ${setup.alternate.title.split('/').pop()}`}
            </Button>
          </div>
        ) : null}
        {failed ? <p role="alert" className="text-xs text-red-700">Could not copy. Select the text and copy it.</p> : null}
      </div>

      {lastUsedAt !== undefined ? (
        <div className="flex items-start gap-2.5 rounded-lg border border-gray-200 bg-white px-3 py-2.5" role="status">
          {connection.connected
            ? <CircleCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
            : <Info className="mt-0.5 h-4 w-4 shrink-0 text-brand-primary-ink" aria-hidden="true" />}
          <div className="text-[13px] leading-5 text-gray-600">
            <div className="font-medium text-gray-900">Check the connection</div>
            {connection.connected ? 'The agent reached DeltaLLM with this key.' : 'Start the agent and send one prompt. The first request shows here.'}
            <div className="mt-1 flex items-center gap-1.5 text-xs text-gray-500">
              <span className={`h-2 w-2 rounded-full ${connection.connected ? 'bg-emerald-500' : 'animate-pulse bg-gray-300'}`} aria-hidden="true" />
              {connection.label}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
