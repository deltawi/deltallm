import Card from '../Card';

interface BoundPromptSummary {
  templateKey: string;
  label?: string | null;
  requiredVariables: string[];
}

interface RouteGroupUsageCardProps {
  groupKey: string;
  liveTrafficEnabled: boolean;
  boundPrompt: BoundPromptSummary | null;
}

function buildCurl(groupKey: string, boundPrompt: BoundPromptSummary | null): string {
  const body: Record<string, unknown> = {
    model: groupKey,
    messages: [{ role: 'user', content: 'Say hello in one sentence.' }],
  };

  if (boundPrompt && boundPrompt.requiredVariables.length > 0) {
    body.metadata = {
      prompt_variables: Object.fromEntries(
        boundPrompt.requiredVariables.map((name) => [name, `<${name}>`])
      ),
    };
  }

  return [
    "curl -sS http://localhost:4000/v1/chat/completions \\",
    '  -H "Authorization: Bearer YOUR_API_KEY" \\',
    '  -H "Content-Type: application/json" \\',
    `  -d '${JSON.stringify(body, null, 2)}'`,
  ].join('\n');
}

export default function RouteGroupUsageCard({ groupKey, liveTrafficEnabled, boundPrompt }: RouteGroupUsageCardProps) {
  const curlExample = buildCurl(groupKey, boundPrompt);

  return (
    <Card title="3. How To Call This Group">
      <div className="space-y-4">
        <div>
          <h4 className="text-sm font-semibold text-slate-900">Use the group key as the model</h4>
          <p className="mt-1 text-xs text-slate-500">
            The request example below stays aligned with this group. If a prompt is bound and requires variables, they are included automatically.
          </p>
        </div>

        {!liveTrafficEnabled && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-900">
            Live traffic is currently off. Enable live traffic in Basics before using this example in production.
          </div>
        )}

        <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700">
          {boundPrompt ? (
            <div className="space-y-1">
              <div>
                Bound prompt: <code className="rounded bg-white px-1.5 py-0.5 text-xs">{boundPrompt.templateKey}</code>
                {boundPrompt.label ? (
                  <>
                    {' '}
                    · label <code className="rounded bg-white px-1.5 py-0.5 text-xs">{boundPrompt.label}</code>
                  </>
                ) : null}
              </div>
              <div className="text-xs text-slate-500">
                {boundPrompt.requiredVariables.length > 0
                  ? `Required variables: ${boundPrompt.requiredVariables.join(', ')}`
                  : 'No required prompt variables.'}
              </div>
            </div>
          ) : (
            <div>
              <div>No prompt is bound to this group.</div>
              <div className="mt-1 text-xs text-slate-500">The example stays minimal until you bind a prompt in Advanced.</div>
            </div>
          )}
        </div>

        <div>
          <p className="mb-1 text-xs font-medium text-slate-500">Ready-to-use curl example</p>
          <pre className="overflow-auto rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 text-xs font-mono">{curlExample}</pre>
        </div>
      </div>
    </Card>
  );
}
