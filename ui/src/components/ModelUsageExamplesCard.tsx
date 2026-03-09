import UsageExamplesCard from './UsageExamplesCard';

interface ModelUsageExamplesCardProps {
  modelName: string;
  mode: string;
}

export default function ModelUsageExamplesCard({ modelName, mode }: ModelUsageExamplesCardProps) {
  return (
    <UsageExamplesCard
      title="Test This Model"
      description="Use the deployed public model name immediately against the gateway."
      modelName={modelName}
      mode={mode}
    />
  );
}
