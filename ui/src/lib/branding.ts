export interface UIBranding {
  instance_name: string;
  logo_mark_url: string | null;
  logo_full_url: string | null;
  favicon_url: string | null;
  primary_color: string;
  secondary_color: string;
  menu_hover_color: string;
}

export const BUILT_IN_BRAND_ASSETS = {
  logo_mark: '/brand/deltallm-delta-on-light.svg',
  logo_full: '/brand/deltallm-delta-lockup-on-light.svg',
} as const;

export const DEFAULT_BRANDING: UIBranding = {
  instance_name: 'DeltaLLM',
  logo_mark_url: null,
  logo_full_url: null,
  favicon_url: null,
  primary_color: '#5B50D6',
  secondary_color: '#8B7CFF',
  menu_hover_color: '#F7F5FF',
};

const HEX_COLOR = /^#[0-9A-F]{6}$/;
const HOST_LABEL = /^[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?$/i;
const WHITE = '#FFFFFF';
const DARK_FOREGROUND = '#111827';
const BLACK = '#000000';
const MIN_TEXT_CONTRAST = 4.5;
const MIN_CONTROL_CONTRAST = 3;
const MIN_MENU_SURFACE_CONTRAST = 1.15;

function normalizedColor(value: unknown, fallback: string): string {
  if (typeof value !== 'string') return fallback;
  const normalized = value.trim().toUpperCase();
  return HEX_COLOR.test(normalized) ? normalized : fallback;
}

function normalizedUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  return normalized && isAllowedBrandAssetUrl(normalized) ? normalized : null;
}

function validHostname(hostname: string): boolean {
  const normalized = hostname.endsWith('.') ? hostname.slice(0, -1) : hostname;
  if (!normalized) return false;
  if (normalized.startsWith('[') && normalized.endsWith(']')) return normalized.includes(':');
  return normalized.split('.').every((label) => HOST_LABEL.test(label));
}

