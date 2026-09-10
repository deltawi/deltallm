/** Allowlisted Batch errors; provider text and internal checkpoints are never public. */
export type BatchItemError = {
  message: string;
  type: 'BatchItemError' | 'BatchItemCancelled';
  code?: 'batch_selector_checkpoint_unavailable';
  retryable?: boolean;
  retry_category?: string;
  terminal_reason?: string;
  attempt?: number;
  max_attempts?: number;
  retry_delay_seconds?: number;
};
