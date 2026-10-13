import { useMemo, type ReactNode } from 'react';
import { bodyCompareText, diffText, parseTemplateBody, placeholderSpans } from '../../lib/promptVersions';

export function PlaceholderText({ text }: { text: string }) {
  return (
    <>
      {placeholderSpans(text).map((span, index) => (span.variable ? (
        <span key={index} className="rounded bg-brand-primary-soft px-0.5 font-medium text-brand-primary-ink">{span.text}</span>
      ) : <span key={index}>{span.text}</span>))}
    </>
  );
}

function PlainText({ text }: { text: string }) {
  return <>{text}</>;
}

function Block({ role, children }: { role: string; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-gray-200">
      <div className="border-b border-gray-200 bg-gray-50 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-gray-500">{role}</div>
      <div className="whitespace-pre-wrap break-words px-3 py-2.5 font-mono text-xs leading-relaxed text-gray-800">{children}</div>
    </div>
  );
}

interface PromptBodyViewProps {
  body: unknown;
  /** When set, shows a word diff from this body to `body`. */
  compareBody?: unknown;
  showJson?: boolean;
  /** Set to false for rendered output, where braces are plain text. */
  highlightVariables?: boolean;
}

export default function PromptBodyView({ body, compareBody, showJson = false, highlightVariables = true }: PromptBodyViewProps) {
  const Text = highlightVariables ? PlaceholderText : PlainText;
  const diff = useMemo(
    () => (compareBody === undefined ? null : diffText(bodyCompareText(compareBody), bodyCompareText(body))),
    [body, compareBody],
  );

  if (showJson) {
    return (
      <pre className="max-h-[32rem] overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-3 font-mono text-xs leading-relaxed text-gray-800">
        {JSON.stringify(body ?? {}, null, 2)}
      </pre>
    );
  }

  if (diff) {
    const changed = diff.some((segment) => segment.kind !== 'same');
    return (
      <div className="space-y-2">
        <Block role="Changes">
          {diff.map((segment, index) => {
            if (segment.kind === 'added') return <ins key={index} className="rounded bg-emerald-100 text-emerald-800 underline decoration-emerald-400">{segment.text}</ins>;
            if (segment.kind === 'removed') return <del key={index} className="rounded bg-red-100 text-red-700">{segment.text}</del>;
            return <PlaceholderText key={index} text={segment.text} />;
          })}
        </Block>
        <p className="text-xs text-gray-500">
          {changed ? 'Added text is green and underlined. Removed text is red and struck through.' : 'The prompt text is the same in both versions.'}
        </p>
      </div>
    );
  }

  const model = parseTemplateBody(body);
  if (model.kind === 'messages') {
    return (
      <div className="space-y-2">
        {model.messages.map((message, index) => (
          <Block key={index} role={message.role}><Text text={message.content} /></Block>
        ))}
      </div>
    );
  }
  if (model.kind === 'text') return <Block role="Text"><Text text={model.text} /></Block>;
  return <Block role="JSON"><Text text={JSON.stringify(model.body, null, 2)} /></Block>;
}
