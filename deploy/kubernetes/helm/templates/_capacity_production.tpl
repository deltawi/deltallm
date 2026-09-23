{{- define "deltallm.validateSaturationScaling" -}}
{{- $a := .Values.autoscaling -}}
{{- if gt (int $a.minReplicas) (int $a.maxReplicas) -}}
{{- fail "HPA minReplicas must not exceed maxReplicas" -}}
{{- end -}}
{{- if and $a.enabled $a.admittedInflight.enabled -}}
{{- $g := (include "deltallm.apiConfigYaml" . | fromYaml).general_settings -}}
{{- if or (not $g.gateway_ingress_enabled) (not .Values.prometheus.customMetrics.enabled) -}}
{{- fail "Admitted in-flight HPA requires gateway ingress and the custom metrics adapter integration" -}}
{{- end -}}
{{- if ge (int $a.admittedInflight.targetAverageValue) (int $g.gateway_ingress_max_active) -}}
{{- fail "Admitted in-flight HPA target must be below the per-process ingress ceiling" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.managedLifecycle.production $a.enabled (not $a.admittedInflight.enabled) -}}
{{- fail "Production API autoscaling requires admitted in-flight saturation" -}}
{{- end -}}
{{- if and .Values.managedLifecycle.production $a.enabled (lt (int $a.scaleDownStabilizationSeconds) (int .Values.terminationGracePeriodSeconds)) -}}
{{- fail "Production autoscaling.scaleDownStabilizationSeconds must cover pod termination grace" -}}
{{- end -}}
{{- $worker := .Values.batchWorker.autoscaling -}}
{{- if and .Values.batchWorker.enabled $worker.enabled (gt (int $worker.minReplicas) (int $worker.maxReplicas)) -}}
{{- fail "Batch worker HPA minReplicas must not exceed maxReplicas" -}}
{{- end -}}
{{- if and .Values.managedLifecycle.production .Values.batchWorker.enabled $worker.enabled (lt (int $worker.scaleDownStabilizationSeconds) (int .Values.terminationGracePeriodSeconds)) -}}
{{- fail "Batch worker autoscaling.scaleDownStabilizationSeconds must cover pod termination grace" -}}
{{- end -}}
{{- end -}}

{{- define "deltallm.validateProductionCapacity" -}}
{{- if .Values.managedLifecycle.production -}}
{{- if not .Values.dependencyCapacity.extended.enabled -}}
{{- fail "Production requires extended dependency capacity validation" -}}
{{- end -}}
{{- $configs := list (include "deltallm.apiConfigYaml" . | fromYaml) -}}
{{- if .Values.batchWorker.enabled -}}{{- $configs = append $configs (include "deltallm.batchWorkerConfigYaml" . | fromYaml) -}}{{- end -}}
{{- range $config := $configs -}}
{{- $g := $config.general_settings -}}
{{- range $flag := list "gateway_ingress_enabled" "gateway_preflight_capacity_enabled" "spend_operation_intents_enabled" "spend_ingestion_worker_enabled" "audit_ingestion_worker_enabled" "audit_enabled" -}}
{{- if not (get $g $flag) -}}{{- fail (printf "Production requires %s" $flag) -}}{{- end -}}
{{- end -}}
{{- if or (ne $g.redis_degraded_mode "fail_closed") (ne $g.budget_enforcement_query_mode "combined") (ne $g.audit_ingestion_mode "outbox") (ne $g.spend_ingestion_mode "outbox") -}}
{{- fail "Production requires fail_closed Redis controls, combined budgets and durable audit/spend outboxes" -}}
{{- end -}}
{{- if or (ne $g.model_deployment_source "db_only") $g.model_deployment_bootstrap_from_config -}}
{{- fail "Production provider capacity requires a pre-seeded DB-only deployment catalog" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
