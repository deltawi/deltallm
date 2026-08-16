import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  brandingCssVariables,
  accessibleHoverColor,
  contrastForeground,
  contrastRatio,
  DEFAULT_BRANDING,
  hexToRgb,
  isAllowedBrandAssetUrl,
  mixHex,
  normalizeBranding,
  readableBrandInk,
  visibleBrandSurface,
  visibleMenuHoverSurface,
} from '../src/lib/branding';

const urlCases = JSON.parse(readFileSync('../tests/fixtures/ui_branding_urls.json', 'utf8')) as {
  valid: string[];
  invalid: string[];
};

test('branding defaults and partial payloads normalize safely', () => {
  assert.deepEqual(normalizeBranding(null), DEFAULT_BRANDING);
  assert.deepEqual(normalizeBranding({
    instance_name: '  Acme AI  ',
    primary_color: '#abcdef',
    secondary_color: 'invalid',
    logo_mark_url: ' /brand/mark.svg ',
  }), {
    ...DEFAULT_BRANDING,
    instance_name: 'Acme AI',
    logo_mark_url: '/brand/mark.svg',
    primary_color: '#ABCDEF',
  });
});

test('brand asset URLs allow only root-relative paths and HTTPS', () => {
  assert.equal(isAllowedBrandAssetUrl(null), true);
  urlCases.valid.forEach((url) => assert.equal(isAllowedBrandAssetUrl(url), true, url));
  urlCases.invalid.forEach((url) => assert.equal(isAllowedBrandAssetUrl(url), false, url));
});

test('hex helpers derive deterministic brand colours', () => {
  assert.deepEqual(hexToRgb('#2563EB'), [37, 99, 235]);
  assert.equal(mixHex('#000000', '#FFFFFF', 0.5), '#808080');
  assert.equal(mixHex('#123456', '#FFFFFF', 0), '#123456');
});

test('foreground selection maximizes contrast for light and dark brands', () => {
  assert.equal(contrastForeground('#FFFFFF'), '#111827');
  assert.equal(contrastForeground('#111827'), '#FFFFFF');
  assert.ok(contrastRatio('#FFFFFF', '#111827') > 14);
  assert.equal(readableBrandInk('#2563EB'), '#2563EB');
  assert.ok(contrastRatio(readableBrandInk('#FDE047'), '#FFFFFF') >= 4.5);
});

test('foreground and hover derivation preserve AA contrast across the grayscale range', () => {
  for (let channel = 0; channel <= 255; channel += 1) {
    const color = `#${channel.toString(16).padStart(2, '0').repeat(3)}`;
    const foreground = contrastForeground(color);
    const hover = accessibleHoverColor(color, foreground);
    assert.ok(contrastRatio(color, foreground) >= 4.5, `${color} normal`);
    assert.ok(contrastRatio(hover, foreground) >= 4.5, `${color} hover`);
    assert.notEqual(hover, color, `${color} hover must be visible`);
  }
});

test('derived ink remains readable on both white and tinted soft surfaces', () => {
  ['#FDE047', '#7C3AED', '#22C55E', '#94A3B8'].forEach((color) => {
    const soft = mixHex(color, '#FFFFFF', 0.9);
    const ink = readableBrandInk(color, ['#FFFFFF', soft]);
    assert.ok(contrastRatio(ink, '#FFFFFF') >= 4.5, `${color} on white`);
    assert.ok(contrastRatio(ink, soft) >= 4.5, `${color} on soft`);
  });
});

test('light custom colours retain visible control and menu affordances', () => {
  ['#FFFFFF', '#F9FAFB', '#FDE047'].forEach((color) => {
    assert.ok(contrastRatio(visibleBrandSurface(color), '#FFFFFF') >= 3, `${color} control`);
  });

  const menuSurface = visibleMenuHoverSurface('#FFFFFF');
  assert.notEqual(menuSurface, '#FFFFFF');
  assert.ok(contrastRatio(menuSurface, '#FFFFFF') >= 1.15);
});

test('CSS variables expose semantic runtime tokens', () => {
  const variables = brandingCssVariables({
    ...DEFAULT_BRANDING,
    primary_color: '#000000',
    secondary_color: '#FFFFFF',
    menu_hover_color: '#111827',
  });

  assert.equal(variables['--brand-primary'], '0 0 0');
  assert.equal(variables['--brand-primary-ink'], '0 0 0');
  assert.equal(variables['--brand-primary-ink-hover'], '0 0 0');
  assert.equal(variables['--brand-on-primary'], '255 255 255');
  assert.equal(variables['--brand-secondary'], hexToRgb(visibleBrandSurface('#FFFFFF')).join(' '));
  assert.notEqual(variables['--brand-secondary'], '255 255 255');
  assert.notEqual(variables['--brand-secondary-ink'], '255 255 255');
  assert.equal(variables['--brand-on-secondary'], '17 24 39');
  assert.equal(variables['--brand-menu-hover-foreground'], '255 255 255');
});
