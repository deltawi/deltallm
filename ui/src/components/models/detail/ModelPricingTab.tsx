import { Pencil } from 'lucide-react';
import type { ModelDeploymentDetail } from '../../../lib/api';
import { modelPriceRows } from '../../../lib/modelCost';
import Button from '../../Button';
import { DetailSection } from './DetailSection';

// Stored prices are per single unit; show that raw value next to the display price.
function perUnit(value: number): string {
  if (value === 0) return '0';
  return value < 0.001 ? value.toExponential(2) : String(Number(value.toPrecision(6)));
}

export default function ModelPricingTab({ model, mode, canEdit, onEdit }: {
  model: ModelDeploymentDetail;
  mode: string;
  canEdit: boolean;
  onEdit: () => void;
}) {
  const mi = model.model_info || {};
  const rows = modelPriceRows(mode, mi);
  const multiplier = Number(mi.batch_price_multiplier);
  const notes = [
    Number.isFinite(multiplier) && multiplier > 0 && mi.batch_price_multiplier != null
      ? `Batch price multiplier: ${multiplier}× the standard price.`
      : null,
    model.access?.governance_source === 'platform' ? 'Organization access, pricing, and rate limits for this platform model are configured in Tiers.' : null,
  ].filter(Boolean);
  const edit = canEdit
    ? <Button size="sm" variant="outline" onClick={onEdit}><Pencil className="h-3.5 w-3.5" aria-hidden="true" />Edit pricing</Button>
    : undefined;

  return (
    <div className="max-w-4xl space-y-3">
      <DetailSection title="Pricing" description="What each call is charged. Prices are stored per unit and shown in the units people compare." action={edit}>
        {rows.length === 0 ? (
          <p className="px-4 py-3 text-xs text-gray-500">No price is set for this model.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px] tabular-nums">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                  <th scope="col" className="px-4 py-2">Rate</th>
                  <th scope="col" className="px-4 py-2 text-right">Price</th>
                  <th scope="col" className="px-4 py-2">Unit</th>
                  <th scope="col" className="px-4 py-2 text-right">Stored per unit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((row) => (
                  <tr key={row.field}>
                    <td className="px-4 py-2 text-gray-700">{row.label}</td>
                    <td className="px-4 py-2 text-right font-medium text-gray-900">{row.price}</td>
                    <td className="px-4 py-2 text-xs text-gray-500">{row.unit.replace(/^\/ /, 'per ')}</td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-gray-500">{perUnit(row.perUnit)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {notes.length > 0 ? (
          <div className="space-y-0.5 rounded-b-xl border-t border-gray-100 bg-gray-50 px-4 py-2 text-xs text-gray-500">
            {notes.map((note) => <p key={note}>{note}</p>)}
          </div>
        ) : null}
      </DetailSection>
    </div>
  );
}
