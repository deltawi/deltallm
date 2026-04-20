-- Legacy release data seed for PR 3 upgrade-path validation.
-- Source tag: v0.1.19
-- Source commit: 44e5ee26
--
-- Deterministic application rows only. This fixture intentionally does not seed
-- _prisma_migrations so Scenario B exercises the legacy baseline helper.

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

INSERT INTO public.deltallm_batch_file (
    file_id,
    purpose,
    filename,
    bytes,
    status,
    storage_backend,
    storage_key,
    checksum,
    created_by_api_key,
    created_at
) VALUES (
    '00000000-0000-0000-0000-000000000191',
    'batch',
    'legacy-v019-input.jsonl',
    48,
    'processed',
    'local',
    'legacy/v019/input.jsonl',
    'legacy-v019-checksum',
    'legacy-key',
    '2026-04-01 10:00:00+00'
);

INSERT INTO public.deltallm_batch_job (
    batch_id,
    endpoint,
    status,
    input_file_id,
    model,
    metadata,
    total_items,
    status_last_updated_at,
    created_by_api_key,
    created_at
) VALUES (
    '00000000-0000-0000-0000-000000000192',
    '/v1/embeddings',
    'queued',
    '00000000-0000-0000-0000-000000000191',
    'text-embedding-3-small',
    '{"scenario":"legacy_v0_1_19_baseline","source_tag":"v0.1.19"}',
    1,
    '2026-04-01 10:00:00+00',
    'legacy-key',
    '2026-04-01 10:00:00+00'
);

INSERT INTO public.deltallm_batch_item (
    item_id,
    batch_id,
    line_number,
    custom_id,
    status,
    request_body,
    created_at
) VALUES (
    '00000000-0000-0000-0000-000000000193',
    '00000000-0000-0000-0000-000000000192',
    1,
    'legacy-v019-item-1',
    'completed',
    '{"model":"text-embedding-3-small","input":"legacy fixture"}',
    '2026-04-01 10:01:00+00'
);

INSERT INTO public.deltallm_batch_completion_outbox (
    completion_id,
    batch_id,
    item_id,
    payload_json,
    status,
    attempt_count,
    max_attempts,
    next_attempt_at,
    created_at,
    updated_at
) VALUES (
    '00000000-0000-0000-0000-000000000194',
    '00000000-0000-0000-0000-000000000192',
    '00000000-0000-0000-0000-000000000193',
    '{"batch_id":"00000000-0000-0000-0000-000000000192","item_id":"00000000-0000-0000-0000-000000000193","source_tag":"v0.1.19"}',
    'queued',
    0,
    5,
    '2026-04-01 10:02:00+00',
    '2026-04-01 10:02:00+00',
    '2026-04-01 10:02:00+00'
);
