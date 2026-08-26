import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import BrandLogo from '../src/components/BrandLogo';
import Button from '../src/components/Button';
import { BUILT_IN_BRAND_ASSETS, DEFAULT_BRANDING, type UIBranding } from '../src/lib/branding';
import { BrandingContext, type BrandingContextValue } from '../src/lib/brandingContext';

function renderLogo(
  branding: UIBranding,
  variant: 'mark' | 'expanded' | 'reveal',
): string {
  const value: BrandingContextValue = {
    branding,
    assetRevision: 0,
    refreshBranding: async () => branding,
    setBranding: () => undefined,
  };
  return renderToStaticMarkup(createElement(
    BrandingContext.Provider,
    { value },
    createElement(BrandLogo, { variant }),
  ));
}

test('built-in brand asset URLs resolve to packaged public files', () => {
  [
    ...Object.values(BUILT_IN_BRAND_ASSETS),
    '/brand/deltallm-delta-reveal.svg',
    '/brand/deltallm-delta-reveal-light.svg',
  ].forEach((assetUrl) => {
    const asset = readFileSync(`public${assetUrl}`, 'utf8');
    assert.match(asset, /^<svg\b/);
  });
});

test('reveal motion finishes within five seconds and respects reduced-motion preferences', () => {
  [
    '/brand/deltallm-delta-reveal.svg',
    '/brand/deltallm-delta-reveal-light.svg',
  ].forEach((assetUrl) => {
    const asset = readFileSync(`public${assetUrl}`, 'utf8');
    assert.match(asset, /corner-blink 0\.75s 2\.2s ease-in-out 3/);
    assert.match(asset, /@media \(prefers-reduced-motion: reduce\)/);
    assert.match(asset, /\.delta-corner \{ animation: none; \}/);
  });
});

test('expanded default branding uses the built-in Delta wordmark', () => {
  const markup = renderLogo(DEFAULT_BRANDING, 'expanded');

  assert.match(markup, /src="\/brand\/deltallm-delta-lockup-on-light\.svg"/);
  assert.match(markup, /alt="DeltaLLM"/);
});

test('default reveal branding uses the light-surface asset', () => {
  const markup = renderLogo(DEFAULT_BRANDING, 'reveal');

  assert.match(markup, /src="\/brand\/deltallm-delta-reveal-light\.svg"/);
});

test('reveal branding preserves custom logo priority', () => {
  const markup = renderLogo({
    ...DEFAULT_BRANDING,
    instance_name: 'Acme AI',
    logo_mark_url: '/branding/mark.svg',
    logo_full_url: '/branding/wordmark.svg',
  }, 'reveal');

  assert.match(markup, /src="\/branding\/wordmark\.svg"/);
  assert.doesNotMatch(markup, /deltallm-delta-reveal/);
});

test('a custom instance without uploads never receives the Delta reveal', () => {
  const markup = renderLogo({ ...DEFAULT_BRANDING, instance_name: 'Acme AI' }, 'reveal');

  assert.match(markup, /src="\/brand\/deltallm-delta-on-light\.svg"/);
  assert.match(markup, />Acme AI</);
  assert.doesNotMatch(markup, /deltallm-delta-reveal/);
});

test('a custom instance without uploads uses the built-in mark and configured name', () => {
  const markup = renderLogo({ ...DEFAULT_BRANDING, instance_name: 'Acme AI' }, 'expanded');

  assert.match(markup, /src="\/brand\/deltallm-delta-on-light\.svg"/);
  assert.match(markup, />Acme AI</);
});

test('logo previews normalize unsafe asset overrides before rendering', () => {
  const value: BrandingContextValue = {
    branding: DEFAULT_BRANDING,
    assetRevision: 0,
    refreshBranding: async () => DEFAULT_BRANDING,
    setBranding: () => undefined,
  };
  const markup = renderToStaticMarkup(createElement(
    BrandingContext.Provider,
    { value },
    createElement(BrandLogo, {
      variant: 'expanded',
      brandingOverride: {
        ...DEFAULT_BRANDING,
        logo_full_url: 'javascript:alert(1)',
      },
    }),
  ));

  assert.match(markup, /src="\/brand\/deltallm-delta-lockup-on-light\.svg"/);
});

test('expanded logo prefers a configured full wordmark', () => {
  const markup = renderLogo({
    ...DEFAULT_BRANDING,
    instance_name: 'Acme AI',
    logo_mark_url: '/branding/mark.svg',
    logo_full_url: '/branding/wordmark.svg',
  }, 'expanded');

  assert.match(markup, /src="\/branding\/wordmark\.svg"/);
  assert.match(markup, /alt="Acme AI"/);
});

test('expanded logo sizing is independent from mark sizing', () => {
  const branding = {
    ...DEFAULT_BRANDING,
    logo_full_url: '/branding/wordmark.svg',
  };
  const value: BrandingContextValue = {
    branding,
    assetRevision: 0,
    refreshBranding: async () => branding,
    setBranding: () => undefined,
  };
  const markup = renderToStaticMarkup(createElement(
    BrandingContext.Provider,
    { value },
    createElement(BrandLogo, {
      variant: 'expanded',
      markClassName: 'h-7 w-7',
      fullClassName: 'h-7 max-w-[10rem]',
    }),
  ));

  assert.match(markup, /h-7 max-w-\[10rem\]/);
  assert.doesNotMatch(markup, /h-7 w-7/);
});

test('button variants use semantic brand foregrounds', () => {
  const primary = renderToStaticMarkup(createElement(Button, null, 'Save'));
  const secondary = renderToStaticMarkup(createElement(Button, { variant: 'secondary' }, 'Cancel'));

  assert.match(primary, /bg-brand-primary/);
  assert.match(primary, /text-brand-on-primary/);
  assert.match(secondary, /border-brand-secondary/);
  assert.match(secondary, /text-brand-secondary-ink/);
});
