{{- define "deltallm.hasRuntimeDependencies" -}}
{{- $dbSecretName := .Values.runtime.database.existingSecret.name -}}
{{- $redisSecretName := .Values.runtime.redis.existingSecret.name -}}
{{- if or $dbSecretName .Values.runtime.database.url .Values.postgresql.enabled $redisSecretName .Values.runtime.redis.url .Values.redis.enabled -}}true{{- end -}}
{{- end -}}

{{- define "deltallm.databaseEnv" -}}
{{- $dbSecretName := .Values.runtime.database.existingSecret.name -}}
{{- if or $dbSecretName .Values.runtime.database.url .Values.postgresql.enabled -}}
- name: DATABASE_URL
  {{- if $dbSecretName }}
  valueFrom:
    secretKeyRef:
      name: {{ $dbSecretName }}
      key: {{ .Values.runtime.database.existingSecret.urlKey }}
  {{- else if .Values.runtime.database.url }}
  value: {{ .Values.runtime.database.url | quote }}
  {{- else }}
  value: {{ include "deltallm.bundledDatabaseUrl" . | quote }}
  {{- end }}
{{- end }}
{{- end -}}

{{- define "deltallm.redisEnv" -}}
{{- $redisSecretName := .Values.runtime.redis.existingSecret.name -}}
{{- if or $redisSecretName .Values.runtime.redis.url .Values.redis.enabled -}}
- name: REDIS_URL
  {{- if $redisSecretName }}
  valueFrom:
    secretKeyRef:
      name: {{ $redisSecretName }}
      key: {{ .Values.runtime.redis.existingSecret.urlKey }}
  {{- else if .Values.runtime.redis.url }}
  value: {{ .Values.runtime.redis.url | quote }}
  {{- else }}
  value: {{ include "deltallm.bundledRedisUrl" . | quote }}
  {{- end }}
{{- end }}
{{- end -}}

{{- define "deltallm.deltallmRedisEnv" -}}
{{- $redisSecretName := .Values.runtime.redis.existingSecret.name -}}
{{- if or $redisSecretName .Values.runtime.redis.url .Values.redis.enabled -}}
- name: DELTALLM_REDIS_URL
  {{- if $redisSecretName }}
  valueFrom:
    secretKeyRef:
      name: {{ $redisSecretName }}
      key: {{ .Values.runtime.redis.existingSecret.urlKey }}
  {{- else if .Values.runtime.redis.url }}
  value: {{ .Values.runtime.redis.url | quote }}
  {{- else }}
  value: {{ include "deltallm.bundledRedisUrl" . | quote }}
  {{- end }}
{{- end }}
{{- end -}}

{{- define "deltallm.s3Env" -}}
{{- $s3SecretName := include "deltallm.s3SecretName" . -}}
{{- if .Values.s3.enabled -}}
- name: AWS_DEFAULT_REGION
  value: {{ .Values.s3.region | quote }}
- name: DELTALLM_S3_BUCKET
  value: {{ .Values.s3.bucket | quote }}
{{- if or .Values.s3.existingSecret.name .Values.s3.accessKeyId }}
- name: AWS_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ $s3SecretName }}
      key: {{ .Values.s3.existingSecret.accessKeyIdKey }}
{{- end }}
{{- if or .Values.s3.existingSecret.name .Values.s3.secretAccessKey }}
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $s3SecretName }}
      key: {{ .Values.s3.existingSecret.secretAccessKeyKey }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "deltallm.runtimeEnv" -}}
{{- $root := .root -}}
{{- $extraEnv := default (list) .extraEnv -}}
{{- $databaseEnv := include "deltallm.databaseEnv" $root -}}
{{- $redisEnv := include "deltallm.redisEnv" $root -}}
{{- $deltallmRedisEnv := include "deltallm.deltallmRedisEnv" $root -}}
{{- $s3Env := include "deltallm.s3Env" $root -}}
- name: HOST
  value: "0.0.0.0"
- name: PORT
  value: {{ $root.Values.service.port | quote }}
- name: DELTALLM_CONFIG_PATH
  value: /app/config/config.yaml
- name: DELTALLM_MASTER_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "deltallm.appSecretName" $root }}
      key: {{ $root.Values.secret.keys.masterKey }}
