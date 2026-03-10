import UsageExamplesCard from '../UsageExamplesCard';

interface BoundPromptSummary {
  templateKey: string;
  label?: string | null;
  requiredVariables: string[];
}

interface RouteGroupUsageCardProps {
  groupKey: string;
  mode: string;
  liveTrafficEnabled: boolean;
  boundPrompt: BoundPromptSummary | null;
}

export default function RouteGroupUsageCard({ groupKey, mode, liveTrafficEnabled, boundPrompt }: RouteGroupUsageCardProps) {
  const requestMetadata = boundPrompt && boundPrompt.requiredVariables.length > 0
    ? {
        prompt_variables: Object.fromEntries(
          boundPrompt.requiredVariables.map((name) => [name, `<${name}>`]),
        ),
      }
    : undefined;

  return (
    <UsageExamplesCard
      title="Test This Group"
      description="Use the group key as the model. The example stays aligned with this group and includes required prompt variables when applicable."
      modelName={groupKey}
      mode={mode}
      warning={!liveTrafficEnabled ? 'Live traffic is currently off. Enable live traffic in Basics before using this example in production.' : undefined}
      context={
        boundPrompt ? (
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
        )
      }
      requestMetadata={requestMetadata}
    />
  );
}
