{{- define "deltallm.validateLifecycleRole" -}}
{{- $root := .root -}}
{{- $general := .general -}}
{{- $sum := addf $general.lifecycle_withdrawal_seconds $general.lifecycle_request_drain_seconds $general.lifecycle_cancellation_seconds $general.lifecycle_worker_drain_seconds $general.lifecycle_close_seconds -}}
{{- if gt $sum (float64 $general.lifecycle_shutdown_seconds) -}}
{{- fail (printf "%s lifecycle_shutdown_seconds must reserve every shutdown phase" .role) -}}
{{- end -}}
{{- if gt (addf $general.lifecycle_shutdown_seconds $root.Values.managedLifecycle.exitMarginSeconds) (float64 $root.Values.terminationGracePeriodSeconds) -}}
{{- fail (printf "%s terminationGracePeriodSeconds must cover lifecycle_shutdown_seconds and exitMarginSeconds" .role) -}}
{{- end -}}
{{- if ge (float64 $general.readiness_probe_timeout_seconds) (float64 $root.Values.probes.readiness.timeoutSeconds) -}}
{{- fail "readiness_probe_timeout_seconds must be below the Kubernetes probe timeout" -}}
{{- end -}}
{{- if and $root.Values.managedLifecycle.production (ne $general.migration_mode "external") -}}
{{- fail "managed production requires migration_mode=external for every role" -}}
{{- end -}}
{{- end -}}

{{- define "deltallm.validateLifecycle" -}}
{{- $managed := .Values.managedLifecycle -}}
{{- if and $managed.production (not $managed.enabled) -}}
{{- fail "managed production cannot disable the process lifecycle" -}}
{{- end -}}
{{- if $managed.enabled -}}
{{- $command := list "python" "-m" "src.server" -}}
{{- range $override := list .Values.command .Values.batchWorker.command -}}
{{- if and $override (ne (toJson $override) (toJson $command)) -}}
{{- fail "managed lifecycle requires the image command or python -m src.server" -}}
{{- end -}}
{{- end -}}
{{- if or .Values.args .Values.batchWorker.args -}}
{{- fail "managed lifecycle uses HOST/PORT; custom process arguments require the development opt-out" -}}
{{- end -}}
{{- if or (ne (int .Values.dependencyCapacity.apiProcessesPerPod) 1) (ne (int .Values.dependencyCapacity.batchWorkerProcessesPerPod) 1) -}}
{{- fail "managed lifecycle requires one process per pod for both roles" -}}
{{- end -}}
{{- include "deltallm.validateLifecycleRole" (dict "root" . "role" "api" "general" (include "deltallm.apiConfigYaml" . | fromYaml).general_settings) -}}
{{- if .Values.batchWorker.enabled -}}
{{- include "deltallm.validateLifecycleRole" (dict "root" . "role" "worker" "general" (include "deltallm.batchWorkerConfigYaml" . | fromYaml).general_settings) -}}
{{- end -}}
{{- end -}}
{{- if $managed.production -}}
{{- range $scaling := list .Values.autoscaling .Values.batchWorker.autoscaling -}}
{{- if and $scaling.enabled (lt (int $scaling.scaleDownStabilizationSeconds) (int $.Values.terminationGracePeriodSeconds)) -}}
{{- fail "production HPA scaleDownStabilizationSeconds must cover pod terminationGracePeriodSeconds" -}}
{{- end -}}
{{- end -}}
{{- if and (not .Values.image.digest) (or (not .Values.image.tag) (eq .Values.image.tag "latest")) -}}
{{- fail "managed production requires an immutable release version or image digest" -}}
{{- end -}}
{{- if not (or (and .Values.migrationJob.enabled .Values.migrationJob.hook.enabled) (and .Values.migrationJob.external (not .Values.migrationJob.enabled))) -}}
{{- fail "managed production requires the pre-release migration hook or explicit external migration orchestration" -}}
{{- end -}}
{{- if lt (int .Values.dependencyCapacity.retiringGenerations) 2 -}}
{{- fail "managed production reserves two retiring generations for rollout and autoscaling overlap" -}}
{{- end -}}
{{- if .Release.IsUpgrade -}}
{{- $pods := lookup "v1" "Pod" .Release.Namespace "" | default dict -}}
{{- range $pod := (get $pods "items" | default list) -}}
{{- if and (eq (dig "metadata" "labels" "app.kubernetes.io/instance" "" $pod) $.Release.Name) (dig "metadata" "deletionTimestamp" "" $pod) -}}
{{- fail "wait for this release's terminating pods before starting another upgrade" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and .Values.migrationJob.enabled .Values.migrationJob.hook.enabled -}}
{{- if or .Values.postgresql.enabled (not (or .Values.runtime.database.url .Values.runtime.database.existingSecret.name)) -}}
{{- fail "migration pre-hooks require an externally ready database URL or pre-existing database Secret" -}}
{{- end -}}
{{- if ne (toJson .Values.migrationJob.command) (toJson (list "python" "-m" "src.prisma_bootstrap")) -}}
{{- fail "migration pre-hooks use src.prisma_bootstrap; run named coordinators explicitly before the release" -}}
{{- end -}}
{{- if le (float64 .Values.migrationJob.activeDeadlineSeconds) (float64 .Values.migrationJob.timeoutSeconds) -}}
{{- fail "migration activeDeadlineSeconds must exceed its subprocess timeoutSeconds" -}}
{{- end -}}
{{- end -}}
{{- end -}}
