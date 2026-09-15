INSERT INTO deltallm_organizationtable (id,organization_id) VALUES ('pr5-upgrade','pr5-upgrade');
INSERT INTO deltallm_teamtable (team_id,organization_id,models) VALUES ('pr5-upgrade','pr5-upgrade',ARRAY[]::text[]);
INSERT INTO deltallm_teammodelspend (team_id,model,spend,updated_at) VALUES ('pr5-upgrade','model',12,NOW());

DO $seed$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='deltallm_teammodelspend' AND column_name='reconciled_at') THEN
    EXECUTE 'UPDATE deltallm_teammodelspend SET reconciled_at=NOW() WHERE team_id=''pr5-upgrade''';
  END IF;
END $seed$;
