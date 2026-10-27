import type { ModelDeploymentDetail } from '../../../lib/api';
import { Chip, ChipList, DetailSection, KeyValueList, Muted, type KeyValueRow } from './DetailSection';
import { formatFraction, formatInteger, formatParamValue } from './modelDetailFormat';

const STAT_COLUMNS: Record<number, string> = { 1: 'grid-cols-1', 2: 'grid-cols-2', 3: 'grid-cols-3', 4: 'grid-cols-2 sm:grid-cols-4' };

// Figures side by side; the 1px gap shows the card's divider color between cells.
function TokenStats({ items }: { items: Array<{ label: string; value: string }> }) {
  return (
    <dl className={`grid gap-px overflow-hidden rounded-b-xl bg-gray-100 ${STAT_COLUMNS[items.length] ?? STAT_COLUMNS[4]}`}>
      {items.map((item) => (
        <div key={item.label} className="flex min-w-0 flex-col-reverse bg-white px-4 py-2.5">
          <dt className="text-xs text-gray-500">{item.label}</dt>
          <dd className="text-[15px] font-semibold tabular-nums text-gray-900">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

const present = (rows: Array<{ label: string; value: string | null }>) =>
  rows.filter((row): row is { label: string; value: string } => Boolean(row.value));

export default function ModelLimitsTab({ model, mode }: { model: ModelDeploymentDetail; mode: string }) {
  const lp = model.deltallm_params || {};
  const mi = model.model_info || {};
  const name = model.api_model_id || model.model_name;
  const batch = mi.batch_capacity && typeof mi.batch_capacity === 'object' ? mi.batch_capacity : {};

  const tokens = present(mode === 'embedding'
    ? [
      { label: 'Context window', value: formatInteger(mi.max_tokens) },
      { label: 'Vector size', value: formatInteger(mi.output_vector_size) },
    ]
    : mode === 'chat'
      ? [
        { label: 'Context window', value: formatInteger(mi.max_tokens) },
        { label: 'Max input', value: formatInteger(mi.max_input_tokens) },
        { label: 'Max output', value: formatInteger(mi.max_output_tokens) },
        { label: 'Per-request cap', value: formatInteger(lp.max_tokens) },
      ]
      : []);

  const weight = mi.weight ?? lp.weight;
  const tags = Array.isArray(mi.tags) ? mi.tags : [];
  const accessGroups = Array.isArray(mi.access_groups) ? mi.access_groups : [];
  const traffic: KeyValueRow[] = [
    { label: 'Requests per minute', value: formatInteger(lp.rpm ?? mi.rpm_limit) || <Muted>No limit</Muted> },
    { label: 'Tokens per minute', value: formatInteger(lp.tpm ?? mi.tpm_limit) || <Muted>No limit</Muted> },
    {
      label: 'Weight',
      value: weight != null
        ? <>{String(weight)} <span className="text-xs text-gray-500">· share of traffic next to other deployments of <code className="font-mono">{name}</code></span></>
        : <Muted>Not set</Muted>,
    },
    { label: 'Priority', value: mi.priority != null ? String(mi.priority) : <Muted>Not set</Muted> },
    { label: 'Tags', value: <ChipList items={tags.map((tag) => <Chip key={tag}>{tag}</Chip>)} /> },
    { label: 'Access groups', value: <ChipList items={accessGroups.map((group) => <Chip key={group}>{group}</Chip>)} /> },
  ];

  const defaults = mi.default_params && typeof mi.default_params === 'object' ? Object.entries(mi.default_params) : [];
  const batchRows = present(mode === 'chat' || mode === 'embedding'
    ? [
      { label: 'Max in flight', value: formatInteger(batch.max_in_flight) },
      { label: 'Max claim work units', value: formatInteger(batch.max_claim_work_units) },
      { label: 'Capacity fraction', value: formatFraction(batch.capacity_fraction) },
      ...(mode === 'embedding' ? [{ label: 'Upstream max inputs', value: formatInteger(mi.upstream_max_batch_inputs) }] : []),
    ]
    : []);

  return (
    <div className="max-w-4xl space-y-3">
      {tokens.length > 0 ? (
        <DetailSection title="Tokens" description={mode === 'embedding' ? 'Input size and output vector.' : 'Context and output caps for each request.'}>
          <TokenStats items={tokens} />
        </DetailSection>
      ) : null}
      {mode === 'realtime' ? (
        <DetailSection title="Session" description="How realtime sessions run and are measured.">
          <KeyValueList rows={[
            { label: 'Session', value: mi.realtime_profile === 'transcription' ? 'Transcription' : 'Conversation' },
            { label: 'Usage measured by', value: mi.realtime_usage_type === 'duration' ? 'Audio duration' : 'Tokens' },
          ]} />
        </DetailSection>
      ) : null}
      <DetailSection title="Traffic" description={<>Applied by the router when it sends requests for <code className="font-mono">{name}</code>.</>}>
        <KeyValueList rows={traffic} />
      </DetailSection>
      {defaults.length > 0 || batchRows.length > 0 ? (
        <div className="grid items-start gap-3 lg:grid-cols-2">
          {defaults.length > 0 ? (
            <DetailSection title="Default parameters" description="Sent when the request leaves them out.">
              <KeyValueList rows={defaults.map(([key, value]) => ({ label: key, value: formatParamValue(value), mono: true }))} />
            </DetailSection>
          ) : null}
          {batchRows.length > 0 ? (
            <DetailSection title="Batch worker" description="Async batch jobs only. Direct API calls are not affected.">
              <KeyValueList rows={batchRows} />
            </DetailSection>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
