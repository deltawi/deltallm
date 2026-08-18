CREATE TABLE "deltallm_ui_branding_asset" (
    "asset_key" TEXT NOT NULL,
    "content_type" TEXT NOT NULL,
    "content" BYTEA NOT NULL,
    "content_sha256" TEXT NOT NULL,
    "size_bytes" INTEGER NOT NULL,
    "original_filename" TEXT,
    "updated_by" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "deltallm_ui_branding_asset_pkey" PRIMARY KEY ("asset_key"),
    CONSTRAINT "deltallm_ui_branding_asset_key_check"
        CHECK ("asset_key" IN ('logo_mark', 'logo_full', 'favicon')),
    CONSTRAINT "deltallm_ui_branding_asset_size_check"
        CHECK ("size_bytes" > 0 AND "size_bytes" <= 2097152),
    CONSTRAINT "deltallm_ui_branding_asset_sha256_check"
        CHECK ("content_sha256" ~ '^[0-9a-f]{64}$')
);
