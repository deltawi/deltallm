import { AlertTriangle, Pencil } from 'lucide-react';
import type { DeploymentHealth, ModelDeploymentDetail } from '../../../lib/api';
import Button from '../../Button';
import ProviderBadge from '../../ProviderBadge';
import { shapingFromModel } from '../requestShaping';
import { Chip, ChipList, DetailSection, KeyValueList, Muted, type KeyValueRow } from './DetailSection';
import { EMPTY_DEPLOYMENT_HEALTH, formatSeconds, formatUnixTimestamp, timeSince } from './modelDetailFormat';

function credentialValue(model: ModelDeploymentDetail) {
  if (model.credential_source === 'named') {
    if (model.credential_binding?.state === 'revoked') {
      return <span className="text-red-700">Revoked. An editor must choose a replacement credential.</span>;
    }
    return (
      <span className="flex flex-wrap items-center gap-2">
        <Chip>{model.named_credential_name || 'Named credential'}</Chip>
        <span className="text-xs text-gray-500">Named credential</span>
      </span>
    );
  }
  return model.inline_credentials_present ? 'Stored with this model' : <Muted>No credential stored</Muted>;
}

// Parameter overrides and drops, header rules and the outbound proxy. Defaults are shown on the Limits tab.
function shapingValue(model: ModelDeploymentDetail) {
  const shaping = shapingFromModel(model.deltallm_params || {}, {});
  const items = [
    ...shaping.params.map((rule) => <Chip key={`p-${rule.action}-${rule.key}`}>{rule.action === 'drop' ? 'Drops' : 'Sets'} <code className="ml-1 font-mono">{rule.key}</code></Chip>),
    ...shaping.headers.map((rule) => (
      <Chip key={`h-${rule.action}-${rule.name}`}>
        {rule.action === 'set' ? 'Adds' : rule.action === 'session' ? 'Session' : 'Forwards'} <code className="ml-1 font-mono">{rule.name}</code>
      </Chip>
    )),
    ...(shaping.outboundProxy ? [<Chip key="proxy">Proxy <code className="ml-1 font-mono">{shaping.outboundProxy}</code></Chip>] : []),
  ];
  return <ChipList items={items} />;
}

export default function ModelConnectionTab({ model, canEdit, onEdit }: {
  model: ModelDeploymentDetail;
  canEdit: boolean;
  onEdit: () => void;
}) {
  const lp = model.deltallm_params || {};
  const summary = model.connection_summary || {};
  const health: DeploymentHealth = model.health || EMPTY_DEPLOYMENT_HEALTH;
  const customAuth = summary.custom_auth_label || summary.auth_header_name || null;
  const timeouts = [
    formatSeconds(lp.timeout) ? `${formatSeconds(lp.timeout)} request` : null,
    formatSeconds(lp.stream_timeout) ? `${formatSeconds(lp.stream_timeout)} stream` : null,
  ].filter(Boolean).join(' · ');
  const connection: KeyValueRow[] = [
    { label: 'Provider', value: <ProviderBadge provider={model.provider} model={lp.model} /> },
    { label: 'Provider model', value: lp.model || '—', mono: true },
    { label: 'API base', value: summary.api_base || lp.api_base || '—', mono: true },
    ...(summary.api_version ? [{ label: 'API version', value: summary.api_version, mono: true }] : []),
    ...(summary.region ? [{ label: 'Region', value: summary.region, mono: true }] : []),
    ...(customAuth ? [{ label: 'Custom auth', value: customAuth, mono: true }] : []),
    { label: 'Credential', value: credentialValue(model) },
    { label: 'Timeout', value: timeouts || <Muted>Gateway default</Muted> },
    { label: 'Request shaping', value: shapingValue(model) },
  ];

  const unknown = model.healthy == null || model.health_status === 'unknown';
  const unhealthy = model.healthy === false && !unknown;
  const status = unknown
    ? <span className="rounded-md bg-gray-100 px-1.5 py-0.5 text-xs font-semibold text-gray-600">Not checked</span>
    : unhealthy
      ? <span className="rounded-md bg-red-50 px-1.5 py-0.5 text-xs font-semibold text-red-700">Unhealthy</span>
      : <span className="rounded-md bg-emerald-50 px-1.5 py-0.5 text-xs font-semibold text-emerald-700">Healthy</span>;

  return (
    <div className="max-w-4xl space-y-3">
      <DetailSection
        title="Connection"
        description={<>Where requests to <code className="font-mono">{model.api_model_id || model.model_name}</code> are sent.</>}
        action={canEdit ? <Button size="sm" variant="outline" onClick={onEdit}><Pencil className="h-3.5 w-3.5" aria-hidden="true" />Edit</Button> : undefined}
      >
        <KeyValueList rows={connection} />
      </DetailSection>
      <DetailSection
        title="Health"
        description={unhealthy ? 'Routing skips this model until a check passes.' : 'Updated by health checks and live traffic.'}
      >
        {unhealthy && health.last_error ? (
          <div role="alert" className="mx-4 mt-3 flex gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <div className="min-w-0 break-words">
              <div className="font-semibold">{health.last_error}</div>
              {health.last_error_at ? <div className="mt-0.5 text-red-700">Last failed check: {formatUnixTimestamp(health.last_error_at)}</div> : null}
            </div>
          </div>
        ) : null}
        <KeyValueList rows={[
          { label: 'Status', value: status },
          { label: 'Last success', value: timeSince(health.last_success_at) || <Muted>Never</Muted> },
          { label: 'Failures in a row', value: String(health.consecutive_failures ?? 0) },
          { label: 'Cooldown', value: health.in_cooldown ? 'Yes, skipped for now' : 'No' },
        ]} />
      </DetailSection>
    </div>
  );
}
