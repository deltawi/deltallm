CREATE TABLE "deltallm_mcpserver" (
  "mcp_server_id" TEXT NOT NULL,
  "server_key" TEXT NOT NULL,
  "name" TEXT NOT NULL,
  "description" TEXT,
  "transport" TEXT NOT NULL DEFAULT 'streamable_http',
  "base_url" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "auth_mode" TEXT NOT NULL DEFAULT 'none',
  "auth_config" JSONB,
  "forwarded_headers_allowlist" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "request_timeout_ms" INTEGER NOT NULL DEFAULT 30000,
  "capabilities_json" JSONB,
  "capabilities_etag" TEXT,
  "capabilities_fetched_at" TIMESTAMP(3),
  "last_health_status" TEXT,
  "last_health_error" TEXT,
  "last_health_at" TIMESTAMP(3),
  "last_health_latency_ms" INTEGER,
  "metadata" JSONB,
  "created_by_account_id" TEXT,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_mcpserver_pkey" PRIMARY KEY ("mcp_server_id")
);

CREATE UNIQUE INDEX "deltallm_mcpserver_server_key_key" ON "deltallm_mcpserver"("server_key");
CREATE INDEX "deltallm_mcpserver_enabled_idx" ON "deltallm_mcpserver"("enabled");
CREATE INDEX "deltallm_mcpserver_created_at_idx" ON "deltallm_mcpserver"("created_at");

CREATE TABLE "deltallm_mcpbinding" (
  "mcp_binding_id" TEXT NOT NULL,
  "mcp_server_id" TEXT NOT NULL,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "tool_allowlist" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_mcpbinding_pkey" PRIMARY KEY ("mcp_binding_id"),
  CONSTRAINT "deltallm_mcpbinding_server_fk"
    FOREIGN KEY ("mcp_server_id") REFERENCES "deltallm_mcpserver"("mcp_server_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX "deltallm_mcpbinding_server_scope_key" ON "deltallm_mcpbinding"("mcp_server_id", "scope_type", "scope_id");
CREATE INDEX "deltallm_mcpbinding_scope_idx" ON "deltallm_mcpbinding"("scope_type", "scope_id", "enabled");
CREATE INDEX "deltallm_mcpbinding_server_idx" ON "deltallm_mcpbinding"("mcp_server_id");

CREATE TABLE "deltallm_mcptoolpolicy" (
  "mcp_tool_policy_id" TEXT NOT NULL,
  "mcp_server_id" TEXT NOT NULL,
  "tool_name" TEXT NOT NULL,
  "scope_type" TEXT NOT NULL,
  "scope_id" TEXT NOT NULL,
  "enabled" BOOLEAN NOT NULL DEFAULT true,
  "require_approval" TEXT,
  "max_rpm" INTEGER,
  "max_concurrency" INTEGER,
  "result_cache_ttl_seconds" INTEGER,
  "metadata" JSONB,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_mcptoolpolicy_pkey" PRIMARY KEY ("mcp_tool_policy_id"),
  CONSTRAINT "deltallm_mcptoolpolicy_server_fk"
    FOREIGN KEY ("mcp_server_id") REFERENCES "deltallm_mcpserver"("mcp_server_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX "deltallm_mcptoolpolicy_server_tool_scope_key" ON "deltallm_mcptoolpolicy"("mcp_server_id", "tool_name", "scope_type", "scope_id");
CREATE INDEX "deltallm_mcptoolpolicy_server_tool_idx" ON "deltallm_mcptoolpolicy"("mcp_server_id", "tool_name");
CREATE INDEX "deltallm_mcptoolpolicy_scope_idx" ON "deltallm_mcptoolpolicy"("scope_type", "scope_id", "enabled");
