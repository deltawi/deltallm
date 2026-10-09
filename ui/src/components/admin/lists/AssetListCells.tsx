import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, Copy } from 'lucide-react';

export function CopyIdentifier({ value, label = 'User ID' }: { value?: string | null; label?: string }) {
  const [copied, setCopied] = useState(false);
  const [manual, setManual] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; if (timer.current) clearTimeout(timer.current); }; }, []);
  useEffect(() => { if (manual) { input.current?.focus(); input.current?.select(); } }, [manual]);
  if (!value) return <span className="text-xs text-gray-400">Not recorded</span>;
  const short = value.length > 20 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
  const copy = async () => {
    if (timer.current) clearTimeout(timer.current);
    setCopied(false);
    try {
      await navigator.clipboard.writeText(value);
      if (!alive.current) return;
      setManual(false); setCopied(true);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch { if (alive.current) setManual(true); }
  };
  return <div className="relative min-w-0" onClick={(event) => event.stopPropagation()}>
    <button ref={button} type="button" aria-label={`Copy ${label.toLowerCase()} ${value}`} title={value} onClick={() => void copy()} className="inline-flex max-w-full items-center gap-1.5 rounded py-1 text-left text-xs text-gray-700 hover:text-brand-primary-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary">
      <code className="min-w-0 break-all"><span className="hidden md:inline">{short}</span><span className="md:hidden">{value}</span></code>{copied ? <Check className="h-3.5 w-3.5 shrink-0 text-emerald-600" /> : <Copy className="h-3.5 w-3.5 shrink-0 text-gray-400" />}
    </button>
    <span className="sr-only" role="status">{copied ? `${label} copied` : ''}</span>
    {manual ? <label className="mt-1 block text-xs text-gray-500">Select the ID and copy it.<input ref={input} value={value} readOnly aria-label={`${label} to copy`} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); setManual(false); button.current?.focus(); } }} className="mt-1 w-full min-w-0 rounded border border-gray-300 p-1.5 font-mono text-base md:text-xs" /></label> : null}
  </div>;
}

export function UpdatedAt({ value }: { value?: string | null }) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return <span className="text-xs text-gray-400">Not recorded</span>;
  return <time dateTime={value!} title={date.toLocaleString()} className="whitespace-nowrap text-xs tabular-nums text-gray-700">{date.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })}<span className="mt-0.5 block text-gray-400">{date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })}</span></time>;
}

export function Visibility({ value }: { value?: string | null }) {
  const visibility = value || 'platform';
  return <span className="text-xs capitalize text-gray-500">{visibility}</span>;
}

export function ListIdentity({ name, identifier, onOpen, secondary, actions }: { name: string; identifier: string; onOpen: () => void; secondary?: ReactNode; actions?: ReactNode }) {
  return <div className="min-w-40 max-w-72">
    <div className="flex items-center gap-2"><button type="button" onClick={(event) => { event.stopPropagation(); onOpen(); }} title={name} className="min-w-0 flex-1 break-words rounded text-left text-sm font-medium text-gray-900 hover:text-brand-primary-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-primary md:truncate">{name}</button>{actions ? <div className="flex shrink-0 items-center gap-0.5" onClick={(event) => event.stopPropagation()}>{actions}</div> : null}</div>
    <code title={identifier} className="mt-0.5 block truncate text-xs text-gray-400">{identifier}</code>
    {secondary ? <div className="mt-1 text-xs text-gray-500">{secondary}</div> : null}
  </div>;
}
