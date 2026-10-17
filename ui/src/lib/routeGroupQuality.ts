import type { RouteGroupMemberQuality } from './api/routeGroups';

/*
 * The route-group "send low-quality models last" option. The policy block is
 * { deprioritize: boolean, sensitivity: 'relaxed' | 'balanced' | 'strict' }.
 * Presets mirror src/router/quality/policy.py; the UI never sends thresholds.
 */

export type QualitySensitivity = 'relaxed' | 'balanced' | 'strict';

export interface QualitySetting {
  deprioritize: boolean;
  sensitivity: QualitySensitivity;
}

export const QUALITY_SENSITIVITIES: ReadonlyArray<{ id: QualitySensitivity; label: string; rule: string }> = [
  { id: 'relaxed', label: 'Relaxed', rule: 'sent last above 40% failed calls' },
  { id: 'balanced', label: 'Balanced', rule: 'sent last above 20% failed calls' },
  { id: 'strict', label: 'Strict', rule: 'sent last above 10% failed calls, or when the first token is 2× slower than the others' },
];

const SENSITIVITY_IDS = new Set<string>(QUALITY_SENSITIVITIES.map((item) => item.id));

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

/** Reads the policy block. Returns null when the policy has none, undefined when it cannot be shown. */
export function readQualitySetting(value: unknown): QualitySetting | null | undefined {
  if (value == null) return null;
  if (!isRecord(value)) return undefined;
  if (Object.keys(value).some((key) => key !== 'deprioritize' && key !== 'sensitivity')) return undefined;
  const sensitivity = value.sensitivity ?? 'balanced';
  if (typeof sensitivity !== 'string' || !SENSITIVITY_IDS.has(sensitivity)) return undefined;
  if (value.deprioritize !== undefined && typeof value.deprioritize !== 'boolean') return undefined;
  return { deprioritize: value.deprioritize === true, sensitivity: sensitivity as QualitySensitivity };
}

export function qualitySettingLabel(setting: QualitySetting | null | undefined): string {
  if (!setting?.deprioritize) return 'Off';
  return QUALITY_SENSITIVITIES.find((item) => item.id === setting.sensitivity)?.label ?? setting.sensitivity;
}

export function sameQualitySetting(a: QualitySetting | null | undefined, b: QualitySetting | null | undefined): boolean {
  if (!a?.deprioritize && !b?.deprioritize) return true;
  return !!a && !!b && a.deprioritize === b.deprioritize && a.sensitivity === b.sensitivity;
}

export type QualityTone = 'good' | 'warn' | 'bad';

/** Share of calls without a provider error or a rejected answer, in percent. */
export function qualityScore(member: RouteGroupMemberQuality): number | null {
  return member.failure_rate == null ? null : (1 - member.failure_rate) * 100;
}

export function qualityTone(score: number): QualityTone {
  if (score >= 95) return 'good';
  if (score >= 80) return 'warn';
  return 'bad';
}

export function formatQualityScore(score: number): string {
  return `${score >= 99.95 ? '100' : score.toFixed(1)}%`;
}

export function formatFirstToken(ms: number | null): string {
  if (ms == null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

export function formatOutputSpeed(tokensPerSecond: number | null): string {
  return tokensPerSecond == null ? '—' : `${Math.round(tokensPerSecond)} tok/s`;
}

export function sentLastText(reason: RouteGroupMemberQuality['sent_last_reason']): string | null {
  if (reason === 'failed_calls') return 'Too many failed calls, so this model gets traffic only when the others are busy or fail.';
  if (reason === 'slow_first_token') return 'The first token is much slower than the other models, so this model gets traffic only when the others are busy or fail.';
  return null;
}
