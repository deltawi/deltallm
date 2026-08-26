import { Image, Paintbrush, RotateCcw, Trash2, Undo2, Upload } from 'lucide-react';

import BrandLogo from '../BrandLogo';
import Button from '../Button';
import ConfirmDialog from '../ConfirmDialog';
import {
  contrastForeground,
  normalizeBranding,
  readableBrandInk,
  type UIBranding,
  visibleBrandSurface,
  visibleMenuHoverSurface,
} from '../../lib/branding';
import type { UIBrandingAssetKind } from '../../lib/api';
import { isHexBrandColor, type ThemeMutation } from '../../lib/settingsTheme';
import { SettingsField, SettingsSection } from './SettingsSection';

const BRANDING_IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp,image/svg+xml';
const BRANDING_FAVICON_ACCEPT = `${BRANDING_IMAGE_ACCEPT},image/x-icon,image/vnd.microsoft.icon`;

interface ThemeSettingsPanelProps {
  value: UIBranding;
  error: string | null;
  dirty: boolean;
  resetDisabled: boolean;
  resetConfirmationOpen: boolean;
  mutation: ThemeMutation;
  onChange: (value: UIBranding) => void;
  onUpload: (assetKey: UIBrandingAssetKind, file: File) => Promise<void>;
  onRemove: (assetKey: UIBrandingAssetKind) => Promise<void>;
  onDiscard: () => void;
  onOpenReset: () => void;
  onCloseReset: () => void;
  onConfirmReset: () => void;
}

function BrandingAssetField({
  assetKey,
  label,
  hint,
  url,
  mutation,
  disabled,
  onUpload,
  onRemove,
}: {
  assetKey: UIBrandingAssetKind;
  label: string;
  hint: string;
  url: string | null;
  mutation: ThemeMutation;
  disabled: boolean;
  onUpload: (file: File) => Promise<void>;
  onRemove: () => Promise<void>;
}) {
  const previewClass = assetKey === 'logo_full'
    ? 'h-12 max-w-full object-contain'
    : 'h-12 w-12 object-contain';
  const working = mutation === `upload:${assetKey}` || mutation === `delete:${assetKey}`;

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
      <div className="mb-3 flex h-16 items-center justify-center rounded-lg border border-gray-200 bg-white px-3">
        {url ? (
          <img src={url} alt={`${label} preview`} className={previewClass} />
        ) : (
          <Image className="h-7 w-7 text-gray-300" aria-hidden="true" />
        )}
      </div>
      <p className="text-sm font-medium text-gray-800">{label}</p>
      <p className="mt-0.5 min-h-8 text-xs text-gray-500">{hint}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <label
          className={`inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-xs font-medium text-brand-on-primary transition-colors hover:bg-brand-primary-hover ${disabled ? 'pointer-events-none opacity-50' : 'cursor-pointer'}`}
        >
          <Upload className="h-3.5 w-3.5" aria-hidden="true" />
          {working ? 'Working…' : url ? 'Replace' : 'Upload'}
          <input
            type="file"
            accept={assetKey === 'favicon' ? BRANDING_FAVICON_ACCEPT : BRANDING_IMAGE_ACCEPT}
            className="sr-only"
            disabled={disabled}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) void onUpload(file);
            }}
          />
        </label>
        {url && (
          <button
            type="button"
            onClick={() => { void onRemove(); }}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-600 transition-colors hover:border-red-200 hover:text-red-600 disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Remove
          </button>
        )}
      </div>
      <p className="mt-2 text-[11px] text-gray-400">
        PNG, JPEG, WebP, SVG{assetKey === 'favicon' ? ', or ICO' : ''}; maximum 2 MB.
      </p>
    </div>
  );
}

function ColorField({
  id,
  label,
  value,
  onChange,
  hint,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint: string;
  disabled: boolean;
}) {
  const pickerValue = isHexBrandColor(value) ? value : '#000000';
  return (
    <SettingsField label={label} hint={hint} htmlFor={id}>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={pickerValue}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          className="h-10 w-12 cursor-pointer rounded-lg border border-gray-200 bg-white p-1 disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={`${label} picker`}
          disabled={disabled}
        />
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 font-mono text-sm uppercase transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-50"
          maxLength={7}
          spellCheck={false}
          disabled={disabled}
        />
      </div>
    </SettingsField>
  );
}

