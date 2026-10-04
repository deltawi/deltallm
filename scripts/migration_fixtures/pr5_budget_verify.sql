DO $pr5$
BEGIN
  IF to_regprocedure('deltallm_enqueue_budget_notification(text,text,numeric,numeric,numeric,integer)') IS NULL
     OR to_regclass('deltallm_budgetnotification_due_idx') IS NULL
     OR to_regclass('deltallm_promptbinding_enabled_top_idx') IS NULL THEN
    RAISE EXCEPTION 'PR5 notification schema missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='deltallm_teammodelspend' AND column_name='reconciled_at'
                   AND is_nullable='YES' AND column_default IS NULL) THEN
    RAISE EXCEPTION 'new counters must default to unverified';
  END IF;
  IF EXISTS (SELECT 1 FROM deltallm_organizationtable WHERE organization_id='pr5-upgrade')
     AND NOT EXISTS (SELECT 1 FROM deltallm_teammodelspend WHERE team_id='pr5-upgrade'
                     AND model='model' AND reconciled_at IS NOT NULL AND spend=12
                     AND COALESCE(spend_exact,spend::numeric)=12 AND reserved_spend_exact=0) THEN
    RAISE EXCEPTION 'upgrade lost existing counter authority or amount';
  END IF;
  IF EXISTS (SELECT 1 FROM deltallm_organizationtable WHERE organization_id='pr5-upgrade')
     AND NOT EXISTS (SELECT 1 FROM deltallm_teammodelspend WHERE team_id='pr5-upgrade'
                     AND model='exact-model' AND reconciled_at IS NOT NULL
                     AND spend_exact=12.000000000000000001 AND reserved_spend_exact=0) THEN
    RAISE EXCEPTION 'upgrade lost exact counter precision';
  END IF;
END $pr5$;
