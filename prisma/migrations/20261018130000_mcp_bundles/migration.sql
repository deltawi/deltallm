-- An MCP bundle shows the tools of several MCP servers at one endpoint.
-- A bundle is a view. It does not give access to a tool.
CREATE TABLE "deltallm_mcpbundle" (
    "mcp_bundle_id" TEXT NOT NULL,
    "bundle_key" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "owner_scope_type" TEXT NOT NULL DEFAULT 'global',
    "owner_scope_id" TEXT,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "metadata" JSONB,
    "created_by_account_id" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_mcpbundle_pkey" PRIMARY KEY ("mcp_bundle_id"),
    CONSTRAINT "deltallm_mcpbundle_owner_scope_check" CHECK (
        ("owner_scope_type" = 'global' AND "owner_scope_id" IS NULL)
        OR ("owner_scope_type" IN ('organization', 'team') AND "owner_scope_id" IS NOT NULL)
    )
);

CREATE UNIQUE INDEX "deltallm_mcpbundle_bundle_key_key" ON "deltallm_mcpbundle"("bundle_key");
CREATE INDEX "deltallm_mcpbundle_owner_scope_idx" ON "deltallm_mcpbundle"("owner_scope_type", "owner_scope_id");

CREATE TABLE "deltallm_mcpbundlemember" (
    "mcp_bundle_member_id" TEXT NOT NULL,
    "mcp_bundle_id" TEXT NOT NULL,
    "mcp_server_id" TEXT NOT NULL,
    "position" INTEGER NOT NULL DEFAULT 0,
    "all_tools" BOOLEAN NOT NULL DEFAULT false,
    "tool_allowlist" TEXT[],
    "name_prefix" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "deltallm_mcpbundlemember_pkey" PRIMARY KEY ("mcp_bundle_member_id")
);

CREATE UNIQUE INDEX "deltallm_mcpbundlemember_bundle_server_key" ON "deltallm_mcpbundlemember"("mcp_bundle_id", "mcp_server_id");
CREATE INDEX "deltallm_mcpbundlemember_server_idx" ON "deltallm_mcpbundlemember"("mcp_server_id");

ALTER TABLE "deltallm_mcpbundlemember" ADD CONSTRAINT "deltallm_mcpbundlemember_mcp_bundle_id_fkey" FOREIGN KEY ("mcp_bundle_id") REFERENCES "deltallm_mcpbundle"("mcp_bundle_id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "deltallm_mcpbundlemember" ADD CONSTRAINT "deltallm_mcpbundlemember_mcp_server_id_fkey" FOREIGN KEY ("mcp_server_id") REFERENCES "deltallm_mcpserver"("mcp_server_id") ON DELETE CASCADE ON UPDATE CASCADE;