export default function ThemeSettingsPanel({
  value,
  error,
  dirty,
  resetDisabled,
  resetConfirmationOpen,
  mutation,
  onChange,
  onUpload,
  onRemove,
  onDiscard,
  onOpenReset,
  onCloseReset,
  onConfirmReset,
}: ThemeSettingsPanelProps) {
  const preview = normalizeBranding(value);
  const primaryPreview = visibleBrandSurface(preview.primary_color);
  const secondaryPreview = visibleBrandSurface(preview.secondary_color);
  const menuHoverPreview = visibleMenuHoverSurface(preview.menu_hover_color);
  const busy = mutation !== null;
  const inputClass = 'w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm transition-all focus:border-transparent focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-primary disabled:cursor-not-allowed disabled:opacity-50';

  return (
    <>
      <SettingsSection title="Theme identity" description="Customize the product shell for this installation" icon={Paintbrush}>
        <div className="space-y-5">
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </div>
          )}
          <SettingsField
            label="Instance Name"
            hint="Shown in the sidebar, authentication screens, browser title, and emails"
            htmlFor="theme-instance-name"
          >
            <input
              id="theme-instance-name"
              value={value.instance_name}
              onChange={(event) => onChange({ ...value, instance_name: event.target.value })}
              className={inputClass}
              maxLength={80}
              disabled={busy}
            />
          </SettingsField>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <BrandingAssetField
              assetKey="logo_mark"
              label="Simple logo"
              hint="Square mark used in collapsed and compact placements."
              url={value.logo_mark_url}
              mutation={mutation}
              disabled={busy}
              onUpload={(file) => onUpload('logo_mark', file)}
              onRemove={() => onRemove('logo_mark')}
            />
            <BrandingAssetField
              assetKey="logo_full"
              label="Expanded logo"
              hint="Horizontal wordmark used when enough space is available."
              url={value.logo_full_url}
              mutation={mutation}
              disabled={busy}
              onUpload={(file) => onUpload('logo_full', file)}
              onRemove={() => onRemove('logo_full')}
            />
            <BrandingAssetField
              assetKey="favicon"
              label="Favicon"
              hint="Browser icon; the built-in mark is used when empty."
              url={value.favicon_url}
              mutation={mutation}
              disabled={busy}
              onUpload={(file) => onUpload('favicon', file)}
              onRemove={() => onRemove('favicon')}
            />
          </div>
        </div>
      </SettingsSection>

      <SettingsSection title="Theme colours" description="Semantic colours are applied without changing warning, success, or danger states" icon={Paintbrush}>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-3">
          <ColorField
            id="theme-primary-color"
            label="Primary"
            value={value.primary_color}
            onChange={(primaryColor) => onChange({ ...value, primary_color: primaryColor })}
            hint="Primary actions and focus rings"
            disabled={busy}
          />
          <ColorField
            id="theme-secondary-color"
            label="Secondary"
            value={value.secondary_color}
            onChange={(secondaryColor) => onChange({ ...value, secondary_color: secondaryColor })}
            hint="Secondary actions and navigation accents"
            disabled={busy}
          />
          <ColorField
            id="theme-menu-hover-color"
            label="Menu Hover"
            value={value.menu_hover_color}
            onChange={(menuHoverColor) => onChange({ ...value, menu_hover_color: menuHoverColor })}
            hint="Navigation hover background"
            disabled={busy}
          />
        </div>
      </SettingsSection>

      <SettingsSection title="Preview" description="Review logo and control treatments before saving">
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <div className="space-y-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">Logo treatments</p>
            <div className="rounded-lg border border-gray-200 bg-white p-3">
              <BrandLogo variant="expanded" brandingOverride={preview} />
            </div>
            <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white p-3">
              <BrandLogo variant="mark" brandingOverride={preview} />
              <span className="text-xs text-gray-500">Collapsed mark</span>
            </div>
          </div>
          <div className="space-y-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">Interactive treatments</p>
            <div className="flex flex-wrap gap-2">
              <span
                className="rounded-lg px-4 py-2 text-sm font-medium"
                style={{ backgroundColor: primaryPreview, color: contrastForeground(primaryPreview) }}
              >
                Primary action
              </span>
              <span
                className="rounded-lg border bg-white px-4 py-2 text-sm font-medium"
                style={{ borderColor: secondaryPreview, color: readableBrandInk(preview.secondary_color) }}
              >
                Secondary action
              </span>
            </div>
            <div
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium"
              style={{ backgroundColor: menuHoverPreview, color: contrastForeground(menuHoverPreview) }}
            >
              <Paintbrush className="h-4 w-4" aria-hidden="true" /> Menu hover
            </div>
          </div>
        </div>
        <div className="mt-5 flex flex-col-reverse justify-end gap-2 sm:flex-row">
          <Button variant="secondary" onClick={onOpenReset} disabled={resetDisabled || busy}>
            <RotateCcw className="h-4 w-4" aria-hidden="true" /> Reset to DeltaLLM defaults
          </Button>
          <Button variant="secondary" onClick={onDiscard} disabled={!dirty || busy}>
            <Undo2 className="h-4 w-4" aria-hidden="true" /> Discard changes
          </Button>
        </div>
      </SettingsSection>

      <ConfirmDialog
        open={resetConfirmationOpen}
        title="Reset theme to DeltaLLM defaults?"
        description="The factory name and colours will be saved, uploaded simple logo, expanded logo, and favicon files will be permanently deleted, and unsaved theme edits will be discarded. Other replicas converge through normal configuration refresh."
        confirmLabel="Reset theme"
        destructive
        confirming={mutation === 'reset'}
        onConfirm={onConfirmReset}
        onClose={onCloseReset}
      />
    </>
  );
}
