-- Previous release migration history seed for PR 3 upgrade-path validation.
-- Source tag: v0.1.20-rc2
-- Source commit: 5df60c85
--
-- PostgreSQL database dump
--

-- Dumped from database version 15.17
-- Dumped by pg_dump version 15.17

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

--
-- Data for Name: _prisma_migrations; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('a32fb105-3115-4bf3-9bab-591dcb47f904', 'ae22450854027c728a62f0d413926e7fea70e1a135f9f3441ab4ecbd44d73848', '2026-04-19 16:56:46.780714+00', '20260325150000_platform_invitations_and_email_tokens', NULL, NULL, '2026-04-19 16:56:46.76252+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('98ab0d5e-6e5d-459b-8e31-647dc5e812c7', 'c94c9dc516b76230968eca61ee36017933ebbb888e93369f750e7b3cdac26236', '2026-04-19 16:56:46.533196+00', '20260301090000_core_schema_baseline', NULL, NULL, '2026-04-19 16:56:46.432743+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('87903763-e21d-4479-a7eb-4cdcbf9cbafc', '1c7ffc551b2121a4af57489d92b225540de8370fde315cc723fff772ed195c6f', '2026-04-19 16:56:46.71127+00', '20260311184500_mcp_foundation', NULL, NULL, '2026-04-19 16:56:46.689292+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('da33e588-234f-4e88-9d17-dace7d021b07', 'bc907c7bab2c0ceeb614f3e40c77d4f83be48c0be9867b7b482a04e519905bc2', '2026-04-19 16:56:46.553022+00', '20260302133000_audit_log_foundation', NULL, NULL, '2026-04-19 16:56:46.534622+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('7c59975a-1086-418f-85cc-dca1b9bfd274', '55a9a0946bab36ede2acfc72f2549b3f65a8e2506ccab577c472b96a4968f540', '2026-04-19 16:56:46.564872+00', '20260304123000_audit_id_defaults', NULL, NULL, '2026-04-19 16:56:46.554149+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('8d028886-a3f5-41bd-a7a0-3b953194adfc', 'd2dbc9e53d9f0a697967a4f788538af9426c80e79ab8468b84a9b1af4f68f28c', '2026-04-19 16:56:46.572735+00', '20260304141000_rbac_account_cascade', NULL, NULL, '2026-04-19 16:56:46.566186+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('84d97b7b-e1ca-41e8-ae46-9f83db531864', '4faab1a65bf6317d5e999f36347bb9490b7fd297df02703ed1ddb9caa66639b9', '2026-04-19 16:56:46.722843+00', '20260311195500_mcp_approval_requests', NULL, NULL, '2026-04-19 16:56:46.71239+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('d887d6b8-d5ba-4ae1-8d45-c554e2c05f50', '128deda4c866e6b3933cc4a0628ec36fcaac3e71a0a95e57d8b572db93eee8b4', '2026-04-19 16:56:46.584865+00', '20260304223000_audit_spend_perf_indexes', NULL, NULL, '2026-04-19 16:56:46.573837+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('08419938-bed4-4c15-8ef2-368180463b00', '4403a2fcd6f7d256f523ddd0ff7baa7a3c15560b3aca21579a6b35c0213d9265', '2026-04-19 16:56:46.599654+00', '20260305162000_route_groups_foundation', NULL, NULL, '2026-04-19 16:56:46.585978+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('65256eac-0d79-4c81-a4a1-9dec955b12a8', '32149dbb613836b453f268be2f900ca26880f3341bf1a8f05296f94bf5531e59', '2026-04-19 16:56:46.821843+00', '20260404103000_named_credentials_foundation', NULL, NULL, '2026-04-19 16:56:46.811206+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('06424ca8-f129-40de-b983-48537fa615b4', 'b9060ffd59d13706ed0887e13df9cfedb8b2cd5a6f33920b10e6438ff47a848f', '2026-04-19 16:56:46.631343+00', '20260305213000_prompt_registry_foundation', NULL, NULL, '2026-04-19 16:56:46.600888+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('9f28d384-dc58-493e-a19e-15226d24fa50', '5e980086f88a877a2068254103c200cd5b957ee92f76b928d1e443b792645463', '2026-04-19 16:56:46.728737+00', '20260314103000_mcp_server_ownership', NULL, NULL, '2026-04-19 16:56:46.723974+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('300384d2-df15-47cc-b90d-9c0dc202bc39', 'e75dfa43b42b50270cb7f20e64ea488ca63a4be0570171260ae64712ca403c76', '2026-04-19 16:56:46.635329+00', '20260306110000_prompt_route_preferences', NULL, NULL, '2026-04-19 16:56:46.63246+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('b14dbb57-5cfe-4796-bac6-7cfe3125c289', '0eaf9de9e885cfc7d96aad78067c66f217e22c0fc3ec3ac059b7e21ff5e5bd45', '2026-04-19 16:56:46.649069+00', '20260309120000_key_ownership_service_accounts', NULL, NULL, '2026-04-19 16:56:46.636451+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('5447318a-295a-4a9f-800d-8938a38e96d9', '33a83f9245735b5523c10778892c49d3207b1a0f6b39a181f9219cff5d13ee81', '2026-04-19 16:56:46.79498+00', '20260325190000_email_feedback_resend_suppressions', NULL, NULL, '2026-04-19 16:56:46.781899+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('7d925c85-e9f6-40c1-a18d-4eb1281abcd4', '27cda3baab5bfcc641b4c692a55cb26ee68d0f4eec53499e2e94e15437356642', '2026-04-19 16:56:46.653669+00', '20260310113000_spendlog_cached_tokens', NULL, NULL, '2026-04-19 16:56:46.650449+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('fee9481e-3249-4354-bada-97daa1d97be7', 'b8870b1a9bc955f23ecb15cb91354a22e103ded114253aeb373457a610ff8340', '2026-04-19 16:56:46.735407+00', '20260314124500_mcp_approval_runtime', NULL, NULL, '2026-04-19 16:56:46.729938+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('415bd70f-c986-407f-bb3e-d5945a9ebd87', '2e557eb2b4c1e586477fe5acfe292cd07251f4f3e3464f28208b630eb9482a22', '2026-04-19 16:56:46.662908+00', '20260310170000_admin_reporting_perf_indexes', NULL, NULL, '2026-04-19 16:56:46.65484+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('dd7fd3d7-b7c7-4f31-9101-eb76ae6e69df', '54324a09f5499c2751dd88d78b0358237c4c23e574fd50211e76e97868f92657', '2026-04-19 16:56:46.681313+00', '20260311113000_unified_spend_events', NULL, NULL, '2026-04-19 16:56:46.663851+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('693ee831-dfda-4353-9d3b-b8321f4c1491', '30ade36182790813a45ba2337c21ea189afaed0448c2ae22de2bcea4628e0ac4', '2026-04-19 16:56:46.68797+00', '20260311153000_drop_legacy_spendlogs', NULL, NULL, '2026-04-19 16:56:46.682272+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('21019d93-1198-4348-8297-747ed06cd312', '077ada3c22c36b0dfe80753901293b368fbedfb32af2bd944c5c27c9f584e87e', '2026-04-19 16:56:46.73985+00', '20260321_add_model_rate_limits', NULL, NULL, '2026-04-19 16:56:46.736447+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('750b3bf7-7570-42e4-9b79-d2950372888b', '6c951f965b871957845505c78ebd526ae6b5bb2346e30d5c8126d565a98ad639', '2026-04-19 16:56:46.744606+00', '20260321_add_multi_window_rate_limits', NULL, NULL, '2026-04-19 16:56:46.740903+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('92eb77df-31f2-4643-8eb9-0f0e91d2a9d9', '69bd01750f98e43d0956e028b70098505dc3447ecb6773b80224f704c2f28803', '2026-04-19 16:56:46.801237+00', '20260325193000_email_outbox_provider_message_index', NULL, NULL, '2026-04-19 16:56:46.796374+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('edd7855d-7e6a-455f-9c37-e4e7c4d15ceb', '21a63960084c6a16b0a44a1dbb0e27b120878208a1a674132cc72f4bcdb49aee', '2026-04-19 16:56:46.749595+00', '20260321_self_service_key_policy', NULL, NULL, '2026-04-19 16:56:46.745739+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('39b4486d-c187-4ad0-85db-1fe77a3d95a4', 'b9ece0d209acf72fcfccb6b5d24e4493d811c0ce45a2cdecfbfe99d85826dc36', '2026-04-19 16:56:46.761504+00', '20260325120000_email_outbox_foundation', NULL, NULL, '2026-04-19 16:56:46.750836+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('e2b57258-cad4-44bf-a0c7-3fb8424bbe04', '8049861bdfaf7eba1b01a8c97e9f1762b50b3bd406b552c374e7e3a9df44c205', '2026-04-19 16:56:46.833204+00', '20260410183000_batch_completion_outbox', NULL, NULL, '2026-04-19 16:56:46.822995+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('08a1d2ec-2ac5-46c4-882f-9423311d3373', 'e9d93c6ce1c6e663daa542f5ece0c14f3af551c898a0b041f1ad9118ada256c8', '2026-04-19 16:56:46.805616+00', '20260325203000_org_soft_budget', NULL, NULL, '2026-04-19 16:56:46.802427+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('1d58bf1e-3aa2-4f3a-bba8-f2f396bae9fd', '91c6f89ab4a03b66b73a6f628622003c2ee3f09a0f0df6ce90a485b37dc039bc', '2026-04-19 16:56:46.810147+00', '20260328153000_request_log_failures', NULL, NULL, '2026-04-19 16:56:46.806646+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('81fe1c15-4342-478d-b848-36c24dd9196c', 'b2998f14368bd2ca7a032d0627a00352c241aae1beeb87007e6be0bb644a0aca', '2026-04-19 16:56:46.862207+00', '20260413153000_batch_create_session_hardening', NULL, NULL, '2026-04-19 16:56:46.857636+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('9b35ab5e-4c14-43fd-8bf6-85491c02122b', '6db61a3a5a691d5e66095194ba5141d823123614f0cc6ffbf1c812e15d1bd570', '2026-04-19 16:56:46.839342+00', '20260411100000_batch_completion_outbox_leases', NULL, NULL, '2026-04-19 16:56:46.834479+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('545e716a-d9a4-4073-b444-cd48ed959d95', '422f48fe028f587d47eaa895f4b0bb762ea6f9539379346f8268b73aeb6fc064', '2026-04-19 16:56:46.894105+00', '20260414103000_batch_job_remove_validating_status', NULL, NULL, '2026-04-19 16:56:46.880699+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('782e10de-fff5-4da6-9845-af2446e58493', 'f1fbade2b188d4739e20a874dd2984e5e1036c4476a8bc251150dc3896894019', '2026-04-19 16:56:46.856332+00', '20260413113000_batch_create_session_foundation', NULL, NULL, '2026-04-19 16:56:46.84033+00', 1);
INSERT INTO "public"."_prisma_migrations" ("id", "checksum", "finished_at", "migration_name", "logs", "rolled_back_at", "started_at", "applied_steps_count") VALUES ('f5046ef8-fb4c-43ae-a95a-20550767c1c2', 'be962e0a0c2af65ceac885430e2c052db3e12be59df9451c4d1e0f8cb5520100', '2026-04-19 16:56:46.8795+00', '20260413190000_batch_job_status_constraint', NULL, NULL, '2026-04-19 16:56:46.86329+00', 1);

--
-- Representative application rows from the previous release fixture.
--

INSERT INTO "public"."deltallm_batch_file" (
    "file_id",
    "purpose",
    "filename",
    "bytes",
    "status",
    "storage_backend",
    "storage_key",
    "checksum",
    "created_by_api_key",
    "created_at"
) VALUES (
    '00000000-0000-0000-0000-000000000201',
    'batch',
    'prev-release-input.jsonl',
    64,
    'processed',
    'local',
    'previous-release/input.jsonl',
    'previous-release-input-checksum',
    'prev-release-key',
    '2026-04-02 09:00:00+00'
);

INSERT INTO "public"."deltallm_batch_file" (
    "file_id",
    "purpose",
    "filename",
    "bytes",
    "status",
    "storage_backend",
    "storage_key",
    "checksum",
    "created_by_api_key",
    "created_at"
) VALUES (
    '00000000-0000-0000-0000-000000000202',
    'batch',
    'prev-release-session.jsonl',
    96,
    'processed',
    'local',
    'previous-release/session.jsonl',
    'previous-release-session-checksum',
    'prev-release-key',
    '2026-04-02 09:05:00+00'
);

INSERT INTO "public"."deltallm_batch_job" (
    "batch_id",
    "endpoint",
    "status",
    "input_file_id",
    "model",
    "metadata",
    "total_items",
    "status_last_updated_at",
    "created_by_api_key",
    "created_at"
) VALUES (
    '00000000-0000-0000-0000-000000000203',
    '/v1/embeddings',
    'finalizing'::"public"."DeltaLLM_BatchJobStatus",
    '00000000-0000-0000-0000-000000000201',
    'text-embedding-3-small',
    '{"scenario":"previous_release_v0_1_20_rc2_upgrade","source_tag":"v0.1.20-rc2"}',
    1,
    '2026-04-02 09:10:00+00',
    'prev-release-key',
    '2026-04-02 09:10:00+00'
);

INSERT INTO "public"."deltallm_batch_item" (
    "item_id",
    "batch_id",
    "line_number",
    "custom_id",
    "status",
    "request_body",
    "created_at"
) VALUES (
    '00000000-0000-0000-0000-000000000204',
    '00000000-0000-0000-0000-000000000203',
    1,
    'prev-release-item-1',
    'completed',
    '{"model":"text-embedding-3-small","input":"previous release fixture"}',
    '2026-04-02 09:11:00+00'
);

INSERT INTO "public"."deltallm_batch_completion_outbox" (
    "completion_id",
    "batch_id",
    "item_id",
    "payload_json",
    "status",
    "attempt_count",
    "max_attempts",
    "next_attempt_at",
    "created_at",
    "updated_at"
) VALUES (
    '00000000-0000-0000-0000-000000000205',
    '00000000-0000-0000-0000-000000000203',
    '00000000-0000-0000-0000-000000000204',
    '{"batch_id":"00000000-0000-0000-0000-000000000203","item_id":"00000000-0000-0000-0000-000000000204","source_tag":"v0.1.20-rc2"}',
    'queued',
    0,
    5,
    '2026-04-02 09:12:00+00',
    '2026-04-02 09:12:00+00',
    '2026-04-02 09:12:00+00'
);

INSERT INTO "public"."deltallm_batch_create_session" (
    "session_id",
    "target_batch_id",
    "status",
    "endpoint",
    "input_file_id",
    "staged_storage_backend",
    "staged_storage_key",
    "staged_checksum",
    "staged_bytes",
    "expected_item_count",
    "inferred_model",
    "metadata",
    "created_by_api_key",
    "created_at",
    "expires_at"
) VALUES (
    '00000000-0000-0000-0000-000000000206',
    'previous-release-create-session-batch',
    'staged',
    '/v1/embeddings',
    '00000000-0000-0000-0000-000000000202',
    'local',
    'previous-release/staged.jsonl',
    'previous-release-stage-checksum',
    96,
    1,
    'text-embedding-3-small',
    '{"scenario":"previous_release_v0_1_20_rc2_upgrade","surface":"create_session"}',
    'prev-release-key',
    '2026-04-02 09:15:00+00',
    '2026-04-02 10:15:00+00'
);


--
-- PostgreSQL database dump complete
--
