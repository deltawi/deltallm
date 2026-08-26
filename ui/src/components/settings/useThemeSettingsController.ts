import { useCallback, useEffect, useRef, useState } from 'react';

import {
  branding as brandingApi,
  type UIBrandingAssetKind,
  type UIBrandingResponse,
  type UIBrandingUpdate,
} from '../../lib/api';
import { normalizeBranding, sameBranding, type UIBranding } from '../../lib/branding';
import {
  resetBranding,
  type UIBrandingResetResponse,
} from '../../lib/brandingResetApi';
import {
  buildBrandingUpdate,
  canResetBranding,
  type ThemeMutation,
  validateBranding,
  validateBrandingAssetSize,
} from '../../lib/settingsTheme';

type ActiveThemeMutation = Exclude<ThemeMutation, null>;

type ThemeToast = {
  tone: 'success' | 'error' | 'info';
  title: string;
  message: string;
};

export interface ThemeSettingsApi {
  update: (payload: UIBrandingUpdate) => Promise<UIBrandingResponse>;
  uploadAsset: (
    assetKey: UIBrandingAssetKind,
    file: File,
  ) => Promise<UIBrandingResponse>;
  deleteAsset: (assetKey: UIBrandingAssetKind) => Promise<UIBrandingResponse>;
  reset: () => Promise<UIBrandingResetResponse>;
}

type UseThemeSettingsControllerOptions = {
  initialBranding: UIBranding;
  setGlobalBranding: (branding: UIBranding) => void;
  pushToast: (toast: ThemeToast) => void;
  onSaved: () => void;
  api?: ThemeSettingsApi;
};

const DEFAULT_THEME_SETTINGS_API: ThemeSettingsApi = {
  update: brandingApi.update,
  uploadAsset: brandingApi.uploadAsset,
  deleteAsset: brandingApi.deleteAsset,
  reset: resetBranding,
};

function assetUrlField(
  assetKey: UIBrandingAssetKind,
): 'logo_mark_url' | 'logo_full_url' | 'favicon_url' {
  if (assetKey === 'logo_mark') return 'logo_mark_url';
  if (assetKey === 'logo_full') return 'logo_full_url';
  return 'favicon_url';
}

