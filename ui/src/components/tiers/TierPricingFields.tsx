import type { ReactNode } from 'react';
import {
  advancedPricingFieldsForProfile,
  pricingFieldsForProfile,
  pricingProfileLabel,
  TIER_PRICING_PROFILES,
  type TierModelPolicyForm,
  type TierPricingFieldDefinition,
  type TierPricingProfile,
} from '../../lib/tiers';

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

  const updateField = (field: TierPricingFieldDefinition, value: string) => {
    onChange({ ...form, [field.formField]: value });
  };

  const updateProfile = (pricingProfile: TierPricingProfile) => {
    onChange({ ...form, pricing_profile: pricingProfile });
  };

  return (
    <FormSection
      title="Pricing"
      description="Choose the billing shape for this model, then set only the prices that apply."
    >
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[260px_minmax(0,1fr)]">
        <label className="block">
          <span className="mb-1 block text-xs font-semibold text-gray-500">Pricing profile</span>
          <select
            value={form.pricing_profile}
            onChange={(event) => updateProfile(event.target.value as TierPricingProfile)}
            disabled={locked}
            className={inputClassName}
          >
            {TIER_PRICING_PROFILES.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-800">
          <p className="font-semibold">{pricingProfileLabel(profile.value)}</p>
          <p className="mt-0.5">{profile.description}</p>
          {inferredMode ? (
            <p className="mt-1 text-blue-700/80">Inferred from model mode: <span className="font-mono">{inferredMode}</span></p>
          ) : (
            <p className="mt-1 text-blue-700/80">Mode could not be inferred; choose the closest billing profile.</p>
          )}
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
        <details className="rounded-lg border border-gray-200 bg-white p-3" open={hasAdvancedValues}>
          <summary className="cursor-pointer text-xs font-semibold text-gray-600">
            Advanced supported pricing fields
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
    </FormSection>
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
      {fields.map((field) => (
        <label key={field.payloadField} className="block rounded-lg border border-gray-100 bg-gray-50 p-3">
          <span className="mb-1 flex items-center justify-between gap-2 text-xs font-semibold text-gray-600">
            <span>{field.label}</span>
            <span className="font-normal text-gray-400">{field.unit}</span>
          </span>
          <input
            value={String(form[field.formField] || '')}
            onChange={(event) => onChange(field, event.target.value)}
            disabled={locked}
            placeholder="Unset"
            className={inputClassName}
          />
          <span className="mt-1 block text-[11px] leading-4 text-gray-400">{field.help}</span>
        </label>
      ))}
    </div>
  );
}

function FormSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3 border-b border-gray-200 pb-4 last:border-b-0">
      <div>
        <h5 className="text-sm font-semibold text-gray-900">{title}</h5>
        <p className="mt-0.5 text-xs text-gray-500">{description}</p>
      </div>
      {children}
    </section>
  );
}
