{{- define "deltallm.validateExternalAuth" -}}
{{- $root := .root -}}
{{- $general := .general -}}
{{- $external := default (dict) (get $general "external_auth") -}}
{{- if get $external "enabled" -}}
{{- if ne (get $external "deployment_protocol") "external_customer_v1" -}}
{{- fail "external_auth requires deployment_protocol=external_customer_v1 after the coordinated rollout" -}}
{{- end -}}
{{- if or (not (get $external "integrations")) (not (get $external "allowed_origins")) -}}
{{- fail "external_auth requires explicit integrations and allowed_origins" -}}
{{- end -}}
{{- if or (ne (get $general "audit_ingestion_mode") "outbox") (not (get $general "audit_ingestion_worker_enabled")) (not (get $general "cache_invalidation_worker_enabled")) -}}
{{- fail "external_auth requires durable audit and cache invalidation workers on API pods" -}}
{{- end -}}
{{- if gt (int (get $general "api_key_auth_cache_ttl_seconds")) 60 -}}
{{- fail "external_auth requires api_key_auth_cache_ttl_seconds <= 60" -}}
{{- end -}}
{{- $capacity := $root.Values.externalAuthCapacity -}}
{{- $pods := int $root.Values.replicaCount -}}
{{- if $root.Values.autoscaling.enabled -}}{{- $pods = int $root.Values.autoscaling.maxReplicas -}}{{- end -}}
{{- $surge := 0 -}}
{{- if eq $root.Values.strategy.type "RollingUpdate" -}}
{{- $value := toString $root.Values.strategy.rollingUpdate.maxSurge -}}
{{- if hasSuffix "%" $value -}}
{{- $surge = int (ceil (divf (mulf (float64 (trimSuffix "%" $value)) $pods) 100)) -}}
{{- else -}}{{- $surge = int $value -}}{{- end -}}
{{- end -}}
{{- $peak := add $pods $surge -}}
{{- if gt (int $peak) (int $capacity.maximumApiPods) -}}
{{- fail "external_auth exceeds externalAuthCapacity.maximumApiPods including rolling surge" -}}
{{- end -}}
{{- $knownOther := mul (int $capacity.maximumApiPods) (add (int (default 20 (get $general "db_pool_size"))) (int (default 5 (get $general "telemetry_db_pool_size")))) -}}
{{- if $root.Values.batchWorker.enabled -}}
{{- $workerPods := int $root.Values.batchWorker.replicaCount -}}
{{- if $root.Values.batchWorker.autoscaling.enabled -}}{{- $workerPods = int $root.Values.batchWorker.autoscaling.maxReplicas -}}{{- end -}}
{{- $workerSurge := 0 -}}
{{- if eq $root.Values.strategy.type "RollingUpdate" -}}
{{- $value := toString $root.Values.strategy.rollingUpdate.maxSurge -}}
{{- if hasSuffix "%" $value -}}
{{- $workerSurge = int (ceil (divf (mulf (float64 (trimSuffix "%" $value)) $workerPods) 100)) -}}
{{- else -}}{{- $workerSurge = int $value -}}{{- end -}}
{{- end -}}
{{- $workerGeneral := mergeOverwrite (deepCopy $general) (default (dict) (get $root.Values.batchWorker.config "general_settings")) -}}
{{- $workerConnections := add (int (default 20 (get $workerGeneral "db_pool_size"))) (int (default 5 (get $workerGeneral "telemetry_db_pool_size"))) -}}
{{- $knownOther = add $knownOther (mul (add $workerPods $workerSurge) $workerConnections) -}}
{{- end -}}
{{- if lt (int $capacity.otherReservedConnections) (int $knownOther) -}}
{{- fail "externalAuthCapacity.otherReservedConnections must include API main and telemetry pools, workers, and all other database clients" -}}
{{- end -}}
{{- $required := add (int $capacity.otherReservedConnections) (mul 4 (int $capacity.maximumApiPods)) -}}
{{- if lt (int $capacity.postgresConnectionBudget) (int $required) -}}
{{- fail "externalAuthCapacity.postgresConnectionBudget cannot cover additive external-auth pools" -}}
{{- end -}}
{{- end -}}
{{- end -}}
