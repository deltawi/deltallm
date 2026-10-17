import ToggleSwitch from '../ToggleSwitch';
import { QUALITY_SENSITIVITIES, type QualitySensitivity, type QualitySetting } from '../../lib/routeGroupQuality';

export const QUALITY_ROUTING_TITLE = 'Send traffic away from low-quality models';
export const QUALITY_ROUTING_DESCRIPTION = 'A model whose answers often fail goes to the end of the order. It still answers when the other models are busy or fail.';

/** The three sensitivity presets and the rule of the selected one. */
export function QualitySensitivityPicker({ sensitivity, disabled, sentLast, onChange }: {
  sensitivity: QualitySensitivity;
  disabled: boolean;
  /** Models the live routing sends last now, or null when that is not known for this setting. */
  sentLast: string[] | null;
  onChange: (sensitivity: QualitySensitivity) => void;
}) {
  const preset = QUALITY_SENSITIVITIES.find((item) => item.id === sensitivity) ?? QUALITY_SENSITIVITIES[1];
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <span id="quality-sensitivity-label" className="text-xs text-gray-500">Sensitivity</span>
        <div role="radiogroup" aria-labelledby="quality-sensitivity-label" className="inline-flex overflow-hidden rounded-lg border border-gray-200">
          {QUALITY_SENSITIVITIES.map((item) => {
            const selected = item.id === sensitivity;
            return (
              <button key={item.id} type="button" role="radio" aria-checked={selected} disabled={disabled}
                onClick={() => onChange(item.id)}
                className={`border-r border-gray-200 px-3 py-1 text-xs last:border-r-0 focus-visible:relative focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary disabled:opacity-60 ${
                  selected ? 'bg-brand-primary/10 font-semibold text-brand-primary-ink' : 'bg-white text-gray-700 hover:bg-gray-50'
                }`}>
                {item.label}
              </button>
            );
          })}
        </div>
      </div>
      <p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">
        {preset.label}: {preset.rule} (minimum 20 calls in the last 15 minutes).
        {sentLast === null ? null : sentLast.length > 0
          ? <> Right now: <strong className="font-semibold text-amber-700">{sentLast.join(', ')}</strong>.</>
          : ' Right now: no model is sent last.'}
      </p>
    </div>
  );
}

interface QualityRoutingCardProps {
  setting: QualitySetting | null | undefined;
  disabled: boolean;
  /** Models the live routing sends last now, or null when that is not known for this setting. */
  sentLast: string[] | null;
  onChange: (setting: QualitySetting) => void;
}

/** The "send traffic away from low-quality models" option: one switch and three presets. */
export default function QualityRoutingCard({ setting, disabled, sentLast, onChange }: QualityRoutingCardProps) {
  const on = !!setting?.deprioritize;
  const sensitivity: QualitySensitivity = setting?.sensitivity ?? 'balanced';

  return (
    <section aria-labelledby="quality-routing-title" className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div>
          <h3 id="quality-routing-title" className="text-sm font-semibold text-gray-900">{QUALITY_ROUTING_TITLE}</h3>
          <p className="mt-0.5 text-xs text-gray-500">{QUALITY_ROUTING_DESCRIPTION}</p>
        </div>
        <ToggleSwitch checked={on} disabled={disabled} activeColor="rgb(var(--brand-primary))" aria-label={QUALITY_ROUTING_TITLE}
          onCheckedChange={(next) => onChange({ deprioritize: next, sensitivity })} />
      </div>
      {on ? (
        <div className="border-t border-gray-100 px-4 py-3">
          <QualitySensitivityPicker sensitivity={sensitivity} disabled={disabled} sentLast={sentLast}
            onChange={(next) => onChange({ deprioritize: true, sensitivity: next })} />
        </div>
      ) : null}
    </section>
  );
}
