export interface MutationOutcome {
  tone: 'success' | 'info';
  message: string;
}

export function mutationOutcome(
  successMessage: string,
  warnings: readonly string[],
): MutationOutcome {
  if (warnings.length === 0) return { tone: 'success', message: successMessage };
  return {
    tone: 'info',
    message: `${successMessage} Runtime warning: ${warnings.join(' ')}`,
  };
}
