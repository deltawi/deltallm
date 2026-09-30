ALTER TABLE "deltallm_platformaccount"
  ADD CONSTRAINT "deltallm_platformaccount_api_namespace_format_check"
  CHECK (
    "api_namespace" IS NULL
    OR (
      "api_namespace" ~ '^[a-z0-9]([a-z0-9-]{1,30}[a-z0-9])$'
      AND position('--' IN "api_namespace") = 0
    )
  );

CREATE FUNCTION "deltallm_enforce_api_namespace_immutable"()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD."api_namespace" IS NOT NULL
    AND NEW."api_namespace" IS DISTINCT FROM OLD."api_namespace"
  THEN
    RAISE EXCEPTION 'creator API namespace is immutable after first use'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER "deltallm_platformaccount_api_namespace_immutable_trg"
BEFORE UPDATE OF "api_namespace" ON "deltallm_platformaccount"
FOR EACH ROW
EXECUTE FUNCTION "deltallm_enforce_api_namespace_immutable"();
