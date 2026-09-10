-- Run against the supported pre-PR-6 schema; never assumes the new column exists.
INSERT INTO deltallm_batch_file
    (file_id,purpose,filename,bytes,storage_key)
VALUES ('migration-selector-file','batch','fixture.jsonl',2,'migration/selector.jsonl');

INSERT INTO deltallm_batch_job
    (batch_id,endpoint,status,input_file_id,total_items)
VALUES ('migration-selector-job','/v1/chat/completions','in_progress','migration-selector-file',2);

INSERT INTO deltallm_batch_item
    (item_id,batch_id,line_number,custom_id,status,request_body)
VALUES
    ('migration-selector-pending','migration-selector-job',1,'pending','pending','{"model":"fixture"}'),
    ('migration-selector-completed','migration-selector-job',2,'completed','completed','{"model":"fixture"}');

INSERT INTO deltallm_batch_completion_outbox
    (completion_id,batch_id,item_id,payload_json,status,next_attempt_at,updated_at)
VALUES
    ('migration-selector-legacy-event','migration-selector-job','migration-selector-completed',
     '{"request_id":"legacy-batch-request"}','queued',NOW(),NOW());
