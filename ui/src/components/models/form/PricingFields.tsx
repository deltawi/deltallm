import { useId } from 'react';
import type { ModelFormValues } from '../../modelFormShared';
import { isPriceText } from '../pricingUnits';
import PriceInput from './PriceInput';
import { Disclosure, FieldLabel } from './formPrimitives';
import { fieldClass } from './formStyles';

type PriceField = {
  [K in keyof ModelFormValues]: ModelFormValues[K] extends string ? K : never;
}[keyof ModelFormValues];

interface PriceSpec {
  field: PriceField;
  label: string;
  placeholder?: string;
  perMillion?: boolean;
  help?: string;
}

const token = (field: PriceField, label: string, placeholder = '0.00', help?: string): PriceSpec => (
  { field, label: `${label} per 1M tokens`, placeholder, perMillion: true, help }
);

const MAIN_PRICES: Partial<Record<ModelFormValues['mode'], PriceSpec[]>> = {
  chat: [
    token('input_cost_per_token', 'Input', '2.50'),
    token('output_cost_per_token', 'Output', '10.00'),
    token('input_cost_per_token_cache_hit', 'Cached input', 'Optional'),
  ],
  embedding: [
    token('input_cost_per_token', 'Input', '0.02'),
    token('input_cost_per_token_cache_hit', 'Cached input', 'Optional'),
  ],
  image_generation: [
    { field: 'input_cost_per_image', label: 'Price per image', placeholder: '0.04' },
  ],
  audio_speech: [
    { field: 'input_cost_per_character', label: 'Input per character', placeholder: '0.000015' },
    { field: 'output_cost_per_character', label: 'Output per character', placeholder: '0.000015' },
    { field: 'input_cost_per_second', label: 'Input per second', placeholder: '0.00025' },
    { field: 'output_cost_per_second', label: 'Output per second', placeholder: '0.00025' },
    token('input_cost_per_audio_token', 'Input audio', '100.00'),
    token('output_cost_per_audio_token', 'Output audio', '100.00'),
  ],
  audio_transcription: [
    { field: 'input_cost_per_second', label: 'Input per second', placeholder: '0.0001', help: 'The main price for duration-based billing.' },
    { field: 'output_cost_per_second', label: 'Output per second', placeholder: '0', help: 'Only for providers that bill output audio time.' },
  ],
  rerank: [
    token('input_cost_per_token', 'Input', '2.00', 'Counts the tokens of the query and the documents.'),
  ],
};

const CACHE_PRICES: PriceSpec[] = [
  token('output_cost_per_token_cache_hit', 'Cached output', 'Optional'),
  token('input_cost_per_token_cache_write', 'Cache write', 'Empty uses input price'),
  token('input_cost_per_token_cache_write_1h', 'Cache write (1 hour)', 'Empty uses cache write price'),
];

const BATCH_PRICES: PriceSpec[] = [
  token('batch_input_cost_per_token', 'Batch input', 'Optional'),
  token('batch_output_cost_per_token', 'Batch output', 'Optional'),
];

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

