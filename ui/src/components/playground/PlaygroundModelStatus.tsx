import StatusBadge from '../StatusBadge';
import type { ModelOption } from './types';

export default function PlaygroundModelStatus({ status, className }: { status: ModelOption['status']; className?: string }) {
  const state = status === 'online' ? 'active' : status === 'offline' ? 'warning' : 'unknown';
  const label = status === 'online' ? 'Online' : status === 'offline' ? 'Offline' : 'Unknown';
  return <span className={className} title={`Status: ${label.toLowerCase()}`}><StatusBadge status={state} label={label} /></span>;
}
