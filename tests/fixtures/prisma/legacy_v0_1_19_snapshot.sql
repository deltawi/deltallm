-- Legacy release schema snapshot for PR 3 upgrade-path validation.
-- Source tag: v0.1.19
-- Source commit: 44e5ee26
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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: deltallm_auditevent; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_auditevent (
    event_id text NOT NULL,
    occurred_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    organization_id text,
    actor_type text,
    actor_id text,
    api_key text,
    action text NOT NULL,
    resource_type text,
    resource_id text,
    request_id text,
    correlation_id text,
    ip text,
    user_agent text,
    status text,
    latency_ms integer,
    input_tokens integer,
    output_tokens integer,
    error_type text,
    error_code text,
    metadata jsonb,
    content_stored boolean DEFAULT false NOT NULL,
    prev_hash text,
    event_hash text
);


--
-- Name: deltallm_auditpayload; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_auditpayload (
    payload_id text NOT NULL,
    event_id text NOT NULL,
    kind text NOT NULL,
    storage_mode text DEFAULT 'inline'::text NOT NULL,
    content_json jsonb,
    storage_uri text,
    content_sha256 text,
    size_bytes integer,
    redacted boolean DEFAULT false NOT NULL,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: deltallm_batch_completion_outbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_batch_completion_outbox (
    completion_id text NOT NULL,
    batch_id text NOT NULL,
    item_id text NOT NULL,
    payload_json jsonb NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    next_attempt_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_error text,
    locked_by text,
    lease_expires_at timestamp(3) without time zone,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL,
    processed_at timestamp(3) without time zone
);


--
-- Name: deltallm_batch_file; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_batch_file (
    file_id text NOT NULL,
    purpose text NOT NULL,
    filename text NOT NULL,
    bytes integer NOT NULL,
    status text DEFAULT 'processed'::text NOT NULL,
    storage_backend text DEFAULT 'local'::text NOT NULL,
    storage_key text NOT NULL,
    checksum text,
    created_by_api_key text,
    created_by_user_id text,
    created_by_team_id text,
    created_by_organization_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    expires_at timestamp(3) without time zone
);


--
-- Name: deltallm_batch_item; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_batch_item (
    item_id text NOT NULL,
    batch_id text NOT NULL,
    line_number integer NOT NULL,
    custom_id text NOT NULL,
    status text NOT NULL,
    request_body jsonb NOT NULL,
    response_body jsonb,
    error_body jsonb,
    usage jsonb,
    provider_cost double precision DEFAULT 0 NOT NULL,
    billed_cost double precision DEFAULT 0 NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    locked_by text,
    lease_expires_at timestamp(3) without time zone,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    started_at timestamp(3) without time zone,
    completed_at timestamp(3) without time zone
);


--
-- Name: deltallm_batch_job; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_batch_job (
    batch_id text NOT NULL,
    endpoint text NOT NULL,
    status text NOT NULL,
    execution_mode text DEFAULT 'managed_internal'::text NOT NULL,
    input_file_id text NOT NULL,
    output_file_id text,
    error_file_id text,
    model text,
    metadata jsonb,
    provider_batch_id text,
    provider_status text,
    provider_error text,
    provider_last_sync_at timestamp(3) without time zone,
    total_items integer DEFAULT 0 NOT NULL,
    in_progress_items integer DEFAULT 0 NOT NULL,
    completed_items integer DEFAULT 0 NOT NULL,
    failed_items integer DEFAULT 0 NOT NULL,
    cancelled_items integer DEFAULT 0 NOT NULL,
    locked_by text,
    lease_expires_at timestamp(3) without time zone,
    cancel_requested_at timestamp(3) without time zone,
    status_last_updated_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_by_api_key text,
    created_by_user_id text,
    created_by_team_id text,
    created_by_organization_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    started_at timestamp(3) without time zone,
    completed_at timestamp(3) without time zone,
    expires_at timestamp(3) without time zone
);


--
-- Name: deltallm_callabletargetbinding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_callabletargetbinding (
    callable_target_binding_id text NOT NULL,
    callable_key text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_callabletargetscopepolicy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_callabletargetscopepolicy (
    callable_target_scope_policy_id text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    mode text DEFAULT 'inherit'::text NOT NULL,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_config (
    config_name text NOT NULL,
    config_value text NOT NULL,
    updated_by text,
    updated_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: deltallm_emailoutbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_emailoutbox (
    email_id text NOT NULL,
    kind text NOT NULL,
    provider text NOT NULL,
    to_addresses text[],
    cc_addresses text[],
    bcc_addresses text[],
    from_address text NOT NULL,
    reply_to text,
    template_key text,
    payload_json jsonb,
    subject text NOT NULL,
    text_body text NOT NULL,
    html_body text,
    status text DEFAULT 'queued'::text NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    next_attempt_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_error text,
    last_provider_message_id text,
    created_by_account_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL,
    sent_at timestamp(3) without time zone
);


--
-- Name: deltallm_emailsuppression; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_emailsuppression (
    email_address text NOT NULL,
    provider text NOT NULL,
    reason text NOT NULL,
    source text NOT NULL,
    provider_message_id text,
    webhook_event_id text,
    metadata jsonb,
    first_seen_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_seen_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_emailtoken; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_emailtoken (
    token_id text NOT NULL,
    purpose text NOT NULL,
    token_hash text NOT NULL,
    account_id text NOT NULL,
    email text NOT NULL,
    invitation_id text,
    expires_at timestamp(3) without time zone NOT NULL,
    consumed_at timestamp(3) without time zone,
    created_by_account_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_emailwebhookevent; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_emailwebhookevent (
    webhook_event_id text NOT NULL,
    provider text NOT NULL,
    event_type text NOT NULL,
    recipient_address text,
    provider_message_id text,
    email_id text,
    payload_json jsonb,
    occurred_at timestamp(3) without time zone,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_mcpapprovalrequest; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_mcpapprovalrequest (
    mcp_approval_request_id text NOT NULL,
    mcp_server_id text NOT NULL,
    tool_name text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    request_fingerprint text NOT NULL,
    requested_by_api_key text,
    requested_by_user text,
    organization_id text,
    request_id text,
    correlation_id text,
    arguments_json jsonb NOT NULL,
    decision_comment text,
    decided_by_account_id text,
    decided_at timestamp(3) without time zone,
    expires_at timestamp(3) without time zone,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_mcpbinding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_mcpbinding (
    mcp_binding_id text NOT NULL,
    mcp_server_id text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    tool_allowlist text[],
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_mcpscopepolicy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_mcpscopepolicy (
    mcp_scope_policy_id text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    mode text DEFAULT 'inherit'::text NOT NULL,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_mcpserver; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_mcpserver (
    mcp_server_id text NOT NULL,
    server_key text NOT NULL,
    name text NOT NULL,
    description text,
    owner_scope_type text DEFAULT 'global'::text NOT NULL,
    owner_scope_id text,
    transport text DEFAULT 'streamable_http'::text NOT NULL,
    base_url text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    auth_mode text DEFAULT 'none'::text NOT NULL,
    auth_config jsonb,
    forwarded_headers_allowlist text[],
    request_timeout_ms integer DEFAULT 30000 NOT NULL,
    capabilities_json jsonb,
    capabilities_etag text,
    capabilities_fetched_at timestamp(3) without time zone,
    last_health_status text,
    last_health_error text,
    last_health_at timestamp(3) without time zone,
    last_health_latency_ms integer,
    metadata jsonb,
    created_by_account_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_mcptoolpolicy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_mcptoolpolicy (
    mcp_tool_policy_id text NOT NULL,
    mcp_server_id text NOT NULL,
    tool_name text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    require_approval text,
    max_rpm integer,
    max_concurrency integer,
    result_cache_ttl_seconds integer,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_modeldeployment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_modeldeployment (
    deployment_id text NOT NULL,
    model_name text NOT NULL,
    named_credential_id text,
    deltallm_params jsonb NOT NULL,
    model_info jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_namedcredential; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_namedcredential (
    credential_id text NOT NULL,
    name text NOT NULL,
    provider text NOT NULL,
    connection_config jsonb NOT NULL,
    metadata jsonb,
    created_by_account_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_organizationmembership; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_organizationmembership (
    membership_id text NOT NULL,
    account_id text NOT NULL,
    organization_id text NOT NULL,
    role text DEFAULT 'org_member'::text NOT NULL,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_organizationtable; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_organizationtable (
    id text NOT NULL,
    organization_id text NOT NULL,
    organization_name text,
    audit_content_storage_enabled boolean DEFAULT false NOT NULL,
    max_budget double precision,
    soft_budget double precision,
    rpm_limit integer,
    tpm_limit integer,
    rph_limit integer,
    rpd_limit integer,
    tpd_limit integer,
    model_rpm_limit jsonb,
    model_tpm_limit jsonb,
    spend double precision DEFAULT 0 NOT NULL,
    budget_duration text,
    budget_reset_at timestamp(3) without time zone,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_platformaccount; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_platformaccount (
    account_id text NOT NULL,
    email text NOT NULL,
    password_hash text,
    role text DEFAULT 'org_user'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    force_password_change boolean DEFAULT false NOT NULL,
    mfa_enabled boolean DEFAULT false NOT NULL,
    mfa_secret text,
    mfa_pending_secret text,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL,
    last_login_at timestamp(3) without time zone
);


--
-- Name: deltallm_platformidentity; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_platformidentity (
    identity_id text NOT NULL,
    account_id text NOT NULL,
    provider text NOT NULL,
    subject text NOT NULL,
    email text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_platforminvitation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_platforminvitation (
    invitation_id text NOT NULL,
    account_id text NOT NULL,
    email text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    invite_scope_type text NOT NULL,
    invited_by_account_id text,
    message_email_id text,
    expires_at timestamp(3) without time zone NOT NULL,
    accepted_at timestamp(3) without time zone,
    cancelled_at timestamp(3) without time zone,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_platformsession; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_platformsession (
    session_id text NOT NULL,
    account_id text NOT NULL,
    session_token_hash text NOT NULL,
    mfa_verified boolean DEFAULT false NOT NULL,
    expires_at timestamp(3) without time zone NOT NULL,
    revoked_at timestamp(3) without time zone,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL,
    last_seen_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: deltallm_promptbinding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_promptbinding (
    prompt_binding_id text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    prompt_template_id text NOT NULL,
    label text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_promptlabel; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_promptlabel (
    prompt_label_id text NOT NULL,
    prompt_template_id text NOT NULL,
    label text NOT NULL,
    prompt_version_id text NOT NULL,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_promptrenderlog; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_promptrenderlog (
    prompt_render_log_id text NOT NULL,
    request_id text,
    api_key text,
    user_id text,
    team_id text,
    organization_id text,
    route_group_key text,
    model text,
    prompt_template_id text,
    prompt_version_id text,
    prompt_key text,
    label text,
    status text NOT NULL,
    latency_ms integer,
    error_code text,
    error_message text,
    variables jsonb,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: deltallm_prompttemplate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_prompttemplate (
    prompt_template_id text NOT NULL,
    template_key text NOT NULL,
    name text NOT NULL,
    description text,
    owner_scope text,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_promptversion; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_promptversion (
    prompt_version_id text NOT NULL,
    prompt_template_id text NOT NULL,
    version integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    template_body jsonb NOT NULL,
    variables_schema jsonb,
    model_hints jsonb,
    route_preferences jsonb,
    published_at timestamp(3) without time zone,
    published_by text,
    archived_at timestamp(3) without time zone,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_routegroup; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_routegroup (
    route_group_id text NOT NULL,
    group_key text NOT NULL,
    name text,
    mode text DEFAULT 'chat'::text NOT NULL,
    routing_strategy text,
    enabled boolean DEFAULT true NOT NULL,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_routegroupbinding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_routegroupbinding (
    route_group_binding_id text NOT NULL,
    route_group_id text NOT NULL,
    scope_type text NOT NULL,
    scope_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_routegroupmember; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_routegroupmember (
    membership_id text NOT NULL,
    route_group_id text NOT NULL,
    deployment_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    weight integer,
    priority integer,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_routepolicy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_routepolicy (
    route_policy_id text NOT NULL,
    route_group_id text NOT NULL,
    version integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    policy_json jsonb NOT NULL,
    published_at timestamp(3) without time zone,
    published_by text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_serviceaccount; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_serviceaccount (
    service_account_id text NOT NULL,
    team_id text NOT NULL,
    name text NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL,
    metadata jsonb,
    created_by_account_id text,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_spendlog_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_spendlog_events (
    id text NOT NULL,
    request_id text NOT NULL,
    call_type text NOT NULL,
    api_key text NOT NULL,
    user_id text,
    team_id text,
    organization_id text,
    end_user_id text,
    model text NOT NULL,
    deployment_model text,
    provider text,
    api_base text,
    spend double precision NOT NULL,
    provider_cost double precision,
    billing_unit text,
    pricing_tier text,
    total_tokens integer DEFAULT 0 NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    cached_input_tokens integer DEFAULT 0 NOT NULL,
    cached_output_tokens integer DEFAULT 0 NOT NULL,
    input_audio_tokens integer DEFAULT 0 NOT NULL,
    output_audio_tokens integer DEFAULT 0 NOT NULL,
    input_characters integer DEFAULT 0 NOT NULL,
    output_characters integer DEFAULT 0 NOT NULL,
    duration_seconds double precision DEFAULT 0 NOT NULL,
    image_count integer DEFAULT 0 NOT NULL,
    rerank_units integer DEFAULT 0 NOT NULL,
    start_time timestamp(3) without time zone NOT NULL,
    end_time timestamp(3) without time zone NOT NULL,
    latency_ms integer,
    cache_hit boolean DEFAULT false NOT NULL,
    cache_key text,
    request_tags text[],
    unpriced_reason text,
    pricing_fields_used jsonb,
    usage_snapshot jsonb,
    metadata jsonb,
    status text DEFAULT 'success'::text NOT NULL,
    http_status_code integer,
    error_type text
);


--
-- Name: deltallm_teammembership; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_teammembership (
    membership_id text NOT NULL,
    account_id text NOT NULL,
    team_id text NOT NULL,
    role text DEFAULT 'team_viewer'::text NOT NULL,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_teammodelspend; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_teammodelspend (
    team_id text NOT NULL,
    model text NOT NULL,
    spend double precision DEFAULT 0 NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_teamtable; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_teamtable (
    team_id text NOT NULL,
    team_alias text,
    organization_id text,
    max_budget double precision,
    soft_budget double precision,
    spend double precision DEFAULT 0 NOT NULL,
    budget_duration text,
    budget_reset_at timestamp(3) without time zone,
    model_max_budget jsonb,
    model_rpm_limit jsonb,
    model_tpm_limit jsonb,
    tpm_limit integer,
    rpm_limit integer,
    rph_limit integer,
    rpd_limit integer,
    tpd_limit integer,
    models text[],
    blocked boolean DEFAULT false NOT NULL,
    metadata jsonb,
    self_service_keys_enabled boolean DEFAULT false NOT NULL,
    self_service_max_keys_per_user integer,
    self_service_budget_ceiling double precision,
    self_service_require_expiry boolean DEFAULT false NOT NULL,
    self_service_max_expiry_days integer,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_usertable; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_usertable (
    user_id text NOT NULL,
    user_email text,
    user_role text DEFAULT 'internal_user'::text NOT NULL,
    max_budget double precision,
    soft_budget double precision,
    spend double precision DEFAULT 0 NOT NULL,
    budget_duration text,
    budget_reset_at timestamp(3) without time zone,
    models text[],
    tpm_limit integer,
    rpm_limit integer,
    rph_limit integer,
    rpd_limit integer,
    tpd_limit integer,
    team_id text,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_verificationtoken; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deltallm_verificationtoken (
    id text NOT NULL,
    token text NOT NULL,
    key_name text,
    user_id text,
    team_id text,
    owner_account_id text,
    owner_service_account_id text,
    models text[],
    max_budget double precision,
    soft_budget double precision,
    spend double precision DEFAULT 0 NOT NULL,
    budget_duration text,
    budget_reset_at timestamp(3) without time zone,
    rpm_limit integer,
    tpm_limit integer,
    rph_limit integer,
    rpd_limit integer,
    tpd_limit integer,
    max_parallel_requests integer,
    expires timestamp(3) without time zone,
    permissions jsonb,
    metadata jsonb,
    created_at timestamp(3) without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp(3) without time zone NOT NULL
);


--
-- Name: deltallm_auditevent deltallm_auditevent_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_auditevent
    ADD CONSTRAINT deltallm_auditevent_pkey PRIMARY KEY (event_id);


--
-- Name: deltallm_auditpayload deltallm_auditpayload_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_auditpayload
    ADD CONSTRAINT deltallm_auditpayload_pkey PRIMARY KEY (payload_id);


--
-- Name: deltallm_batch_completion_outbox deltallm_batch_completion_outbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_completion_outbox
    ADD CONSTRAINT deltallm_batch_completion_outbox_pkey PRIMARY KEY (completion_id);


--
-- Name: deltallm_batch_file deltallm_batch_file_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_file
    ADD CONSTRAINT deltallm_batch_file_pkey PRIMARY KEY (file_id);


--
-- Name: deltallm_batch_item deltallm_batch_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_item
    ADD CONSTRAINT deltallm_batch_item_pkey PRIMARY KEY (item_id);


--
-- Name: deltallm_batch_job deltallm_batch_job_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_job
    ADD CONSTRAINT deltallm_batch_job_pkey PRIMARY KEY (batch_id);


--
-- Name: deltallm_callabletargetbinding deltallm_callabletargetbinding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_callabletargetbinding
    ADD CONSTRAINT deltallm_callabletargetbinding_pkey PRIMARY KEY (callable_target_binding_id);


--
-- Name: deltallm_callabletargetscopepolicy deltallm_callabletargetscopepolicy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_callabletargetscopepolicy
    ADD CONSTRAINT deltallm_callabletargetscopepolicy_pkey PRIMARY KEY (callable_target_scope_policy_id);


--
-- Name: deltallm_config deltallm_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_config
    ADD CONSTRAINT deltallm_config_pkey PRIMARY KEY (config_name);


--
-- Name: deltallm_emailoutbox deltallm_emailoutbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailoutbox
    ADD CONSTRAINT deltallm_emailoutbox_pkey PRIMARY KEY (email_id);


--
-- Name: deltallm_emailsuppression deltallm_emailsuppression_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailsuppression
    ADD CONSTRAINT deltallm_emailsuppression_pkey PRIMARY KEY (email_address);


--
-- Name: deltallm_emailtoken deltallm_emailtoken_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailtoken
    ADD CONSTRAINT deltallm_emailtoken_pkey PRIMARY KEY (token_id);


--
-- Name: deltallm_emailwebhookevent deltallm_emailwebhookevent_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailwebhookevent
    ADD CONSTRAINT deltallm_emailwebhookevent_pkey PRIMARY KEY (webhook_event_id);


--
-- Name: deltallm_mcpapprovalrequest deltallm_mcpapprovalrequest_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcpapprovalrequest
    ADD CONSTRAINT deltallm_mcpapprovalrequest_pkey PRIMARY KEY (mcp_approval_request_id);


--
-- Name: deltallm_mcpbinding deltallm_mcpbinding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcpbinding
    ADD CONSTRAINT deltallm_mcpbinding_pkey PRIMARY KEY (mcp_binding_id);


--
-- Name: deltallm_mcpscopepolicy deltallm_mcpscopepolicy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcpscopepolicy
    ADD CONSTRAINT deltallm_mcpscopepolicy_pkey PRIMARY KEY (mcp_scope_policy_id);


--
-- Name: deltallm_mcpserver deltallm_mcpserver_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcpserver
    ADD CONSTRAINT deltallm_mcpserver_pkey PRIMARY KEY (mcp_server_id);


--
-- Name: deltallm_mcptoolpolicy deltallm_mcptoolpolicy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcptoolpolicy
    ADD CONSTRAINT deltallm_mcptoolpolicy_pkey PRIMARY KEY (mcp_tool_policy_id);


--
-- Name: deltallm_modeldeployment deltallm_modeldeployment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_modeldeployment
    ADD CONSTRAINT deltallm_modeldeployment_pkey PRIMARY KEY (deployment_id);


--
-- Name: deltallm_namedcredential deltallm_namedcredential_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_namedcredential
    ADD CONSTRAINT deltallm_namedcredential_pkey PRIMARY KEY (credential_id);


--
-- Name: deltallm_organizationmembership deltallm_organizationmembership_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_organizationmembership
    ADD CONSTRAINT deltallm_organizationmembership_pkey PRIMARY KEY (membership_id);


--
-- Name: deltallm_organizationtable deltallm_organizationtable_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_organizationtable
    ADD CONSTRAINT deltallm_organizationtable_pkey PRIMARY KEY (id);


--
-- Name: deltallm_platformaccount deltallm_platformaccount_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platformaccount
    ADD CONSTRAINT deltallm_platformaccount_pkey PRIMARY KEY (account_id);


--
-- Name: deltallm_platformidentity deltallm_platformidentity_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platformidentity
    ADD CONSTRAINT deltallm_platformidentity_pkey PRIMARY KEY (identity_id);


--
-- Name: deltallm_platforminvitation deltallm_platforminvitation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platforminvitation
    ADD CONSTRAINT deltallm_platforminvitation_pkey PRIMARY KEY (invitation_id);


--
-- Name: deltallm_platformsession deltallm_platformsession_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platformsession
    ADD CONSTRAINT deltallm_platformsession_pkey PRIMARY KEY (session_id);


--
-- Name: deltallm_promptbinding deltallm_promptbinding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptbinding
    ADD CONSTRAINT deltallm_promptbinding_pkey PRIMARY KEY (prompt_binding_id);


--
-- Name: deltallm_promptlabel deltallm_promptlabel_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptlabel
    ADD CONSTRAINT deltallm_promptlabel_pkey PRIMARY KEY (prompt_label_id);


--
-- Name: deltallm_promptrenderlog deltallm_promptrenderlog_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptrenderlog
    ADD CONSTRAINT deltallm_promptrenderlog_pkey PRIMARY KEY (prompt_render_log_id);


--
-- Name: deltallm_prompttemplate deltallm_prompttemplate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_prompttemplate
    ADD CONSTRAINT deltallm_prompttemplate_pkey PRIMARY KEY (prompt_template_id);


--
-- Name: deltallm_promptversion deltallm_promptversion_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptversion
    ADD CONSTRAINT deltallm_promptversion_pkey PRIMARY KEY (prompt_version_id);


--
-- Name: deltallm_routegroup deltallm_routegroup_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routegroup
    ADD CONSTRAINT deltallm_routegroup_pkey PRIMARY KEY (route_group_id);


--
-- Name: deltallm_routegroupbinding deltallm_routegroupbinding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routegroupbinding
    ADD CONSTRAINT deltallm_routegroupbinding_pkey PRIMARY KEY (route_group_binding_id);


--
-- Name: deltallm_routegroupmember deltallm_routegroupmember_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routegroupmember
    ADD CONSTRAINT deltallm_routegroupmember_pkey PRIMARY KEY (membership_id);


--
-- Name: deltallm_routepolicy deltallm_routepolicy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routepolicy
    ADD CONSTRAINT deltallm_routepolicy_pkey PRIMARY KEY (route_policy_id);


--
-- Name: deltallm_serviceaccount deltallm_serviceaccount_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_serviceaccount
    ADD CONSTRAINT deltallm_serviceaccount_pkey PRIMARY KEY (service_account_id);


--
-- Name: deltallm_spendlog_events deltallm_spendlog_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_spendlog_events
    ADD CONSTRAINT deltallm_spendlog_events_pkey PRIMARY KEY (id);


--
-- Name: deltallm_teammembership deltallm_teammembership_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_teammembership
    ADD CONSTRAINT deltallm_teammembership_pkey PRIMARY KEY (membership_id);


--
-- Name: deltallm_teammodelspend deltallm_teammodelspend_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_teammodelspend
    ADD CONSTRAINT deltallm_teammodelspend_pkey PRIMARY KEY (team_id, model);


--
-- Name: deltallm_teamtable deltallm_teamtable_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_teamtable
    ADD CONSTRAINT deltallm_teamtable_pkey PRIMARY KEY (team_id);


--
-- Name: deltallm_usertable deltallm_usertable_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_usertable
    ADD CONSTRAINT deltallm_usertable_pkey PRIMARY KEY (user_id);


--
-- Name: deltallm_verificationtoken deltallm_verificationtoken_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_verificationtoken
    ADD CONSTRAINT deltallm_verificationtoken_pkey PRIMARY KEY (id);


--
-- Name: deltallm_auditevent_action_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_action_idx ON public.deltallm_auditevent USING btree (action);


--
-- Name: deltallm_auditevent_action_occurred_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_action_occurred_at_idx ON public.deltallm_auditevent USING btree (action, occurred_at);


--
-- Name: deltallm_auditevent_actor_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_actor_id_idx ON public.deltallm_auditevent USING btree (actor_id);


--
-- Name: deltallm_auditevent_correlation_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_correlation_id_idx ON public.deltallm_auditevent USING btree (correlation_id);


--
-- Name: deltallm_auditevent_occurred_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_occurred_at_idx ON public.deltallm_auditevent USING btree (occurred_at);


--
-- Name: deltallm_auditevent_org_occurred_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_org_occurred_at_idx ON public.deltallm_auditevent USING btree (organization_id, occurred_at);


--
-- Name: deltallm_auditevent_organization_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_organization_id_idx ON public.deltallm_auditevent USING btree (organization_id);


--
-- Name: deltallm_auditevent_request_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_request_id_idx ON public.deltallm_auditevent USING btree (request_id);


--
-- Name: deltallm_auditevent_status_occurred_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditevent_status_occurred_at_idx ON public.deltallm_auditevent USING btree (status, occurred_at);


--
-- Name: deltallm_auditpayload_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditpayload_created_at_idx ON public.deltallm_auditpayload USING btree (created_at);


--
-- Name: deltallm_auditpayload_event_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditpayload_event_id_idx ON public.deltallm_auditpayload USING btree (event_id);


--
-- Name: deltallm_auditpayload_kind_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_auditpayload_kind_idx ON public.deltallm_auditpayload USING btree (kind);


--
-- Name: deltallm_batch_completion_outbox_item_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_batch_completion_outbox_item_id_key ON public.deltallm_batch_completion_outbox USING btree (item_id);


--
-- Name: deltallm_batch_file_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_file_created_at_idx ON public.deltallm_batch_file USING btree (created_at);


--
-- Name: deltallm_batch_file_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_file_expires_at_idx ON public.deltallm_batch_file USING btree (expires_at);


--
-- Name: deltallm_batch_file_purpose_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_file_purpose_idx ON public.deltallm_batch_file USING btree (purpose);


--
-- Name: deltallm_batch_item_batch_id_custom_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_batch_item_batch_id_custom_id_key ON public.deltallm_batch_item USING btree (batch_id, custom_id);


--
-- Name: deltallm_batch_item_batch_id_line_number_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_batch_item_batch_id_line_number_key ON public.deltallm_batch_item USING btree (batch_id, line_number);


--
-- Name: deltallm_batch_item_batch_id_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_item_batch_id_status_idx ON public.deltallm_batch_item USING btree (batch_id, status);


--
-- Name: deltallm_batch_item_lease_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_item_lease_expires_at_idx ON public.deltallm_batch_item USING btree (lease_expires_at);


--
-- Name: deltallm_batch_job_created_by_api_key_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_job_created_by_api_key_created_at_idx ON public.deltallm_batch_job USING btree (created_by_api_key, created_at);


--
-- Name: deltallm_batch_job_created_by_organization_id_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_job_created_by_organization_id_created_at_idx ON public.deltallm_batch_job USING btree (created_by_organization_id, created_at);


--
-- Name: deltallm_batch_job_created_by_team_id_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_job_created_by_team_id_created_at_idx ON public.deltallm_batch_job USING btree (created_by_team_id, created_at);


--
-- Name: deltallm_batch_job_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_job_expires_at_idx ON public.deltallm_batch_job USING btree (expires_at);


--
-- Name: deltallm_batch_job_lease_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_job_lease_expires_at_idx ON public.deltallm_batch_job USING btree (lease_expires_at);


--
-- Name: deltallm_batch_job_status_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batch_job_status_created_at_idx ON public.deltallm_batch_job USING btree (status, created_at);


--
-- Name: deltallm_batchcompletionoutbox_batch_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batchcompletionoutbox_batch_created_idx ON public.deltallm_batch_completion_outbox USING btree (batch_id, created_at);


--
-- Name: deltallm_batchcompletionoutbox_lease_expires_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batchcompletionoutbox_lease_expires_idx ON public.deltallm_batch_completion_outbox USING btree (lease_expires_at);


--
-- Name: deltallm_batchcompletionoutbox_status_next_attempt_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_batchcompletionoutbox_status_next_attempt_idx ON public.deltallm_batch_completion_outbox USING btree (status, next_attempt_at);


--
-- Name: deltallm_callabletargetbinding_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_callabletargetbinding_scope_idx ON public.deltallm_callabletargetbinding USING btree (scope_type, scope_id, enabled);


--
-- Name: deltallm_callabletargetbinding_scope_target_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_callabletargetbinding_scope_target_key ON public.deltallm_callabletargetbinding USING btree (callable_key, scope_type, scope_id);


--
-- Name: deltallm_callabletargetbinding_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_callabletargetbinding_target_idx ON public.deltallm_callabletargetbinding USING btree (callable_key);


--
-- Name: deltallm_callabletargetscopepolicy_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_callabletargetscopepolicy_scope_idx ON public.deltallm_callabletargetscopepolicy USING btree (scope_type, scope_id);


--
-- Name: deltallm_callabletargetscopepolicy_scope_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_callabletargetscopepolicy_scope_key ON public.deltallm_callabletargetscopepolicy USING btree (scope_type, scope_id);


--
-- Name: deltallm_emailoutbox_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailoutbox_created_at_idx ON public.deltallm_emailoutbox USING btree (created_at);


--
-- Name: deltallm_emailoutbox_created_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailoutbox_created_by_idx ON public.deltallm_emailoutbox USING btree (created_by_account_id);


--
-- Name: deltallm_emailoutbox_provider_message_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailoutbox_provider_message_idx ON public.deltallm_emailoutbox USING btree (provider, last_provider_message_id);


--
-- Name: deltallm_emailoutbox_status_next_attempt_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailoutbox_status_next_attempt_idx ON public.deltallm_emailoutbox USING btree (status, next_attempt_at);


--
-- Name: deltallm_emailsuppression_last_seen_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailsuppression_last_seen_idx ON public.deltallm_emailsuppression USING btree (last_seen_at);


--
-- Name: deltallm_emailsuppression_provider_reason_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailsuppression_provider_reason_idx ON public.deltallm_emailsuppression USING btree (provider, reason);


--
-- Name: deltallm_emailtoken_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailtoken_account_idx ON public.deltallm_emailtoken USING btree (account_id);


--
-- Name: deltallm_emailtoken_invitation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailtoken_invitation_idx ON public.deltallm_emailtoken USING btree (invitation_id);


--
-- Name: deltallm_emailtoken_purpose_expires_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailtoken_purpose_expires_idx ON public.deltallm_emailtoken USING btree (purpose, expires_at);


--
-- Name: deltallm_emailtoken_token_hash_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_emailtoken_token_hash_key ON public.deltallm_emailtoken USING btree (token_hash);


--
-- Name: deltallm_emailwebhookevent_occurred_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailwebhookevent_occurred_idx ON public.deltallm_emailwebhookevent USING btree (occurred_at);


--
-- Name: deltallm_emailwebhookevent_provider_msg_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_emailwebhookevent_provider_msg_idx ON public.deltallm_emailwebhookevent USING btree (provider, provider_message_id);


--
-- Name: deltallm_mcpapproval_fingerprint_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpapproval_fingerprint_idx ON public.deltallm_mcpapprovalrequest USING btree (request_fingerprint);


--
-- Name: deltallm_mcpapproval_server_status_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpapproval_server_status_created_idx ON public.deltallm_mcpapprovalrequest USING btree (mcp_server_id, status, created_at);


--
-- Name: deltallm_mcpapproval_status_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpapproval_status_created_idx ON public.deltallm_mcpapprovalrequest USING btree (status, created_at);


--
-- Name: deltallm_mcpbinding_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpbinding_scope_idx ON public.deltallm_mcpbinding USING btree (scope_type, scope_id, enabled);


--
-- Name: deltallm_mcpbinding_server_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpbinding_server_idx ON public.deltallm_mcpbinding USING btree (mcp_server_id);


--
-- Name: deltallm_mcpbinding_server_scope_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_mcpbinding_server_scope_key ON public.deltallm_mcpbinding USING btree (mcp_server_id, scope_type, scope_id);


--
-- Name: deltallm_mcpscopepolicy_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpscopepolicy_scope_idx ON public.deltallm_mcpscopepolicy USING btree (scope_type, scope_id);


--
-- Name: deltallm_mcpscopepolicy_scope_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_mcpscopepolicy_scope_key ON public.deltallm_mcpscopepolicy USING btree (scope_type, scope_id);


--
-- Name: deltallm_mcpserver_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpserver_created_at_idx ON public.deltallm_mcpserver USING btree (created_at);


--
-- Name: deltallm_mcpserver_enabled_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpserver_enabled_idx ON public.deltallm_mcpserver USING btree (enabled);


--
-- Name: deltallm_mcpserver_owner_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcpserver_owner_scope_idx ON public.deltallm_mcpserver USING btree (owner_scope_type, owner_scope_id);


--
-- Name: deltallm_mcpserver_server_key_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_mcpserver_server_key_key ON public.deltallm_mcpserver USING btree (server_key);


--
-- Name: deltallm_mcptoolpolicy_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcptoolpolicy_scope_idx ON public.deltallm_mcptoolpolicy USING btree (scope_type, scope_id, enabled);


--
-- Name: deltallm_mcptoolpolicy_server_tool_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_mcptoolpolicy_server_tool_idx ON public.deltallm_mcptoolpolicy USING btree (mcp_server_id, tool_name);


--
-- Name: deltallm_mcptoolpolicy_server_tool_scope_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_mcptoolpolicy_server_tool_scope_key ON public.deltallm_mcptoolpolicy USING btree (mcp_server_id, tool_name, scope_type, scope_id);


--
-- Name: deltallm_modeldeployment_model_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_modeldeployment_model_name_idx ON public.deltallm_modeldeployment USING btree (model_name);


--
-- Name: deltallm_modeldeployment_named_credential_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_modeldeployment_named_credential_idx ON public.deltallm_modeldeployment USING btree (named_credential_id);


--
-- Name: deltallm_namedcredential_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_namedcredential_created_at_idx ON public.deltallm_namedcredential USING btree (created_at);


--
-- Name: deltallm_namedcredential_name_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_namedcredential_name_key ON public.deltallm_namedcredential USING btree (name);


--
-- Name: deltallm_namedcredential_provider_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_namedcredential_provider_idx ON public.deltallm_namedcredential USING btree (provider);


--
-- Name: deltallm_organizationmembership_account_id_organization_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_organizationmembership_account_id_organization_id_key ON public.deltallm_organizationmembership USING btree (account_id, organization_id);


--
-- Name: deltallm_organizationmembership_organization_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_organizationmembership_organization_id_idx ON public.deltallm_organizationmembership USING btree (organization_id);


--
-- Name: deltallm_organizationtable_organization_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_organizationtable_organization_id_key ON public.deltallm_organizationtable USING btree (organization_id);


--
-- Name: deltallm_platformaccount_email_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_platformaccount_email_key ON public.deltallm_platformaccount USING btree (email);


--
-- Name: deltallm_platformidentity_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_platformidentity_account_id_idx ON public.deltallm_platformidentity USING btree (account_id);


--
-- Name: deltallm_platformidentity_provider_subject_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_platformidentity_provider_subject_key ON public.deltallm_platformidentity USING btree (provider, subject);


--
-- Name: deltallm_platforminvitation_account_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_platforminvitation_account_idx ON public.deltallm_platforminvitation USING btree (account_id);


--
-- Name: deltallm_platforminvitation_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_platforminvitation_email_idx ON public.deltallm_platforminvitation USING btree (email);


--
-- Name: deltallm_platforminvitation_invited_by_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_platforminvitation_invited_by_idx ON public.deltallm_platforminvitation USING btree (invited_by_account_id);


--
-- Name: deltallm_platforminvitation_status_expires_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_platforminvitation_status_expires_idx ON public.deltallm_platforminvitation USING btree (status, expires_at);


--
-- Name: deltallm_platformsession_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_platformsession_account_id_idx ON public.deltallm_platformsession USING btree (account_id);


--
-- Name: deltallm_platformsession_expires_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_platformsession_expires_at_idx ON public.deltallm_platformsession USING btree (expires_at);


--
-- Name: deltallm_platformsession_session_token_hash_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_platformsession_session_token_hash_key ON public.deltallm_platformsession USING btree (session_token_hash);


--
-- Name: deltallm_promptbinding_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_promptbinding_scope_idx ON public.deltallm_promptbinding USING btree (scope_type, scope_id, enabled, priority);


--
-- Name: deltallm_promptbinding_scope_target_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_promptbinding_scope_target_key ON public.deltallm_promptbinding USING btree (scope_type, scope_id, prompt_template_id, label);


--
-- Name: deltallm_promptbinding_template_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_promptbinding_template_idx ON public.deltallm_promptbinding USING btree (prompt_template_id);


--
-- Name: deltallm_promptlabel_template_label_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_promptlabel_template_label_key ON public.deltallm_promptlabel USING btree (prompt_template_id, label);


--
-- Name: deltallm_promptlabel_version_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_promptlabel_version_idx ON public.deltallm_promptlabel USING btree (prompt_version_id);


--
-- Name: deltallm_promptrenderlog_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_promptrenderlog_created_at_idx ON public.deltallm_promptrenderlog USING btree (created_at);


--
-- Name: deltallm_promptrenderlog_prompt_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_promptrenderlog_prompt_lookup_idx ON public.deltallm_promptrenderlog USING btree (prompt_key, label, created_at);


--
-- Name: deltallm_promptrenderlog_request_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_promptrenderlog_request_idx ON public.deltallm_promptrenderlog USING btree (request_id);


--
-- Name: deltallm_prompttemplate_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_prompttemplate_created_at_idx ON public.deltallm_prompttemplate USING btree (created_at);


--
-- Name: deltallm_prompttemplate_template_key_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_prompttemplate_template_key_key ON public.deltallm_prompttemplate USING btree (template_key);


--
-- Name: deltallm_promptversion_template_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_promptversion_template_status_idx ON public.deltallm_promptversion USING btree (prompt_template_id, status);


--
-- Name: deltallm_promptversion_template_version_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_promptversion_template_version_key ON public.deltallm_promptversion USING btree (prompt_template_id, version);


--
-- Name: deltallm_routegroup_enabled_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_routegroup_enabled_idx ON public.deltallm_routegroup USING btree (enabled);


--
-- Name: deltallm_routegroup_group_key_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_routegroup_group_key_key ON public.deltallm_routegroup USING btree (group_key);


--
-- Name: deltallm_routegroupbinding_group_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_routegroupbinding_group_idx ON public.deltallm_routegroupbinding USING btree (route_group_id);


--
-- Name: deltallm_routegroupbinding_scope_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_routegroupbinding_scope_idx ON public.deltallm_routegroupbinding USING btree (scope_type, scope_id, enabled);


--
-- Name: deltallm_routegroupbinding_scope_target_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_routegroupbinding_scope_target_key ON public.deltallm_routegroupbinding USING btree (route_group_id, scope_type, scope_id);


--
-- Name: deltallm_routegroupmember_deployment_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_routegroupmember_deployment_id_idx ON public.deltallm_routegroupmember USING btree (deployment_id);


--
-- Name: deltallm_routegroupmember_route_group_id_deployment_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_routegroupmember_route_group_id_deployment_id_key ON public.deltallm_routegroupmember USING btree (route_group_id, deployment_id);


--
-- Name: deltallm_routepolicy_group_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_routepolicy_group_status_idx ON public.deltallm_routepolicy USING btree (route_group_id, status);


--
-- Name: deltallm_routepolicy_route_group_id_version_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_routepolicy_route_group_id_version_key ON public.deltallm_routepolicy USING btree (route_group_id, version);


--
-- Name: deltallm_serviceaccount_created_by_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_serviceaccount_created_by_account_id_idx ON public.deltallm_serviceaccount USING btree (created_by_account_id);


--
-- Name: deltallm_serviceaccount_team_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_serviceaccount_team_id_idx ON public.deltallm_serviceaccount USING btree (team_id);


--
-- Name: deltallm_serviceaccount_team_id_name_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_serviceaccount_team_id_name_key ON public.deltallm_serviceaccount USING btree (team_id, name);


--
-- Name: deltallm_spendlog_events_api_key_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_api_key_time_idx ON public.deltallm_spendlog_events USING btree (api_key, start_time);


--
-- Name: deltallm_spendlog_events_model_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_model_time_idx ON public.deltallm_spendlog_events USING btree (model, start_time);


--
-- Name: deltallm_spendlog_events_org_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_org_time_idx ON public.deltallm_spendlog_events USING btree (organization_id, start_time);


--
-- Name: deltallm_spendlog_events_provider_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_provider_time_idx ON public.deltallm_spendlog_events USING btree (provider, start_time);


--
-- Name: deltallm_spendlog_events_request_tags_gin_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_request_tags_gin_idx ON public.deltallm_spendlog_events USING gin (request_tags);


--
-- Name: deltallm_spendlog_events_start_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_start_time_idx ON public.deltallm_spendlog_events USING btree (start_time);


--
-- Name: deltallm_spendlog_events_team_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_team_time_idx ON public.deltallm_spendlog_events USING btree (team_id, start_time);


--
-- Name: deltallm_spendlog_events_user_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_spendlog_events_user_time_idx ON public.deltallm_spendlog_events USING btree (user_id, start_time);


--
-- Name: deltallm_teammembership_account_id_team_id_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_teammembership_account_id_team_id_key ON public.deltallm_teammembership USING btree (account_id, team_id);


--
-- Name: deltallm_teammembership_team_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_teammembership_team_id_idx ON public.deltallm_teammembership USING btree (team_id);


--
-- Name: deltallm_teammodelspend_team_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_teammodelspend_team_idx ON public.deltallm_teammodelspend USING btree (team_id);


--
-- Name: deltallm_teamtable_organization_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_teamtable_organization_id_idx ON public.deltallm_teamtable USING btree (organization_id);


--
-- Name: deltallm_usertable_team_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_usertable_team_id_idx ON public.deltallm_usertable USING btree (team_id);


--
-- Name: deltallm_usertable_user_email_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_usertable_user_email_key ON public.deltallm_usertable USING btree (user_email);


--
-- Name: deltallm_verificationtoken_expires_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_verificationtoken_expires_idx ON public.deltallm_verificationtoken USING btree (expires);


--
-- Name: deltallm_verificationtoken_owner_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_verificationtoken_owner_account_id_idx ON public.deltallm_verificationtoken USING btree (owner_account_id);


--
-- Name: deltallm_verificationtoken_owner_service_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_verificationtoken_owner_service_account_id_idx ON public.deltallm_verificationtoken USING btree (owner_service_account_id);


--
-- Name: deltallm_verificationtoken_team_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_verificationtoken_team_id_idx ON public.deltallm_verificationtoken USING btree (team_id);


--
-- Name: deltallm_verificationtoken_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_verificationtoken_token_idx ON public.deltallm_verificationtoken USING btree (token);


--
-- Name: deltallm_verificationtoken_token_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deltallm_verificationtoken_token_key ON public.deltallm_verificationtoken USING btree (token);


--
-- Name: deltallm_verificationtoken_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deltallm_verificationtoken_user_id_idx ON public.deltallm_verificationtoken USING btree (user_id);


--
-- Name: deltallm_auditpayload deltallm_auditpayload_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_auditpayload
    ADD CONSTRAINT deltallm_auditpayload_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.deltallm_auditevent(event_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_batch_item deltallm_batch_item_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_item
    ADD CONSTRAINT deltallm_batch_item_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.deltallm_batch_job(batch_id) ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: deltallm_batch_job deltallm_batch_job_error_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_job
    ADD CONSTRAINT deltallm_batch_job_error_file_id_fkey FOREIGN KEY (error_file_id) REFERENCES public.deltallm_batch_file(file_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_batch_job deltallm_batch_job_input_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_job
    ADD CONSTRAINT deltallm_batch_job_input_file_id_fkey FOREIGN KEY (input_file_id) REFERENCES public.deltallm_batch_file(file_id) ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: deltallm_batch_job deltallm_batch_job_output_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_batch_job
    ADD CONSTRAINT deltallm_batch_job_output_file_id_fkey FOREIGN KEY (output_file_id) REFERENCES public.deltallm_batch_file(file_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_emailoutbox deltallm_emailoutbox_created_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailoutbox
    ADD CONSTRAINT deltallm_emailoutbox_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_emailtoken deltallm_emailtoken_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailtoken
    ADD CONSTRAINT deltallm_emailtoken_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_emailtoken deltallm_emailtoken_created_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailtoken
    ADD CONSTRAINT deltallm_emailtoken_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_emailtoken deltallm_emailtoken_invitation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_emailtoken
    ADD CONSTRAINT deltallm_emailtoken_invitation_id_fkey FOREIGN KEY (invitation_id) REFERENCES public.deltallm_platforminvitation(invitation_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_mcpapprovalrequest deltallm_mcpapprovalrequest_mcp_server_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcpapprovalrequest
    ADD CONSTRAINT deltallm_mcpapprovalrequest_mcp_server_id_fkey FOREIGN KEY (mcp_server_id) REFERENCES public.deltallm_mcpserver(mcp_server_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_mcpbinding deltallm_mcpbinding_mcp_server_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcpbinding
    ADD CONSTRAINT deltallm_mcpbinding_mcp_server_id_fkey FOREIGN KEY (mcp_server_id) REFERENCES public.deltallm_mcpserver(mcp_server_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_mcptoolpolicy deltallm_mcptoolpolicy_mcp_server_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_mcptoolpolicy
    ADD CONSTRAINT deltallm_mcptoolpolicy_mcp_server_id_fkey FOREIGN KEY (mcp_server_id) REFERENCES public.deltallm_mcpserver(mcp_server_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_modeldeployment deltallm_modeldeployment_named_credential_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_modeldeployment
    ADD CONSTRAINT deltallm_modeldeployment_named_credential_id_fkey FOREIGN KEY (named_credential_id) REFERENCES public.deltallm_namedcredential(credential_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_organizationmembership deltallm_organizationmembership_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_organizationmembership
    ADD CONSTRAINT deltallm_organizationmembership_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_platformidentity deltallm_platformidentity_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platformidentity
    ADD CONSTRAINT deltallm_platformidentity_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_platforminvitation deltallm_platforminvitation_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platforminvitation
    ADD CONSTRAINT deltallm_platforminvitation_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_platforminvitation deltallm_platforminvitation_invited_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platforminvitation
    ADD CONSTRAINT deltallm_platforminvitation_invited_by_account_id_fkey FOREIGN KEY (invited_by_account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_platforminvitation deltallm_platforminvitation_message_email_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platforminvitation
    ADD CONSTRAINT deltallm_platforminvitation_message_email_id_fkey FOREIGN KEY (message_email_id) REFERENCES public.deltallm_emailoutbox(email_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_platformsession deltallm_platformsession_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_platformsession
    ADD CONSTRAINT deltallm_platformsession_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_promptbinding deltallm_promptbinding_prompt_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptbinding
    ADD CONSTRAINT deltallm_promptbinding_prompt_template_id_fkey FOREIGN KEY (prompt_template_id) REFERENCES public.deltallm_prompttemplate(prompt_template_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_promptlabel deltallm_promptlabel_prompt_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptlabel
    ADD CONSTRAINT deltallm_promptlabel_prompt_template_id_fkey FOREIGN KEY (prompt_template_id) REFERENCES public.deltallm_prompttemplate(prompt_template_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_promptlabel deltallm_promptlabel_prompt_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptlabel
    ADD CONSTRAINT deltallm_promptlabel_prompt_version_id_fkey FOREIGN KEY (prompt_version_id) REFERENCES public.deltallm_promptversion(prompt_version_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_promptversion deltallm_promptversion_prompt_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_promptversion
    ADD CONSTRAINT deltallm_promptversion_prompt_template_id_fkey FOREIGN KEY (prompt_template_id) REFERENCES public.deltallm_prompttemplate(prompt_template_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_routegroupbinding deltallm_routegroupbinding_route_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routegroupbinding
    ADD CONSTRAINT deltallm_routegroupbinding_route_group_id_fkey FOREIGN KEY (route_group_id) REFERENCES public.deltallm_routegroup(route_group_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_routegroupmember deltallm_routegroupmember_deployment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routegroupmember
    ADD CONSTRAINT deltallm_routegroupmember_deployment_id_fkey FOREIGN KEY (deployment_id) REFERENCES public.deltallm_modeldeployment(deployment_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_routegroupmember deltallm_routegroupmember_route_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routegroupmember
    ADD CONSTRAINT deltallm_routegroupmember_route_group_id_fkey FOREIGN KEY (route_group_id) REFERENCES public.deltallm_routegroup(route_group_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_routepolicy deltallm_routepolicy_route_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_routepolicy
    ADD CONSTRAINT deltallm_routepolicy_route_group_id_fkey FOREIGN KEY (route_group_id) REFERENCES public.deltallm_routegroup(route_group_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_serviceaccount deltallm_serviceaccount_created_by_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_serviceaccount
    ADD CONSTRAINT deltallm_serviceaccount_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_serviceaccount deltallm_serviceaccount_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_serviceaccount
    ADD CONSTRAINT deltallm_serviceaccount_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.deltallm_teamtable(team_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_teammembership deltallm_teammembership_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_teammembership
    ADD CONSTRAINT deltallm_teammembership_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: deltallm_usertable deltallm_usertable_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_usertable
    ADD CONSTRAINT deltallm_usertable_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.deltallm_teamtable(team_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_verificationtoken deltallm_verificationtoken_owner_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_verificationtoken
    ADD CONSTRAINT deltallm_verificationtoken_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.deltallm_platformaccount(account_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_verificationtoken deltallm_verificationtoken_owner_service_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_verificationtoken
    ADD CONSTRAINT deltallm_verificationtoken_owner_service_account_id_fkey FOREIGN KEY (owner_service_account_id) REFERENCES public.deltallm_serviceaccount(service_account_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_verificationtoken deltallm_verificationtoken_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_verificationtoken
    ADD CONSTRAINT deltallm_verificationtoken_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.deltallm_teamtable(team_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- Name: deltallm_verificationtoken deltallm_verificationtoken_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deltallm_verificationtoken
    ADD CONSTRAINT deltallm_verificationtoken_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.deltallm_usertable(user_id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--