- name: DELTALLM_SALT_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "deltallm.appSecretName" $root }}
      key: {{ $root.Values.secret.keys.saltKey }}
{{- if $databaseEnv }}
{{ $databaseEnv }}
{{- end }}
{{- if $redisEnv }}
{{ $redisEnv }}
{{- end }}
{{- if $deltallmRedisEnv }}
{{ $deltallmRedisEnv }}
{{- end }}
{{- if $s3Env }}
{{ $s3Env }}
{{- end }}
{{- with $root.Values.env }}
{{ toYaml . }}
{{- end }}
{{- with $extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "deltallm.envFrom" -}}
{{- $root := .root -}}
{{- $extraEnvFrom := default (list) .extraEnvFrom -}}
{{- if or $root.Values.envFrom $extraEnvFrom -}}
envFrom:
{{- with $root.Values.envFrom -}}
{{ toYaml . | nindent 2 }}
{{- end }}
{{- with $extraEnvFrom -}}
{{ toYaml . | nindent 2 }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "deltallm.dependencyWaitInitContainers" -}}
{{- if and .Values.dependencyWait.enabled (include "deltallm.hasRuntimeDependencies" .) -}}
{{- $databaseEnv := include "deltallm.databaseEnv" . -}}
{{- $redisEnv := include "deltallm.redisEnv" . -}}
initContainers:
  - name: wait-for-runtime-dependencies
    image: "{{ .Values.image.repository }}:{{ default .Chart.AppVersion .Values.image.tag }}"
    imagePullPolicy: {{ .Values.image.pullPolicy }}
    securityContext:
      {{- toYaml .Values.securityContext | nindent 6 }}
    command:
      - python
      - -c
      - |
        import os
        import socket
        import sys
        import time
        from urllib.parse import urlparse

        deadline = time.time() + {{ .Values.dependencyWait.timeoutSeconds }}
        interval = {{ .Values.dependencyWait.periodSeconds }}
        targets = []

        database_url = os.getenv("DATABASE_URL", "").strip()
        if database_url:
            parsed = urlparse(database_url)
            targets.append(("database", parsed.hostname, parsed.port or 5432))

        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            parsed = urlparse(redis_url)
            targets.append(("redis", parsed.hostname, parsed.port or 6379))

        for name, host, port in targets:
            while True:
                try:
                    with socket.create_connection((host, port), timeout=5):
                        print(f"{name} is reachable at {host}:{port}", flush=True)
                        break
                except OSError as exc:
                    if time.time() >= deadline:
                        print(f"Timed out waiting for {name} at {host}:{port}: {exc}", file=sys.stderr, flush=True)
                        raise SystemExit(1)
                    print(f"Waiting for {name} at {host}:{port}: {exc}", flush=True)
                    time.sleep(interval)
    env:
      {{- if $databaseEnv -}}
{{ $databaseEnv | nindent 6 }}
      {{- end }}
      {{- if $redisEnv -}}
{{ $redisEnv | nindent 6 }}
      {{- end }}
{{- end }}
{{- end -}}

{{- define "deltallm.probes" -}}
{{- if .Values.probes.startup.enabled -}}
startupProbe:
  httpGet:
    path: {{ .Values.probes.startup.path }}
    port: http
  failureThreshold: {{ .Values.probes.startup.failureThreshold }}
  periodSeconds: {{ .Values.probes.startup.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.startup.timeoutSeconds }}
{{- end }}
livenessProbe:
  httpGet:
    path: {{ .Values.probes.liveness.path }}
    port: http
  initialDelaySeconds: {{ .Values.probes.liveness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.liveness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.liveness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.liveness.failureThreshold }}
readinessProbe:
  httpGet:
    path: {{ .Values.probes.readiness.path }}
    port: http
  initialDelaySeconds: {{ .Values.probes.readiness.initialDelaySeconds }}
  periodSeconds: {{ .Values.probes.readiness.periodSeconds }}
  timeoutSeconds: {{ .Values.probes.readiness.timeoutSeconds }}
  failureThreshold: {{ .Values.probes.readiness.failureThreshold }}
{{- end -}}
