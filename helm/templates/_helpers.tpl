{{- define "deltallm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "deltallm.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "deltallm.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "deltallm.labels" -}}
helm.sh/chart: {{ include "deltallm.chart" . }}
{{ include "deltallm.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "deltallm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "deltallm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "deltallm.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "deltallm.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "deltallm.appSecretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- printf "%s-app" (include "deltallm.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "deltallm.s3SecretName" -}}
{{- if .Values.s3.existingSecret.name -}}
{{- .Values.s3.existingSecret.name -}}
{{- else -}}
{{- printf "%s-s3" (include "deltallm.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "deltallm.postgresqlHost" -}}
{{- printf "%s-postgresql" (include "deltallm.fullname" .) -}}
{{- end -}}

{{- define "deltallm.redisHost" -}}
{{- printf "%s-redis-master" (include "deltallm.fullname" .) -}}
{{- end -}}

{{- define "deltallm.bundledDatabaseUrl" -}}
{{- $username := required "postgresql.auth.username is required when postgresql.enabled=true and runtime.database.url is not set" .Values.postgresql.auth.username -}}
{{- $password := required "postgresql.auth.password is required when postgresql.enabled=true and runtime.database.url is not set" .Values.postgresql.auth.password -}}
{{- $database := required "postgresql.auth.database is required when postgresql.enabled=true and runtime.database.url is not set" .Values.postgresql.auth.database -}}
{{- printf "postgresql://%s:%s@%s:5432/%s" ($username | urlquery) ($password | urlquery) (include "deltallm.postgresqlHost" .) $database -}}
{{- end -}}

{{- define "deltallm.bundledAsyncDatabaseUrl" -}}
{{ include "deltallm.bundledDatabaseUrl" . | replace "postgresql://" "postgresql+asyncpg://" }}
{{- end -}}

{{- define "deltallm.bundledRedisUrl" -}}
{{- if .Values.redis.auth.enabled -}}
{{- $password := required "redis.auth.password is required when redis.enabled=true and redis.auth.enabled=true unless runtime.redis.existingSecret.name or runtime.redis.url is set" .Values.redis.auth.password -}}
{{- printf "redis://:%s@%s:6379/0" ($password | urlquery) (include "deltallm.redisHost" .) -}}
{{- else -}}
{{- printf "redis://%s:6379/0" (include "deltallm.redisHost" .) -}}
{{- end -}}
{{- end -}}
