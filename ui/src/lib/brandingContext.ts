import { createContext, useContext } from 'react';
import type { UIBranding } from './branding';

export interface BrandingContextValue {
  branding: UIBranding;
  assetRevision: number;
  refreshBranding: () => Promise<UIBranding>;
  setBranding: (branding: UIBranding) => void;
}

export const BrandingContext = createContext<BrandingContextValue | null>(null);

export function useBranding(): BrandingContextValue {
  const context = useContext(BrandingContext);
  if (!context) throw new Error('useBranding must be used within BrandingProvider');
  return context;
}