export function useThemeSettingsController({
  initialBranding,
  setGlobalBranding,
  pushToast,
  onSaved,
  api = DEFAULT_THEME_SETTINGS_API,
}: UseThemeSettingsControllerOptions) {
  const normalizedInitial = normalizeBranding(initialBranding);
  const [value, setValue] = useState<UIBranding>(normalizedInitial);
  const [persisted, setPersisted] = useState<UIBranding>(normalizedInitial);
  const [error, setError] = useState<string | null>(null);
  const [mutation, setMutation] = useState<ThemeMutation>(null);
  const [resetConfirmationOpen, setResetConfirmationOpen] = useState(false);
  const mountedRef = useRef(true);
  const mutationRef = useRef<ThemeMutation>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      mutationRef.current = null;
      generationRef.current += 1;
    };
  }, []);

  const beginMutation = useCallback((nextMutation: ActiveThemeMutation): number | null => {
    if (!mountedRef.current || mutationRef.current !== null) return null;
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    mutationRef.current = nextMutation;
    setMutation(nextMutation);
    setError(null);
    return generation;
  }, []);

  const isCurrent = useCallback((generation: number): boolean => (
    mountedRef.current && generationRef.current === generation
  ), []);

  const finishMutation = useCallback((generation: number) => {
    if (!isCurrent(generation)) return;
    mutationRef.current = null;
    setMutation(null);
  }, [isCurrent]);

  const loadBranding = useCallback((branding: UIBranding) => {
    if (!mountedRef.current || mutationRef.current !== null) return;
    generationRef.current += 1;
    const normalized = normalizeBranding(branding);
    setValue(normalized);
    setPersisted(normalized);
    setError(null);
  }, []);

  const save = useCallback(async () => {
    const validationError = validateBranding(value);
    if (validationError) {
      setError(validationError);
      return;
    }
    const generation = beginMutation('save');
    if (generation === null) return;

    try {
      const saved = normalizeBranding(await api.update(buildBrandingUpdate(value)));
      if (!isCurrent(generation)) return;
      setValue(saved);
      setPersisted(saved);
      setGlobalBranding(saved);
      onSaved();
    } catch (saveError) {
      if (isCurrent(generation)) {
        setError(saveError instanceof Error ? saveError.message : 'Could not save theme.');
      }
    } finally {
      finishMutation(generation);
    }
  }, [api, beginMutation, finishMutation, isCurrent, onSaved, setGlobalBranding, value]);

  const applySavedAsset = useCallback((
    assetKey: UIBrandingAssetKind,
    saved: UIBranding,
    generation: number,
  ) => {
    if (!isCurrent(generation)) return;
    const field = assetUrlField(assetKey);
    setValue((current) => ({ ...current, [field]: saved[field] }));
    setPersisted((current) => ({ ...current, [field]: saved[field] }));
    setGlobalBranding(saved);
  }, [isCurrent, setGlobalBranding]);

  const upload = useCallback(async (assetKey: UIBrandingAssetKind, file: File) => {
    const sizeError = validateBrandingAssetSize(file.size);
    if (sizeError) {
      setError(sizeError);
      return;
    }
    const generation = beginMutation(`upload:${assetKey}`);
    if (generation === null) return;

    try {
      const saved = normalizeBranding(await api.uploadAsset(assetKey, file));
      applySavedAsset(assetKey, saved, generation);
    } catch (uploadError) {
      if (isCurrent(generation)) {
        setError(
          uploadError instanceof Error
            ? uploadError.message
            : 'Could not upload branding asset.',
        );
      }
    } finally {
      finishMutation(generation);
    }
  }, [api, applySavedAsset, beginMutation, finishMutation, isCurrent]);

  const remove = useCallback(async (assetKey: UIBrandingAssetKind) => {
    const generation = beginMutation(`delete:${assetKey}`);
    if (generation === null) return;

    try {
      const saved = normalizeBranding(await api.deleteAsset(assetKey));
      applySavedAsset(assetKey, saved, generation);
    } catch (removeError) {
      if (isCurrent(generation)) {
        setError(
          removeError instanceof Error
            ? removeError.message
            : 'Could not remove branding asset.',
        );
      }
    } finally {
      finishMutation(generation);
    }
  }, [api, applySavedAsset, beginMutation, finishMutation, isCurrent]);

  const reset = useCallback(async () => {
    const generation = beginMutation('reset');
    if (generation === null) return;

    try {
      const response = await api.reset();
      const saved = normalizeBranding(response);
      if (!isCurrent(generation)) return;
      setValue(saved);
      setPersisted(saved);
      setGlobalBranding(saved);
      setResetConfirmationOpen(false);
      onSaved();
      pushToast(response.reconciliation_pending ? {
        tone: 'info',
        title: 'Theme reset saved; refresh pending',
        message: 'Defaults were saved in PostgreSQL, but this replica has not applied them yet. It will retry automatically.',
      } : {
        tone: 'success',
        title: 'Theme reset',
        message: 'DeltaLLM defaults were saved. Other replicas will converge through normal configuration refresh.',
      });
    } catch (resetError) {
      if (isCurrent(generation)) {
        const message = resetError instanceof Error ? resetError.message : 'Could not reset theme.';
        setError(message);
        pushToast({ tone: 'error', title: 'Reset failed', message });
      }
    } finally {
      finishMutation(generation);
    }
  }, [api, beginMutation, finishMutation, isCurrent, onSaved, pushToast, setGlobalBranding]);

  const discard = useCallback(() => {
    if (mutationRef.current !== null) return;
    setValue(persisted);
    setError(null);
  }, [persisted]);

  const openResetConfirmation = useCallback(() => {
    if (mutationRef.current === null) setResetConfirmationOpen(true);
  }, []);

  const closeResetConfirmation = useCallback(() => {
    if (mutationRef.current === null) setResetConfirmationOpen(false);
  }, []);

  return {
    value,
    persisted,
    error,
    mutation,
    dirty: !sameBranding(value, persisted),
    resetDisabled: !canResetBranding(value, persisted),
    resetConfirmationOpen,
    setValue,
    loadBranding,
    save,
    upload,
    remove,
    reset,
    discard,
    openResetConfirmation,
    closeResetConfirmation,
  };
}
