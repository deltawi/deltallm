-- Deliberately separate from installation: the history scan must not retain
-- the ADD COLUMN / ADD CONSTRAINT ACCESS EXCLUSIVE lock.
SET lock_timeout = '5s';
SET statement_timeout = '30s';

ALTER TABLE deltallm_batch_item
    VALIDATE CONSTRAINT deltallm_batch_selector_checkpoint_bound;

RESET statement_timeout;
RESET lock_timeout;
