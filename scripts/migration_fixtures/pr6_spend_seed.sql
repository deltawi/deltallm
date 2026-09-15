INSERT INTO deltallm_spend_ingestion_outbox(event_id,event_type,payload_json,status)
VALUES ('pr6-upgrade-queued','spend','{"cost_exact":"0.125","model":"upgrade"}','queued'),
       ('pr6-upgrade-blocked','spend','{"cost_exact":"0.25","model":"upgrade"}','blocked');
UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count+2 WHERE queue_name='spend';