export function isAllowedBrandAssetUrl(value: string | null): boolean {
  if (!value?.trim()) return true;
  const normalized = value.trim();
  if (
    normalized.length > 2048
    || normalized.includes('\\')
    || [...normalized].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
  ) return false;
  if (normalized.startsWith('/')) return !normalized.startsWith('//');
  if (!/^https:\/\//i.test(normalized)) return false;

  try {
    const parsed = new URL(normalized);
    const authority = normalized.slice('https://'.length).split(/[/?#]/, 1)[0];
    return parsed.protocol === 'https:'
      && Boolean(authority)
      && !normalized.slice('https://'.length).startsWith('/')
      && !authority.includes('%')
      && validHostname(parsed.hostname)
      && !parsed.username
      && !parsed.password;
  } catch {
    return false;
  }
}

export function normalizeBranding(value: Partial<UIBranding> | null | undefined): UIBranding {
  const instanceName = typeof value?.instance_name === 'string' ? value.instance_name.trim() : '';
  return {
    instance_name: instanceName || DEFAULT_BRANDING.instance_name,
    logo_mark_url: normalizedUrl(value?.logo_mark_url),
    logo_full_url: normalizedUrl(value?.logo_full_url),
    favicon_url: normalizedUrl(value?.favicon_url),
    primary_color: normalizedColor(value?.primary_color, DEFAULT_BRANDING.primary_color),
    secondary_color: normalizedColor(value?.secondary_color, DEFAULT_BRANDING.secondary_color),
    menu_hover_color: normalizedColor(value?.menu_hover_color, DEFAULT_BRANDING.menu_hover_color),
  };
}

export function hexToRgb(value: string): [number, number, number] {
  const normalized = normalizedColor(value, '#000000');
  return [
    Number.parseInt(normalized.slice(1, 3), 16),
    Number.parseInt(normalized.slice(3, 5), 16),
    Number.parseInt(normalized.slice(5, 7), 16),
  ];
}

function rgbToHex(rgb: [number, number, number]): string {
  return `#${rgb.map((channel) => Math.round(channel).toString(16).padStart(2, '0')).join('')}`.toUpperCase();
}

export function mixHex(source: string, target: string, targetWeight: number): string {
  const sourceRgb = hexToRgb(source);
  const targetRgb = hexToRgb(target);
  const weight = Math.min(1, Math.max(0, targetWeight));
  return rgbToHex(sourceRgb.map((channel, index) => (
    channel * (1 - weight) + targetRgb[index] * weight
  )) as [number, number, number]);
}

function relativeLuminance(color: string): number {
  const channels = hexToRgb(color).map((channel) => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

export function contrastRatio(first: string, second: string): number {
  const firstLuminance = relativeLuminance(first);
  const secondLuminance = relativeLuminance(second);
  const lighter = Math.max(firstLuminance, secondLuminance);
  const darker = Math.min(firstLuminance, secondLuminance);
  return (lighter + 0.05) / (darker + 0.05);
}

export function contrastForeground(background: string): '#FFFFFF' | '#111827' | '#000000' {
  const preferred = [WHITE, DARK_FOREGROUND] as const;
  const preferredRatios = preferred.map((color) => contrastRatio(background, color));
  const preferredIndex = preferredRatios[0] >= preferredRatios[1] ? 0 : 1;
  if (preferredRatios[preferredIndex] >= MIN_TEXT_CONTRAST) return preferred[preferredIndex];
  return contrastRatio(background, WHITE) >= contrastRatio(background, BLACK) ? WHITE : BLACK;
}

export function visibleBrandSurface(
  color: string,
  background = WHITE,
  minimumContrast = MIN_CONTROL_CONTRAST,
): string {
  const normalized = normalizedColor(color, BLACK);
  if (contrastRatio(normalized, background) >= minimumContrast) return normalized;

  const targets = [BLACK, WHITE].sort(
    (first, second) => contrastRatio(second, background) - contrastRatio(first, background),
  );
  for (const target of targets) {
    for (let weight = 0.02; weight <= 1; weight += 0.02) {
      const candidate = mixHex(normalized, target, weight);
      if (contrastRatio(candidate, background) >= minimumContrast) return candidate;
    }
  }
  return contrastRatio(BLACK, background) >= contrastRatio(WHITE, background) ? BLACK : WHITE;
}

export function visibleMenuHoverSurface(color: string): string {
  return visibleBrandSurface(color, WHITE, MIN_MENU_SURFACE_CONTRAST);
}

export function readableBrandInk(color: string, backgrounds: string[] = [WHITE]): string {
  const isReadable = (candidate: string) => backgrounds.every(
    (background) => contrastRatio(candidate, background) >= MIN_TEXT_CONTRAST,
  );
  if (isReadable(color)) return normalizedColor(color, DARK_FOREGROUND);
  for (let weight = 0.02; weight <= 1; weight += 0.02) {
    const candidate = mixHex(color, BLACK, weight);
    if (isReadable(candidate)) return candidate;
  }
  return BLACK;
}

export function accessibleHoverColor(background: string, foreground: string): string {
  const normalizedBackground = normalizedColor(background, BLACK);
  const lightForeground = relativeLuminance(foreground) > 0.5;
  const preferredTarget = lightForeground ? BLACK : WHITE;
  const alternateTarget = lightForeground ? WHITE : BLACK;
  const candidates = [
    mixHex(normalizedBackground, preferredTarget, 0.14),
    mixHex(normalizedBackground, alternateTarget, 0.14),
    mixHex(normalizedBackground, preferredTarget, 0.22),
    mixHex(normalizedBackground, alternateTarget, 0.22),
  ];
  const visiblyDifferent = candidates.find((candidate) => (
    candidate !== normalizedBackground
    && contrastRatio(candidate, foreground) >= MIN_TEXT_CONTRAST
  ));
  return visiblyDifferent || normalizedBackground;
}

function rgbChannels(color: string): string {
  return hexToRgb(color).join(' ');
}

export function brandingCssVariables(branding: UIBranding): Record<string, string> {
  const primarySurface = visibleBrandSurface(branding.primary_color);
  const primaryForeground = contrastForeground(primarySurface);
  const primaryHover = accessibleHoverColor(primarySurface, primaryForeground);
  const primarySoft = mixHex(branding.primary_color, WHITE, 0.9);
  const primaryInk = readableBrandInk(branding.primary_color, [WHITE, primarySoft]);
  const secondarySurface = visibleBrandSurface(branding.secondary_color);
  const secondaryForeground = contrastForeground(secondarySurface);
  const secondaryHover = accessibleHoverColor(secondarySurface, secondaryForeground);
  const secondarySoft = mixHex(branding.secondary_color, WHITE, 0.92);
  const secondaryInk = readableBrandInk(branding.secondary_color, [WHITE, secondarySoft]);
  const menuHoverSurface = visibleMenuHoverSurface(branding.menu_hover_color);
  const menuHoverForeground = contrastForeground(menuHoverSurface);

  return {
    '--brand-primary': rgbChannels(primarySurface),
    '--brand-primary-ink': rgbChannels(primaryInk),
    '--brand-primary-ink-hover': rgbChannels(mixHex(primaryInk, '#000000', 0.14)),
    '--brand-primary-hover': rgbChannels(primaryHover),
    '--brand-primary-soft': rgbChannels(primarySoft),
    '--brand-on-primary': rgbChannels(primaryForeground),
    '--brand-secondary': rgbChannels(secondarySurface),
    '--brand-secondary-ink': rgbChannels(secondaryInk),
    '--brand-secondary-ink-hover': rgbChannels(mixHex(secondaryInk, '#000000', 0.14)),
    '--brand-secondary-hover': rgbChannels(secondaryHover),
    '--brand-secondary-soft': rgbChannels(secondarySoft),
    '--brand-on-secondary': rgbChannels(secondaryForeground),
    '--brand-menu-hover': rgbChannels(menuHoverSurface),
    '--brand-menu-hover-foreground': rgbChannels(menuHoverForeground),
  };
}
