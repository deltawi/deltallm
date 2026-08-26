import assert from 'node:assert/strict';
import test from 'node:test';

import { DEFAULT_BRANDING, sameBranding } from '../src/lib/branding';
import {
  buildBrandingUpdate,
  canResetBranding,
  validateBranding,
  validateBrandingAssetSize,
} from '../src/lib/settingsTheme';

test('theme helpers validate and build the server update without asset fields', () => {
  const customized = {
    ...DEFAULT_BRANDING,
    instance_name: '  Acme AI  ',
    logo_mark_url: '/branding/mark.svg',
    primary_color: '#123ABC',
  };

  assert.equal(validateBranding(customized), null);
  assert.deepEqual(buildBrandingUpdate(customized), {
    instance_name: 'Acme AI',
    primary_color: '#123ABC',
    secondary_color: DEFAULT_BRANDING.secondary_color,
    menu_hover_color: DEFAULT_BRANDING.menu_hover_color,
  });
  assert.equal(validateBranding({ ...customized, instance_name: '   ' }), 'Instance name is required.');
  assert.equal(
    validateBranding({ ...customized, primary_color: 'blue' }),
    'Primary colour must use the #RRGGBB format.',
  );
});

test('theme equality keeps discard and factory reset decisions explicit', () => {
  const customized = { ...DEFAULT_BRANDING, instance_name: 'Acme AI' };

  assert.equal(sameBranding(DEFAULT_BRANDING, { ...DEFAULT_BRANDING }), true);
  assert.equal(sameBranding(DEFAULT_BRANDING, customized), false);
  assert.equal(canResetBranding(DEFAULT_BRANDING, DEFAULT_BRANDING), false);
  assert.equal(canResetBranding(customized, DEFAULT_BRANDING), true);
  assert.equal(canResetBranding(DEFAULT_BRANDING, customized), true);
});

test('branding asset size validation matches the backend limit', () => {
  assert.equal(validateBrandingAssetSize(2 * 1024 * 1024), null);
  assert.equal(
    validateBrandingAssetSize(2 * 1024 * 1024 + 1),
    'Branding assets must be 2 MB or smaller.',
  );
});
