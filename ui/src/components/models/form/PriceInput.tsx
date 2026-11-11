import { useState } from 'react';
import { isPriceText, perMillionFromPerToken, perTokenFromPerMillion } from '../pricingUnits';
import { FieldLabel } from './formPrimitives';
import { fieldClass } from './formStyles';

interface PriceInputProps {
  id: string;
  label: string;
  /** The stored value: a price per token, or per unit when perMillion is false. */
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Show and edit the price per 1M tokens. The stored value stays per token. */
  perMillion?: boolean;
  help?: string;
}

export default function PriceInput({ id, label, value, onChange, placeholder, perMillion = false, help }: PriceInputProps) {
  // Keep the text the user types (for example "2."), and follow outside changes such as auto-fill.
  const [text, setText] = useState(() => (perMillion ? perMillionFromPerToken(value) : value));
  const shown = !perMillion ? value : perTokenFromPerMillion(text) === value ? text : perMillionFromPerToken(value);
  const invalid = !isPriceText(value);
  const errorId = `${id}-error`;
  return (
    <div>
      <FieldLabel htmlFor={id} label={label} />
      <div className="relative">
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-400" aria-hidden="true">$</span>
        <input
          id={id}
          type="text"
          inputMode="decimal"
          value={shown}
          placeholder={placeholder}
          onChange={(event) => {
            const next = event.target.value;
            if (perMillion) {
              setText(next);
              onChange(perTokenFromPerMillion(next));
            } else {
              onChange(next);
            }
          }}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          className={`${fieldClass(invalid)} pl-7 tabular-nums`}
        />
      </div>
      {invalid ? <p id={errorId} className="mt-1 text-[11px] text-red-600">Enter a number, 0 or more.</p> : null}
      {help ? <p className="mt-1 text-[11px] text-gray-400">{help}</p> : null}
    </div>
  );
}
