{{/* Mutates the report assembled by capacityReport; arithmetic has one owner. */}}
{{- define "deltallm.extendedCapacity" -}}
{{- $root := .root -}}
{{- $report := .report -}}
{{- $e := $root.Values.dependencyCapacity.extended -}}
{{- if and $e.enabled (lt (int $e.auxiliaryHttpConnectionsPerProcess) 20) -}}
{{- fail "Extended capacity requires at least 20 auxiliary HTTP connections per process for the shared notification client; reserve optional integrations separately" -}}
{{- end -}}
{{- $fd := $e.fileDescriptors -}}
{{- $maxPodFD := 0 -}}
{{- $peakPods := 0 -}}
{{- range $name, $role := $report.roles -}}
{{- $peakPods = add $peakPods (div $role.peakProcesses $role.processesPerPod) -}}
{{- $g := (include (ternary "deltallm.apiConfigYaml" "deltallm.batchWorkerConfigYaml" (eq $name "api")) $root | fromYaml).general_settings -}}
{{- $engines := 2 -}}
{{- if or (eq $g.audit_ingestion_mode "outbox") (eq $g.spend_ingestion_mode "outbox") -}}
{{- $engines = 4 -}}
{{- if $g.spend_operation_intents_enabled -}}{{- $engines = 5 -}}{{- end -}}
{{- end -}}
{{- $p := $role.pools -}}
{{/* Prisma's HTTPX client uses the default 100-connection ceiling per engine. */}}
{{- $pythonFD := add $p.upstreamHttp $p.controlHttp $p.auxiliaryHttp $p.redisCritical $p.redisCache (mul $engines 100) $fd.inboundConnectionsPerProcess $fd.otherPerProcess $fd.headroomPerProcess -}}
{{- $engineFD := add (max $g.db_pool_size $g.db_foreground_pool_size $g.telemetry_db_pool_size $g.telemetry_worker_db_pool_size) 100 $fd.otherPerProcess $fd.headroomPerProcess -}}
{{- $podFD := mul (add $pythonFD (mul $engines $engineFD)) $role.processesPerPod -}}
{{- $maxPodFD = max $maxPodFD $podFD -}}
{{- $_ := set $role "fileDescriptors" (dict "python" $pythonFD "engine" $engineFD "engineProcesses" $engines "pod" $podFD) -}}
{{- if $e.enabled -}}
{{- if gt $pythonFD (int $fd.processLimit) -}}{{- fail (printf "%s Python file descriptor budget exceeds declared process limit" $name) -}}{{- end -}}
{{- if gt $engineFD (int $fd.engineLimit) -}}{{- fail (printf "%s Prisma file descriptor budget exceeds declared engine limit" $name) -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- if and $e.enabled (lt (int $fd.maxPodsPerNode) $peakPods) -}}
{{- fail "Node placement allowance must cover all peak pods; preferred spreading is not a hard placement bound" -}}
{{- end -}}
{{- $nodeFD := add (include "deltallm.capacityProduct" (list $maxPodFD $fd.maxPodsPerNode) | int64) $fd.nodeReserved -}}
{{- if and $e.enabled (gt $nodeFD (int $fd.nodeLimit)) -}}{{- fail "Node file descriptor budget exceeds declared maximum" -}}{{- end -}}
{{- $_ := set $report "fileDescriptors" (dict "processLimit" $fd.processLimit "engineLimit" $fd.engineLimit "inboundConnectionsPerProcess" $fd.inboundConnectionsPerProcess "nodePeak" $nodeFD "nodeLimit" $fd.nodeLimit "maxPodsPerNode" $fd.maxPodsPerNode) -}}
{{- $providers := dict -}}
{{- range $name, $domain := $e.providerDomains -}}
{{- $rpm := int $domain.reservedRpm -}}
{{- $tpm := int $domain.reservedTpm -}}
{{- $concurrent := int $domain.reservedConcurrency -}}
{{- range $roleName, $role := $report.roles -}}
{{- $requestRate := ternary $domain.apiRpmPerProcess $domain.workerRpmPerProcess (eq $roleName "api") -}}
{{- $attempts := include "deltallm.capacityProduct" (list $role.peakProcesses $requestRate $domain.attemptsPerRequest) | int64 -}}
{{- $rpm = add $rpm $attempts -}}
{{- $tpm = add $tpm (include "deltallm.capacityProduct" (list $attempts $domain.tokensPerAttempt) | int64) -}}
{{/* Conservatively allow all upstream transports to target each failover domain. */}}
{{- $concurrent = add $concurrent (mul $role.peakProcesses $role.pools.upstreamHttp) -}}
{{- end -}}
{{- if $e.enabled -}}
{{- if gt $rpm (int $domain.rpm) -}}{{- fail (printf "Provider %s RPM allocation exceeds declared quota" $name) -}}{{- end -}}
{{- if gt $tpm (int $domain.tpm) -}}{{- fail (printf "Provider %s TPM allocation exceeds declared quota" $name) -}}{{- end -}}
{{- if gt $concurrent (int $domain.concurrency) -}}{{- fail (printf "Provider %s concurrent transport allocation exceeds declared quota" $name) -}}{{- end -}}
{{- end -}}
{{- $_ := set $providers $name (dict "rpm" $rpm "tpm" $tpm "concurrency" $concurrent "maximumRpm" $domain.rpm "maximumTpm" $domain.tpm "maximumConcurrency" $domain.concurrency "enforcement" "workload-envelope" "modelIds" $domain.modelIds) -}}
{{- end -}}
{{- if and $e.enabled (empty $providers) -}}
{{- fail "Extended capacity requires explicit providerDomains; use actual quotas or the documented local-mock fixture" -}}
{{- end -}}
{{- $_ := set $report "providerDomains" $providers -}}
{{- end -}}
