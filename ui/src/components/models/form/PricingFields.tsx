import { useId } from 'react';
import type { ModelFormValues } from '../../modelFormShared';
import { isPriceText } from '../pricingUnits';
import PriceInput from './PriceInput';
import { Disclosure, FieldLabel } from './formPrimitives';
import { fieldClass } from './formStyles';
import { BATCH_PRICES, CACHE_PRICES, MAIN_PRICES, type PriceSpec } from './priceFieldSpecs';

interface PricingFieldsProps {
  form: ModelFormValues;
  onChange: (patch: Partial<ModelFormValues>) => void;
}

function PriceGrid({ specs, form, onChange, idPrefix }: PricingFieldsProps & { specs: PriceSpec[]; idPrefix: string }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {specs.map((spec) => (
        <PriceInput
          key={spec.field}
          id={`${idPrefix}-${spec.field}`}
          label={spec.label}
          value={form[spec.field]}
          placeholder={spec.placeholder}
          perMillion={spec.perMillion}
          help={spec.help}
          onChange={(value) => onChange({ [spec.field]: value })}
        />
      ))}
    </div>
  );
}

/** The price fields for the model type. Token prices show per 1M tokens. */
export default function PricingFields({ form, onChange }: PricingFieldsProps) {
  const idPrefix = useId();
  const mode = form.mode;
  const main = MAIN_PRICES[mode] || [];
  const hasValue = (specs: PriceSpec[]) => specs.some((spec) => form[spec.field].trim() !== '');
  const hasInvalid = (specs: PriceSpec[]) => specs.some((spec) => !isPriceText(form[spec.field]));
  return (
    <>
      <PriceGrid specs={main} form={form} onChange={onChange} idPrefix={idPrefix} />
      {mode === 'chat' ? (
        <Disclosure label="Cache write and cached output prices" defaultOpen={hasValue(CACHE_PRICES)} forceOpen={hasInvalid(CACHE_PRICES)}>
          <PriceGrid specs={CACHE_PRICES} form={form} onChange={onChange} idPrefix={idPrefix} />
          <p className="text-[11px] text-gray-400">
            Cache write prices apply when the provider charges to store a prompt in its cache, for example Anthropic prompt caching.
          </p>
        </Disclosure>
      ) : null}
      {mode === 'chat' || mode === 'embedding' ? (
        <Disclosure
          label="Batch prices"
          defaultOpen={hasValue(BATCH_PRICES) || form.batch_price_multiplier.trim() !== ''}
          forceOpen={hasInvalid(BATCH_PRICES)}
        >
          <p className="text-[11px] text-gray-400">
            Optional prices for batch jobs. Set a multiplier (0.5 is a 50% discount) or batch prices. Batch prices take priority over the multiplier.
          </p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <FieldLabel htmlFor={`${idPrefix}-batch-multiplier`} label="Price multiplier" />
              <input
                id={`${idPrefix}-batch-multiplier`}
                type="number"
                step="any"
                min="0"
                value={form.batch_price_multiplier}
                onChange={(event) => onChange({ batch_price_multiplier: event.target.value })}
                placeholder="0.5"
                className={fieldClass()}
              />
            </div>
            {BATCH_PRICES.map((spec) => (
              <PriceInput
                key={spec.field}
                id={`${idPrefix}-${spec.field}`}
                label={spec.label}
                value={form[spec.field]}
                placeholder={spec.placeholder}
                perMillion={spec.perMillion}
                onChange={(value) => onChange({ [spec.field]: value })}
              />
            ))}
          </div>
        </Disclosure>
      ) : null}
    </>
  );
}
