DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='deltallm_batch_item'::regclass
          AND conname='deltallm_batch_selector_checkpoint_bound'
          AND NOT convalidated
    ) THEN
        RAISE EXCEPTION 'Batch checkpoint installation must commit before validation';
    END IF;
    BEGIN
        UPDATE deltallm_batch_item SET selector_checkpoint='{}'::jsonb
        WHERE item_id='migration-selector-pending';
        RAISE EXCEPTION 'Unvalidated checkpoint constraint accepted an invalid write';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;
