import { readGuidedSelector } from '../../lib/routeGroupSelector';

export default function PolicySelectorSummary({ policy, label = 'Selector' }: {
  policy: Record<string, unknown>;
  label?: string;
}) {
  const selector = readGuidedSelector(policy);
  if (selector.state === 'disabled' || selector.state === 'removed') return <p className="text-xs text-slate-500">{label}: none — routing strategy only</p>;
  if (selector.state === 'unsupported') return <p className="text-xs text-amber-700">{label}: configuration available in policy JSON</p>;
  return <div className="space-y-1 text-xs text-slate-600">
    <p className="break-all"><span className="font-medium">{label}:</span> {selector.classifier} · safe default: {selector.defaultLane}</p>
    <p>{selector.timeoutMs} ms timeout · {selector.maxInputChars} input characters · one paid call per uncached request</p>
    <ul className="list-inside list-disc">
      {[...selector.lanes].sort((a, b) => Number(a.rank) - Number(b.rank)).map((lane) => <li key={lane.id} className="break-all">
        {lane.id} (rank {lane.rank}): {Object.entries(selector.assignments).filter(([, assigned]) => assigned === lane.id).map(([id]) => id).join(', ') || 'No assignments'}
      </li>)}
    </ul>
  </div>;
}
