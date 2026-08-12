import {
  advancedPricingFieldsForProfile,
  pricingFieldsForProfile,
  pricingProfileLabel,
  TIER_PRICING_PROFILES,
  type TierModelPolicyForm,
  type TierPricingFieldDefinition,
  type TierPricingProfile,
} from '../../lib/tiers';
import { useState } from 'react';
import { TierField, TierFieldHelp } from './TierEditorControls';

type TierPricingFieldsProps = {
  form: TierModelPolicyForm;
  locked: boolean;
  inputClassName: string;
  inferredMode?: string | null;
  onChange: (form: TierModelPolicyForm) => void;
};

export default function TierPricingFields({
  form,
  locked,
  inputClassName,
  inferredMode,
  onChange,
}: TierPricingFieldsProps) {
  const primaryFields = pricingFieldsForProfile(form.pricing_profile);
  const advancedFields = advancedPricingFieldsForProfile(form.pricing_profile);
  const profile = TIER_PRICING_PROFILES.find((item) => item.value === form.pricing_profile)
    || TIER_PRICING_PROFILES[0];
  const hasAdvancedValues = advancedFields.some((field) => String(form[field.formField] || '').trim());
  const [advancedOpen, setAdvancedOpen] = useState(hasAdvancedValues);

  const updateField = (field: TierPricingFieldDefinition, value: string) => {
    onChange({ ...form, [field.formField]: value });
  };

  const updateProfile = (pricingProfile: TierPricingProfile) => {
    onChange({ ...form, pricing_profile: pricingProfile });
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[260px_minmax(0,1fr)]">
        <TierField
          id="tier-policy-pricing-profile"
          label="Pricing profile"
          help="Controls which pricing inputs are shown; it does not change the model. Blank prices are unconfigured, while an explicit 0 makes that usage free."
        >
          <select
            id="tier-policy-pricing-profile"
            value={form.pricing_profile}
            onChange={(event) => updateProfile(event.target.value as TierPricingProfile)}
            disabled={locked}
            className={inputClassName}
          >
            {TIER_PRICING_PROFILES.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </TierField>
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          <p><span className="font-semibold">{pricingProfileLabel(profile.value)}:</span> {profile.description}</p>
          {inferredMode ? (
            <p className="mt-1 text-blue-700/80">Inferred from model mode: <span className="font-mono">{inferredMode}</span></p>
          ) : (
            <p className="mt-1 text-blue-700/80">Mode could not be inferred; choose the closest billing profile.</p>
          )}
          <p className="mt-1 font-medium text-blue-900">Blank = not configured · 0 = intentionally free</p>
        </div>
      </div>

      <PricingGrid
        fields={primaryFields}
        form={form}
        locked={locked}
        inputClassName={inputClassName}
        onChange={updateField}
      />

      {advancedFields.length > 0 ? (
        <details
          key={form.pricing_profile}
          className="rounded-lg border border-gray-200 bg-white p-3"
          open={advancedOpen}
          onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}
        >
          <summary className="cursor-pointer text-xs font-semibold text-gray-600">
            Advanced pricing fields{hasAdvancedValues ? ' · configured' : ''}
          </summary>
          <p className="mt-1 text-xs text-gray-500">
            Values here are preserved even when hidden by the selected profile.
          </p>
          <PricingGrid
            fields={advancedFields}
            form={form}
            locked={locked}
            inputClassName={inputClassName}
            onChange={updateField}
          />
        </details>
      ) : null}
    </div>
  );
}

function PricingGrid({
  fields,
  form,
  locked,
  inputClassName,
  onChange,
}: {
  fields: TierPricingFieldDefinition[];
  form: TierModelPolicyForm;
  locked: boolean;
  inputClassName: string;
  onChange: (field: TierPricingFieldDefinition, value: string) => void;
}) {
  if (fields.length === 0) {
    return <p className="text-sm text-gray-400">No pricing fields for this profile.</p>;
  }

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {fields.map((field) => {
        const inputId = `tier-pricing-${field.formField}`;
        const help = `${field.help} ${pricingExample(field.unit)}`;
        return (
        <div key={field.payloadField} className="block rounded-lg border border-gray-100 bg-gray-50 p-3">
          <div className="mb-1 flex min-h-4 items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-1">
              <label htmlFor={inputId} className="truncate text-xs font-semibold text-gray-600">{field.label}</label>
              <TierFieldHelp label={field.label} help={help} />
            </span>
            <span className="shrink-0 text-xs font-normal text-gray-400">{field.unit}</span>
          </div>
          <input
            id={inputId}
            value={String(form[field.formField] || '')}
            onChange={(event) => onChange(field, event.target.value)}
            disabled={locked}
            inputMode="decimal"
            placeholder="Not configured"
            className={inputClassName}
          />
        </div>
        );
      })}
    </div>
  );
}

function pricingExample(unit: string): string {
  switch (unit) {
    case '/token':
      return 'Example: 5 per million tokens is entered as 0.000005.';
    case '/audio token':
      return 'Example: 10 per million audio tokens is entered as 0.00001.';
    case '/char':
      return 'Example: 15 per million characters is entered as 0.000015.';
    case '/sec':
      return 'Example: 0.006 charges 0.006 per billable second.';
    case '/image':
      return 'Example: 0.04 charges 0.04 per image.';
    case '/request':
      return 'Example: 0.01 charges 0.01 for every request.';
    case 'x':
      return 'Example: 0.5 charges half the synchronous token price for batch usage.';
    default:
      return '';
  }
}
