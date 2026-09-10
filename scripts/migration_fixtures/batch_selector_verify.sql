DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_batch_item'::regclass
          AND conname='deltallm_batch_selector_checkpoint_bound' AND convalidated
    ) THEN
        RAISE EXCEPTION 'Batch checkpoint validation did not finish';
    END IF;
    IF (
        SELECT COUNT(*) FROM deltallm_batch_item
        WHERE item_id IN ('migration-selector-pending','migration-selector-completed')
          AND selector_checkpoint IS NULL
          AND request_body='{"model":"fixture"}'::jsonb
          AND ((custom_id='pending' AND status='pending')
               OR (custom_id='completed' AND status='completed'))
    ) <> 2 THEN
        RAISE EXCEPTION 'Batch checkpoint upgrade changed legacy item state or input';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM deltallm_batch_completion_outbox
        WHERE completion_id='migration-selector-legacy-event'
          AND batch_id='migration-selector-job'
          AND item_id='migration-selector-completed'
          AND status='queued'
          AND payload_json='{"request_id":"legacy-batch-request"}'::jsonb
    ) THEN
        RAISE EXCEPTION 'Batch checkpoint upgrade changed legacy completion billing identity';
    END IF;
END $$;
