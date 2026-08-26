import {
  DEFAULT_BRANDING,
  sameBranding,
  type UIBranding,
} from './branding';
import type { UIBrandingAssetKind, UIBrandingUpdate } from './api';

const HEX_COLOR = /^#[0-9A-Fa-f]{6}$/;
const BRANDING_ASSET_MAX_BYTES = 2 * 1024 * 1024;

export function isHexBrandColor(value: string): boolean {
  return HEX_COLOR.test(value);
}

export function validateBranding(value: UIBranding): string | null {
  if (!value.instance_name.trim()) return 'Instance name is required.';
  if (value.instance_name.trim().length > 80) return 'Instance name must be 80 characters or fewer.';
  if ([...value.instance_name].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) {
    return 'Instance name cannot contain control characters.';
  }
  if (!isHexBrandColor(value.primary_color)) return 'Primary colour must use the #RRGGBB format.';
  if (!isHexBrandColor(value.secondary_color)) return 'Secondary colour must use the #RRGGBB format.';
  if (!isHexBrandColor(value.menu_hover_color)) return 'Menu hover colour must use the #RRGGBB format.';
  return null;
}

export function buildBrandingUpdate(value: UIBranding): UIBrandingUpdate {
  return {
    instance_name: value.instance_name.trim(),
    primary_color: value.primary_color,
    secondary_color: value.secondary_color,
    menu_hover_color: value.menu_hover_color,
  };
}

export function canResetBranding(form: UIBranding, persisted: UIBranding): boolean {
  return !sameBranding(form, DEFAULT_BRANDING) || !sameBranding(persisted, DEFAULT_BRANDING);
}

export function validateBrandingAssetSize(sizeBytes: number): string | null {
  return sizeBytes > BRANDING_ASSET_MAX_BYTES
    ? 'Branding assets must be 2 MB or smaller.'
    : null;
}

export type ThemeMutation =
  | 'save'
  | 'reset'
  | `upload:${UIBrandingAssetKind}`
  | `delete:${UIBrandingAssetKind}`
  | null;
