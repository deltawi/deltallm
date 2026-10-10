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
{{- if or (not (get $general "audit_enabled")) (ne (get $general "audit_ingestion_mode") "outbox") (not (get $general "audit_ingestion_worker_enabled")) (not (get $general "cache_invalidation_worker_enabled")) -}}
{{- fail "external_auth requires durable audit and cache invalidation workers on API pods" -}}
{{- end -}}
{{- if gt (int (get $general "api_key_auth_cache_ttl_seconds")) 60 -}}
{{- fail "external_auth requires api_key_auth_cache_ttl_seconds <= 60" -}}
{{- end -}}
{{- $capacity := $root.Values.externalAuthCapacity -}}
{{- $report := include "deltallm.capacityReport" $root | fromJson -}}
{{- $api := get $report.roles "api" -}}
{{- $peak := div $api.peakProcesses $api.processesPerPod -}}
{{- if gt (int $peak) (int $capacity.maximumApiPods) -}}
{{- fail "external_auth exceeds externalAuthCapacity.maximumApiPods including rolling surge and retiring pods" -}}
{{- end -}}
{{- $knownOther := sub $report.postgresqlConnections (mul 4 $api.peakProcesses) -}}
{{- if lt (int $capacity.otherReservedConnections) (int $knownOther) -}}
{{- fail "externalAuthCapacity.otherReservedConnections must include API main and telemetry pools, workers, and all other database clients" -}}
{{- end -}}
{{- $required := add (int $capacity.otherReservedConnections) (mul 4 (int $capacity.maximumApiPods) $api.processesPerPod) -}}
{{- if lt (int $capacity.postgresConnectionBudget) (int $required) -}}
{{- fail "externalAuthCapacity.postgresConnectionBudget cannot cover additive external-auth pools" -}}
{{- end -}}
{{- end -}}
{{- end -}}
