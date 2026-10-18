-- Per-user OAuth connections for MCP servers with auth_mode 'oauth2'.
-- Each row holds the tokens of one platform account for one MCP server.
-- Token columns hold AES-GCM envelopes, never plaintext. A connection is
-- deleted with its account or its server.
CREATE TABLE "deltallm_mcpoauthconnection" (
  "mcp_oauth_connection_id" TEXT NOT NULL,
  "mcp_server_id" TEXT NOT NULL,
  "account_id" TEXT NOT NULL,
  "status" TEXT NOT NULL DEFAULT 'connected',
  "status_reason" TEXT,
  "client_id" TEXT NOT NULL,
  "access_token_ciphertext" TEXT NOT NULL,
  "refresh_token_ciphertext" TEXT,
  "token_type" TEXT,
  "scopes" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  "account_label" TEXT,
  "expires_at" TIMESTAMP(3),
  "token_version" INTEGER NOT NULL DEFAULT 1,
  "refresh_lease_owner" TEXT,
  "refresh_lease_expires_at" TIMESTAMP(3),
  "connected_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_refreshed_at" TIMESTAMP(3),
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_mcpoauthconnection_pkey" PRIMARY KEY ("mcp_oauth_connection_id"),
  CONSTRAINT "deltallm_mcpoauthconnection_status_check"
    CHECK ("status" IN ('connected', 'needs_reconnect')),
  CONSTRAINT "deltallm_mcpoauthconnection_server_fk"
    FOREIGN KEY ("mcp_server_id") REFERENCES "deltallm_mcpserver"("mcp_server_id")
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT "deltallm_mcpoauthconnection_account_fk"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE UNIQUE INDEX "deltallm_mcpoauthconnection_server_account_key"
ON "deltallm_mcpoauthconnection"("mcp_server_id", "account_id");
CREATE INDEX "deltallm_mcpoauthconnection_account_idx"
ON "deltallm_mcpoauthconnection"("account_id");

-- Pending authorization requests. The gateway deletes a row when it uses it,
-- so each state value works one time. Rows expire after a short time.
CREATE TABLE "deltallm_mcpoauthstate" (
  "state_hash" TEXT NOT NULL,
  "mcp_server_id" TEXT NOT NULL,
  "account_id" TEXT NOT NULL,
  "client_id" TEXT NOT NULL,
  "redirect_uri" TEXT NOT NULL,
  "code_verifier_ciphertext" TEXT NOT NULL,
  "expires_at" TIMESTAMP(3) NOT NULL,
  "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "deltallm_mcpoauthstate_pkey" PRIMARY KEY ("state_hash"),
  CONSTRAINT "deltallm_mcpoauthstate_server_fk"
    FOREIGN KEY ("mcp_server_id") REFERENCES "deltallm_mcpserver"("mcp_server_id")
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT "deltallm_mcpoauthstate_account_fk"
    FOREIGN KEY ("account_id") REFERENCES "deltallm_platformaccount"("account_id")
    ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX "deltallm_mcpoauthstate_account_created_idx"
ON "deltallm_mcpoauthstate"("account_id", "created_at");
CREATE INDEX "deltallm_mcpoauthstate_expires_idx"
ON "deltallm_mcpoauthstate"("expires_at");
CREATE INDEX "deltallm_mcpoauthstate_server_idx"
ON "deltallm_mcpoauthstate"("mcp_server_id");
